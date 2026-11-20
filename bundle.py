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
from concurrent.futures import ThreadPoolExecutor, as_completed

import net

UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
CACHE_S = int(os.environ.get("BUNDLE_CACHE_S", "600"))        # fresh for 10 minutes
STALE_OK_S = int(os.environ.get("BUNDLE_STALE_OK_S", "21600"))  # last-good still usable for 6 h if refresh fails
GAP_S = float(os.environ.get("BUNDLE_GAP_S", "2.0"))           # min gap between upstream calls
STORE = os.environ.get("BUNDLE_CACHE", os.path.join(os.environ.get("LIVE_DIR", "/tmp/xscanner-live"), "bundle_last.json"))
SEED = os.environ.get("CACHE_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "data", "seed_caches.json"))
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# thresholds
NEGLIGIBLE_PCT = 1.0     # non-pool top-20 holders hold less than this combined: no usable holder data (pool-only token)
FLAG_SHARE, FLAG_WALLETS, FLAG_ONE_NET, FLAG_TOP10 = 10.0, 50, 5.0, 50.0
WARN_SHARE, WARN_WALLETS, WARN_TOP10, WARN_TOP20, WARN_INS_HOLDERS = 5.0, 20, 30.0, 45.0, 3
# score caps applied in app.py
CAP = {"flagged": 20, "elevated": 45, "unavailable": 60}
SAFETY_CAP = {"flagged": 0, "elevated": 8, "unavailable": 15}   # out of 25

_lock = threading.Lock()
_cache = {}            # mint -> {"t": epoch, "a": analysis dict}
RAW = {}               # mint -> (epoch, slim raw RugCheck report): top holders, LP lock, insiders. Memory only, bounded.
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


UNI_TOL, UNI_MIN = 0.05, 5          # holders within 5% of each other's balance, at least 5 of them
ANALYSIS_V = 6                      # bump when analyze() changes shape: older cached analyses are refreshed first


def value_cluster(amts, supply, tol=UNI_TOL, min_n=UNI_MIN):
    """Largest group of balances within `tol` of each other (>= min_n holders, each >= 0.05% of supply).
    Returns (holders, combined % of supply). (0, 0.0) when there is none."""
    a = sorted((x for x in amts if x and x > 0), reverse=True)
    best, bs = (0, 0.0), (0, 0.0)
    for i in range(len(a)):
        j = i
        while j + 1 < len(a) and a[j + 1] >= a[i] * (1 - tol): j += 1
        n = j - i + 1
        if n < min_n or a[j] / supply * 100 < 0.05: continue
        s = sum(a[i:j + 1])
        if (n, s) > bs: bs, best = (n, s), (n, round(s / supply * 100, 2))
    return best


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
    net_raw = sum(shares)
    net_share = min(100.0, net_raw)
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
    net_credible = net_raw <= 100.5       # the networks cannot hold more than the whole supply: RugCheck double-counts (pool / cumulative amounts)
    # near-identical balances: cluster the top-20 non-pool holders BY VALUE (>= UNI_MIN holders within UNI_TOL of each other).
    amts = [_num(h.get("amount")) for h in holders[:20]]
    uni_n, uni_pct = 0, 0.0
    if len(amts) >= 10 and supply:
        uni_n, uni_pct = value_cluster(amts, supply)
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
        "av": ANALYSIS_V, "wallets": wallets, "networks": len(nets),
        "net_share": round(net_share, 1) if net_credible else None, "net_share_raw": round(net_raw, 1),
        "largest_share": round(big[0], 1) if net_credible else None, "largest_size": big[1],
        "holders_negligible": (not top20) or top20 < NEGLIGIBLE_PCT,
        "top10_ex_lp": round(top10, 1), "top20_ex_lp": round(top20, 1), "n_holders_seen": len(holders),
        "insider_top_holders": len(ins_top), "insider_top_pct": round(ins_top_pct, 1),
        "holders": rep.get("totalHolders"), "risk_hits": risk_hit, "supply_known": bool(supply),
    }


FUND_CACHE_S = 3600
FUND_FLAG_N, FUND_FLAG_FRAC, FUND_WARN_N, FUND_WARN_FRAC, FUND_MIN_RESOLVED = 5, 0.5, 3, 0.3, 6


def funding_check(a, budget_s=25.0, workers=1):
    """Public-RPC check: which wallet created/funded each of the top holders (oldest transaction), and in which slot.
    Many top holders created by ONE wallet in the same few slots = a bundle. Best-effort; failures leave a['fund']=None.

    workers=1 (the background scan) stays strictly serial and keeps the global RPC gap.
    A user waiting on a lookup passes workers>1: those calls skip the gap and run together,
    so ten holders do not add ten round trips end to end. The verdict math below is unchanged."""
    import sol_rpc
    t0, res, errs = time.time(), [], 0
    owners = (a.get("owners") or [])[:10]
    if workers <= 1:
        for owner, pct in owners:
            if time.time() - t0 > budget_s or errs >= 3: break
            try:
                r = sol_rpc.first_funding(owner)
            except sol_rpc.RpcError:
                errs += 1
                continue
            if r: res.append((owner, pct, r[0], r[1]))
    else:
        lock = threading.Lock()
        def one(item):
            nonlocal errs
            owner, pct = item
            if time.time() - t0 > budget_s: return
            with lock:
                if errs >= 3: return
            try:
                r = sol_rpc.first_funding(owner, gap=0.0)
            except sol_rpc.RpcError:
                with lock: errs += 1
                return
            if r:
                with lock: res.append((owner, pct, r[0], r[1]))
        n_workers = max(1, min(int(workers), 4, len(owners) or 1))
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(one, it) for it in owners]
            for f in as_completed(futs):
                if time.time() - t0 > budget_s: break
                f.result()
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
    uni_pct = a.get("uniform_pct") or 0.0
    uni_flag = uni >= 8 or (uni >= 5 and uni_pct >= 15.0)
    uni_warn = uni >= 5 and uni_pct >= 3.0
    share_known = a.get("net_share") is not None
    parts = []
    # 'linked wallets' is ONLY RugCheck's insider-network count; near-identical balances are labelled as holders
    if w or a.get("networks"):
        if share_known and share:
            parts.append(f"{w} linked wallets hold {_fmt_pct(share)}%" if w else f"insider networks hold {_fmt_pct(share)}%")
        elif not share_known and w:
            parts.append(f"{w} linked wallets (share unknown: RugCheck reports over 100% of supply)")
        elif w:
            parts.append(f"{w} linked wallets")
    if fund_flag or fund_warn:
        parts.append(f"{f_n} of {f_res} checked top holders were created by one wallet ({fd.get('funder','')[:4]}…{fd.get('funder','')[-4:]})"
                     + (f", {fd.get('same_slots')} within seconds of each other" if (fd.get("same_slots") or 0) >= 3 else ""))
    if uni_flag or uni_warn:
        parts.append(f"{uni} near-identical holders hold {_fmt_pct(uni_pct)}%")
    if t10: parts.append(f"top 10 holders (excl. pool) {_fmt_pct(t10)}%")
    # the cluster that triggered (or best explains) the verdict: holders + combined share held, whatever detected it
    cl = [(w, share if share_known else None, "insider network")]
    if uni_warn: cl.append((uni, uni_pct, "near-identical holders"))
    if fund_warn: cl.append((f_n, fd.get("pct") or 0.0, "same funding wallet"))
    cw, cs, cb = max(cl, key=lambda t: (t[0], t[1] or 0.0))
    if not cw and t10 >= WARN_TOP10:       # pure concentration: the cluster is the top-10 holders themselves
        cw, cs, cb = min(10, a.get("n_holders_seen") or 10), t10, "top-10 holders"
    share_t = share if share_known else min(100.0, a.get("net_share_raw") or 0.0)   # thresholds only; never displayed
    net_flag = share_t >= FLAG_SHARE or w >= FLAG_WALLETS or one >= FLAG_ONE_NET or fund_flag or uni_flag
    flagged = net_flag or t10 >= FLAG_TOP10
    elevated = (not flagged) and (share_t >= WARN_SHARE or w >= WARN_WALLETS or t10 >= WARN_TOP10 or t20 >= WARN_TOP20
                                  or (a.get("insider_top_holders") or 0) >= WARN_INS_HOLDERS or bool(hits) or fund_warn or uni_warn)
    negligible = a["holders_negligible"] if "holders_negligible" in a else (t20 < NEGLIGIBLE_PCT)
    if flagged:
        head = "Bundled" if net_flag else "Concentrated"
        lvl, status = "flagged", "flagged"
        reason = f"{head}: " + ", ".join(parts)
    elif elevated:
        lvl, status = "elevated", "elevated"
        reason = "Bundle risk: " + (", ".join(parts) or "; ".join(hits))
    elif a.get("n_holders_seen") == 0 or negligible:
        # no usable non-pool holder data (only the pool, or only dust holders): that is unknown, never a pass
        lvl, status, flagged = "unknown", "unavailable", False
        reason = ("Bundle check unavailable: no holder data beyond the liquidity pool" if not a.get("n_holders_seen") else
                  f"Bundle check unavailable: the {a.get('n_holders_seen')} holders left after excluding the pool hold only {_fmt_pct(t20)}% combined")
    else:
        lvl, status = "ok", "ok"
        reason = "Bundle check passed" + (f" ({_fmt_pct(share)}% in linked wallets, top 10 {_fmt_pct(t10)}%)" if ((share and share_known) or t10) else "")
    out = {"status": status, "level": lvl, "flagged": flagged, "reason": reason, "source": source, **a}
    noun = {"insider network": "linked wallets", "near-identical holders": "near-identical holders",
            "same funding wallet": "holders created by one wallet", "top-10 holders": "top holders"}.get(cb, "linked wallets")
    if cw:
        out["cluster_wallets"], out["cluster_share"], out["cluster_basis"] = int(cw), (round(float(cs), 1) if cs is not None else None), cb
        out["cluster_text"] = f"{int(cw)} {noun} hold {_fmt_pct(float(cs))}%" if cs is not None else f"{int(cw)} {noun} (share unknown)"
    else:
        out["cluster_wallets"], out["cluster_share"], out["cluster_basis"], out["cluster_text"] = 0, 0.0, None, None
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


def _slim_raw(rep):
    """The parts of a RugCheck report the token page shows (holders table, LP lock, insiders, risks). Small, no secrets."""
    markets, lp_accts = [], set()
    for m in rep.get("markets") or []:
        if not isinstance(m, dict): continue
        for k in ("pubkey", "liquidityA", "liquidityB"):
            if m.get(k): lp_accts.add(m[k])
        lp = m.get("lp") or {}
        markets.append({"type": m.get("marketType"), "pubkey": m.get("pubkey"),
                        "lp": {k: lp.get(k) for k in ("lpLockedPct", "lpLockedUSD", "lpLocked", "lpUnlocked", "quoteUSD", "baseUSD", "lpTotalSupply") if k in lp}})
    top = [{"address": h.get("address"), "owner": h.get("owner"), "pct": _num(h.get("pct")), "insider": bool(h.get("insider"))}
           for h in (rep.get("topHolders") or [])[:20] if isinstance(h, dict)]
    nets = [{"size": n.get("size"), "type": n.get("type"), "tokenAmount": n.get("tokenAmount"), "activeAccounts": n.get("activeAccounts")}
            for n in (rep.get("insiderNetworks") or [])[:6] if isinstance(n, dict)]
    risks = [{"name": r.get("name"), "level": r.get("level"), "description": r.get("description")}
             for r in (rep.get("risks") or [])[:10] if isinstance(r, dict)]
    return {"creator": rep.get("creator"), "creator_balance": rep.get("creatorBalance"), "creator_tokens": rep.get("creatorTokens"),
            "rugged": rep.get("rugged"), "launchpad": (rep.get("launchpad") or {}).get("name") if isinstance(rep.get("launchpad"), dict) else rep.get("launchpad"),
            "transfer_fee": rep.get("transferFee"), "holders": rep.get("totalHolders"), "markets": markets, "lp_accts": sorted(lp_accts), "top": top,
            "insider_networks": nets, "insiders_detected": rep.get("graphInsidersDetected"), "risks": risks,
            "score": rep.get("score_normalised") if rep.get("score_normalised") is not None else rep.get("score"),
            "mint_auth": (rep.get("token") or {}).get("mintAuthority") or rep.get("mintAuthority"),
            "freeze_auth": (rep.get("token") or {}).get("freezeAuthority") or rep.get("freezeAuthority"),
            "total_liq": rep.get("totalMarketLiquidity"), "price": rep.get("price")}


def fetch_raw(mint, timeout=6):
    """Make sure the slim raw report (holders table, LP lock) is fresh: one RugCheck call unless we have it already."""
    v = RAW.get(mint)
    if v and time.time() - v[0] < CACHE_S: return True
    if not mint or not MINT_RE.match(mint): return False
    return _do_fetch(mint, timeout=timeout, gap=False)


def get_raw(mint):
    v = RAW.get(mint)
    return (v[1], time.time() - v[0]) if v else (None, None)


def _http(mint, timeout=15, scope=""):
    # Shared with the live lookup: if that scan already asked RugCheck for this mint, this is a cache hit.
    return net.get_json(URL.format(mint=mint), timeout=timeout, headers=UA, errors="raise", scope=scope)


def _do_fetch(mint, timeout=15, gap=True):
    """One upstream call. Returns True on success. Sets the shared backoff on 429/5xx.
    gap=False skips the inter-call sleep. The background worker keeps the gap; a person waiting
    on Paste-CA does not sit through it."""
    global _backoff_until, _backoff_n, _last_call
    with _lock:
        if time.time() < _backoff_until: return False
        wait = (_last_call + GAP_S - time.time()) if gap else 0
        _last_call = time.time() + max(0, wait)
    if wait > 0: time.sleep(wait)
    try:
        rep = _http(mint, timeout, scope="" if gap else "user")
        if not isinstance(rep, dict) or "mint" not in rep: raise ValueError("unexpected report")
        a = analyze(rep)
        try:
            RAW[mint] = (time.time(), _slim_raw(rep))
            if len(RAW) > 150:
                for k in sorted(RAW, key=lambda k: RAW[k][0])[:50]: RAW.pop(k, None)
        except Exception:
            pass
        with _lock:
            old = (_cache.get(mint) or {}).get("a") or {}
            if old.get("fund") and time.time() - (old["fund"].get("checked_at") or 0) < FUND_CACHE_S:
                a["fund"] = old["fund"]      # who funded a wallet never changes: keep the last check
            _cache[mint] = {"t": time.time(), "a": a}
            _fail.pop(mint, None); _backoff_n = 0
            stats["fetched"] += 1
        _save()
        return True
    except (urllib.error.HTTPError, net.UpstreamError) as e:
        code = getattr(e, "code", None) or 0
        with _lock:
            _fail[mint] = time.time(); stats["failed"] += 1; stats["last_error"] = f"HTTP {code}"
            if code == 429 or code >= 500:
                if code == 429: stats["rate_limited"] += 1
                _backoff_n = min(_backoff_n + 1, 5)
                _backoff_until = time.time() + min(30 * 2 ** (_backoff_n - 1), 600)
    except Exception as e:
        with _lock:
            _fail[mint] = time.time(); stats["failed"] += 1; stats["last_error"] = type(e).__name__
    return False


def ensure_funding(mint, budget_s=25.0, workers=1):
    """Run the RPC funding check for a cached report if it has none (or an old one). Never raises."""
    v = _cache.get(mint)
    if not v: return
    a = v["a"]; fd = a.get("fund")
    if fd and time.time() - (fd.get("checked_at") or 0) < FUND_CACHE_S and (fd.get("resolved") or 0) >= 3: return
    if fd and time.time() - (fd.get("checked_at") or 0) < 120: return
    try:
        funding_check(a, budget_s, workers=workers)
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
        old_shape = bool(v) and (v.get("a") or {}).get("av") != ANALYSIS_V     # cached by an older analyze(): refresh it first
        if v and now - v["t"] < CACHE_S and not old_shape: return
        if mint in _queued or now - _fail.get(mint, 0) < 60: return
        if len(_queue) > 200: return
        _queued.add(mint)
        if old_shape: _queue.insert(0, mint)
        else: _queue.append(mint)
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
    """Blocking, short-timeout fetch for a user-initiated lookup. Falls back silently to the cache.
    The background worker stays serial; this path overlaps the holder-funding reads and skips the
    inter-call gap so the HTTP response is not stuck behind the scan loop's politeness delay."""
    _load()
    if not mint or not MINT_RE.match(mint): return False
    a, age = get_cached(mint)
    if a and age < CACHE_S:
        ensure_funding(mint, 5.0, workers=4); return True
    if time.time() < _backoff_until: return False
    ok = _do_fetch(mint, timeout=timeout, gap=False)
    if ok: ensure_funding(mint, 5.0, workers=4)
    return ok
