"""OHLCV proxy for the coin chart. Read-only market data only.

Primary source: GeckoTerminal public API (no key). Alternate: DexPaprika (no key;
hourly+ bars when GeckoTerminal is rate-limited). Per-(pool, timeframe) cache of
about 60s, a single-file request queue with backoff, and stale data when the
upstream budget is spent. Many viewers of the public instance share one IP with
the live scanner, so we stay well under GeckoTerminal's free cap (~30 calls/min).

Every Gecko 429 or backoff goes straight to DexPaprika (report pool, then a
pool looked up by mint). Last-good candles are kept in memory and on disk so
a burst or a fresh boot can still serve a chart.
"""
import json, os, re, threading, time, urllib.request, urllib.error, urllib.parse
from collections import deque
from datetime import datetime, timezone

B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
      "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
# DexPaprika keyless plan allows 1h and 24h only; shorter tfs fall back to 1h bars.
DP_TF = {"1m": ("1h", True), "5m": ("1h", True), "15m": ("1h", True),
         "1h": ("1h", False), "4h": ("1h", True), "1d": ("24h", False)}
CACHE_TTL = 60           # seconds a fresh answer is reused (all timeframes at least this)
STALE_MAX = 6 * 3600     # last-good may be served this long when upstreams fail
UPSTREAM_N, UPSTREAM_WIN = 8, 60   # GeckoTerminal calls per minute per process (shared IP)
TTL_BY_TF = {"1m": 60, "5m": 60, "15m": 60, "1h": 90, "4h": 120, "1d": 300}
COOLDOWN = 45            # pause GeckoTerminal calls this long after a 429
MIN_GAP = 2.0            # seconds between GeckoTerminal calls
DP_GAP = 0.25            # seconds between DexPaprika calls (avoid stampeding the fallback)
DP_CONCUR = 3
DP_MISS_TTL = 600        # remember "no DexPaprika pool" so we don't research every time
LIMIT = 300
UA = {"User-Agent": "memecoin-scanner-platform/1.0", "Accept": "application/json"}
STORE = os.environ.get("OHLCV_CACHE", "/tmp/xscanner-live/ohlcv_last.json")

_cool = [0.0]
_cache, _lock = {}, threading.Lock()
_up, _up_lock = deque(), threading.Lock()
_inflight = {}
_space_lock, _last_call = threading.Lock(), [0.0]
_dp_sema = threading.Semaphore(DP_CONCUR)
_dp_gap_lock, _dp_last, _dp_cool = threading.Lock(), [0.0], [0.0]
_dp_pool, _dp_pool_lock = {}, threading.Lock()
_dp_miss, _dp_miss_lock = {}, threading.Lock()
_warm_lock, _warm_gen = threading.Lock(), [0]


class ChartError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status, self.msg = status, msg


def _gecko_available():
    """Peek only — do not consume the Gecko budget until we actually call it."""
    now = time.time()
    if now < _cool[0]:
        return False
    with _up_lock:
        while _up and now - _up[0] > UPSTREAM_WIN:
            _up.popleft()
        return len(_up) < UPSTREAM_N


def _gecko_consume():
    with _up_lock:
        _up.append(time.time())


def _http_json(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _http_json_code(url, timeout=10):
    """Return (status, data_or_none). Never raises."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = None
        if e.code == 429:
            _dp_cool[0] = time.time() + 8
        return e.code, body
    except Exception:
        return 0, None


def _norm_candles(rows):
    candles = sorted(({"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                      for r in rows if isinstance(r, list) and len(r) >= 6 and r[4] is not None), key=lambda x: x["t"])
    dedup = {}
    for c in candles:
        dedup[c["t"]] = c
    return list(dedup.values())


def _fetch_gecko(pool, tf, token):
    """Serialized, spaced GeckoTerminal call. Raises ChartError."""
    with _space_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        if time.time() < _cool[0]:
            raise ChartError(429, "GeckoTerminal rate limit hit; retrying shortly.")
        _gecko_consume()
        _last_call[0] = time.time()
        period, agg = TF[tf]
        url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{period}"
               f"?aggregate={agg}&limit={LIMIT}&currency=usd" + (f"&token={token}" if token else ""))
        try:
            d = _http_json(url, headers={"Accept": "application/json;version=20230302"})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ChartError(404, "GeckoTerminal has no chart for this pool yet.")
            if e.code == 429:
                _cool[0] = time.time() + COOLDOWN
                raise ChartError(429, "GeckoTerminal rate limit hit; retrying shortly.")
            raise ChartError(502, f"GeckoTerminal error {e.code}.")
        except Exception as e:
            raise ChartError(502, f"GeckoTerminal unreachable ({type(e).__name__}).")
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = _norm_candles(rows)
    meta = d.get("meta") or {}
    return {"pool": pool, "tf": tf, "candles": candles,
            "base": {k: (meta.get("base") or {}).get(k) for k in ("symbol", "address")},
            "quote": {k: (meta.get("quote") or {}).get(k) for k in ("symbol", "address")},
            "source": "geckoterminal"}


def _fetch_gecko_token(token, tf):
    """Same host as pool OHLCV; only used on 404, not on 429 (would share the limit)."""
    if not token:
        return None
    with _space_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        if time.time() < _cool[0] or not _gecko_available():
            return None
        _gecko_consume()
        _last_call[0] = time.time()
        period, agg = TF[tf]
        url = (f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token}/ohlcv/{period}"
               f"?aggregate={agg}&limit={LIMIT}&currency=usd")
        try:
            d = _http_json(url, headers={"Accept": "application/json;version=20230302"})
        except Exception:
            return None
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = _norm_candles(rows)
    if not candles:
        return None
    return {"pool": token, "tf": tf, "candles": candles, "base": {}, "quote": {},
            "source": "geckoterminal"}


def _ts(s):
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


_QUOTES = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _dp_slot():
    """Wait for a DexPaprika concurrency slot and inter-call gap. Returns a release fn."""
    _dp_sema.acquire()
    try:
        with _dp_gap_lock:
            wait = max(_dp_last[0] + DP_GAP, _dp_cool[0]) - time.time()
            if wait > 0:
                time.sleep(min(wait, 8))
            _dp_last[0] = time.time()
    except Exception:
        _dp_sema.release()
        raise
    return _dp_sema.release


def _fetch_dexpaprika_pool(pool, tf):
    """Keyless DexPaprika OHLCV for a known pool id. 1h/24h only; shorter tfs get 1h bars."""
    interval, degraded = DP_TF.get(tf, ("1h", True))
    start = "-24h"  # keyless DexPaprika only allows the last 24 hours
    url = (f"https://api.dexpaprika.com/networks/solana/pools/{pool}/ohlcv?"
           + urllib.parse.urlencode({"start": start, "interval": interval, "limit": 300}))
    status, d = _http_json_code(url, timeout=10)
    if status == 429:
        time.sleep(0.6)
        status, d = _http_json_code(url, timeout=10)
    if status != 200 or not isinstance(d, list):
        return None
    candles = []
    for row in d:
        if not isinstance(row, dict) or row.get("close") is None:
            continue
        t = _ts(row.get("time_open") or row.get("time_close"))
        if t is None:
            continue
        candles.append({"t": t, "o": row.get("open"), "h": row.get("high"),
                        "l": row.get("low"), "c": row.get("close"), "v": row.get("volume") or 0})
    candles = _norm_candles([[c["t"], c["o"], c["h"], c["l"], c["c"], c["v"]] for c in candles])
    if not candles:
        return None
    out = {"pool": pool, "tf": tf if not degraded else interval, "candles": candles,
           "source": "dexpaprika", "base": {}, "quote": {}}
    if degraded:
        out["note"] = f"Showing {interval} candles while the {tf} feed is busy."
    return out


def _resolve_dp_pool(token):
    """Find a DexPaprika pool for a mint when the report's pool id isn't in their catalog."""
    if not token:
        return None
    with _dp_pool_lock:
        if token in _dp_pool:
            return _dp_pool[token]
    with _dp_miss_lock:
        t = _dp_miss.get(token)
        if t and time.time() - t < DP_MISS_TTL:
            return None
    status, d = _http_json_code("https://api.dexpaprika.com/search?query=" + urllib.parse.quote(token), timeout=8)
    if status == 429:
        time.sleep(0.6)
        status, d = _http_json_code("https://api.dexpaprika.com/search?query=" + urllib.parse.quote(token), timeout=8)
    if status != 200 or not isinstance(d, dict):
        return None
    cands = []
    for p in d.get("pools") or []:
        if not isinstance(p, dict) or p.get("chain") != "solana":
            continue
        ids = [t.get("id") for t in (p.get("tokens") or []) if isinstance(t, dict)]
        if token not in ids:
            continue
        quote_ok = any(i in _QUOTES for i in ids)
        cands.append(((1 if quote_ok else 0), float(p.get("volume_usd") or 0), p.get("id")))
    if not cands:
        with _dp_miss_lock:
            _dp_miss[token] = time.time()
        return None
    cands.sort(reverse=True)
    pid = cands[0][2]
    with _dp_pool_lock:
        _dp_pool[token] = pid
    return pid


def _fetch_dexpaprika(pool, tf, token=None):
    """DexPaprika fallback: try the given pool, then look up a pool by mint if needed."""
    release = _dp_slot()
    try:
        d = _fetch_dexpaprika_pool(pool, tf) if pool else None
        if d:
            return d
        alt = _resolve_dp_pool(token)
        if alt and alt != pool:
            d = _fetch_dexpaprika_pool(alt, tf)
            if d:
                d["note"] = ((d.get("note") + " ") if d.get("note") else "") + "Pool looked up on DexPaprika by mint."
                return d
        return None
    finally:
        release()


def _fetch(pool, tf, token):
    gecko_err = None
    try:
        d = _fetch_gecko(pool, tf, token)
        if d.get("candles"):
            return d
    except ChartError as ce:
        gecko_err = ce
        if ce.status == 404 and token:
            t = _fetch_gecko_token(token, tf)
            if t:
                return t
    # Any Gecko 429/404/502/empty: fall back to DexPaprika (direct pool, then mint lookup)
    alt = _fetch_dexpaprika(pool, tf, token)
    if alt:
        return alt
    if gecko_err:
        raise gecko_err
    return d


def _cache_key(pool, tf, token):
    return f"{pool}|{tf}|{token or ''}"


def _load_store():
    try:
        d = json.load(open(STORE, encoding="utf-8"))
        if not isinstance(d, dict):
            return
        now = time.time()
        with _lock:
            for k, e in d.items():
                if not isinstance(e, dict) or not e.get("data"):
                    continue
                ts = float(e.get("ts") or 0)
                if now - ts > STALE_MAX:
                    continue
                parts = str(k).split("|")
                if len(parts) < 2:
                    continue
                key = (parts[0], parts[1], parts[2] if len(parts) > 2 and parts[2] else None)
                cur = _cache.get(key)
                if not cur or ts > cur[0]:
                    _cache[key] = (ts, e["data"])
    except Exception:
        pass


def _save_store_entry(pool, tf, token, ts, data):
    try:
        os.makedirs(os.path.dirname(STORE) or ".", exist_ok=True)
        try:
            store = json.load(open(STORE, encoding="utf-8"))
            if not isinstance(store, dict):
                store = {}
        except Exception:
            store = {}
        slim = {k: data[k] for k in data if k in ("pool", "tf", "candles", "source", "base", "quote", "note", "fetched_at")}
        store[_cache_key(pool, tf, token)] = {"ts": ts, "data": slim}
        # drop ancient entries
        now = time.time()
        store = {k: v for k, v in store.items()
                 if isinstance(v, dict) and now - float(v.get("ts") or 0) < STALE_MAX}
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(tmp, STORE)
    except Exception:
        pass


_load_store()


def _remember(pool, tf, token, data):
    ts = time.time()
    data = {**data, "fetched_at": data.get("fetched_at") or ts}
    key = (pool, tf, token)
    with _lock:
        _cache[key] = (ts, data)
        if len(_cache) > 800:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:200]:
                _cache.pop(k, None)
    _save_store_entry(pool, tf, token, ts, data)
    return {**data, "cached": False, "age_s": 0}


def _stale_from_cache(pool, tf, token, now, msg):
    """Prefer an exact-key last-good entry; otherwise any recent cache for this pool or mint."""
    with _lock:
        keys = [(pool, tf, token), (pool, tf, None)]
        if token:
            keys += [k for k in _cache if k[1] == tf and (k[0] == pool or k[2] == token)]
        best = None
        for k in keys:
            e = _cache.get(k)
            if e and now - e[0] < STALE_MAX and (e[1].get("candles")):
                if best is None or e[0] > best[0]:
                    best = e
        if not best:
            return None
        return {**best[1], "cached": True, "stale": True, "age_s": round(now - best[0], 1),
                "note": ((best[1].get("note") + " ") if best[1].get("note") else "") + msg}


def get_ohlcv(pool, tf, token=None):
    if not B58_RE.match(pool or ""):
        raise ChartError(400, "Invalid pool address.")
    if token and not B58_RE.match(token):
        token = None
    if tf not in TF:
        raise ChartError(400, f"tf must be one of {', '.join(TF)}.")
    key = (pool, tf, token)
    now = time.time()
    with _lock:
        e = _cache.get(key)
        if e and now - e[0] < TTL_BY_TF.get(tf, CACHE_TTL):
            return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}
        lk = _inflight.setdefault(key, threading.Lock())
    with lk:                                   # one upstream call per key at a time
        with _lock:
            e = _cache.get(key)
            now = time.time()
            if e and now - e[0] < TTL_BY_TF.get(tf, CACHE_TTL):
                return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}

        def stale(msg):
            return _stale_from_cache(pool, tf, token, time.time(), msg)

        # Backoff / spent Gecko budget: go straight to DexPaprika, then last-good.
        # _fetch already tries DexPaprika after any Gecko error, so don't call it twice.
        gecko_ok = _gecko_available()
        try:
            if not gecko_ok:
                alt = _fetch_dexpaprika(pool, tf, token)
                if alt:
                    return _remember(pool, tf, token, alt)
                s = stale("Chart is delayed; showing the last good candles.")
                if s:
                    return s
                raise ChartError(429, "Chart is busy (too many requests right now). Retrying shortly.")
            d = _fetch(pool, tf, token)
        except ChartError as ce:
            s = stale("Chart is delayed; showing the last good candles.")
            if s:
                return s
            raise
        return _remember(pool, tf, token, d)


def prewarm(coins, tf="5m"):
    """Slowly fetch OHLCV for the current top coins so drawers open from cache.

    Prefers DexPaprika (doesn't spend the Gecko budget). Misses retry Gecko only
    when its budget is free. Safe to call after every scan; overlapping runs are
    coalesced.
    """
    items, seen = [], set()
    for c in coins or []:
        if not isinstance(c, dict):
            continue
        pool, mint = c.get("pool"), c.get("mint")
        if not pool or not B58_RE.match(str(pool)):
            continue
        key = (pool, mint or None)
        if key in seen:
            continue
        seen.add(key)
        items.append(key)
    if not items:
        return
    with _warm_lock:
        _warm_gen[0] += 1
        gen = _warm_gen[0]

    def run():
        # First pass: DexPaprika only (cheap, works during Gecko backoff).
        misses = []
        for pool, mint in items:
            if gen != _warm_gen[0]:
                return
            with _lock:
                e = _cache.get((pool, tf, mint))
                if e and time.time() - e[0] < TTL_BY_TF.get(tf, CACHE_TTL):
                    continue
            try:
                alt = _fetch_dexpaprika(pool, tf, mint)
                if alt:
                    _remember(pool, tf, mint, alt)
                else:
                    misses.append((pool, mint))
            except Exception:
                misses.append((pool, mint))
            time.sleep(0.5)
        # Second pass: Gecko for coins DexPaprika doesn't have, only if budget is free.
        for pool, mint in misses:
            if gen != _warm_gen[0]:
                return
            if not _gecko_available():
                time.sleep(3)
                if not _gecko_available():
                    continue
            try:
                get_ohlcv(pool, tf, mint)
            except Exception:
                pass
            time.sleep(2.2)

    threading.Thread(target=run, name="ohlcv-prewarm", daemon=True).start()


def prewarm_from_report(path):
    try:
        r = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    top = r.get("top") or r.get("coins") or []
    prewarm(top)
