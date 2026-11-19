"""Bundle / insider-cluster detection for xScanner (read-only, keyless, GET requests only).

Source: RugCheck's full public report (https://api.rugcheck.xyz/v1/tokens/{mint}/report):
  graphInsidersDetected, insiderNetworks (size + tokens held), topHolders (insider flag), markets (LP accounts), risks.
From it we derive: linked-wallet count, share of supply held by insider networks, top-10 / top-20 holder
concentration EXCLUDING liquidity-pool and bonding-curve accounts.

Rules (see assess()):
  flagged     -> HIGH RISK: insider/bundle network holds >= 10% of supply, OR >= 50 linked wallets, OR one network >= 5%,
                 OR top-10 (ex-LP) >= 50%.
  elevated    -> caution: >= 5% / >= 20 wallets / top-10 (ex-LP) >= 30% / top-20 >= 45% / >= 3 insider-flagged top holders.
  unavailable -> "Bundle check unavailable": no usable data. Missing data is NEVER treated as safe.
  ok          -> a passing check (full report fetched, nothing above the thresholds).

Never blocks a scan: assess() only reads the cache and queues a background fetch (one worker thread, ~1 request every
2 s, 10-minute cache, exponential backoff on 429/5xx, last-good data kept and used for up to 6 h).
fetch_now() is the only blocking call, used for a user-initiated single-coin lookup with a short timeout.
No wallets, keys, signing, trading or posting.
"""
import json, os, re, threading, time, urllib.error, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
CACHE_S = int(os.environ.get("BUNDLE_CACHE_S", "600"))        # fresh for 10 minutes
STALE_OK_S = int(os.environ.get("BUNDLE_STALE_OK_S", "21600"))  # last-good still usable for 6 h if refresh fails
GAP_S = float(os.environ.get("BUNDLE_GAP_S", "2.0"))           # min gap between upstream calls
STORE = os.environ.get("BUNDLE_CACHE", os.path.join(os.environ.get("LIVE_DIR", "/tmp/xscanner-live"), "bundle_last.json"))
SEED = os.environ.get("CACHE_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "data", "seed_caches.json"))
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# thresholds
FLAG_SHARE, FLAG_WALLETS, FLAG_ONE_NET, FLAG_TOP10 = 10.0, 50, 5.0, 50.0
WARN_SHARE, WARN_WALLETS, WARN_TOP10, WARN_TOP20, WARN_INS_HOLDERS = 5.0, 20, 30.0, 45.0, 3
# score caps applied in app.py
CAP = {"flagged": 20, "elevated": 45, "unavailable": 60}
SAFETY_CAP = {"flagged": 0, "elevated": 8, "unavailable": 15}   # out of 25

_lock = threading.Lock()
_cache = {}            # mint -> {"t": epoch, "a": analysis dict}
_fail = {}             # mint -> epoch of the last failed attempt
_queue = []            # mints waiting for the worker
_queued = set()
_backoff_until = 0.0
_backoff_n = 0
_last_call = 0.0
_started = False
_loaded = False
stats = {"fetched": 0, "failed": 0, "rate_limited": 0, "last_error": None}


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def analyze(rep):
    """Derive bundle numbers from a RugCheck full report dict."""
    supply = _num((rep.get("token") or {}).get("supply")) or 0.0
    dec = int(_num((rep.get("token") or {}).get("decimals"), 0))
    markets = rep.get("markets") or []
    if not supply:
        for m in markets:
            s = _num((m.get("mintAAccount") or {}).get("supply"))
            if s: supply = s; break
    lp = set()
    for m in markets:
        for k in ("pubkey", "liquidityA", "liquidityB"):
            if m.get(k): lp.add(m[k])
    for a, info in (rep.get("knownAccounts") or {}).items():
        if isinstance(info, dict) and str(info.get("type", "")).upper() in ("AMM", "POOL", "LP", "PROTOCOL"):
            lp.add(a)
    nets = [n for n in (rep.get("insiderNetworks") or []) if isinstance(n, dict)]
    held = [_num(n.get("currentHolding"), _num(n.get("tokenAmount"))) for n in nets]
    shares = [(h / supply * 100.0 if supply else 0.0) for h in held]
    net_share = min(100.0, sum(shares))
    sizes = [int(_num(n.get("size"))) for n in nets]
    wallets = max(int(_num(rep.get("graphInsidersDetected"))), sum(sizes))
    holders = []
    for h in rep.get("topHolders") or []:
        if not isinstance(h, dict): continue
        if h.get("address") in lp or h.get("owner") in lp: continue
        holders.append(h)
    pcts = [_num(h.get("pct")) for h in holders]
    top10, top20 = sum(pcts[:10]), sum(pcts[:20])
    ins_top = [h for h in holders[:20] if h.get("insider")]
    ins_top_pct = sum(_num(h.get("pct")) for h in ins_top)
    risk_names = [str(r.get("name")) for r in (rep.get("risks") or []) if isinstance(r, dict) and r.get("name")]
    risk_hit = [n for n in risk_names if re.search(r"insider|bundl|sybil|linked|cluster", n, re.I)]
    big = max(zip(shares, sizes), default=(0.0, 0))
    # uniform balances: many top holders with near-identical amounts = sybil / farm wallets (a bundle RugCheck's graph may miss)
    amts = [_num(h.get("amount")) for h in holders[:20]]
    uni_n, uni_pct = 0, 0.0
    if len(amts) >= 10:
        srt = sorted(amts); med = srt[len(srt) // 2]
        if supply and med / supply * 100 >= 0.05:      # ignore dust-sized tokens / whales
            near = [a for a in amts if abs(a - med) <= 0.10 * med]
            uni_n, uni_pct = len(near), round(sum(near) / supply * 100, 2)
    top_mkt = max(markets, key=lambda m: _num((m.get("lp") or {}).get("quoteUSD")) + _num((m.get("lp") or {}).get("baseUSD")), default={})
    lpd = top_mkt.get("lp") or {}
    detected = rep.get("detectedAt")
    rcx = {"price": rep.get("price"), "liq": rep.get("totalMarketLiquidity"), "holders": rep.get("totalHolders"),
           "supply_raw": supply or None, "decimals": dec,
           "mint_auth": (rep.get("token") or {}).get("mintAuthority") or rep.get("mintAuthority"),
           "freeze_auth": (rep.get("token") or {}).get("freezeAuthority") or rep.get("freezeAuthority"),
           "lp_locked_pct": lpd.get("lpLockedPct") if lpd else None, "market_type": top_mkt.get("marketType"),
           "detected_at": detected, "rugged": rep.get("rugged"), "creator": rep.get("creator"),
           "meta_desc": ((rep.get("fileMeta") or {}).get("description") or "")[:400] or None,
           "top10_raw": round(sum(_num(h.get("pct")) for h in (rep.get("topHolders") or [])[:10] if isinstance(h, dict)), 2)
                        if rep.get("topHolders") else None}
    owners = [[h.get("owner") or h.get("address"), _num(h.get("pct"))] for h in holders[:10] if (h.get("owner") or h.get("address"))]
    top_accts = [h.get("address") for h in holders[:20] if h.get("address")]
    return {
        "uniform_n": uni_n, "uniform_pct": uni_pct, "uniform_of": len(amts), "rc": rcx, "owners": owners, "top_accts": top_accts,
        "top20_pct_reported": round(top20, 1),
        "wallets": wallets, "networks": len(nets), "net_share": round(net_share, 1),
        "largest_share": round(big[0], 1), "largest_size": big[1],
        "top10_ex_lp": round(top10, 1), "top20_ex_lp": round(top20, 1), "n_holders_seen": len(holders),
        "insider_top_holders": len(ins_top), "insider_top_pct": round(ins_top_pct, 1),
        "holders": rep.get("totalHolders"), "risk_hits": risk_hit, "supply_known": bool(supply),
    }


FUND_CACHE_S = 3600
FUND_FLAG_N, FUND_FLAG_FRAC, FUND_WARN_N, FUND_WARN_FRAC, FUND_MIN_RESOLVED = 5, 0.5, 3, 0.3, 6


def funding_check(a, budget_s=25.0):
    """Public-RPC check: which wallet created/funded each of the top holders (oldest transaction), and in which slot.
    Many top holders created by ONE wallet in the same few slots = a bundle. Best-effort; failures leave a['fund']=None."""
    import sol_rpc
    t0, res, errs = time.time(), [], 0
    for owner, pct in (a.get("owners") or [])[:10]:
        if time.time() - t0 > budget_s or errs >= 3: break
        try:
            r = sol_rpc.first_funding(owner)
        except sol_rpc.RpcError:
            errs += 1
            continue
        if r: res.append((owner, pct, r[0], r[1]))
    n = len(res)
    if n < 3:
        a["fund"] = {"resolved": n, "checked_at": time.time(), "largest": 0}
        return a["fund"]
    groups = {}
    for owner, pct, funder, slot in res: groups.setdefault(funder, []).append((owner, pct, slot))
    funder, mem = max(groups.items(), key=lambda kv: len(kv[1]))
    slots = sorted(m[2] for m in mem if m[2])
    same = max((sum(1 for y in slots if 0 <= y - x <= 5) for x in slots), default=0)
    a["fund"] = {"resolved": n, "largest": len(mem), "funder": funder, "pct": round(sum(m[1] for m in mem), 2),
                 "same_slots": same, "checked_at": time.time()}
    return a["fund"]


def _fmt_pct(p):
    return f"{p:.1f}".rstrip("0").rstrip(".") if p < 10 else f"{p:.0f}"


def assess_numbers(a, source="rugcheck"):
    """Turn bundle numbers into {status, level, reason, ...}. a=None -> unavailable."""
    if not a:
        return {"status": "unavailable", "level": "unknown", "flagged": False, "reason": "Bundle check unavailable",
                "source": source}
    w, share, one, t10, t20 = a.get("wallets") or 0, a.get("net_share") or 0, a.get("largest_share") or 0, \
        a.get("top10_ex_lp") or 0, a.get("top20_ex_lp") or 0
    hits = a.get("risk_hits") or []
    fd = a.get("fund") or {}
    f_n, f_res = fd.get("largest") or 0, fd.get("resolved") or 0
    fund_flag = f_res >= FUND_MIN_RESOLVED and f_n >= FUND_FLAG_N and f_n / f_res >= FUND_FLAG_FRAC
    fund_warn = f_res >= FUND_MIN_RESOLVED and f_n >= FUND_WARN_N and f_n / f_res >= FUND_WARN_FRAC
    uni, uni_of = a.get("uniform_n") or 0, a.get("uniform_of") or 0
    uni_flag, uni_warn = uni >= 12, uni >= 8
    parts = []
    if share and (w or a.get("networks")):
        parts.append(f"{w} linked wallets hold {_fmt_pct(share)}%" if w else f"insider networks hold {_fmt_pct(share)}%")
    elif w:
        parts.append(f"{w} linked wallets")
    if fund_flag or fund_warn:
        parts.append(f"{f_n} of {f_res} checked top holders were created by one wallet ({fd.get('funder','')[:4]}…{fd.get('funder','')[-4:]})"
                     + (f", {fd.get('same_slots')} within seconds of each other" if (fd.get("same_slots") or 0) >= 3 else ""))
    if uni_flag or uni_warn:
        parts.append(f"{uni} of the top {uni_of} holders hold near-identical amounts (~{_fmt_pct(a.get('uniform_pct',0)/max(uni,1))}% each)")
    if t10: parts.append(f"top 10 holders (excl. pool) {_fmt_pct(t10)}%")
    # the cluster that triggered (or best explains) the verdict: wallets + combined share held, whatever detected it
    cl = [(w, share, "insider network")]
    if uni_warn: cl.append((uni, a.get("uniform_pct") or 0.0, "near-identical holders"))
    if fund_warn: cl.append((f_n, fd.get("pct") or 0.0, "same funding wallet"))
    cw, cs, cb = max(cl, key=lambda t: (t[0], t[1]))
    net_flag = share >= FLAG_SHARE or w >= FLAG_WALLETS or one >= FLAG_ONE_NET or fund_flag or uni_flag
    flagged = net_flag or t10 >= FLAG_TOP10
    elevated = (not flagged) and (share >= WARN_SHARE or w >= WARN_WALLETS or t10 >= WARN_TOP10 or t20 >= WARN_TOP20
                                  or (a.get("insider_top_holders") or 0) >= WARN_INS_HOLDERS or bool(hits) or fund_warn or uni_warn)
    if flagged:
        head = "Bundled" if net_flag else "Concentrated"
        lvl, status = "flagged", "flagged"
        reason = f"{head}: " + ", ".join(parts)
    elif elevated:
        lvl, status = "elevated", "elevated"
        reason = "Bundle risk: " + (", ".join(parts) or "; ".join(hits))
    elif a.get("n_holders_seen") == 0:
        # no non-pool holder data at all (e.g. the only holder is the pool): that is unknown, never a pass
        lvl, status, flagged = "unknown", "unavailable", False
        reason = "Bundle check unavailable: no holder data beyond the liquidity pool"
    else:
        lvl, status = "ok", "ok"
        reason = "Bundle check passed" + (f" ({_fmt_pct(share)}% in linked wallets, top 10 {_fmt_pct(t10)}%)" if (share or t10) else "")
    out = {"status": status, "level": lvl, "flagged": flagged, "reason": reason, "source": source, **a}
    out["cluster_wallets"], out["cluster_share"], out["cluster_basis"] = (int(cw), round(float(cs), 1), cb) if (cw and cs) or cw else (0, 0.0, None)
    if status == "unavailable":
        out["top10_ex_lp"] = out["top20_ex_lp"] = None   # unknown, not 0%
    return out


def _load():
    global _loaded
    if _loaded: return
    _loaded = True
    now = time.time()
    for path, key in ((SEED, "bundle"), (STORE, None)):      # disk store last, so it wins when newer
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            for m, v in ((d.get(key) if key else d) or {}).items():
                if isinstance(v, dict) and isinstance(v.get("a"), dict) and now - v.get("t", 0) < STALE_OK_S \
                        and v.get("t", 0) >= (_cache.get(m) or {}).get("t", 0):
                    _cache[m] = v
        except Exception:
            pass


def export():
    """Cache entries for the seed file (box -> repo) so a redeploy does not reset every coin to 'unavailable'."""
    _load()
    now = time.time()
    return {m: v for m, v in _cache.items() if now - v.get("t", 0) < STALE_OK_S}


def _save():
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(_cache, f)
        os.replace(tmp, STORE)
    except Exception:
        pass


def _http(mint, timeout=15):
    req = urllib.request.Request(URL.format(mint=mint), headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _do_fetch(mint, timeout=15):
    """One upstream call. Returns True on success. Sets the shared backoff on 429/5xx."""
    global _backoff_until, _backoff_n, _last_call
    with _lock:
        if time.time() < _backoff_until: return False
        wait = _last_call + GAP_S - time.time()
        _last_call = time.time() + max(0, wait)
    if wait > 0: time.sleep(wait)
    try:
        rep = _http(mint, timeout)
        if not isinstance(rep, dict) or "mint" not in rep: raise ValueError("unexpected report")
        a = analyze(rep)
        with _lock:
            old = (_cache.get(mint) or {}).get("a") or {}
            if old.get("fund") and time.time() - (old["fund"].get("checked_at") or 0) < FUND_CACHE_S:
                a["fund"] = old["fund"]      # who funded a wallet never changes: keep the last check
            _cache[mint] = {"t": time.time(), "a": a}
            _fail.pop(mint, None); _backoff_n = 0
            stats["fetched"] += 1
        _save()
        return True
    except urllib.error.HTTPError as e:
        with _lock:
            _fail[mint] = time.time(); stats["failed"] += 1; stats["last_error"] = f"HTTP {e.code}"
            if e.code == 429 or e.code >= 500:
                if e.code == 429: stats["rate_limited"] += 1
                _backoff_n = min(_backoff_n + 1, 5)
                _backoff_until = time.time() + min(30 * 2 ** (_backoff_n - 1), 600)
    except Exception as e:
        with _lock:
            _fail[mint] = time.time(); stats["failed"] += 1; stats["last_error"] = type(e).__name__
    return False


def ensure_funding(mint, budget_s=25.0):
    """Run the RPC funding check for a cached report if it has none (or an old one). Never raises."""
    v = _cache.get(mint)
    if not v: return
    a = v["a"]; fd = a.get("fund")
    if fd and time.time() - (fd.get("checked_at") or 0) < FUND_CACHE_S and (fd.get("resolved") or 0) >= 3: return
    if fd and time.time() - (fd.get("checked_at") or 0) < 120: return
    try:
        funding_check(a, budget_s)
        _save()
    except Exception as e:
        stats["last_error"] = "funding " + type(e).__name__


def _worker():
    while True:
        mint = None
        with _lock:
            if _queue and time.time() >= _backoff_until: mint = _queue.pop(0)
        if mint is None:
            time.sleep(1.0); continue
        try:
            if _do_fetch(mint): ensure_funding(mint, 25.0)
        finally:
            with _lock: _queued.discard(mint)


def _ensure_worker():
    global _started
    with _lock:
        if _started: return
        _started = True
    threading.Thread(target=_worker, name="bundle-worker", daemon=True).start()


def _want(mint):
    """Queue a refresh when the cached entry is missing/old and we are not in a retry cool-down."""
    now = time.time()
    with _lock:
        v = _cache.get(mint)
        if v and now - v["t"] < CACHE_S: return
        if mint in _queued or now - _fail.get(mint, 0) < 60: return
        if len(_queue) > 200: return
        _queued.add(mint); _queue.append(mint)
    _ensure_worker()


def get_cached(mint):
    """(analysis dict | None, age_s). Never touches the network."""
    _load()
    v = _cache.get(mint)
    if not v: return None, None
    age = time.time() - v["t"]
    return (v["a"], age) if age < STALE_OK_S else (None, None)


def fallback_numbers(rc):
    """Partial numbers from the scanner's own RugCheck summary (used only while the full report is not cached)."""
    if not isinstance(rc, dict): return None
    w, nets, pct, t10 = rc.get("bundle_wallets"), rc.get("insider_networks"), rc.get("bundle_pct"), rc.get("top10")
    if w is None and nets is None and pct is None: return None
    return {"wallets": int(_num(w)), "networks": int(_num(nets)), "net_share": round(_num(pct), 1), "largest_share": 0.0,
            "largest_size": 0, "top10_ex_lp": 0.0, "top20_ex_lp": 0.0, "n_holders_seen": 0, "insider_top_holders": 0,
            "insider_top_pct": 0.0, "holders": rc.get("holders"), "risk_hits": [], "partial": True,
            "top10_incl_lp": t10}


def assess(mint, rc=None, queue=True):
    """Non-blocking verdict for a coin. Full cached report wins; otherwise partial scanner data can only make things
    WORSE (a partial result is never promoted to 'ok'); with no data at all -> unavailable."""
    if not mint or not MINT_RE.match(mint):
        return assess_numbers(None)
    a, age = get_cached(mint)
    if queue: _want(mint)
    if a:
        r = assess_numbers(a)
        r["age_s"] = int(age); r["stale"] = age >= CACHE_S
        return r
    fb = fallback_numbers(rc)
    if fb:
        r = assess_numbers(fb, source="scanner")
        if r["status"] == "ok":   # partial data cannot clear a coin
            r = {**r, "status": "unavailable", "level": "unknown", "flagged": False, "reason": "Bundle check unavailable"}
        r["pending"] = True
        return r
    r = assess_numbers(None); r["pending"] = True
    return r


def fetch_now(mint, timeout=6):
    """Blocking, short-timeout fetch for a user-initiated lookup. Falls back silently to the cache."""
    _load()
    if not mint or not MINT_RE.match(mint): return False
    a, age = get_cached(mint)
    if a and age < CACHE_S:
        ensure_funding(mint, 5.0); return True
    if time.time() < _backoff_until: return False
    ok = _do_fetch(mint, timeout=timeout)
    if ok: ensure_funding(mint, 5.0)
    return ok
