"""Data-confidence cross-check layer for xScanner (read-only, keyless, GET/JSON-RPC reads only).

For every coin we pull the same key fields from independent sources and compare them:
  DexScreener (fresh pull), GeckoTerminal, RugCheck (the scanner's own copy / full report), public Solana RPC
  (supply + mint/freeze authority straight from the chain).
Per field: agree (>= 2 sources within tolerance) | mismatch (> 15% apart, or authorities/supply disagree) |
           single (only one source) | missing (no source) | stale (data too old).
Score = weighted share of fields that agree (single = half credit, mismatch/missing = 0, stale = half).
Levels: high >= 75, medium 50-74, low < 50. MISSING DATA IS NEVER SAFE: low confidence caps the score at 45 (below
'watch'), 'unchecked' (nothing fetched yet) caps it at 60 (below 'look'); medium keeps its score but names the shaky fields.

Never blocks a scan: assess() only reads the cache and queues a background fetch (one worker thread, polite gaps,
exponential back-off on 429/5xx, last-good data kept for 6 h and then marked stale). fetch_now() is the only blocking
call (user-initiated single-coin lookup, short timeouts).
"""
import json, os, re, threading, time, urllib.error, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
CACHE_S = int(os.environ.get("CONF_CACHE_S", "600"))
STALE_S = int(os.environ.get("CONF_STALE_S", "1800"))      # external pulls older than this count as stale
KEEP_S = int(os.environ.get("CONF_KEEP_S", "21600"))
GT_GAP_S = float(os.environ.get("CONF_GT_GAP_S", "2.3"))   # GeckoTerminal free tier ~30 req/min
STORE = os.environ.get("CONF_CACHE", os.path.join(os.environ.get("LIVE_DIR", "/tmp/xscanner-live"), "confidence_last.json"))
SEED = os.environ.get("CACHE_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "data", "seed_caches.json"))
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TOL = 0.15                    # sources further apart than this (relative) disagree
TOP10_TOL_PP = 6.0            # top-10 share: percentage points
CAP = {"low": 45, "medium": 100, "unchecked": 60, "high": 100}
WEIGHTS = {"mint_authority": 8, "freeze_authority": 7, "supply": 10, "liquidity": 20, "market_cap": 15, "price": 10,
           "holders": 10, "top10": 10, "lp_lock": 5, "age": 5}
LABEL = {"mint_authority": "mint authority", "freeze_authority": "freeze authority", "supply": "supply",
         "liquidity": "liquidity", "market_cap": "market cap", "price": "price", "holders": "holder count",
         "top10": "top-10 share", "lp_lock": "LP lock/burn", "age": "age"}

_lock = threading.Lock()
_cache, _queue, _queued, _fail = {}, [], set(), {}
_state = {"gt_until": 0.0, "gt_n": 0, "gt_last": 0.0, "started": False, "loaded": False}
stats = {"fetched": 0, "failed": 0, "last_error": None}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pos(v):
    """A zero / negative / missing number is not a usable value for money fields (it is reported by sanity_problems instead)."""
    f = _num(v)
    return f if f is not None and f > 0 else None


MCAP_MAX = 1e11          # no memecoin is worth more than $100B
LIQ_MIN = 100.0          # under $100 of liquidity is a dead pool, not a market


def sanity_problems(c, ds=None, gt=None, rc=None):
    """Impossible or absurd money numbers across every source we hold. Returns {field label: reason}.
    A zero/negative mcap or liquidity, an mcap above $100B, liquidity under $100, or liquidity above market cap
    (in any one source, or between the best values) can never be 'high' confidence."""
    ds, gt, rc = ds or {}, gt or {}, rc or {}
    mc = {"scanner": c.get("mcap"), "dexscreener": ds.get("mcap"), "geckoterminal": gt.get("fdv"), "rugcheck": rc.get("mcap")}
    lq = {"scanner": c.get("liq"), "dexscreener": ds.get("liq"), "geckoterminal": gt.get("reserve"), "rugcheck": rc.get("liq")}
    mc = {k: _num(v) for k, v in mc.items() if _num(v) is not None}
    lq = {k: _num(v) for k, v in lq.items() if _num(v) is not None}
    out = {}
    zero = [k for k, v in mc.items() if v <= 0]
    if zero: out["market cap"] = f"market cap is ${_fmt(min(mc[k] for k in zero))} ({zero[0]})"
    elif mc and max(mc.values()) > MCAP_MAX: out["market cap"] = f"market cap ${_fmt(max(mc.values()))} is absurd"
    elif not mc: out["market cap"] = "market cap missing from every source"
    zl = [k for k, v in lq.items() if v <= 0]
    if zl: out["liquidity"] = f"liquidity is ${_fmt(min(lq[k] for k in zl))} ({zl[0]})"
    elif lq and max(lq.values()) < LIQ_MIN: out["liquidity"] = f"liquidity ${_fmt(max(lq.values()))} is near zero"
    elif not lq: out["liquidity"] = "liquidity missing from every source"
    pm = {k: v for k, v in mc.items() if v > 0}; pl = {k: v for k, v in lq.items() if v > 0}
    if "liquidity" not in out or "market cap" not in out:
        bad = [k for k in pl if k in pm and pl[k] > pm[k]]
        if bad: out.setdefault("liquidity", f"liquidity ${_fmt(pl[bad[0]])} is above market cap ${_fmt(pm[bad[0]])} ({bad[0]})")
        elif pm and pl and max(pl.values()) > max(pm.values()):
            out.setdefault("liquidity", f"liquidity ${_fmt(max(pl.values()))} is above market cap ${_fmt(max(pm.values()))}")
    if "liquidity" in out and "above market cap" in out["liquidity"]:
        out.setdefault("market cap", "market cap is below the liquidity in the pool")
    return out


def _get(url, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------ fetchers (each returns a dict or None; never raises)
def pull_ds(mint, timeout=8):
    try:
        d = _get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout)
        pairs = [p for p in (d.get("pairs") or []) if p.get("chainId") == "solana" and (p.get("baseToken") or {}).get("address") == mint]
        if not pairs: return None
        p = max(pairs, key=lambda p: _num((p.get("liquidity") or {}).get("usd")) or 0)
        info, tx, vol = p.get("info") or {}, p.get("txns") or {}, p.get("volume") or {}
        n = lambda k: (_num((tx.get(k) or {}).get("buys")) or 0) + (_num((tx.get(k) or {}).get("sells")) or 0)
        return {"price": _num(p.get("priceUsd")), "liq": _num((p.get("liquidity") or {}).get("usd")),
                "mcap": _num(p.get("marketCap")) or _num(p.get("fdv")), "created_ms": _num(p.get("pairCreatedAt")),
                "websites": [w.get("url") for w in (info.get("websites") or []) if isinstance(w, dict) and w.get("url")][:3],
                "socials": [s.get("type") for s in (info.get("socials") or []) if isinstance(s, dict) and s.get("type")][:6],
                "pc6": _num((p.get("priceChange") or {}).get("h6")), "tx_h1": n("h1"), "tx_h24": n("h24"),
                "vol_h1": _num(vol.get("h1")), "vol_h24": _num(vol.get("h24"))}
    except Exception as e:
        stats["last_error"] = "ds " + type(e).__name__
        return None


def _auth_set(v):
    """GeckoTerminal reports an authority as 'no' (renounced), null (unknown) or something else (set)."""
    if v is None: return None
    s = str(v).strip().lower()
    return False if s in ("no", "false", "none", "revoked", "") else True


def pull_gt(mint, timeout=8, info=True):
    """GeckoTerminal token + info endpoints, paced at GT_GAP_S, with shared back-off on 429."""
    out = {}
    for suffix, key in (("", "tok"), ("/info", "info")):
        if suffix and not info: continue
        with _lock:
            if time.time() < _state["gt_until"]: break
            wait = _state["gt_last"] + GT_GAP_S - time.time()
            _state["gt_last"] = time.time() + max(0.0, wait)
        if wait > 0: time.sleep(wait)
        try:
            out[key] = (_get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}{suffix}", timeout).get("data") or {}).get("attributes") or {}
            _state["gt_n"] = 0
        except urllib.error.HTTPError as e:
            stats["last_error"] = f"gt HTTP {e.code}"
            if e.code == 429 or e.code >= 500:
                with _lock:
                    _state["gt_n"] = min(_state["gt_n"] + 1, 5); _state["gt_until"] = time.time() + min(30 * 2 ** (_state["gt_n"] - 1), 600)
                break
        except Exception as e:
            stats["last_error"] = "gt " + type(e).__name__
    if not out: return None
    t, i = out.get("tok") or {}, out.get("info") or {}
    hold = i.get("holders") or {}
    dist = _num((hold.get("distribution_percentage") or {}).get("top_10"))
    dec = _num(t.get("decimals"))
    return {"price": _num(t.get("price_usd")), "fdv": _num(t.get("fdv_usd")), "reserve": _num(t.get("total_reserve_in_usd")),
            "supply_raw": _num(t.get("total_supply")), "decimals": dec,
            "holders": _num(hold.get("count")) if hold else None, "top10": dist,
            "mint_set": _auth_set(i.get("mint_authority")), "freeze_set": _auth_set(i.get("freeze_authority")),
            "desc": (i.get("description") or "")[:400] or None, "websites": (i.get("websites") or [])[:3] or None,
            "twitter": i.get("twitter_handle") or None, "telegram": i.get("telegram_handle") or None,
            "completed_at": ((i.get("launchpad_details") or {}).get("completed_at")) or ((t.get("launchpad_details") or {}).get("completed_at"))}


def pull_rpc(mint):
    try:
        import sol_rpc
        m = sol_rpc.mint_info(mint)
        return {"supply_raw": m["supply_raw"], "decimals": m["decimals"], "mint_set": bool(m["mint_auth"]),
                "freeze_set": bool(m["freeze_auth"])}
    except Exception as e:
        stats["last_error"] = "rpc " + type(e).__name__
        return None


def _load():
    if _state["loaded"]: return
    _state["loaded"] = True
    now = time.time()
    for path, key in ((SEED, "confidence"), (STORE, None)):      # disk store last, so it wins when newer
        try:
            with open(path, encoding="utf-8") as f: d = json.load(f)
            for m, v in ((d.get(key) if key else d) or {}).items():
                if isinstance(v, dict) and now - v.get("t", 0) < KEEP_S and v.get("t", 0) >= (_cache.get(m) or {}).get("t", 0):
                    _cache[m] = v
        except Exception:
            pass


def export():
    _load()
    now = time.time()
    return {m: v for m, v in _cache.items() if now - v.get("t", 0) < KEEP_S}


def _save():
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(_cache, f)
        os.replace(tmp, STORE)
    except Exception:
        pass


def _do_fetch(mint, timeout=8, gt=True):
    new = {"t": time.time(), "ds": pull_ds(mint, timeout), "rpc": pull_rpc(mint),
           "gt": pull_gt(mint, timeout) if gt else None}
    with _lock:
        old = _cache.get(mint) or {}
        for k in ("ds", "gt", "rpc"):          # keep last-good for a source that failed this time (kept with its own age)
            if new[k] is None and old.get(k) is not None:
                new[k] = old[k]; new.setdefault("kept", {})[k] = old.get("kt", {}).get(k, old.get("t"))
        new["kt"] = {k: (new.get("kept", {}).get(k) or new["t"]) for k in ("ds", "gt", "rpc") if new[k] is not None}
        ok = any(new[k] is not None for k in ("ds", "gt", "rpc"))
        if ok: _cache[mint] = new; stats["fetched"] += 1; _fail.pop(mint, None)
        else: stats["failed"] += 1; _fail[mint] = time.time()
    if ok: _save()
    return ok


def _worker():
    while True:
        mint = None
        with _lock:
            if _queue: mint = _queue.pop(0)
        if mint is None:
            time.sleep(1.0); continue
        try: _do_fetch(mint)
        except Exception as e: stats["last_error"] = "worker " + type(e).__name__
        finally:
            with _lock: _queued.discard(mint)


def _want(mint):
    now = time.time()
    with _lock:
        v = _cache.get(mint)
        if v and now - v["t"] < CACHE_S: return
        if mint in _queued or now - _fail.get(mint, 0) < 60 or len(_queue) > 200: return
        _queued.add(mint); _queue.append(mint)
        if not _state["started"]:
            _state["started"] = True
            threading.Thread(target=_worker, name="confidence-worker", daemon=True).start()


def fetch_now(mint, timeout=6):
    _load()
    if not mint or not MINT_RE.match(mint): return False
    v = _cache.get(mint)
    if v and time.time() - v["t"] < CACHE_S: return True
    return _do_fetch(mint, timeout=timeout)


# ------------------------------------------------------------------ comparison
def _rel_close(vals, tol=TOL):
    lo, hi = min(vals), max(vals)
    return hi == 0 or (hi - lo) / hi <= tol


def _cmp_numeric(srcs, tol=TOL, pp=None):
    """srcs: {name: value|None}. -> (status, values)."""
    vals = {k: v for k, v in srcs.items() if v is not None}
    if not vals: return "missing", {}
    if len(vals) == 1: return "single", vals
    v = list(vals.values())
    ok = (max(v) - min(v) <= pp) if pp is not None else _rel_close(v, tol)
    if ok: return "agree", vals
    # a majority within tolerance still counts as agreement of those sources, but the outlier is named
    names = list(vals)
    for n in names:
        rest = [vals[m] for m in names if m != n]
        if len(rest) >= 2 and ((max(rest) - min(rest) <= pp) if pp is not None else _rel_close(rest, tol)):
            return "mismatch", vals
    return "mismatch", vals


def _cmp_flag(srcs):
    vals = {k: v for k, v in srcs.items() if v is not None}
    if not vals: return "missing", {}
    if len(vals) == 1: return "single", vals
    return ("agree" if len(set(vals.values())) == 1 else "mismatch"), vals


def evaluate(c, entry, scan_age_s=None, rc_extra=None):
    """Compare a coin's numbers (scanner copy = DexScreener-at-scan-time + RugCheck) with the cached independent pulls."""
    rc = c.get("rugcheck") or {}
    rx = rc_extra or {}
    ds, gt, rp = (entry or {}).get("ds") or {}, (entry or {}).get("gt") or {}, (entry or {}).get("rpc") or {}
    kt = (entry or {}).get("kt") or {}
    now = time.time()
    stale_src = {{"ds": "dexscreener", "gt": "geckoterminal", "rpc": "rpc"}[k] for k, t in kt.items() if now - t > STALE_S}
    scan_stale = scan_age_s is not None and scan_age_s > 7200
    dec = _num(rp.get("decimals")) if rp else _num(gt.get("decimals"))
    f = {}
    # authorities: chain is the authority; RugCheck and GeckoTerminal must agree with it
    mset = lambda v: None if v is None and "mint_auth" not in rc else bool(v)
    f["mint_authority"] = _cmp_flag({"rpc": rp.get("mint_set"), "rugcheck": (bool(rc.get("mint_auth")) if "mint_auth" in rc else None), "geckoterminal": gt.get("mint_set")})
    f["freeze_authority"] = _cmp_flag({"rpc": rp.get("freeze_set"), "rugcheck": (bool(rc.get("freeze_auth")) if "freeze_auth" in rc else None), "geckoterminal": gt.get("freeze_set")})
    f["supply"] = _cmp_numeric({"rpc": rp.get("supply_raw"), "geckoterminal": gt.get("supply_raw"), "rugcheck": _num(rx.get("supply_raw"))}, tol=0.02)
    f["liquidity"] = _cmp_numeric({"dexscreener": _pos(ds.get("liq")), "geckoterminal": _pos(gt.get("reserve")),
                                   "rugcheck": _pos(rc.get("liq")), "scanner": _pos(c.get("liq"))})
    sup_ui = (_num(rp.get("supply_raw")) / 10 ** dec) if rp.get("supply_raw") and dec is not None else None
    chain_mcap = (ds.get("price") * sup_ui) if (ds.get("price") and sup_ui) else None
    f["market_cap"] = _cmp_numeric({"dexscreener": _pos(ds.get("mcap")), "geckoterminal": _pos(gt.get("fdv")), "rugcheck": _pos(rc.get("mcap")),
                                    "scanner": _pos(c.get("mcap")), "chain supply x price": chain_mcap})
    f["price"] = _cmp_numeric({"dexscreener": ds.get("price"), "geckoterminal": gt.get("price"), "rugcheck": _num(rc.get("price"))})
    f["holders"] = _cmp_numeric({"geckoterminal": gt.get("holders"), "rugcheck": _num(rc.get("holders"))})
    t10_rc = _num(rx.get("top10_raw")) if rx.get("top10_raw") is not None else None
    rc_t10 = t10_rc if t10_rc is not None else _num(rc.get("top10"))
    g10 = gt.get("top10")
    if g10 is not None and rc_t10 is not None and g10 - rc_t10 > TOP10_TOL_PP:
        # GeckoTerminal's top-10 counts liquidity-pool accounts, RugCheck's list excludes them (verified on-chain for a dumped coin
        # whose pool held 97.5% of supply). A gap that large cannot be told apart from a pool without more RPC calls -> single source.
        f["top10"] = ("single", {"rugcheck (ex-pool)": rc_t10, "geckoterminal (incl. pool)": g10})
    else:
        f["top10"] = _cmp_numeric({"geckoterminal": g10, "rugcheck": rc_t10}, pp=TOP10_TOL_PP)
    lp = rx.get("lp_locked_pct")
    f["lp_lock"] = ("single", {"rugcheck": lp}) if lp is not None else ("missing", {})
    # age: a token cannot be younger than its trading pair; compare the pair age with RugCheck's detection time
    age = ("missing", {})
    ds_age = (now - ds["created_ms"] / 1000) / 3600 if ds.get("created_ms") else _num(c.get("age_h"))
    det = rx.get("detected_at")
    rc_age = None
    if det:
        try:
            from datetime import datetime
            rc_age = (now - datetime.fromisoformat(str(det).replace("Z", "+00:00")).timestamp()) / 3600
        except Exception: pass
    if ds_age is not None and rc_age is not None:
        age = ("agree" if rc_age >= ds_age - 1.0 else "mismatch", {"dexscreener (pair)": round(ds_age, 1), "rugcheck (detected)": round(rc_age, 1)})
    elif ds_age is not None: age = ("single", {"dexscreener (pair)": round(ds_age, 1)})
    f["age"] = age
    problems = sanity_problems(c, ds, gt, rc)
    if not entry:      # nothing fetched yet: "missing everywhere" is just 'pending', not a data problem
        problems = {k: v for k, v in problems.items() if not v.endswith("missing from every source")}
    for lab, why in problems.items():
        k = "market_cap" if lab == "market cap" else "liquidity"
        f[k] = ("mismatch", f[k][1])
    # staleness: externally pulled data too old, or the scanner copy itself is old
    res, got, pts = {}, 0, 0.0
    for k, (st, vals) in f.items():
        srcs = set(vals)
        ext = srcs & {"dexscreener", "geckoterminal", "rpc"}
        stale = bool(ext) and ext <= stale_src or (scan_stale and bool(srcs & {"rugcheck", "scanner"}))
        if st in ("agree", "single") and stale: st = "stale"
        credit = {"agree": 1.0, "single": 0.5, "stale": 0.5}.get(st, 0.0)
        res[k] = {"status": st, "values": vals, "label": LABEL[k]}
        pw = problems.get(LABEL[k])
        if pw: res[k]["problem"] = pw
        pts += WEIGHTS[k] * credit
    score = int(round(pts))
    n_src = len({s for v in f.values() for s in v[1]})
    if not entry or not any(entry.get(k) for k in ("ds", "gt", "rpc")):
        level, why = "unchecked", "Independent sources not fetched yet"
        score = min(score, 40)
    else:
        level = "high" if score >= 75 else "medium" if score >= 50 else "low"
    if problems:      # impossible numbers force LOW whatever else agrees, and the fields are named
        level, score = "low", min(score, 40)
    bad = [k for k, v in res.items() if v["status"] in ("mismatch", "missing", "stale")]
    # a disagreement on supply or authorities is never "high"
    if level == "high" and any(res[k]["status"] == "mismatch" for k in ("mint_authority", "freeze_authority", "supply")):
        level = "medium"
    notes = []
    for k in bad:
        v = res[k]
        det_s = ", ".join(f"{s} {_fmt(x)}" for s, x in v["values"].items())
        if v.get("problem"):
            notes.append(f"{LABEL[k]}: {v['problem']}"); continue
        notes.append(f"{LABEL[k]} ({v['status']}" + (f": {det_s}" if det_s and v["status"] == "mismatch" else "") + ")")
    return {"score": score, "level": level, "cap": CAP[level], "uncertain": [LABEL[k] for k in bad], "notes": notes,
            "fields": res, "sources": sorted({s for v in f.values() for s in v[1]}), "n_sources": n_src,
            "checked_age_s": int(now - entry["t"]) if entry else None,
            "label": {"high": "Data confidence high", "medium": "Data confidence medium", "low": "Data uncertain",
                      "unchecked": "Data confidence pending"}[level],
            "scan_stale": scan_stale, "problems": problems}


def _fmt(x):
    if isinstance(x, bool): return "set" if x else "none"
    if isinstance(x, (int, float)):
        a = abs(x)
        return f"{x:,.0f}" if a >= 1000 else (f"{x:.4g}")
    return str(x)


def assess(c, rc_extra=None, scan_age_s=None, queue=True):
    """Non-blocking. Never raises; any problem -> 'unchecked' (which caps the score; it is not safe)."""
    mint = c.get("mint")
    try:
        _load()
        if mint and MINT_RE.match(mint):
            if queue: _want(mint)
            return evaluate(c, _cache.get(mint), scan_age_s, rc_extra)
    except Exception as e:
        stats["last_error"] = "assess " + type(e).__name__
    return evaluate(c, None, scan_age_s, rc_extra)
