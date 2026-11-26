"""OHLCV proxy for the coin chart. Read-only market data only. A request NEVER waits on a rate-limited upstream.

Sources, in order (all keyless, none paid):
  1. fresh cache                     (per pool+timeframe, about a minute)
  2. Jupiter keyless chart API by mint (real candles + volume, fast) then GeckoTerminal OHLCV by pool (real exchange candles). One background worker owns every upstream call: single file,
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
SUB_TF = {"1s": 1, "15s": 15, "30s": 30}   # live tick frames; not a GeckoTerminal/Jupiter interval
CACHE_TTL = 60
TTL_BY_TF = {"1m": 60, "5m": 90, "15m": 120, "1h": 180, "4h": 300, "1d": 600}
SWR_MAX = 900            # a cached answer up to this old is served at once while a refresh runs in the background
STALE_MAX = 6 * 3600     # last-good may be served this long when upstreams fail
GAP_S = 3.0              # seconds between GeckoTerminal calls (one worker)
UPSTREAM_N, UPSTREAM_WIN = 10, 60   # calls per minute budget (the scanner and the confidence worker share the IP)
COOLDOWN0, COOLDOWN_MAX = 45, 600
INLINE_WAIT_S = 6.0      # longest a viewer's request waits for the worker
PREWARM_TFS = ("5m", "15m", "1h")      # 5m first for every coin (one pool at a time), then the longer frames
RESAMPLE_FROM = {"15m": 900, "1h": 3600, "4h": 14400}   # built locally from real 5m candles while the direct frame is missing
JUP_ENABLED = os.environ.get("CHART_JUPITER", "1") != "0"
JUP_TF = {"1m": "1_MINUTE", "5m": "5_MINUTE", "15m": "15_MINUTE", "1h": "1_HOUR", "4h": "4_HOUR", "1d": "1_DAY"}
JUP_GAP_S = 0.7          # Jupiter's keyless chart API is fast and per-mint: viewers and the backfill both use it first
JUP_N, JUP_WIN = 45, 60  # calls per minute budget
JUP_COOL0, JUP_COOL_MAX = 30, 300
GAP_LO_S = 8.0           # background backfill: one pool at a time, this far apart (viewers use GAP_S)
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
_jcalls = deque()
_g = {"jup_cool_until": 0.0, "jup_cool_n": 0, "jup_last": 0.0, "jup_ok": 0, "jup_429": 0, "jup_err": 0,
      "cool_until": 0.0, "cool_n": 0, "last_call": 0.0, "worker": False, "dirty": False, "last_flush": 0.0,
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


def export(tfs=PREWARM_TFS, keep=300):
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


def _jup_ok():
    if not JUP_ENABLED: return False
    now = time.time()
    if now < _g["jup_cool_until"]: return False
    while _jcalls and now - _jcalls[0] > JUP_WIN: _jcalls.popleft()
    return len(_jcalls) < JUP_N


def _jup_call(pool, tf, token):
    """Real candles (with volume) from Jupiter's keyless chart API, by mint. Worker thread only. Raises ChartError."""
    url = (f"https://datapi.jup.ag/v2/charts/{token}?interval={JUP_TF[tf]}&to={int(time.time() * 1000)}"
           f"&candles={LIMIT}&type=price")
    _jcalls.append(time.time()); _g["jup_last"] = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, "User-Agent": "Mozilla/5.0 (xScanner read-only)"}), timeout=10) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _g["jup_429"] += 1; _g["jup_cool_n"] = min(_g["jup_cool_n"] + 1, 4)
            _g["jup_cool_until"] = time.time() + min(JUP_COOL0 * 2 ** (_g["jup_cool_n"] - 1), JUP_COOL_MAX)
        else:
            _g["jup_err"] += 1; _g["last_error"] = f"jupiter HTTP {e.code}"
        raise ChartError(502, f"Jupiter chart HTTP {e.code}.")
    except Exception as e:
        _g["jup_err"] += 1; _g["last_error"] = "jupiter " + type(e).__name__
        raise ChartError(502, "Jupiter chart unreachable.")
    rows = [k for k in (d.get("candles") or []) if isinstance(k, dict)]
    cs = _norm_candles([[k.get("time"), k.get("open"), k.get("high"), k.get("low"), k.get("close"), k.get("volume") or 0] for k in rows
                        if k.get("time") is not None])
    if not cs: raise ChartError(404, "Jupiter has no candles for this token.")
    _g["jup_cool_n"] = 0; _g["jup_ok"] += 1
    return {"pool": pool, "tf": tf, "candles": cs, "source": "jupiter", "base": {}, "quote": {}}


def _fetch(pool, tf, token, hi):
    """One job: Jupiter first (fast, by mint), GeckoTerminal when it is free. Waits out cool-downs, but gives up after a while
    (the next pre-warm queues the job again) so a long GeckoTerminal cool-down never freezes the queue."""
    deadline = time.time() + (30 if hi else 240)
    tried_jup = False
    while time.time() < deadline:
        if token and not tried_jup and _jup_ok():
            tried_jup = True
            wait = _g["jup_last"] + JUP_GAP_S - time.time()
            if wait > 0: time.sleep(wait)
            try:
                return _jup_call(pool, tf, token)
            except ChartError:
                pass
        if _budget_ok():
            wait = _g["last_call"] + (GAP_S if hi else GAP_LO_S) - time.time()
            if wait > 0: time.sleep(wait)
            try:
                return _gt_call(pool, tf, token)
            except ChartError:
                return None
        time.sleep(1.5)
    return None


def _worker():
    while True:
        job, job_hi = None, True
        with _cond:
            while job is None:
                for pr in ("hi", "lo"):
                    if _q[pr]: job = _q[pr].popleft(); job_hi = (pr == "hi"); break
                if job is None: _cond.wait(timeout=5); _flush()
        key = job
        pool, tf, token = key
        try:
            e = _cache.get(key)
            ttl = TTL_BY_TF.get(tf, CACHE_TTL)
            if e and time.time() - e[0] < ttl * 0.8:
                continue
            d = _fetch(pool, tf, token, job_hi)
            if d and d["candles"]: _remember(pool, tf, token, d)
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
        if len(_q["lo"]) > 400: _q["lo"].popleft()
        if not _g["worker"]:
            _g["worker"] = True
            threading.Thread(target=_worker, name="ohlcv-worker", daemon=True).start()
        _cond.notify()
        return ev


# ------------------------------------------------------------------ candle hygiene
def _med(xs):
    s = sorted(xs); n = len(s)
    return None if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def sanitize(candles, ratio=10.0, win=3):
    """Make every candle internally consistent and drop spikes.
    - sorted, one candle per timestamp, no non-positive / non-numeric prices
    - low <= min(open, close) and high >= max(open, close)
    - a candle whose open AND close sit more than `ratio`x away from the median close of its neighbours is dropped; a wick that
      reaches more than `ratio`x past that median is clamped to the candle body (a bad print, not a real trade range)."""
    rows = {}
    for k in candles or []:
        try:
            o, h, l, c_ = (float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]))
            v = float(k.get("v") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(x == x and x > 0 for x in (o, h, l, c_)): continue
        rows[int(k["t"])] = {"t": int(k["t"]), "o": o, "h": h, "l": l, "c": c_, "v": v if v == v and v >= 0 else 0.0}
    cs = [rows[t] for t in sorted(rows)]
    if len(cs) >= 5:
        keep = []
        for i, k in enumerate(cs):
            nb = [cs[j]["c"] for j in range(max(0, i - win), min(len(cs), i + win + 1)) if j != i]
            m = _med(nb)
            if m and m > 0:
                if min(k["o"], k["c"]) > m * ratio or max(k["o"], k["c"]) < m / ratio: continue      # whole candle is an outlier
                if k["h"] > m * ratio: k["h"] = max(k["o"], k["c"])
                if k["l"] < m / ratio: k["l"] = min(k["o"], k["c"])
            keep.append(k)
        cs = keep
    for k in cs:
        k["h"] = max(k["h"], k["o"], k["c"], k["l"]); k["l"] = min(k["l"], k["o"], k["c"], k["h"])
    return cs


def resample(candles, sec):
    """Aggregate finer real candles into `sec` candles (volume summed)."""
    b = {}
    for k in sorted(candles or [], key=lambda x: x["t"]):
        t = k["t"] - k["t"] % sec
        if t in b:
            x = b[t]; x["h"] = max(x["h"], k["h"]); x["l"] = min(x["l"], k["l"]); x["c"] = k["c"]; x["v"] = (x["v"] or 0) + (k.get("v") or 0)
        else:
            b[t] = {"t": t, "o": k["o"], "h": k["h"], "l": k["l"], "c": k["c"], "v": k.get("v") or 0}
    return [b[t] for t in sorted(b)]


def _annotate(d):
    """Final shape of every /api/ohlcv answer: sane candles + source / limited / n_candles / history_s."""
    cs = sanitize(d.get("candles"))
    src = d.get("source") or "unknown"
    out = {**d, "candles": cs, "n_candles": len(cs), "history_s": (cs[-1]["t"] - cs[0]["t"]) if len(cs) > 1 else 0,
           "has_volume": any((k.get("v") or 0) > 0 for k in cs)}
    out["limited"] = src in ("snapshots", "derived")      # true = our own thin history, not exchange candles
    if out["limited"]:
        out["limited_reason"] = ("approximate candles from price-change windows" if src == "derived"
                                 else "built from our own price snapshots; exchange candles are rate-limited right now")
    return out


# ------------------------------------------------------------------ fallbacks
def _best_cached(pool, tf, token, now):
    with _lock:
        best = None
        for k in [(pool, tf, token), (pool, tf, None)] + [k for k in _cache if k[1] == tf and (k[0] == pool or (token and k[2] == token))]:
            e = _cache.get(k)
            if e and now - e[0] < STALE_MAX and e[1].get("candles") and (best is None or e[0] > best[0]): best = e
        return best


def _best_or_resampled(pool, tf, token, now):
    """Last-good candles for tf; failing that, real 5m candles resampled to tf (so one GeckoTerminal success serves every frame)."""
    best = _best_cached(pool, tf, token, now)
    if best or tf not in RESAMPLE_FROM: return best
    b5 = _best_cached(pool, "5m", token, now)
    if not b5: return None
    cs = resample(b5[1]["candles"], RESAMPLE_FROM[tf])
    if not cs: return None
    return b5[0], {**b5[1], "tf": tf, "candles": cs, "note": "Resampled from real 5m candles."}


def _fallback(pool, tf, token, now, why):
    """Best answer that needs no upstream: last-good (+ our snapshots), our snapshot candles, then derived candles."""
    mint = token or ph.pool_mint(pool)
    best = _best_or_resampled(pool, tf, token, now)
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
def _subminute(pool, tf, token):
    """1s/15s/30s are live tick frames. Never 400 and never call GeckoTerminal/Jupiter with an invalid interval.
    Seed history from our snapshots or a cached 1m/5m chart; the browser appends live /api/tick candles."""
    mint = token
    cs, src, note = [], "ticks", "Live sub-minute candles build from /api/tick."
    if mint:
        cs = ph.candles(mint, tf) or []
        if cs:
            src, note = "snapshots", "Built from our own price snapshots; new candles append from /api/tick."
        else:
            cs = ph.candles(mint, "1m") or []
            if cs:
                src, note = "snapshots", "Seeded from 1-minute snapshots; live 1s/15s/30s candles append from /api/tick."
    if not cs:
        with _lock:
            for k in [(pool, "1m", token), (pool, "1m", None), (pool, "5m", token), (pool, "5m", None)]:
                e = _cache.get(k)
                if e and (e[1] or {}).get("candles"):
                    cs = e[1]["candles"]
                    src, note = e[1].get("source") or "resampled", "Seeded from longer candles; live sub-minute bars append from /api/tick."
                    break
    return {"pool": pool, "tf": tf, "candles": cs or [], "source": src, "base": {}, "quote": {},
            "note": note, "cached": False, "age_s": 0}


def _get_ohlcv(pool, tf, token=None):
    if not B58_RE.match(pool or ""):
        raise ChartError(400, "Invalid pool address.")
    if token and not B58_RE.match(token):
        token = None
    if tf in SUB_TF:
        return _subminute(pool, tf, token)
    if tf not in TF:
        raise ChartError(400, f"tf must be one of {', '.join(list(SUB_TF)+list(TF))}.")
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
    if tf in RESAMPLE_FROM:                              # real 5m candles already here: answer from them, fetch the real frame behind it
        rs = _best_or_resampled(pool, tf, token, now)
        if rs and rs[1].get("note", "").startswith("Resampled"):
            _enqueue(key, False)
            return {**rs[1], "cached": True, "stale": True, "age_s": round(now - rs[0], 1)}
    ev = _enqueue(key, True)
    depth = len(_q["hi"])                                # viewers are served before the background backfill
    if (_budget_ok() or (token and _jup_ok())) and depth <= 3:
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


def get_ohlcv(pool, tf, token=None):
    """Sanitised, annotated candles (see _annotate). Raises ChartError like before."""
    return _annotate(_get_ohlcv(pool, tf, token))


def prewarm(coins, tfs=PREWARM_TFS):
    """Queue background refreshes of the default timeframes for the current coins (low priority, never blocks)."""
    _load_store()
    now = time.time()
    try:
        ph.register([c for c in coins or [] if isinstance(c, dict)])
    except Exception:
        pass
    for tf in tfs:                      # 5m for every coin first, then the longer frames: one pool at a time, slowly
        for c in coins or []:
            if not isinstance(c, dict): continue
            pool, mint = c.get("pool"), c.get("mint")
            if not pool or not B58_RE.match(str(pool)): continue
            if mint and B58_RE.match(str(mint)): ph.watch(mint, pool)
            key = (pool, tf, mint or None)
            with _lock:
                e = _cache.get(key)
            if e and now - e[0] < (PREWARM_EVERY if tf == "5m" else PREWARM_EVERY * 3): continue
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
            "cooldown_s": max(0, round(_g["cool_until"] - time.time())),
            "jupiter": {"enabled": JUP_ENABLED, "ok": _g["jup_ok"], "rate_limited": _g["jup_429"], "errors": _g["jup_err"],
                        "cooldown_s": max(0, round(_g["jup_cool_until"] - time.time()))}, "queue": len(_q["hi"]) + len(_q["lo"]),
            "price_snapshots": ph.status()}
