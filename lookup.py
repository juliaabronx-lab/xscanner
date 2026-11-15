"""Single-coin live lookup ("paste CA to scan").

Reuses Advisor's scoring logic from /workspace/scanner/scan.py WITHOUT importing it as a module
(a normal import would write __pycache__ into the scanner folder). Instead the source is read and
exec'd into a private namespace with bytecode writing disabled. Nothing is ever written to
/workspace/scanner or /workspace/paper. Read-only HTTP calls to DexScreener + RugCheck only.
No wallets, keys, signing or trading.
"""
import os, re, threading, time, types
from collections import defaultdict, deque

SCANNER = os.environ.get("SCANNER_DIR", "/workspace/scanner")
SCAN_PY = os.path.join(SCANNER, "scan.py")
B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
CACHE_TTL = 300
RATE_N, RATE_WIN = 10, 60          # live lookups per IP per minute
GLOBAL_N = 60                      # live lookups per minute, all IPs (protects upstream APIs)

_cache, _cache_lock = {}, threading.Lock()
_hits, _global, _rate_lock = defaultdict(deque), deque(), threading.Lock()
_inflight = defaultdict(threading.Lock)


class LookupError_(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status, self.msg = status, msg


def valid_mint(s):
    return bool(B58_RE.match(s or ""))


def load_scan_module():
    """Fresh namespace each call so scan.NOW / config are current. Never writes bytecode."""
    src = open(SCAN_PY, encoding="utf-8").read()
    mod = types.ModuleType("scan_lookup")
    mod.__file__ = SCAN_PY
    exec(compile(src, SCAN_PY, "exec"), mod.__dict__)   # __name__ != "__main__" -> main() not run
    return mod


def rate_check(ip):
    now = time.time()
    with _rate_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WIN: q.popleft()
        while _global and now - _global[0] > RATE_WIN: _global.popleft()
        if len(q) >= RATE_N:
            raise LookupError_(429, f"Slow down: max {RATE_N} new-coin scans per minute. Try again in {int(RATE_WIN - (now - q[0])) + 1}s.")
        if len(_global) >= GLOBAL_N:
            raise LookupError_(429, "The scanner is busy right now. Try again in a minute.")
        q.append(now); _global.append(now)


def cached(mint):
    with _cache_lock:
        e = _cache.get(mint)
        if e and time.time() - e[0] < CACHE_TTL:
            return e[1], int(time.time() - e[0])
        _cache.pop(mint, None)
    return None, None


def _ds_pairs(S, mint):
    """Pairs where `mint` is the base token. Tries both DexScreener endpoints; filters before deciding,
    so a response holding only quote-side pairs (or an upstream error) still falls through to the second one.
    Returns (pairs, upstream_error)."""
    def keep(ps):
        return [p for p in ps if isinstance(p, dict) and p.get("chainId") == "solana" and (p.get("baseToken") or {}).get("address") == mint]
    errs = []
    r = S.get(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", tries=3)
    if isinstance(r, dict) and "_error" in r: errs.append(r["_error"])
    pairs = keep(r if isinstance(r, list) else [])
    if not pairs:
        r = S.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", tries=3)
        if isinstance(r, dict) and "_error" in r: errs.append(r["_error"])
        pairs = keep((r.get("pairs") or []) if isinstance(r, dict) else [])
    return pairs, (errs[0] if len(errs) == 2 else None)


def live_scan(mint):
    S = load_scan_module()
    pairs, ds_err = _ds_pairs(S, mint)
    if not pairs and ds_err:
        raise LookupError_(503, "DexScreener didn't answer just now (busy or rate-limited). Try again in a minute.")
    if not pairs:
        rc_probe = S.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary", timeout=10, tries=1)
        if isinstance(rc_probe, dict) and "_error" not in rc_probe and rc_probe.get("score_normalised") is not None:
            raise LookupError_(404, "This token exists on RugCheck but has no trading pair on DexScreener yet, so there is no price/volume to score. Try again in a few minutes.")
        raise LookupError_(404, "No Solana token found for that address on DexScreener or RugCheck. Check that you pasted the token's contract (mint) address, not a wallet or pool address.")
    c = S.summarize_token(mint, pairs)

    # copycats: same-symbol Solana coins on DexScreener (scan.py logic minus X mint mentions)
    c["copycats"], c["primary"] = 0, True
    try:
        sym = S.norm_sym(c["symbol"])
        if sym:
            group = defaultdict(list)
            for p in S.ds_search(c["symbol"]):
                if S.norm_sym(p["baseToken"].get("symbol")) == sym:
                    group[p["baseToken"]["address"]].append(p)
            group[mint] = pairs
            coins = [S.summarize_token(m, ps) if m != mint else c for m, ps in group.items()]
            coins = [x for x in coins if x]
            def rank_key(x):
                b1, s1 = x["h1"]
                return (x["is_pump"], (b1 + s1) >= 100 and ((x["liq"] or 0) >= 10000 or not x["liq"]), (x["mcap"] or 0), -(x["age_h"] or 0))
            coins.sort(key=rank_key, reverse=True)
            orig = coins[0]
            if orig is c:
                c["copycats"] = len(coins) - 1
            else:
                c["primary"] = False
                c["is_copy"] = (c["mcap"] or 0) < 0.5 * (orig["mcap"] or 0) or (c["age_h"] or 0) < (orig["age_h"] or 0)
                c["copy_of"] = {"symbol": orig["symbol"], "mint": orig["mint"], "mcap": orig["mcap"]}
    except Exception:
        pass

    # X: no live X search here (needs agent MCP tools). Reuse last scan's stored posts read-only.
    try:
        raw = S.load_x()
    except Exception:
        raw = {"posts": {}, "users": {}, "counts": {}, "trends": []}
    enriched = S.analyse_x(raw)
    c["x"] = S.x_stats(enriched, {mint}, c["symbol"] if c["primary"] else None, c["name"] if c["primary"] else None)
    c["vel"] = S.velocity(raw.get("counts", {}), mint)
    c["trend"] = S.trend_match(c, raw.get("trends", []))
    c["blacklisted"] = S.is_blacklisted(c)
    n24 = c["h24"][0] + c["h24"][1]
    c["fees_est"] = round(n24 * S.CFG["fee_per_trade_sol_est"], 1) if n24 else None

    rc = S.rugcheck(mint, [p["pairAddress"] for p in pairs])
    c["rc"] = rc
    if not c["liq"] and rc.get("liq"): c["liq"] = rc["liq"]; c["liq_src"] = "rugcheck"
    if not c["mcap"] and rc.get("mcap"): c["mcap"] = rc["mcap"]; c["mcap_src"] = "rugcheck"
    c["score"], c["parts"], c["flags"] = S.score(c)
    c["reason"] = S.reason(c)

    x_avail = c["x"]["mentions"] > 0
    flags = []
    for f in c["flags"]:
        if f.startswith("weak X narrative") and not x_avail:
            flags.append("X data unavailable for live lookups (no paid-shill check) — score can't exceed 50 without an X narrative (same rule as scan.py)")
        else:
            flags.append(f)
    if x_avail:
        flags.append(f"X data from the last scan's stored posts only ({c['x']['mentions']} mentions); no live X search")
    c["flags"] = flags
    c["x_available"] = x_avail
    return c


def lookup_live(mint, ip):
    hit, age = cached(mint)
    if hit: return {**hit, "cached": True, "cache_age_s": age}
    with _inflight[mint]:                       # dedupe concurrent lookups of the same mint
        hit, age = cached(mint)
        if hit: return {**hit, "cached": True, "cache_age_s": age}
        rate_check(ip)
        last = None
        for i in range(3):
            try:
                c = live_scan(mint)
                last = None
                break
            except LookupError_ as e:
                last = e
                if e.status not in (502, 503) or i == 2:
                    raise
                time.sleep(1.5 * (2 ** i))   # cold / rate-limited upstream: retry inside the server
        if last:
            raise last
        with _cache_lock:
            _cache[mint] = (time.time(), c)
            if len(_cache) > 500:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]: _cache.pop(k, None)
        return {**c, "cached": False, "cache_age_s": 0}
