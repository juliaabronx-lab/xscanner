"""xScanner trader tools: on-chain reads and small public-API aggregations behind the Pulse board and the token page.

Everything is READ-ONLY (HTTP GET, public JSON-RPC reads). No wallet keys, no signing, no transactions, no orders.
  * bonding_curve_pda / curves(): pump.fun bonding-curve state straight from the chain (real token reserves -> % complete).
  * early_scan(): who bought in the first blocks of a pump.fun token (snipers) and whether 4+ txs landed in one block (bundle).
  * trades(): GeckoTerminal last trades of a pool (the live tape) + top traders aggregated from them.
  * dev_history(): other pump.fun tokens a dev wallet launched and how they ended (RPC + DexScreener).
  * wallet_activity(): recent token buys / sells of a watched public wallet (RPC + DexScreener names).
  * quotes(): batch prices for the watchlist.
Every upstream call is cached, budgeted and degrades to an honest "unavailable" instead of blocking.
"""
import base64, hashlib, json, os, re, struct, threading, time, urllib.error, urllib.request
from collections import Counter, deque

import sol_rpc as rpc

UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
GT_HDR = {**UA, "Accept": "application/json;version=20230302"}
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WSOL = "So11111111111111111111111111111111111111112"
STABLES = {WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
CURVE_TOTAL_RAW = 793_100_000_000_000          # real token reserves of a fresh pump.fun curve (793.1M tokens, 6 decimals)
SNIPE_SLOTS = 2                                 # buys within this many slots after the create tx count as "sniped"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def num(v, d=None):
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else d
    except (TypeError, ValueError):
        return d


def b58dec(s):
    n = 0
    for ch in s: n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + raw


def b58enc(b):
    n = int.from_bytes(b, "big"); out = ""
    while n: n, r = divmod(n, 58); out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def _on_curve(b):
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P: return False
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def find_pda(seeds, program):
    pb = b58dec(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + pb + b"ProgramDerivedAddress").digest()
        if not _on_curve(h): return b58enc(h)
    return None


def bonding_curve_pda(mint):
    return find_pda([b"bonding-curve", b58dec(mint)], PUMP_PROGRAM)


# ---------------------------------------------------------------- bonding curve state (chain)
_curve_cache = {}      # account -> (t, dict)
CURVE_TTL = 6.0


def decode_curve(raw):
    if len(raw) < 49: return None
    vt, vs, rt, rs, supply = struct.unpack_from("<5Q", raw, 8)
    complete = bool(raw[48])
    prog = 100.0 if complete else max(0.0, min(100.0, (1 - rt / CURVE_TOTAL_RAW) * 100.0))
    return {"progress": round(prog, 2), "complete": complete, "real_sol": rs / 1e9, "real_tokens": rt / 1e6,
            "virtual_sol": vs / 1e9, "virtual_tokens": vt / 1e6, "supply": supply / 1e6}


def curves(accounts, ttl=CURVE_TTL):
    """{account: curve dict} for pump.fun bonding-curve accounts: one getMultipleAccounts call per 10 accounts (free RPC limit), 49-byte slices. Missing = not a curve / RPC down."""
    out, need, now = {}, [], time.time()
    for a in accounts:
        c = _curve_cache.get(a)
        if c and now - c[0] < ttl: 
            if c[1]: out[a] = c[1]
        else: need.append(a)
    for i in range(0, len(need), rpc.MAX_ACCOUNTS):        # free RPC: at most 10 accounts per call
        chunk = need[i:i + rpc.MAX_ACCOUNTS]
        try:
            res = rpc.call("getMultipleAccounts", [chunk, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 49}}], timeout=8)
        except rpc.RpcError:
            continue
        for a, v in zip(chunk, (res or {}).get("value") or []):
            d = None
            if v and v.get("owner") == PUMP_PROGRAM:
                try: d = decode_curve(base64.b64decode(v["data"][0]))
                except Exception: d = None
            _curve_cache[a] = (time.time(), d)
            if d: out[a] = d
    if len(_curve_cache) > 3000:
        for k in sorted(_curve_cache, key=lambda k: _curve_cache[k][0])[:1000]: _curve_cache.pop(k, None)
    return out


def curve_from_mcap(mcap_usd, sol_usd):
    """Estimate % complete from market cap for the classic pump.fun curve (1B supply, constant product). None if unknown."""
    if not mcap_usd or not sol_usd: return None
    p = mcap_usd / sol_usd / 1e9                     # SOL per token
    if p <= 0: return None
    vt = (30 * 1_073_000_000 / p) ** 0.5            # virtual token reserves from k = vs * vt
    sold = 1_073_000_000 - vt
    return round(max(0.0, min(100.0, sold / 793_100_000 * 100)), 1)


# ---------------------------------------------------------------- generic cached JSON fetch
_http_lock = threading.Lock()
_gt_calls = deque()
_gt_cool = [0.0, 0]
GT_BUDGET = 8          # token-page GeckoTerminal calls per minute (the Pulse worker and the chart worker have their own)


def _fetch(url, headers=None, timeout=8):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _gt_get(path, timeout=8):
    now = time.time()
    with _http_lock:
        if now < _gt_cool[0]: raise RuntimeError("rate-limited, retry in %ds" % int(_gt_cool[0] - now))
        while _gt_calls and now - _gt_calls[0] > 60: _gt_calls.popleft()
        if len(_gt_calls) >= GT_BUDGET: raise RuntimeError("busy (budget)")
        _gt_calls.append(now)
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/" + path, GT_HDR, timeout)
        _gt_cool[1] = 0
        return d
    except urllib.error.HTTPError as e:
        if e.code == 429:
            with _http_lock:
                _gt_cool[1] = min(_gt_cool[1] + 1, 5); _gt_cool[0] = time.time() + min(20 * 2 ** (_gt_cool[1] - 1), 300)
        raise RuntimeError("HTTP %s" % e.code)


# ---------------------------------------------------------------- live trades tape + top traders
_trades_cache = {}      # pool -> (t, payload)
TRADES_TTL = 8.0


def trades(pool, token=None):
    """Last trades of a pool (GeckoTerminal, up to 300 over 24 h) and top traders aggregated from them."""
    now = time.time()
    c = _trades_cache.get(pool)
    if c and now - c[0] < TRADES_TTL: return c[1]
    try:
        d = _gt_get("pools/%s/trades" % pool)
    except Exception as e:
        if c: return {**c[1], "stale": True, "stale_reason": str(e)}
        return {"available": False, "reason": "Trades tape unavailable right now (%s)." % e, "trades": [], "traders": []}
    rows = []
    for t in d.get("data") or []:
        a = t.get("attributes") or {}
        vol = num(a.get("volume_in_usd"))
        if a.get("kind") not in ("buy", "sell") or vol is None: continue
        # the traded token's own amount / price (the non-quote side)
        tok_side = a.get("to_token_address") if a["kind"] == "buy" else a.get("from_token_address")
        amt = num(a.get("to_token_amount") if a["kind"] == "buy" else a.get("from_token_amount"))
        px = num(a.get("price_to_in_usd") if a["kind"] == "buy" else a.get("price_from_in_usd"))
        try:
            import datetime as _dt
            ts = int(_dt.datetime.strptime(a["block_timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp())
        except Exception:
            continue
        rows.append({"t": ts, "k": a["kind"], "usd": round(vol, 2), "px": px, "amt": amt, "w": a.get("tx_from_address"),
                     "sig": a.get("tx_hash"), "anom": bool(a.get("is_anomalous")), "tok": tok_side})
    rows.sort(key=lambda r: -r["t"])
    # top traders over the trades we can see (this is NOT full on-chain PnL)
    agg = {}
    for r in rows:
        if not r["w"] or r["anom"]: continue
        e = agg.setdefault(r["w"], {"w": r["w"], "buys": 0, "sells": 0, "buy_usd": 0.0, "sell_usd": 0.0, "first": r["t"], "last": r["t"]})
        if r["k"] == "buy": e["buys"] += 1; e["buy_usd"] += r["usd"]
        else: e["sells"] += 1; e["sell_usd"] += r["usd"]
        e["first"] = min(e["first"], r["t"]); e["last"] = max(e["last"], r["t"])
    traders = sorted(agg.values(), key=lambda e: -(e["buy_usd"] + e["sell_usd"]))[:20]
    for e in traders:
        e["buy_usd"], e["sell_usd"] = round(e["buy_usd"], 2), round(e["sell_usd"], 2); e["net_usd"] = round(e["sell_usd"] - e["buy_usd"], 2)
    out = {"available": True, "trades": rows[:80], "n_seen": len(rows), "traders": traders,
           "span_s": (rows[0]["t"] - rows[-1]["t"]) if len(rows) > 1 else 0, "fetched_at": int(now),
           "note": "Last trades GeckoTerminal reports for this pool (up to 300). Top traders are summed from those trades only: not full on-chain PnL."}
    _trades_cache[pool] = (now, out)
    if len(_trades_cache) > 300:
        for k in sorted(_trades_cache, key=lambda k: _trades_cache[k][0])[:100]: _trades_cache.pop(k, None)
    return out


# ---------------------------------------------------------------- early buyers: snipers + same-block bundle (RPC)
_early_cache = {}     # mint -> (t, result)
_early_lock = threading.Lock()


def _owner_deltas(tx, mint):
    """{owner: ui token delta} for one parsed transaction."""
    meta = tx.get("meta") or {}
    pre, post = {}, {}
    for key, dst in (("preTokenBalances", pre), ("postTokenBalances", post)):
        for b in meta.get(key) or []:
            if b.get("mint") != mint: continue
            amt = num(((b.get("uiTokenAmount") or {}).get("uiAmount")), 0.0) or 0.0
            o = b.get("owner")
            if o: dst[o] = dst.get(o, 0.0) + amt
    return {o: (post.get(o, 0.0) - pre.get(o, 0.0), post.get(o, 0.0)) for o in set(pre) | set(post)}


def early_scan(mint, curve_acct, creator=None, supply=1_000_000_000.0, max_pages=3, max_tx=14, budget_s=12.0):
    """Snipers and bundles from the FIRST transactions touching a pump.fun bonding curve.
    Bundle = 4 or more successful txs in one block near launch (Axiom's public definition). Sniper = a wallet (not the dev) that
    bought within SNIPE_SLOTS slots after creation. Only possible while the curve has <= max_pages*100 txs (older tokens: 'too_old')."""
    now = time.time()
    c = _early_cache.get(mint)
    if c and (now - c[0] < 3600 or c[1].get("status") in ("ok", "too_old")): return c[1]
    t0 = time.time()
    sigs, before, more = [], None, False
    try:
        for _ in range(max_pages):
            p = {"limit": 100}
            if before: p["before"] = before
            r = rpc.call("getSignaturesForAddress", [curve_acct, p], timeout=8) or []
            sigs += r
            if len(r) < 100: break
            before = r[-1]["signature"]
        else:
            more = True
    except rpc.RpcError as e:
        return {"status": "unavailable", "reason": "RPC busy (%s)" % e}
    if more:
        res = {"status": "too_old", "reason": "More than %d trades so far, so the launch blocks are out of reach for the free RPC." % (max_pages * 100)}
        _early_cache[mint] = (now, res); return res
    ok = sorted([s for s in sigs if not s.get("err") and s.get("slot")], key=lambda s: (s["slot"], s.get("transactionIndex") or 0))
    if not ok: return {"status": "unavailable", "reason": "No transactions found for the curve."}
    s0 = ok[0]["slot"]
    per = Counter(s["slot"] for s in ok if s["slot"] <= s0 + 6)
    bundle_txs = max(per.values()) if per else 0
    early = [s for s in ok if s["slot"] <= s0 + SNIPE_SLOTS]
    buyers, parsed = {}, 0
    for s in early[:max_tx]:
        if time.time() - t0 > budget_s: break
        try:
            tx = rpc.call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}], timeout=8)
        except rpc.RpcError:
            continue
        if not tx: continue
        parsed += 1
        for o, (dlt, post_bal) in _owner_deltas(tx, mint).items():
            # a holder of 40%+ of supply right after the trade is the curve / pool itself, not a buyer
            if dlt > 0 and o != curve_acct and not (supply and post_bal >= 0.4 * supply):
                e = buyers.setdefault(o, {"w": o, "tokens": 0.0, "slot": s["slot"] - s0})
                e["tokens"] += dlt; e["slot"] = min(e["slot"], s["slot"] - s0)
    dev_bought = buyers.pop(creator, None) if creator else None
    same_slot = [b for b in buyers.values() if b["slot"] == 0]
    snipers = [b for b in buyers.values() if 0 < b["slot"] <= SNIPE_SLOTS]
    pct = lambda lst: round(sum(b["tokens"] for b in lst) / supply * 100, 2) if supply else None
    res = {"status": "ok", "first_slot": s0, "txs_seen": len(ok), "txs_parsed": parsed, "bundle_max_txs_in_block": bundle_txs,
           "bundle_flag": bundle_txs >= 4, "same_block_buyers": len(same_slot), "same_block_pct": pct(same_slot),
           "snipers": len(snipers), "sniper_pct": pct(snipers), "dev_first_buy_pct": pct([dev_bought]) if dev_bought else None,
           "partial": len(early) > parsed, "checked_at": int(time.time()),
           "top": sorted(({"w": b["w"], "pct": round(b["tokens"] / supply * 100, 2), "slot": b["slot"]} for b in buyers.values()), key=lambda x: -x["pct"])[:8]}
    _early_cache[mint] = (time.time(), res)
    if len(_early_cache) > 800:
        for k in sorted(_early_cache, key=lambda k: _early_cache[k][0])[:300]: _early_cache.pop(k, None)
    return res


def early_cached(mint):
    c = _early_cache.get(mint)
    return c[1] if c else None


# ---------------------------------------------------------------- dexscreener helpers
def ds_tokens(mints, timeout=8):
    """DexScreener pairs for up to 30 mints in one call."""
    mints = [m for m in mints if MINT_RE.match(m or "")][:30]
    if not mints: return []
    d = _fetch("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints), UA, timeout)
    return d if isinstance(d, list) else []


AMM_DEX = {"pumpswap", "raydium", "raydium-cpmm", "raydium-clmm", "meteora", "meteora-damm-v2", "orca", "fluxbeam"}


def best_pair(pairs, mint):
    """Most relevant pair of `mint`: a live AMM pool of a launchpad token beats its old bonding curve; else the biggest liquidity."""
    mine = [p for p in pairs if (p.get("baseToken") or {}).get("address") == mint]
    if not mine: return None
    def key(p):
        liq = num((p.get("liquidity") or {}).get("usd"), 0.0) or 0.0
        sol = (p.get("quoteToken") or {}).get("address") == WSOL
        return (1 if sol else 0, liq)
    return max(mine, key=key)


_quote_cache = {}      # mint -> (t, dict)


def quotes(mints, ttl=10.0, ready=None):
    """{mint: {px, mc, lq, pc5, pc1, pc24, sym, name, dex}} for a watchlist (<= 30 mints). `ready` = mint->row from the Pulse universe."""
    now = time.time(); out, need = {}, []
    for m in [m for m in mints if MINT_RE.match(m or "")][:30]:
        c = _quote_cache.get(m)
        if c and now - c[0] < ttl: out[m] = c[1]
        elif ready and ready.get(m) and now - (ready[m].get("_ds") or 0) < ttl:
            r = ready[m]; out[m] = {"px": r.get("px"), "mc": r.get("mc"), "lq": r.get("lq"), "pc5": r.get("pc5"), "pc1": r.get("pc1"), "pc24": r.get("pc24"),
                                    "sym": r.get("s"), "name": r.get("n"), "dex": r.get("d"), "pool": r.get("p"), "t": int(r.get("_ds") or now)}
        else: need.append(m)
    if need:
        try:
            pairs = ds_tokens(need)
        except Exception:
            pairs = []
        for m in need:
            p = best_pair(pairs, m)
            if not p: continue
            q = {"px": num(p.get("priceUsd")), "mc": num(p.get("marketCap"), num(p.get("fdv"))), "lq": num((p.get("liquidity") or {}).get("usd")),
                 "pc5": num((p.get("priceChange") or {}).get("m5")), "pc1": num((p.get("priceChange") or {}).get("h1")),
                 "pc24": num((p.get("priceChange") or {}).get("h24")), "sym": (p.get("baseToken") or {}).get("symbol"),
                 "name": (p.get("baseToken") or {}).get("name"), "dex": p.get("dexId"), "pool": p.get("pairAddress"), "t": int(now)}
            _quote_cache[m] = (now, q); out[m] = q
    return out


# ---------------------------------------------------------------- dev wallet history
_dev_jobs = {}        # wallet -> {"status", "started", "result"}
_dev_lock = threading.Lock()
DEV_TTL = 900


def _tx_new_mints(tx):
    keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
    return [k["pubkey"] for k in keys if isinstance(k, dict) and k.get("signer") and str(k.get("pubkey", "")).endswith("pump")]


def _dev_job(wallet, current_mint, max_sigs, max_tx):
    t0 = time.time(); mints, seen_tx, err = {}, 0, None
    try:
        sigs = rpc.call("getSignaturesForAddress", [wallet, {"limit": max_sigs}], timeout=8) or []
        for s in [x for x in sigs if not x.get("err")][:max_tx]:
            if time.time() - t0 > 40: break
            try:
                tx = rpc.call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}], timeout=8)
            except rpc.RpcError:
                continue
            seen_tx += 1
            for m in _tx_new_mints(tx or {}):
                mints.setdefault(m, s.get("blockTime"))
    except rpc.RpcError as e:
        err = str(e)
    toks = []
    ml = list(mints)
    for i in range(0, len(ml), 30):
        try: pairs = ds_tokens(ml[i:i + 30])
        except Exception: pairs = []
        for m in ml[i:i + 30]:
            p = best_pair(pairs, m)
            created = mints[m]
            if not p:
                toks.append({"mint": m, "created": created, "state": "no market found", "end": "dead"}); continue
            mc, liq, dex = num(p.get("marketCap"), num(p.get("fdv"))) or 0.0, num((p.get("liquidity") or {}).get("usd"), 0.0) or 0.0, p.get("dexId")
            migrated = dex in AMM_DEX
            end = "migrated" if migrated else ("live" if mc >= 5000 else "dead")
            if migrated and mc < 3000: end = "migrated, then faded"
            toks.append({"mint": m, "sym": (p.get("baseToken") or {}).get("symbol"), "name": (p.get("baseToken") or {}).get("name"), "created": created,
                         "mcap": mc, "liq": liq, "dex": dex, "state": end, "end": "migrated" if migrated else end,
                         "pc24": num((p.get("priceChange") or {}).get("h24"))})
    toks.sort(key=lambda t: -(t.get("created") or 0))
    others = [t for t in toks if t["mint"] != current_mint]
    n = len(others)
    res = {"status": "ok" if not err else "partial", "wallet": wallet, "tokens": others[:25], "launched_seen": n,
           "migrated": sum(1 for t in others if t["end"] == "migrated"), "dead": sum(1 for t in others if t["end"] == "dead"),
           "live": sum(1 for t in others if t["end"] == "live"), "txs_checked": seen_tx, "sigs_seen": max_sigs,
           "error": err, "checked_at": int(time.time()),
           "note": "Built from this wallet's latest transactions (RPC) and current DexScreener data. Older launches may be missing; 'dead' = under $5K market cap and not migrated."}
    with _dev_lock:
        _dev_jobs[wallet] = {"status": "done", "t": time.time(), "result": res}


def dev_history(wallet, current_mint=None):
    """Non-blocking: starts a background job (max one at a time) and returns {'status':'pending'} until it has a result."""
    if not MINT_RE.match(wallet or ""): return {"status": "error", "reason": "invalid wallet"}
    with _dev_lock:
        j = _dev_jobs.get(wallet)
        if j and j["status"] == "done" and time.time() - j["t"] < DEV_TTL: return j["result"]
        if j and j["status"] == "running" and time.time() - j["t"] < 90: return {"status": "pending"}
        if sum(1 for x in _dev_jobs.values() if x["status"] == "running" and time.time() - x["t"] < 90) >= 2:
            return {"status": "pending", "queued": True}
        _dev_jobs[wallet] = {"status": "running", "t": time.time()}
        if len(_dev_jobs) > 200:
            for k in sorted(_dev_jobs, key=lambda k: _dev_jobs[k]["t"])[:60]: _dev_jobs.pop(k, None)
    threading.Thread(target=_dev_job, args=(wallet, current_mint, 80, 30), daemon=True, name="dev-hist").start()
    return {"status": "pending"}


# ---------------------------------------------------------------- watch-only wallet tracker
_wallet_cache = {}
WALLET_TTL = 45
_wallet_lock = threading.Lock()
_wallet_running = {}


def _wallet_job(addr, limit):
    t0 = time.time(); events, err = [], None
    try:
        sigs = [s for s in (rpc.call("getSignaturesForAddress", [addr, {"limit": limit}], timeout=8) or []) if not s.get("err")]
    except rpc.RpcError as e:
        sigs, err = [], str(e)
    for s in sigs[:limit]:
        if time.time() - t0 > 22: break
        try:
            tx = rpc.call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}], timeout=8)
        except rpc.RpcError:
            continue
        if not tx: continue
        meta = tx.get("meta") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k for k in (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])]
        sol = 0.0
        if addr in keys:
            i = keys.index(addr)
            try: sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9 + (meta.get("fee") or 0) / 1e9 * (1 if i == 0 else 0)
            except Exception: sol = 0.0
        d = {}
        for key, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
            for b in meta.get(key) or []:
                if b.get("owner") != addr: continue
                d[b["mint"]] = d.get(b["mint"], 0.0) + sign * (num((b.get("uiTokenAmount") or {}).get("uiAmount"), 0.0) or 0.0)
        wsol = d.pop(WSOL, 0.0)
        for st in list(STABLES - {WSOL}): d.pop(st, None)
        quote_sol = sol + wsol
        for m, dl in d.items():
            if abs(dl) < 1e-9: continue
            if (dl > 0) == (quote_sol < 0) or abs(quote_sol) < 1e-6:          # tokens in + SOL out (buy) / tokens out + SOL in (sell)
                events.append({"sig": s["signature"], "t": s.get("blockTime"), "mint": m, "side": "buy" if dl > 0 else "sell", "tokens": abs(dl),
                               "sol": round(abs(quote_sol), 4) if abs(quote_sol) >= 1e-6 else None})
            else:
                events.append({"sig": s["signature"], "t": s.get("blockTime"), "mint": m, "side": "in" if dl > 0 else "out", "tokens": abs(dl), "sol": None})
    names = {}
    ms = list({e["mint"] for e in events})
    for i in range(0, len(ms), 30):
        try: pairs = ds_tokens(ms[i:i + 30])
        except Exception: pairs = []
        for m in ms[i:i + 30]:
            p = best_pair(pairs, m)
            if p: names[m] = {"sym": (p.get("baseToken") or {}).get("symbol"), "name": (p.get("baseToken") or {}).get("name"), "px": num(p.get("priceUsd")),
                              "mc": num(p.get("marketCap"), num(p.get("fdv"))), "pool": p.get("pairAddress")}
    for e in events:
        e.update(names.get(e["mint"], {}))
        if e.get("px") and e["side"] in ("buy", "sell"): e["now_usd"] = round(e["tokens"] * e["px"], 2)
    res = {"status": "ok" if not err else "partial", "wallet": addr, "events": events[:60], "txs_checked": len(sigs), "error": err, "checked_at": int(time.time()),
           "note": "Swaps are inferred from this wallet's token balance changes (RPC). Moves without a SOL leg show as in/out. Read-only: no keys, no trading."}
    with _wallet_lock:
        _wallet_cache[addr] = (time.time(), res); _wallet_running.pop(addr, None)


def wallet_activity(addr, limit=15):
    if not MINT_RE.match(addr or ""): return {"status": "error", "reason": "That is not a valid Solana address."}
    now = time.time()
    with _wallet_lock:
        c = _wallet_cache.get(addr)
        if c and now - c[0] < WALLET_TTL: return c[1]
        if _wallet_running.get(addr) and now - _wallet_running[addr] < 60:
            return {**(c[1] if c else {}), "status": "pending", "wallet": addr} if c else {"status": "pending", "wallet": addr}
        if len([1 for t in _wallet_running.values() if now - t < 60]) >= 3:
            return {**(c[1] if c else {}), "status": "pending", "queued": True, "wallet": addr} if c else {"status": "pending", "queued": True, "wallet": addr}
        _wallet_running[addr] = now
    threading.Thread(target=_wallet_job, args=(addr, limit), daemon=True, name="wallet-job").start()
    return {**(c[1] if c else {}), "status": "pending", "wallet": addr} if c else {"status": "pending", "wallet": addr}


# ---------------------------------------------------------------- mint authorities (chain) with cache
_mint_cache = {}


def mint_state(mint):
    c = _mint_cache.get(mint)
    if c and time.time() - c[0] < 600: return c[1]
    try:
        i = rpc.mint_info(mint)
        r = {"mint_auth": i["mint_auth"], "freeze_auth": i["freeze_auth"], "decimals": i["decimals"], "supply": i["supply_raw"] / (10 ** i["decimals"]),
             "token2022": i.get("program", "").startswith("TokenzQd"), "source": "chain"}
    except Exception as e:
        r = {"error": str(e)}
    _mint_cache[mint] = (time.time(), r)
    return r
