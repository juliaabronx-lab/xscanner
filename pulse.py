"""xScanner Pulse: a live three-column board of Solana launchpad tokens (New pairs / Final stretch / Migrated).

One background thread keeps a small "universe" of tokens fresh from free public APIs and the Solana RPC:
  discovery   GeckoTerminal new pools + pump.fun / pumpswap pool lists, Jupiter lite "recent" + trending (all keyless, all GET)
  enrichment  DexScreener batch pairs (price, market cap, liquidity, volume, buy/sell counts, socials),
              Jupiter token stats (holders, top-10 %, dev %, mint / freeze authority, dev mint count),
              the pump.fun bonding curve read straight from the chain (exact % complete),
              RugCheck bundle check (via bundle.py, queued sparingly), early-block sniper / same-block bundle scan (RPC).
Every upstream has its own gap, per-minute budget and 429 cool-down; when one is down the field stays empty (shown as "—"),
it is never invented. pump.fun's own site API is behind Cloudflare, so it is NOT used.
Viewer driven: full speed only while someone has polled /api/pulse in the last few minutes, a slow trickle otherwise.
Read-only toward the outside world. No wallets, keys, signing or trading.
"""
import json, os, threading, time, urllib.error, urllib.request
from collections import deque

import bundle as bd
import trader as tr

LIVE_DIR = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
STORE = os.path.join(LIVE_DIR, "pulse_universe.json")
UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
GT_HDR = {**UA, "Accept": "application/json;version=20230302"}
ENABLED = os.environ.get("PULSE", "1") != "0"
FINAL_AT = 70.0                 # % of the bonding curve from which a token is in the Final stretch column
NEW_MAX_AGE_S = 6 * 3600        # a token still on its curve below FINAL_AT stays in New pairs this long
MIG_MAX_AGE_S = 24 * 3600       # a migrated token stays in the Migrated column this long
KEEP_S = 8 * 3600               # rows not displayed are forgotten after this long
MAX_ROWS = 600
COLS_N = {"new": 150, "final": 80, "migrated": 100}
ACTIVE_S = 240                  # full speed while a viewer polled within this many seconds
CURVE_DEX = {"pumpfun", "pump-fun"}
OTHER_CURVE_DEX = {"letsbonk-fun", "raydium-launchlab", "moonshot", "boop-fun", "meteora-dbc", "heaven", "bags-fm", "moonit", "daos-fun", "zora", "wavebreak", "printr-v2"}
GT_DEX = {"pump-fun": "pumpfun"}

_lock = threading.RLock()
U = {}                           # mint -> row
VER = 0
_cols_hash = ""
_cols = {"new": [], "final": [], "migrated": []}
_sol = {"usd": None, "t": 0}
_viewer = {"t": 0.0, "n": 0}
_wake = threading.Event()
_started = False
_loaded = False
SRC = {k: {"ok": 0, "err": 0, "last_ok": None, "last_err": None, "cool": 0.0, "cool_n": 0, "calls": deque()} for k in ("gecko", "dexscreener", "jupiter", "rpc", "rugcheck")}
stats = {"ticks": 0, "last_tick": None, "last_tick_s": None, "started": None}


def _num(v, d=None):
    return tr.num(v, d)


# ---------------------------------------------------------------- upstream plumbing
def _budget(src, n_per_min):
    s = SRC[src]; now = time.time()
    if now < s["cool"]: return False
    while s["calls"] and now - s["calls"][0] > 60: s["calls"].popleft()
    return len(s["calls"]) < n_per_min


def _get(src, url, headers, timeout=10, n_per_min=20):
    s = SRC[src]
    if not _budget(src, n_per_min): raise RuntimeError("budget/cool-down")
    s["calls"].append(time.time())
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        s["ok"] += 1; s["last_ok"] = int(time.time()); s["cool_n"] = 0
        return d
    except urllib.error.HTTPError as e:
        s["err"] += 1; s["last_err"] = "HTTP %s" % e.code
        if e.code in (429, 403, 503):
            s["cool_n"] = min(s["cool_n"] + 1, 5); s["cool"] = time.time() + min(25 * 2 ** (s["cool_n"] - 1), 400)
        raise
    except Exception as e:
        s["err"] += 1; s["last_err"] = type(e).__name__
        raise


def _gt(path, n=14):
    return _get("gecko", "https://api.geckoterminal.com/api/v2/networks/solana/" + path, GT_HDR, 10, n)


def _jup(path):
    return _get("jupiter", "https://lite-api.jup.ag/tokens/v2/" + path, UA, 10, 10)


# ---------------------------------------------------------------- universe
def _iso_epoch(s):
    if not s: return None
    try:
        from datetime import datetime, timezone
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def _row(m):
    r = U.get(m)
    if r is None:
        r = {"m": m, "_seen": time.time(), "_v": 0}
        U[m] = r
    return r


_touched = set()


def _put(r, **kw):
    ch = False
    for k, v in kw.items():
        if v is None and k not in ("curve_reset",): continue
        if r.get(k) != v:
            r[k] = v; ch = True
    if ch: _touched.add(r["m"])
    return ch


def _merge_gecko_pool(p, tokens):
    a = p.get("attributes") or {}
    rel = p.get("relationships") or {}
    bt = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
    mint = bt.split("_", 1)[-1] if bt else None
    if not mint or not tr.MINT_RE.match(mint): return
    dex = GT_DEX.get(((rel.get("dex") or {}).get("data") or {}).get("id"), ((rel.get("dex") or {}).get("data") or {}).get("id"))
    name = a.get("name") or ""
    quote = name.split(" / ")[-1].strip() if " / " in name else None
    tok = tokens.get(mint) or {}
    created = _iso_epoch(a.get("pool_created_at"))
    tx = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    pc = a.get("price_change_percentage") or {}
    mc = _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd"))
    with _lock:
        is_curve = dex in CURVE_DEX or dex in OTHER_CURVE_DEX
        is_amm = dex in tr.AMM_DEX
        launch = bool(is_curve or mint.endswith("pump") or (U.get(mint) or {}).get("lp"))
        if not (is_curve or (is_amm and launch)): return
        r = _row(mint); r["_seen"] = time.time()
        kw = dict(n=tok.get("name"), s=tok.get("symbol") or (name.split(" / ")[0] if name else None), img=tok.get("image_url"),
                  p=p.get("id", "").split("_", 1)[-1] or a.get("address"), d=dex, q=quote,
                  mc=mc, lq=_num(a.get("reserve_in_usd")), px=_num(a.get("base_token_price_usd")),
                  v5=_num(vol.get("m5")), v1=_num(vol.get("h1")), v24=_num(vol.get("h24")),
                  b5=(tx.get("m5") or {}).get("buys"), s5=(tx.get("m5") or {}).get("sells"),
                  b1=(tx.get("h1") or {}).get("buys"), s1=(tx.get("h1") or {}).get("sells"),
                  pc5=_num(pc.get("m5")), pc1=_num(pc.get("h1")), pc24=_num(pc.get("h24")))
        if is_curve:
            kw["cr"] = created if not r.get("cr") or (created and created < r["cr"]) else r["cr"]
            kw["cp"] = kw["p"]
        elif is_amm:
            kw["mg"] = created
            if not r.get("cr"): kw["cr"] = None
        _put(r, **{k: v for k, v in kw.items() if v is not None})
        if is_curve and (quote or "").upper() == "SOL":
            qp = _num(a.get("quote_token_price_usd"))
            if qp and 5 < qp < 5000: _sol.update(usd=qp, t=time.time())


def _poll_gecko_new(pages):
    for pg in pages:
        try:
            d = _gt("new_pools?include=base_token&page=%d" % pg)
        except Exception:
            return False
        toks = {(i.get("attributes") or {}).get("address"): i.get("attributes") or {} for i in d.get("included") or [] if i.get("type") == "token"}
        for p in d.get("data") or []: _merge_gecko_pool(p, toks)
        time.sleep(0.4)
    return True


def _poll_gecko_dex(dex, sort, page):
    try:
        d = _gt("dexes/%s/pools?include=base_token&sort=%s&page=%d" % (dex, sort, page))
    except Exception:
        return False
    toks = {(i.get("attributes") or {}).get("address"): i.get("attributes") or {} for i in d.get("included") or [] if i.get("type") == "token"}
    for p in d.get("data") or []: _merge_gecko_pool(p, toks)
    return True


def _merge_jup(t):
    m = t.get("id")
    if not m or not tr.MINT_RE.match(m): return
    lp = t.get("launchpad")
    with _lock:
        existing = U.get(m)
        if not existing and not (lp or m.endswith("pump")): return
        r = existing or _row(m)
        r["_seen"] = time.time(); r["_jup"] = time.time()
        au = t.get("audit") or {}
        s5, s1 = t.get("stats5m") or {}, t.get("stats1h") or {}
        fp = t.get("firstPool") or {}
        kw = dict(n=t.get("name"), s=t.get("symbol"), lp=lp, h=t.get("holderCount"), t10=_num(au.get("topHoldersPercentage")),
                  dv=_num(au.get("devBalancePercentage")), dm=au.get("devMints"), dev=t.get("dev"),
                  ma=au.get("mintAuthorityDisabled"), fa=au.get("freezeAuthorityDisabled"), tw=t.get("twitter") or None, tg=t.get("telegram") or None,
                  web=t.get("website") or None, org=t.get("organicScoreLabel"), t22=("token-2022" in (t.get("tags") or [])) or None)
        icon = t.get("icon")
        if icon and not r.get("img"): kw["img"] = icon
        cr = _iso_epoch(t.get("createdAt") or fp.get("createdAt"))
        if cr and (not r.get("cr") or cr < r["cr"]) and not r.get("mg") or (cr and not r.get("cr")): kw["cr"] = cr
        if not existing or r.get("mc") is None:
            kw.update(mc=_num(t.get("mcap")), lq=_num(t.get("liquidity")), px=_num(t.get("usdPrice")),
                      b5=s5.get("numBuys"), s5=s5.get("numSells"), b1=s1.get("numBuys"), s1=s1.get("numSells"),
                      v5=(_num(s5.get("buyVolume"), 0) or 0) + (_num(s5.get("sellVolume"), 0) or 0) if s5 else None,
                      v1=(_num(s1.get("buyVolume"), 0) or 0) + (_num(s1.get("sellVolume"), 0) or 0) if s1 else None)
        if not r.get("d") and lp: kw["d"] = "pumpfun" if lp == "pump.fun" else lp
        _put(r, **{k: v for k, v in kw.items() if v is not None})


def _poll_jup_recent():
    try:
        d = _jup("recent")
    except Exception:
        return False
    for t in d if isinstance(d, list) else []: _merge_jup(t)
    return True


def _poll_jup_trending(win="5m"):
    try:
        d = _jup("toptrending/%s?limit=50" % win)
    except Exception:
        return False
    for t in d if isinstance(d, list) else []: _merge_jup(t)
    return True


def _enrich_jup(mints):
    try:
        d = _jup("search?query=" + ",".join(mints[:100]))
    except Exception:
        return False
    got = set()
    for t in d if isinstance(d, list) else []:
        _merge_jup(t); got.add(t.get("id"))
    with _lock:
        for m in mints:
            r = U.get(m)
            if r is not None: r["_jup"] = time.time()
    return True


def _merge_ds(mints, pairs):
    now = time.time()
    with _lock:
        for m in mints:
            r = U.get(m)
            if r is None: continue
            r["_ds"] = now
            p = tr.best_pair(pairs, m)
            if not p: continue
            dex = p.get("dexId")
            tx = p.get("txns") or {}; vol = p.get("volume") or {}; pc = p.get("priceChange") or {}
            info = p.get("info") or {}
            socials = {s.get("type"): s.get("url") for s in info.get("socials") or [] if isinstance(s, dict)}
            webs = [w.get("url") for w in info.get("websites") or [] if isinstance(w, dict)]
            q = (p.get("quoteToken") or {}).get("symbol")
            created = int(p["pairCreatedAt"] / 1000) if p.get("pairCreatedAt") else None
            kw = dict(n=(p.get("baseToken") or {}).get("name"), s=(p.get("baseToken") or {}).get("symbol"), d=dex, p=p.get("pairAddress"), q=q,
                      mc=_num(p.get("marketCap"), _num(p.get("fdv"))), lq=_num((p.get("liquidity") or {}).get("usd")), px=_num(p.get("priceUsd")),
                      pn=_num(p.get("priceNative")), v5=_num(vol.get("m5")), v1=_num(vol.get("h1")), v24=_num(vol.get("h24")),
                      b5=(tx.get("m5") or {}).get("buys"), s5=(tx.get("m5") or {}).get("sells"),
                      b1=(tx.get("h1") or {}).get("buys"), s1=(tx.get("h1") or {}).get("sells"),
                      b24=(tx.get("h24") or {}).get("buys"), s24=(tx.get("h24") or {}).get("sells"),
                      pc5=_num(pc.get("m5")), pc1=_num(pc.get("h1")), pc24=_num(pc.get("h24")),
                      tw=socials.get("twitter"), tg=socials.get("telegram"), web=(webs[0] if webs else None), img=info.get("imageUrl"))
            if dex in CURVE_DEX:
                kw["cp"] = p.get("pairAddress")
                if created and (not r.get("cr") or created < r["cr"]): kw["cr"] = created
            elif dex in tr.AMM_DEX:
                kw["mg"] = created
                if not r.get("cr"):
                    cr = created
                    kw["cr"] = None
            if q and q.upper() == "SOL" and kw["px"] and kw["pn"] and kw["pn"] > 0:
                su = kw["px"] / kw["pn"]
                if 5 < su < 5000: _sol.update(usd=su, t=now)
            old_img = r.get("img")
            _put(r, **{k: v for k, v in kw.items() if v is not None and not (k == "img" and old_img)})


# ---------------------------------------------------------------- stage / curve / flags
def _curve_pct(r):
    if r.get("d") in CURVE_DEX and r.get("cv_chain") is not None: return r["cv_chain"], "chain"
    if r.get("d") in CURVE_DEX and (r.get("q") or "").upper() == "SOL":
        e = tr.curve_from_mcap(r.get("mc"), _sol["usd"] if time.time() - _sol["t"] < 900 else None)
        if e is not None: return e, "est"
    return None, None


def _stage(r, now):
    d = r.get("d")
    if d in CURVE_DEX:
        cv, _ = _curve_pct(r)
        if r.get("cv_done"): return "final"
        if cv is not None and cv >= FINAL_AT: return "final"
        if r.get("cr") and now - r["cr"] > NEW_MAX_AGE_S: return None
        return "new"
    if d in OTHER_CURVE_DEX:
        return "new" if r.get("cr") and now - r["cr"] <= NEW_MAX_AGE_S else None
    if d in tr.AMM_DEX and (r.get("lp") or str(r.get("m", "")).endswith("pump") or r.get("cp")):
        mg = r.get("mg") or r.get("cr")
        if mg and now - mg <= MIG_MAX_AGE_S and (r.get("lq") or 0) >= 200: return "migrated"
    return None


def _flags(r):
    """Bundle flag (RugCheck + our clustering, only for coins already checked) and sniper numbers (RPC early-block scan)."""
    m = r["m"]
    try:
        a, age = bd.get_cached(m)
    except Exception:
        a = None
    if a:
        b = bd.assess_numbers(a)
        _put(r, bd=b.get("status"), bdp=b.get("cluster_share", b.get("net_share")), bdw=b.get("cluster_wallets", b.get("wallets")),
             hl=(a.get("rc") or {}).get("holders"), t10x=a.get("top10_ex_lp"), lpl=(a.get("rc") or {}).get("lp_locked_pct"))
        if r.get("h") is None and (a.get("rc") or {}).get("holders") is not None: _put(r, h=(a.get("rc") or {}).get("holders"))
    e = tr.early_cached(m)
    if e and e.get("status") == "ok":
        _put(r, sn=e.get("sniper_pct"), snn=e.get("snipers"), ebl=e.get("bundle_flag"), ebm=e.get("bundle_max_txs_in_block"), sbp=e.get("same_block_pct"))
    elif e:
        _put(r, es=e.get("status"))


def _recompute():
    """Re-evaluate stage + curve for every row, rebuild the column lists, bump the version if anything changed."""
    global VER, _cols_hash, _cols
    now = time.time()
    with _lock:
        for r in U.values():
            if r.get("d") in CURVE_DEX and not r.get("cp") and str(r["m"]).endswith("pump"):
                pda = tr.bonding_curve_pda(r["m"])          # the curve account of any pump.fun mint is derivable: no lookup needed
                if pda: r["cp"] = pda; r.setdefault("p", pda)
            cv, src = _curve_pct(r)
            _put(r, cv=cv, cvs=src)
            _flags(r)
            st = _stage(r, now)
            if r.get("st") != st:                      # _put ignores None, but "no longer on the board" must stick
                r["st"] = st; _touched.add(r["m"])
            if st == "migrated" and r.get("cv") != 100.0: _put(r, cv=100.0, cvs="done")       # graduated = the curve is full
        # evict
        if len(U) > MAX_ROWS or True:
            for m in [m for m, r in U.items() if not r.get("st") and now - r.get("_seen", 0) > KEEP_S]: U.pop(m, None)
            if len(U) > MAX_ROWS:
                for m in sorted(U, key=lambda m: (1 if U[m].get("st") else 0, U[m].get("_seen", 0)))[:len(U) - MAX_ROWS]: U.pop(m, None)
        cols = {"new": [], "final": [], "migrated": []}
        for m, r in U.items():
            if r.get("st") in cols: cols[r["st"]].append(r)
        cols["new"].sort(key=lambda r: -(r.get("cr") or 0))
        cols["final"].sort(key=lambda r: -(r.get("cv") or 0))
        cols["migrated"].sort(key=lambda r: -(r.get("mg") or r.get("cr") or 0))
        ids = {k: [r["m"] for r in v[:COLS_N[k]]] for k, v in cols.items()}
        h = str(hash(json.dumps(ids, separators=(",", ":"))))
        if _touched or h != _cols_hash:
            VER += 1
            for m in _touched:
                if m in U: U[m]["_v"] = VER
            _touched.clear()
        _cols_hash = h; _cols = ids


# ---------------------------------------------------------------- worker
_last = {k: 0.0 for k in ("gnew", "gnew2", "gdex", "jrec", "jtrend", "jenr", "ds", "curve", "bdq", "early", "save", "gpump", "gswap")}
_gdex_i = [0]


def _due(k, every):
    now = time.time()
    if now - _last[k] >= every:
        _last[k] = now; return True
    return False


def _active():
    return time.time() - _viewer["t"] < ACTIVE_S


def _displayed(stages=("final", "migrated", "new")):
    with _lock:
        out = []
        for r in U.values():
            if r.get("st") in stages: out.append(r)
        return out


def _tick():
    t0 = time.time()
    act = _active()
    # ---- discovery
    if _due("gnew", 14 if act else 90):
        _poll_gecko_new([1, 2, 3] if act else [1])
    if _due("jrec", 22 if act else 150): _poll_jup_recent()
    if act and _due("jtrend", 75): _poll_jup_trending("5m" if int(t0 / 75) % 2 else "1h")
    if act and _due("gdex", 45):
        # alternate: busiest pump.fun curves (final stretch candidates) and busiest pumpswap pools (migrated)
        i = _gdex_i[0]; _gdex_i[0] += 1
        dex, sort = [("pump-fun", "h24_volume_usd_desc"), ("pumpswap", "h24_volume_usd_desc"), ("pump-fun", "h24_tx_count_desc"), ("pumpswap", "h24_tx_count_desc")][i % 4]
        _poll_gecko_dex(dex, sort, 1 + (i // 4) % 2)
    # ---- chain: bonding curves of every curve row (one RPC call per 100)
    if _due("curve", 6 if act else 40):
        with _lock:
            rows = [r for r in U.values() if r.get("d") in CURVE_DEX and (r.get("st") in ("new", "final") or r.get("st") is None) and r.get("cp") and not r.get("cv_done")]
        # free RPC allows 10 accounts per call: refresh the hot rows (final stretch / est >= 50%) every cycle, the rest round-robin
        now_c = time.time()
        hot = [r for r in rows if r.get("st") == "final" or (r.get("cv") or 0) >= 50]
        cold = sorted([r for r in rows if r not in hot], key=lambda r: r.get("_cvt", 0))
        pick = hot[:50] + cold[:30]
        for r in pick: r["_cvt"] = now_c
        accts = [r["cp"] for r in pick]
        if accts:
            e0 = tr.rpc.stats["errors"]
            res = tr.curves(accts, ttl=4.0)
            if tr.rpc.stats["errors"] > e0: SRC["rpc"]["err"] += 1; SRC["rpc"]["last_err"] = tr.rpc.stats.get("last_error")
            else: SRC["rpc"]["ok"] += 1; SRC["rpc"]["last_ok"] = int(time.time())
            with _lock:
                for r in rows:
                    c = res.get(r["cp"])
                    if c:
                        _put(r, cv_chain=c["progress"], cv_done=c["complete"] or None)
    # ---- market data: DexScreener batches, stalest displayed rows first (final stretch rows every cycle)
    if act and _due("ds", 3.5):
        now = time.time()
        with _lock:
            cand = [r for r in U.values() if r.get("st") or (now - r.get("_seen", 0) < 600)]
            cand.sort(key=lambda r: ((0 if r.get("st") == "final" else 1 if r.get("st") == "migrated" else 2) * 6 + min(5, (now - r.get("_ds", 0)) / 6) * -1,))
            cand = [r for r in cand if now - r.get("_ds", 0) > (8 if r.get("st") == "final" else 14)]
            batch = [r["m"] for r in cand[:30]]
        if batch:
            try:
                pairs = tr.ds_tokens(batch)
                SRC["dexscreener"]["ok"] += 1; SRC["dexscreener"]["last_ok"] = int(time.time())
                _merge_ds(batch, pairs)
            except Exception as e:
                SRC["dexscreener"]["err"] += 1; SRC["dexscreener"]["last_err"] = type(e).__name__
                with _lock:
                    for m in batch:
                        if m in U: U[m]["_ds"] = time.time() - 6
    # ---- holders / top-10 / dev % (Jupiter): rows without it, or stale
    if act and _due("jenr", 9):
        now = time.time()
        with _lock:
            cand = [r for r in U.values() if r.get("st") and now - r.get("_jup", 0) > (45 if r.get("h") is None else 120)]
            cand.sort(key=lambda r: ({"final": 0, "migrated": 1, "new": 2}.get(r.get("st"), 3), -(r.get("cr") or 0)))
            batch = [r["m"] for r in cand[:60]]
        if batch: _enrich_jup(batch)
    # ---- RugCheck bundle check: queue the most interesting unchecked rows, a few at a time
    if act and _due("bdq", 5):
        try:
            qn = len(bd._queue)
        except Exception:
            qn = 0
        if qn < 5:
            now = time.time()
            with _lock:
                cand = [r for r in U.values() if r.get("st") and r.get("bd") is None and (r.get("lq") or 0) >= 1500 and now - r.get("cr", now) >= 45]
                cand.sort(key=lambda r: ({"final": 0, "migrated": 1, "new": 2}.get(r.get("st"), 3), -(r.get("lq") or 0)))
            for r in cand[:2]:
                try: bd._want(r["m"])
                except Exception: pass
    # ---- early-block scan (snipers / same-block bundle) for young curve tokens, one at a time
    if act and _due("early", 4):
        now = time.time()
        with _lock:
            cand = [r for r in U.values() if r.get("st") in ("new", "final") and r.get("d") in CURVE_DEX and r.get("cp") and r.get("es") is None and r.get("sn") is None
                    and tr.early_cached(r["m"]) is None and now - (r.get("cr") or now) >= 20 and ((r.get("b5") or 0) + (r.get("s5") or 0)) >= 3]
            cand.sort(key=lambda r: -(r.get("cr") or 0))
            r0 = cand[0] if cand else None
        if r0 is not None:
            res = tr.early_scan(r0["m"], r0["cp"], r0.get("dev"), max_pages=3, max_tx=10, budget_s=6.0)
            if res.get("status") == "unavailable":
                with _lock: r0["_ds"] = r0.get("_ds", 0)
                tr._early_cache[r0["m"]] = (time.time() - 3500, res)          # retry in ~100 s
    _recompute()
    if _due("save", 90): save()
    stats["ticks"] += 1; stats["last_tick"] = int(time.time()); stats["last_tick_s"] = round(time.time() - t0, 2)


def _loop():
    # first fill: pages of new pools + busiest pump.fun / pumpswap pools + Jupiter, so a cold start has all three columns quickly
    time.sleep(2)
    try:
        _poll_gecko_new([1, 2, 3, 4]); _poll_jup_recent(); _poll_gecko_dex("pump-fun", "h24_volume_usd_desc", 1); _poll_gecko_dex("pumpswap", "h24_volume_usd_desc", 1)
        _recompute()
    except Exception:
        pass
    while True:
        try:
            _tick()
        except Exception as e:
            stats["last_error"] = "%s: %s" % (type(e).__name__, e)
        _wake.wait(2.0 if _active() else 8.0); _wake.clear()


def start():
    global _started
    if not ENABLED or _started: return
    _started = True
    load()
    stats["started"] = int(time.time())
    threading.Thread(target=_loop, name="pulse", daemon=True).start()


# ---------------------------------------------------------------- persistence
def save():
    try:
        os.makedirs(LIVE_DIR, exist_ok=True)
        with _lock:
            keep = {m: {k: v for k, v in r.items() if not k.startswith("_") or k in ("_seen", "_jup", "_ds")} for m, r in U.items() if r.get("st")}
        tmp = STORE + ".tmp"
        with open(tmp, "w") as f: json.dump({"t": time.time(), "rows": keep, "sol": _sol}, f)
        os.replace(tmp, STORE)
    except Exception:
        pass


def load():
    global _loaded
    if _loaded: return
    _loaded = True
    try:
        d = json.load(open(STORE))
        if time.time() - d.get("t", 0) < 2 * 3600:
            with _lock:
                for m, r in (d.get("rows") or {}).items():
                    r["_v"] = 0; U[m] = r
            if d.get("sol"): _sol.update(d["sol"])
    except Exception:
        pass


# ---------------------------------------------------------------- API payloads
PUB = ("m", "n", "s", "p", "d", "q", "img", "cr", "mg", "mc", "lq", "px", "v5", "v1", "v24", "b5", "s5", "b1", "s1", "b24", "s24", "pc5", "pc1", "pc24",
       "h", "t10", "dv", "dm", "ma", "fa", "tw", "tg", "web", "lp", "cv", "cvs", "cv_done", "st",
       "bd", "bdp", "bdw", "t10x", "lpl", "sn", "snn", "ebl", "ebm", "sbp", "es")


def _pub(r):
    o = {k: r[k] for k in PUB if r.get(k) is not None}
    o["v"] = r.get("_v", 0)
    return o


def source_status():
    now = time.time(); out = {}
    for k, s in SRC.items():
        out[k] = {"ok": s["ok"], "errors": s["err"], "last_ok_s": int(now - s["last_ok"]) if s["last_ok"] else None, "last_error": s["last_err"],
                  "cooling_s": max(0, int(s["cool"] - now))}
    return out


def payload(since=0, h=""):
    """Incremental board payload: rows changed since `since`, plus the column order unless the client already has it (h)."""
    _viewer["t"] = time.time(); _viewer["n"] += 1
    _wake.set()
    with _lock:
        ids_changed = h != _cols_hash
        if since > VER: since = 0                    # server restarted: client re-syncs fully
        rows = []
        if ids_changed or since < VER:
            wanted = set(m for v in _cols.values() for m in v)
            rows = [_pub(U[m]) for m in wanted if m in U and U[m].get("_v", 0) > since]
        out = {"ver": VER, "h": _cols_hash, "t": int(time.time()), "rows": rows, "sol_usd": _sol["usd"],
               "counts": {k: len(v) for k, v in _cols.items()}, "universe": len(U), "warming": not any(_cols.values()),
               "active": True}
        if ids_changed: out["cols"] = _cols
        out["sources"] = source_status()
        return out


def get_row(mint):
    with _lock:
        r = U.get(mint)
        return dict(r) if r else None


def ready_rows():
    with _lock:
        return {m: dict(r) for m, r in U.items()}


def status():
    return {"enabled": ENABLED, "rows": len(U), "ver": VER, "counts": {k: len(v) for k, v in _cols.items()}, **stats, "sol_usd": _sol["usd"],
            "viewer_active": _active(), "sources": source_status()}
