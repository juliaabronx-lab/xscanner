"""OHLCV proxy for the coin chart. Read-only market data only.

Primary source: GeckoTerminal public API (no key). Alternate: DexPaprika (no key;
hourly+ bars when GeckoTerminal is rate-limited). Per-(pool, timeframe) cache of
about 60s, a single-file request queue with backoff, and stale data when the
upstream budget is spent. Many viewers of the public instance share one IP with
the live scanner, so we stay well under GeckoTerminal's free cap (~30 calls/min).
"""
import json, re, threading, time, urllib.request, urllib.error, urllib.parse
from collections import deque
from datetime import datetime, timezone

B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
      "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
# DexPaprika keyless plan allows 1h and 24h only; shorter tfs fall back to 1h bars.
DP_TF = {"1m": ("1h", True), "5m": ("1h", True), "15m": ("1h", True),
         "1h": ("1h", False), "4h": ("1h", True), "1d": ("24h", False)}
CACHE_TTL = 60           # seconds a fresh answer is reused (all timeframes at least this)
STALE_MAX = 900          # seconds stale data may be served when the budget is spent / upstream errors
UPSTREAM_N, UPSTREAM_WIN = 8, 60   # GeckoTerminal calls per minute per process (shared IP)
TTL_BY_TF = {"1m": 60, "5m": 60, "15m": 60, "1h": 90, "4h": 120, "1d": 300}
COOLDOWN = 45            # pause GeckoTerminal calls this long after a 429
MIN_GAP = 2.0            # seconds between GeckoTerminal calls
LIMIT = 300
UA = {"User-Agent": "memecoin-scanner-platform/1.0", "Accept": "application/json"}

_cool = [0.0]
_cache, _lock = {}, threading.Lock()
_up, _up_lock = deque(), threading.Lock()
_inflight = {}
_space_lock, _last_call = threading.Lock(), [0.0]


class ChartError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status, self.msg = status, msg


def _budget_ok():
    now = time.time()
    if now < _cool[0]: return False
    with _up_lock:
        while _up and now - _up[0] > UPSTREAM_WIN: _up.popleft()
        if len(_up) >= UPSTREAM_N: return False
        _up.append(now); return True


def _http_json(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _norm_candles(rows):
    candles = sorted(({"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                      for r in rows if isinstance(r, list) and len(r) >= 6 and r[4] is not None), key=lambda x: x["t"])
    dedup = {}
    for c in candles: dedup[c["t"]] = c
    return list(dedup.values())


def _fetch_gecko(pool, tf, token):
    """Serialized, spaced GeckoTerminal call. Raises ChartError."""
    with _space_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0: time.sleep(wait)
        if time.time() < _cool[0]:
            raise ChartError(429, "GeckoTerminal rate limit hit; retrying shortly.")
        _last_call[0] = time.time()
        period, agg = TF[tf]
        url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{period}"
               f"?aggregate={agg}&limit={LIMIT}&currency=usd" + (f"&token={token}" if token else ""))
        try:
            d = _http_json(url, headers={"Accept": "application/json;version=20230302"})
        except urllib.error.HTTPError as e:
            if e.code == 404: raise ChartError(404, "GeckoTerminal has no chart for this pool yet.")
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
    if not token: return None
    period, agg = TF[tf]
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token}/ohlcv/{period}"
           f"?aggregate={agg}&limit={LIMIT}&currency=usd")
    with _space_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0: time.sleep(wait)
        if time.time() < _cool[0]: return None
        _last_call[0] = time.time()
        try:
            d = _http_json(url, headers={"Accept": "application/json;version=20230302"})
        except Exception:
            return None
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = _norm_candles(rows)
    if not candles: return None
    return {"pool": None, "tf": tf, "candles": candles, "source": "geckoterminal-token",
            "base": {"symbol": None, "address": token}, "quote": {}}


def _ts(s):
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def _fetch_dexpaprika_pool(pool, tf):
    """Keyless DexPaprika OHLCV for a known pool id. 1h/24h only; shorter tfs get 1h bars."""
    interval, degraded = DP_TF.get(tf, ("1h", True))
    start = "-24h"  # keyless DexPaprika only allows the last 24 hours
    url = (f"https://api.dexpaprika.com/networks/solana/pools/{pool}/ohlcv?"
           + urllib.parse.urlencode({"start": start, "interval": interval, "limit": 300}))
    try:
        d = _http_json(url, timeout=10)
    except Exception:
        return None
    if not isinstance(d, list): return None
    candles = []
    for row in d:
        if not isinstance(row, dict) or row.get("close") is None: continue
        t = _ts(row.get("time_open") or row.get("time_close"))
        if t is None: continue
        candles.append({"t": t, "o": row.get("open"), "h": row.get("high"),
                        "l": row.get("low"), "c": row.get("close"), "v": row.get("volume") or 0})
    candles = _norm_candles([[c["t"], c["o"], c["h"], c["l"], c["c"], c["v"]] for c in candles])
    if not candles: return None
    out = {"pool": pool, "tf": tf if not degraded else interval, "candles": candles,
           "source": "dexpaprika", "base": {}, "quote": {}}
    if degraded: out["note"] = f"Showing {interval} candles while the {tf} feed is busy."
    return out


# token mint -> DexPaprika pool id (the scan report's pool is sometimes unknown to DexPaprika)
_dp_pool, _dp_pool_lock = {}, threading.Lock()
_QUOTES = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _resolve_dp_pool(token):
    """Find a DexPaprika pool for a mint when the report's pool id isn't in their catalog."""
    if not token: return None
    with _dp_pool_lock:
        if token in _dp_pool: return _dp_pool[token]
    try:
        d = _http_json("https://api.dexpaprika.com/search?query=" + urllib.parse.quote(token), timeout=8)
    except Exception:
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
        return None
    cands.sort(reverse=True)
    pid = cands[0][2]
    with _dp_pool_lock:
        _dp_pool[token] = pid
    return pid


def _fetch_dexpaprika(pool, tf, token=None):
    """DexPaprika fallback: try the given pool, then look up a pool by mint if needed."""
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


def _fetch(pool, tf, token):
    gecko_err = None
    try:
        d = _fetch_gecko(pool, tf, token)
        if d.get("candles"): return d
    except ChartError as ce:
        gecko_err = ce
        # 404: try token-level Gecko, then DexPaprika (including mint lookup)
        if ce.status == 404 and token:
            t = _fetch_gecko_token(token, tf)
            if t: return t
    # Any Gecko 429/404/502/empty: fall back to DexPaprika (direct pool, then mint lookup)
    alt = _fetch_dexpaprika(pool, tf, token)
    if alt: return alt
    if gecko_err:
        raise gecko_err
    return d


def _stale_from_cache(pool, tf, token, now, msg):
    """Prefer an exact-key stale entry; otherwise any recent cache for this pool or mint."""
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
        return {**best[1], "cached": True, "stale": True, "age_s": round(now - best[0], 1), "note": msg}


def get_ohlcv(pool, tf, token=None):
    if not B58_RE.match(pool or ""): raise ChartError(400, "Invalid pool address.")
    if token and not B58_RE.match(token): token = None
    if tf not in TF: raise ChartError(400, f"tf must be one of {', '.join(TF)}.")
    key = (pool, tf, token)
    now = time.time()
    with _lock:
        e = _cache.get(key)
        if e and now - e[0] < TTL_BY_TF.get(tf, CACHE_TTL):
            return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}
        lk = _inflight.setdefault(key, threading.Lock())
    with lk:                                   # one upstream call per key at a time
        with _lock:
            e = _cache.get(key); now = time.time()
            if e and now - e[0] < TTL_BY_TF.get(tf, CACHE_TTL):
                return {**e[1], "cached": True, "age_s": round(now - e[0], 1)}
        def stale(msg):
            return _stale_from_cache(pool, tf, token, now, msg)
        # If GeckoTerminal is in cooldown or over budget, try the alternate first, then stale.
        gecko_ok = _budget_ok()
        try:
            if not gecko_ok:
                alt = _fetch_dexpaprika(pool, tf, token)
                if alt:
                    alt["fetched_at"] = time.time()
                    with _lock:
                        _cache[key] = (time.time(), alt)
                    return {**alt, "cached": False, "age_s": 0}
                s = stale("Chart is busy; showing data from a few seconds ago.")
                if s: return s
                raise ChartError(429, "Chart is busy (too many requests right now). Retrying shortly.")
            try:
                d = _fetch(pool, tf, token)
            except ChartError as ce:
                if ce.status != 429 or (e and now - e[0] < STALE_MAX): raise
                time.sleep(3); _cool[0] = 0.0
                d = _fetch(pool, tf, token)
        except ChartError as ce:
            # last resort: DexPaprika mint lookup + stale cache on any error
            alt = _fetch_dexpaprika(pool, tf, token)
            if alt:
                alt["fetched_at"] = time.time()
                with _lock:
                    _cache[key] = (time.time(), alt)
                return {**alt, "cached": False, "age_s": 0}
            s = stale(ce.msg)
            if s: return s
            raise
        d["fetched_at"] = time.time()
        with _lock:
            _cache[key] = (time.time(), d)
            if len(_cache) > 800:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:200]: _cache.pop(k, None)
        return {**d, "cached": False, "age_s": 0}
