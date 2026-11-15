"""OHLCV proxy for the coin chart (GeckoTerminal public API, no key). Read-only market data only.
Short per-(pool, timeframe) cache + an upstream budget so many viewers of the public instance
can't push us past GeckoTerminal's free limit (~30 calls/min). Stale data is served when the budget is spent.
"""
import json, re, threading, time, urllib.request, urllib.error
from collections import deque

B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
      "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
CACHE_TTL = 10           # seconds a fresh answer is reused
STALE_MAX = 600          # seconds stale data may be served when the upstream budget is spent / upstream errors
UPSTREAM_N, UPSTREAM_WIN = 12, 60   # GeckoTerminal calls per minute per process (2 instances share one IP; free tier ~30/min)
TTL_BY_TF = {"1m": 10, "5m": 10, "15m": 15, "1h": 45, "4h": 90, "1d": 300}
COOLDOWN = 20            # pause upstream calls this long after a GeckoTerminal 429
_cool = [0.0]
LIMIT = 300

_cache, _lock = {}, threading.Lock()
_up, _up_lock = deque(), threading.Lock()
_inflight = {}


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


_space_lock, _last_call = threading.Lock(), [0.0]
MIN_GAP = 1.2            # seconds between upstream calls (GeckoTerminal is bursty about limits)


def _fetch(pool, tf, token):
    with _space_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0: time.sleep(wait)
        _last_call[0] = time.time()
    period, agg = TF[tf]
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{period}"
           f"?aggregate={agg}&limit={LIMIT}&currency=usd" + (f"&token={token}" if token else ""))
    req = urllib.request.Request(url, headers={"Accept": "application/json;version=20230302",
                                               "User-Agent": "memecoin-scanner-platform/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: raise ChartError(404, "GeckoTerminal has no chart for this pool yet.")
        if e.code == 429:
            _cool[0] = time.time() + COOLDOWN
            raise ChartError(429, "GeckoTerminal rate limit hit; retrying shortly.")
        raise ChartError(502, f"GeckoTerminal error {e.code}.")
    except Exception as e:
        raise ChartError(502, f"GeckoTerminal unreachable ({type(e).__name__}).")
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = sorted(({"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                      for r in rows if isinstance(r, list) and len(r) >= 6 and r[4] is not None), key=lambda x: x["t"])
    dedup = {}
    for c in candles: dedup[c["t"]] = c
    candles = list(dedup.values())
    meta = d.get("meta") or {}
    return {"pool": pool, "tf": tf, "candles": candles,
            "base": {k: (meta.get("base") or {}).get(k) for k in ("symbol", "address")},
            "quote": {k: (meta.get("quote") or {}).get(k) for k in ("symbol", "address")},
            "source": "geckoterminal"}


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
            if e and now - e[0] < STALE_MAX:
                return {**e[1], "cached": True, "stale": True, "age_s": round(now - e[0], 1), "note": msg}
            return None
        if not _budget_ok():
            s = stale("Chart is busy; showing data from a few seconds ago.")
            if s: return s
            raise ChartError(429, "Chart is busy (too many requests right now). Retrying shortly.")
        try:
            try:
                d = _fetch(pool, tf, token)
            except ChartError as ce:
                if ce.status != 429 or (e and now - e[0] < STALE_MAX): raise
                time.sleep(3); _cool[0] = 0.0       # no data to fall back on: one polite retry
                d = _fetch(pool, tf, token)
        except ChartError as ce:
            if ce.status in (429, 502):
                s = stale(ce.msg)
                if s: return s
            raise
        d["fetched_at"] = time.time()
        with _lock:
            _cache[key] = (time.time(), d)
            if len(_cache) > 800:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:200]: _cache.pop(k, None)
        return {**d, "cached": False, "age_s": 0}
