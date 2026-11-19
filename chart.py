"""OHLCV proxy for the coin chart. Read-only market data only. A request NEVER waits on a rate-limited upstream.

Sources, in order (all keyless, none paid):
  1. fresh cache                     (per pool+timeframe, about a minute)
  2. GeckoTerminal OHLCV by pool     (real exchange candles). One background worker owns every upstream call: single file,
                                     >= GAP_S between calls, exponential cool-down after a 429, a per-minute budget, a
                                     priority queue (viewers first, pre-warm after). A request only waits for it for a few
                                     seconds, and only when the queue is short.
  3. last-good candles               (memory + disk, up to 6 h), topped up with our own price snapshots (pricehist.py)
  4. our own snapshot candles        (real prices collected every minute from Jupiter's keyless price API)
  5. derived candles                 (coarse, labelled approximate, from DexScreener's 5m/1h/24h price changes)
DexPaprika was dropped: it answers 402 Payment Required for this host. The browser still has its own GeckoTerminal
fallback (static/chart.js).

Every outcome is JSON. app.py turns ChartError and any unexpected error into a JSON body (never an HTML error page).
Last-good candles are persisted to disk and seeded from deploy/data/seed_caches.json, so a restart or redeploy still has
a chart for every coin.
"""
import json, os, re, threading, time, urllib.request, urllib.error
from collections import deque

import pricehist as ph

B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
      "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
CACHE_TTL = 60
TTL_BY_TF = {"1m": 60, "5m": 90, "15m": 120, "1h": 180, "4h": 300, "1d": 600}
SWR_MAX = 900            # a cached answer up to this old is served at once while a refresh runs in the background
STALE_MAX = 6 * 3600     # last-good may be served this long when upstreams fail
GAP_S = 2.5              # seconds between GeckoTerminal calls (one worker)
UPSTREAM_N, UPSTREAM_WIN = 14, 60   # calls per minute budget (the scanner and the confidence worker share the IP)
COOLDOWN0, COOLDOWN_MAX = 45, 600
INLINE_WAIT_S = 6.0      # longest a viewer's request waits for the worker
PREWARM_TFS = ("5m", "15m")
PREWARM_EVERY = 330      # seconds between background refreshes of a pre-warmed chart
LIMIT = 300
UA = {"User-Agent": "memecoin-scanner-platform/1.0", "Accept": "application/json"}
LIVE_DIR = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
STORE = os.environ.get("OHLCV_CACHE", os.path.join(LIVE_DIR, "ohlcv_last.json"))
SEED = os.environ.get("CACHE_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "data", "seed_caches.json"))

_lock = threading.Lock()
_cache = {}                       # (pool, tf, mint|None) -> (ts, data)
_q = {"hi": deque(), "lo": deque()}
_queued = {}                      # key -> Event set when the job finishes
_cond = threading.Condition()
_calls = deque()                  # timestamps of upstream calls (budget window)
_g = {"cool_until": 0.0, "cool_n": 0, "last_call": 0.0, "worker": False, "dirty": False, "last_flush": 0.0,
      "ok": 0, "429": 0, "err": 0, "last_error": None, "loaded": False}


class ChartError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status, self.msg = status, msg


# ------------------------------------------------------------------ persistence
def _slim(data):
    return {k: data[k] for k in data if k in ("pool", "tf", "candles", "source", "base", "quote", "note", "fetched_at")}


def _load_store():
    if _g["loaded"]:
        return
    _g["loaded"] = True
    now = time.time()
    for path, key in ((STORE, None), (SEED, "ohlcv")):
        try:
            d = json.load(open(path, encoding="utf-8"))
            d = d.get(key) if key else d
            for k, e in (d or {}).items():
                if not isinstance(e, dict) or not (e.get("data") or {}).get("candles"): continue
                ts = float(e.get("ts") or 0)
                if now - ts > STALE_MAX: continue
                parts = str(k).split("|")
                if len(parts) < 2: continue
                key2 = (parts[0], parts[1], parts[2] if len(parts) > 2 and parts[2] else None)
                with _lock:
                    cur = _cache.get(key2)
                    if not cur or ts > cur[0]: _cache[key2] = (ts, e["data"])
        except Exception:
            pass


def _flush(force=False):
    if not _g["dirty"] or (not force and time.time() - _g["last_flush"] < 20): return
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        now = time.time()
        with _lock:
            store = {f"{k[0]}|{k[1]}|{k[2] or ''}": {"ts": ts, "data": _slim(d)} for k, (ts, d) in _cache.items() if now - ts < STALE_MAX}
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(store, f)
        os.replace(tmp, STORE)
        _g["dirty"] = False; _g["last_flush"] = now
    except Exception:
        pass


def export(tfs=PREWARM_TFS, keep=160):
    """Trimmed last-good candles for the seed file (box -> repo), so a redeploy starts with charts."""
    _load_store()
    out = {}
    with _lock:
        for (pool, tf, mint), (ts, d) in _cache.items():
            if tf in tfs and d.get("candles") and time.time() - ts < STALE_MAX and d.get("source") != "derived":
                s = _slim(d); s["candles"] = s["candles"][-keep:]
                out[f"{pool}|{tf}|{mint or ''}"] = {"ts": ts, "data": s}
    return out


def _remember(pool, tf, token, data):
    ts = time.time()
    data = {**data, "fetched_at": data.get("fetched_at") or ts}
    with _lock:
        _cache[(pool, tf, token)] = (ts, data)
        if len(_cache) > 800:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:200]: _cache.pop(k, None)
    _g["dirty"] = True
    return ts, data


# ------------------------------------------------------------------ GeckoTerminal (worker only)
def _norm_candles(rows):
    candles = sorted(({"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                      for r in rows if isinstance(r, list) and len(r) >= 6 and r[4] is not None), key=lambda x: x["t"])
    dedup = {}
    for c in candles: dedup[c["t"]] = c
    return list(dedup.values())


def _budget_ok():
    now = time.time()
    if now < _g["cool_until"]: return False
    while _calls and now - _calls[0] > UPSTREAM_WIN: _calls.popleft()
    return len(_calls) < UPSTREAM_N


def _gt_call(pool, tf, token):
    """One GeckoTerminal request (worker thread only). Returns data dict or raises ChartError."""
    period, agg = TF[tf]
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{period}"
           f"?aggregate={agg}&limit={LIMIT}&currency=usd" + (f"&token={token}" if token else ""))
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/json;version=20230302"})
    _calls.append(time.time()); _g["last_call"] = time.time()
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: raise ChartError(404, "GeckoTerminal has no chart for this pool yet.")
        if e.code == 429:
            _g["429"] += 1; _g["cool_n"] = min(_g["cool_n"] + 1, 4)
            _g["cool_until"] = time.time() + min(COOLDOWN0 * 2 ** (_g["cool_n"] - 1), COOLDOWN_MAX)
            raise ChartError(429, "GeckoTerminal rate limit hit.")
        _g["err"] += 1; _g["last_error"] = f"HTTP {e.code}"
        raise ChartError(502, f"GeckoTerminal error {e.code}.")
    except Exception as e:
        _g["err"] += 1; _g["last_error"] = type(e).__name__
        raise ChartError(502, f"GeckoTerminal unreachable ({type(e).__name__}).")
    _g["cool_n"] = 0; _g["ok"] += 1
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    meta = d.get("meta") or {}
    return {"pool": pool, "tf": tf, "candles": _norm_candles(rows), "source": "geckoterminal",
            "base": {k: (meta.get("base") or {}).get(k) for k in ("symbol", "address")},
            "quote": {k: (meta.get("quote") or {}).get(k) for k in ("symbol", "address")}}


def _worker():
    while True:
        job = None
        with _cond:
            while job is None:
                for pr in ("hi", "lo"):
                    if _q[pr]: job = _q[pr].popleft(); break
                if job is None: _cond.wait(timeout=5); _flush()
        key = job
        pool, tf, token = key
        try:
            e = _cache.get(key)
            ttl = TTL_BY_TF.get(tf, CACHE_TTL)
            if e and time.time() - e[0] < ttl * 0.8:
                continue
            while not _budget_ok():                  # honour cool-down / per-minute budget, never hammer
                time.sleep(min(max(_g["cool_until"] - time.time(), 1.0), 5.0))
            wait = _g["last_call"] + GAP_S - time.time()
            if wait > 0: time.sleep(wait)
            try:
                d = _gt_call(pool, tf, token)
                if d["candles"]: _remember(pool, tf, token, d)
            except ChartError:
                pass
        except Exception as ex:
            _g["last_error"] = "worker " + type(ex).__name__
        finally:
            with _cond:
                ev = _queued.pop(key, None)
            if ev: ev.set()
            _flush()


def _enqueue(key, hi):
    with _cond:
        ev = _queued.get(key)
        if ev is not None:
            if hi and key in _q["lo"]:
                _q["lo"].remove(key); _q["hi"].append(key)
            return ev
        ev = threading.Event(); _queued[key] = ev
        _q["hi" if hi else "lo"].append(key)
        if len(_q["lo"]) > 120: _q["lo"].popleft()
        if not _g["worker"]:
            _g["worker"] = True
            threading.Thread(target=_worker, name="ohlcv-worker", daemon=True).start()
        _cond.notify()
        return ev


# ------------------------------------------------------------------ fallbacks
def _best_cached(pool, tf, token, now):
    with _lock:
        best = None
        for k in [(pool, tf, token), (pool, tf, None)] + [k for k in _cache if k[1] == tf and (k[0] == pool or (token and k[2] == token))]:
            e = _cache.get(k)
            if e and now - e[0] < STALE_MAX and e[1].get("candles") and (best is None or e[0] > best[0]): best = e
        return best


def _fallback(pool, tf, token, now, why):
    """Best answer that needs no upstream: last-good (+ our snapshots), our snapshot candles, then derived candles."""
    mint = token or ph.pool_mint(pool)
    best = _best_cached(pool, tf, token, now)
    snap = ph.candles(mint, tf) if mint else []
    if best and snap:
        last = best[1]["candles"][-1]["t"]
        extra = [c for c in snap if c["t"] > last]
        if extra:
            cs = best[1]["candles"] + extra
            if best[1]["candles"][-1]["c"] is not None and extra[0]["o"] is not None:
                extra[0] = {**extra[0], "o": best[1]["candles"][-1]["c"]}
                cs = best[1]["candles"] + extra
            return {**best[1], "candles": cs[-LIMIT:], "cached": True, "stale": True, "age_s": round(now - best[0], 1),
                    "source": "geckoterminal+snapshots", "note": why + " Latest candles come from our own price snapshots."}
    if best:
        return {**best[1], "cached": True, "stale": True, "age_s": round(now - best[0], 1), "note": why + " Showing the last good candles."}
    if len(snap) >= 2:
        return {"pool": pool, "tf": tf, "candles": snap, "source": "snapshots", "cached": False, "age_s": 0, "base": {}, "quote": {},
                "fetched_at": now, "note": why + " Candles are built from our own price snapshots (volume not available)."}
    der = ph.derived(mint, tf, now) if mint else []
    if der:
        return {"pool": pool, "tf": tf, "candles": der, "source": "derived", "approximate": True, "cached": False, "age_s": 0,
                "base": {}, "quote": {}, "fetched_at": now,
                "note": why + " Approximate: coarse candles from DexScreener's 5m / 1h / 24h price changes."}
    if snap:
        return {"pool": pool, "tf": tf, "candles": snap, "source": "snapshots", "cached": False, "age_s": 0, "base": {}, "quote": {},
                "fetched_at": now, "note": why + " Only one price snapshot so far; more arrive every minute."}
    return None


# ------------------------------------------------------------------ public API
def get_ohlcv(pool, tf, token=None):
    if not B58_RE.match(pool or ""):
        raise ChartError(400, "Invalid pool address.")
    if token and not B58_RE.match(token):
        token = None
    if tf not in TF:
        raise ChartError(400, f"tf must be one of {', '.join(TF)}.")
    _load_store()
    if token: ph.watch(token, pool)
    key = (pool, tf, token)
    now = time.time()
    ttl = TTL_BY_TF.get(tf, CACHE_TTL)
    with _lock:
        e = _cache.get(key) or _cache.get((pool, tf, None))
    if e and now - e[0] < ttl:
        return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}
    if e and now - e[0] < SWR_MAX:                      # stale-while-revalidate: answer now, refresh behind it
        _enqueue(key, True)
        return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}
    ev = _enqueue(key, True)
    depth = len(_q["hi"]) + len(_q["lo"])
    if _budget_ok() and depth <= 3:
        ev.wait(INLINE_WAIT_S)
        with _lock:
            e = _cache.get(key)
        if e and time.time() - e[0] < ttl:
            return {**e[1], "cached": False, "age_s": round(time.time() - e[0], 1)}
    fb = _fallback(pool, tf, token, time.time(), "Live candles are busy.")
    if fb: return fb
    if token and ph.snapshot_now(token, pool):          # a coin nobody tracks yet: take one price right now
        fb = _fallback(pool, tf, token, time.time(), "Live candles are busy.")
        if fb: return fb
    raise ChartError(503, "No candles yet for this pair: price snapshots start within a minute. Try again shortly or use the DexScreener chart.")


def prewarm(coins, tfs=PREWARM_TFS):
    """Queue background refreshes of the default timeframes for the current coins (low priority, never blocks)."""
    _load_store()
    now = time.time()
    try:
        ph.register([c for c in coins or [] if isinstance(c, dict)])
    except Exception:
        pass
    for c in coins or []:
        if not isinstance(c, dict): continue
        pool, mint = c.get("pool"), c.get("mint")
        if not pool or not B58_RE.match(str(pool)): continue
        if mint and B58_RE.match(str(mint)): ph.watch(mint, pool)
        for tf in tfs:
            key = (pool, tf, mint or None)
            with _lock:
                e = _cache.get(key)
            if e and now - e[0] < PREWARM_EVERY: continue
            _enqueue(key, False)


def prewarm_from_report(path):
    try:
        r = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    prewarm(r.get("top") or r.get("coins") or [])


def stats():
    with _lock:
        n = len(_cache)
    return {"cached": n, "ok": _g["ok"], "rate_limited": _g["429"], "errors": _g["err"], "last_error": _g["last_error"],
            "cooldown_s": max(0, round(_g["cool_until"] - time.time())), "queue": len(_q["hi"]) + len(_q["lo"]),
            "price_snapshots": ph.status()}
