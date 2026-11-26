"""Price snapshots and server-built candles (keyless). Read-only market data.

Why: GeckoTerminal's free OHLCV is shared by every viewer and the scanner on one IP, and DexPaprika now answers 402 for
our host. When no upstream candles are available the chart is built from data we collect ourselves:

  1. snapshots  every TICK_S seconds the tracker records one price per tracked coin (Jupiter lite price API, one batched
                call for all coins; fallback: scanner mcap / supply for pump.fun mints). Snapshots are bucketed into real
                open/high/low/close candles for any timeframe (volume is not known for these, so it is 0).
  2. derived    when a coin has fewer than 2 snapshot candles (just deployed / just discovered) we build coarse candles from
                DexScreener's own 5m / 1h / 6h / 24h price-change windows. They are labelled approximate.

History is persisted to disk (LIVE_DIR) and seeded from deploy/data/seed_caches.json so a restart or redeploy does not blank
the charts. Never blocks a request: everything here is in-memory; the only network calls run on the tracker thread.
"""
import json, os, re, threading, time, urllib.error, urllib.request

LIVE_DIR = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
STORE = os.environ.get("PRICEHIST_CACHE", os.path.join(LIVE_DIR, "pricehist.json"))
HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.environ.get("CACHE_SEED", os.path.join(HERE, "deploy", "data", "seed_caches.json"))
JUP = "https://lite-api.jup.ag/price/v3?ids="
UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TICK_S = float(os.environ.get("PRICE_TICK_S", "60"))
KEEP_S = 30 * 3600          # keep 24h+ of history
MAX_PTS = 2400
FULL_RES_S = 3 * 3600        # points older than this are thinned to one per COMPACT_S (disk / seed stay small)
COMPACT_S = 300
WATCH_S = 6 * 3600           # a coin someone opened (not in the report) is tracked this long
TF_S = {"1s": 1, "15s": 15, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

price_hint = None             # optional callable(mint) -> price from other caches (DexScreener pull, RugCheck, mcap / supply)
_lock = threading.Lock()
_h = {}                       # mint -> {"pool", "pts": [[t, price]], "pc": {minutes: pct}, "age_h", "mcap", "seen"}
_watch = {}                   # mint -> (pool, last_seen)
_state = {"loaded": False, "started": False, "until": 0.0, "n_fail": 0, "last_tick": None, "last_error": None, "ticks": 0}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _median(xs):
    s = sorted(xs); n = len(s)
    return None if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def clean_points(pts, ratio=10.0, win=3, spike=1.6):
    """Drop bad price ticks.
    - one-point spikes: a point more than `spike`x away from the median of the points before it AND the median of the points
      after it (a real step change agrees with one side, a bad tick agrees with neither), e.g. 0.00898 among ~0.0034
    - edge points (no neighbours on one side) and anything else more than `ratio`x from the median of all neighbours."""
    n = len(pts)
    if n < 5: return list(pts)
    out = []
    for i, (t, p) in enumerate(pts):
        before = [pts[j][1] for j in range(max(0, i - win), i)]
        after = [pts[j][1] for j in range(i + 1, min(n, i + win + 1))]
        mb, ma = _median(before), _median(after)
        m = _median(before + after)
        if m and m > 0 and (p > m * ratio or p < m / ratio): continue
        if mb and ma and mb > 0 and ma > 0:
            if (p > mb * spike and p > ma * spike) or (p < mb / spike and p < ma / spike): continue
        out.append([t, p])
    return out


def compact(pts, now=None):
    """Full resolution for the last FULL_RES_S, then one point (the last) per COMPACT_S bucket."""
    now = now or time.time()
    old, new, seen = [], [], {}
    for t, p in pts:
        if now - t > FULL_RES_S: seen[t - t % COMPACT_S] = [t, p]
        else: new.append([t, p])
    return [seen[k] for k in sorted(seen)] + new


def load():
    if _state["loaded"]:
        return
    _state["loaded"] = True
    now = time.time()
    for path, key in ((STORE, None), (SEED, "pricehist")):
        try:
            d = json.load(open(path, encoding="utf-8"))
            d = d.get(key) if key else d
            for m, v in (d or {}).items():
                if not isinstance(v, dict) or not isinstance(v.get("pts"), list): continue
                pts = [p for p in v["pts"] if isinstance(p, list) and len(p) == 2 and now - p[0] < KEEP_S]
                with _lock:
                    cur = _h.get(m)
                    if cur:     # merge: union of timestamps
                        have = {p[0] for p in cur["pts"]}
                        cur["pts"] = sorted(cur["pts"] + [p for p in pts if p[0] not in have])[-MAX_PTS:]
                    elif pts:
                        _h[m] = {**v, "pts": pts[-MAX_PTS:]}
        except Exception:
            pass


def save():
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        with _lock:
            snap = {m: {**v, "pts": compact(v["pts"])} for m, v in _h.items()}
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(snap, f)
        os.replace(tmp, STORE)
    except Exception:
        pass


def export(max_pts=700, recent_s=4 * 3600):
    """Seed export: coins tracked in the last `recent_s`, thinned history (24h+ at 5-minute resolution)."""
    now = time.time()
    with _lock:
        return {m: {**{k: v[k] for k in v if k != "pts"}, "pts": compact(v["pts"], now)[-max_pts:]}
                for m, v in _h.items() if now - (v.get("seen") or 0) < recent_s}


def watch(mint, pool=None):
    if mint and B58.match(mint):
        with _lock:
            _watch[mint] = (pool, time.time())
            if len(_watch) > 80:
                for k in sorted(_watch, key=lambda k: _watch[k][1])[:20]: _watch.pop(k, None)


def pool_mint(pool):
    load()
    with _lock:
        for m, v in _h.items():
            if v.get("pool") == pool: return m
        for m, (p, _) in _watch.items():
            if p == pool: return m
    return None


def _hint(mint):
    try:
        p = _num(price_hint(mint)) if price_hint else None
        return p if p and p > 0 else None
    except Exception:
        return None


def _jup_prices(mints, timeout=8):
    out = {}
    for i in range(0, len(mints), 50):
        url = JUP + ",".join(mints[i:i + 50])
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            d = json.loads(r.read().decode())
        for m, v in (d or {}).items():
            p = _num((v or {}).get("usdPrice")) if isinstance(v, dict) else None
            if p and p > 0: out[m] = p
    return out


def tick(coins, now=None, network=True):
    """Record one snapshot per tracked coin. coins: report 'top' dicts (mint, pool, mcap, pc5, pc1, pc24, age_h)."""
    load()
    now = now or time.time()
    info = {}
    for c in coins or []:
        if isinstance(c, dict) and c.get("mint") and B58.match(str(c["mint"])):
            info[c["mint"]] = c
    with _lock:
        for m, (p, t) in list(_watch.items()):
            if now - t > WATCH_S: _watch.pop(m, None)
            elif m not in info: info[m] = {"mint": m, "pool": p}
    prices, err = {}, None
    if info and network and now >= _state["until"]:
        try:
            prices = _jup_prices(list(info))
            _state["n_fail"] = 0
        except urllib.error.HTTPError as e:
            err = f"jupiter HTTP {e.code}"
        except Exception as e:
            err = "jupiter " + type(e).__name__
        if err:
            _state["n_fail"] = min(_state["n_fail"] + 1, 5)
            _state["until"] = now + min(30 * 2 ** (_state["n_fail"] - 1), 600)
            _state["last_error"] = err
    n = 0
    for m, c in info.items():
        p = prices.get(m)
        mcap = _num(c.get("mcap"))
        if p is None: p = _hint(m)
        if p is None and mcap and mcap > 0 and m.endswith("pump") and not (_h.get(m) or {}).get("pts"):
            p = mcap / 1e9            # pump.fun supply is a fixed 1B tokens; only to seed a brand-new coin (mixing units makes spikes)
        if p is None or p <= 0: continue
        with _lock:
            v = _h.setdefault(m, {"pts": []})
            if c.get("pool"): v["pool"] = c["pool"]
            pc = {k: _num(c.get(f)) for k, f in (("5", "pc5"), ("60", "pc1"), ("1440", "pc24")) if _num(c.get(f)) is not None}
            if pc: v["pc"] = {**v.get("pc", {}), **pc, "t": now}
            if _num(c.get("age_h")) is not None: v["age_h"] = _num(c.get("age_h"))
            if mcap: v["mcap"] = mcap
            if not v["pts"] or now - v["pts"][-1][0] >= TICK_S * 0.6:
                v["pts"].append([int(now), p]); n += 1
            v["pts"] = compact([q for q in v["pts"] if now - q[0] < KEEP_S], now)[-MAX_PTS:]
            v["seen"] = now
    if network: _state.update(last_tick=int(now), ticks=_state["ticks"] + 1)
    if n: save()
    return n


def register(coins, now=None):
    """No-network snapshot for coins the scanner just published (mcap / 1B supply for pump.fun mints, plus the price-change
    anchors), so a chart exists the moment a coin appears; the tracker thread replaces it with Jupiter's price."""
    return tick(coins, now=now, network=False)


def snapshot_now(mint, pool=None, timeout=3.0):
    """One immediate Jupiter price for a coin nobody has tracked yet (a lookup / a coin opened from a link)."""
    load()
    now = time.time()
    if not mint or not B58.match(mint): return False
    with _lock:
        v = _h.get(mint)
        if v and v["pts"] and now - v["pts"][-1][0] < 30: return True
    try:
        p = _jup_prices([mint], timeout=timeout).get(mint)
    except Exception:
        p = None
    p = p or _hint(mint)
    if not p: return False
    with _lock:
        v = _h.setdefault(mint, {"pts": []})
        if pool: v["pool"] = pool
        v["pts"].append([int(now), p]); v["seen"] = now
    return True


def candles(mint, tf, limit=300):
    """Real OHLC candles from our own snapshots (volume 0 = unknown)."""
    load()
    sec = TF_S.get(tf)
    with _lock:
        pts = list((_h.get(mint) or {}).get("pts") or [])
    if not sec or not pts: return []
    pts = clean_points(pts)
    b = {}
    for t, p in pts:
        k = t - t % sec
        if k in b:
            c = b[k]; c["h"] = max(c["h"], p); c["l"] = min(c["l"], p); c["c"] = p
        else:
            b[k] = {"t": k, "o": p, "h": p, "l": p, "c": p, "v": 0}
    out = [b[k] for k in sorted(b)]
    for a, c in zip(out, out[1:]):      # candles join up: each opens where the previous one closed
        c["o"] = a["c"]; c["h"] = max(c["h"], c["o"]); c["l"] = min(c["l"], c["o"])
    return out[-limit:]


def last_price(mint):
    with _lock:
        pts = (_h.get(mint) or {}).get("pts") or []
        return pts[-1][1] if pts else None


def derived(mint, tf, now=None):
    """Coarse candles between DexScreener's own price-change anchors (5m / 1h / 24h). Approximate; labelled as such."""
    load()
    now = now or time.time()
    with _lock:
        v = _h.get(mint) or {}
        pts, pc, age_h = v.get("pts") or [], v.get("pc") or {}, v.get("age_h")
    if not pts or not pc: return []
    p0 = pts[-1][1]
    anchors = [(int(now), p0)]
    for mins in ("5", "60", "1440"):
        ch = _num(pc.get(mins))
        if ch is None or ch <= -99.9: continue
        if age_h is not None and age_h * 60 < int(mins) * 0.9: continue
        anchors.append((int(now - int(mins) * 60), p0 / (1 + ch / 100.0)))
    if len(anchors) < 2: return []
    anchors.sort()
    out = []
    for (t0, a), (t1, b) in zip(anchors, anchors[1:]):
        out.append({"t": t0, "o": a, "h": max(a, b), "l": min(a, b), "c": b, "v": 0})
    return out


def status():
    with _lock:
        n = len(_h); pts = sum(len(v["pts"]) for v in _h.values())
    return {"coins": n, "points": pts, **{k: _state[k] for k in ("ticks", "last_tick", "last_error")}}


def start(get_coins):
    """Tracker thread: one snapshot per TICK_S for the current report coins + watched mints. Never raises."""
    with _lock:
        if _state["started"]: return
        _state["started"] = True
    load()

    def run():
        while True:
            try:
                tick(get_coins() or [])
            except Exception as e:
                _state["last_error"] = "tick " + type(e).__name__
            time.sleep(TICK_S)
    threading.Thread(target=run, name="price-tracker", daemon=True).start()
