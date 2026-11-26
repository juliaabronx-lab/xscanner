"""Live trades tape for GET /api/trades/{pool}.

GeckoTerminal is the usual source (trader.trades). When it is rate-limited,
cooling down, or otherwise unavailable, this falls back to a read-only Solana
RPC read: getSignaturesForAddress on the pool, then getTransaction, and a
small swap parse (token-balance deltas). No wallet, no signature, no send.

Concurrent viewers of one pool share a single upstream fetch. A recent answer
is served as-is for a few seconds, then stale-while-revalidate until it is
too old to be honest. The JSON keys match the token page: available, reason,
trades (t, k, usd, px, amt, w, sig, anom, tok), traders, n_seen, span_s,
fetched_at, note, stale, stale_reason. `source` is extra and safe to ignore.
"""
import threading, time, urllib.request

FRESH_S = 8.0
SWR_S = 90.0
NEGATIVE_S = 8.0
SIG_LIMIT = 15
TX_LIMIT = 8
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTES = {WSOL, USDC, USDT}
STABLES = {USDC, USDT}
NOTE_RPC = ("Recent swaps read from the Solana RPC (getSignaturesForAddress + getTransaction) "
            "because GeckoTerminal was unavailable. Top traders are summed from those swaps only: not full on-chain PnL.")

_lock = threading.Lock()
_cache = {}          # pool -> {ts, payload, retry_at}
_flights = {}        # pool -> (Event, box)
_kicked = set()      # pools with a stale-while-revalidate refresh already started
_sol = {"t": 0.0, "usd": None}


def reset():
    with _lock:
        _cache.clear()
        _flights.clear()
        _kicked.clear()
    _sol["t"] = 0.0
    _sol["usd"] = None


def _unavailable(reason):
    return {"available": False, "reason": reason, "trades": [], "traders": [], "source": None,
            "n_seen": 0, "span_s": 0, "fetched_at": int(time.time())}


def _rate_limited(*parts):
    text = " ".join(str(p) for p in parts if p).lower()
    return "429" in text or "rate-limited" in text or "rate limited" in text or "busy (budget)" in text


def _reason(gt, gt_err, rpc_err):
    bits = []
    if isinstance(gt, dict):
        bits.append(gt.get("reason") or gt.get("stale_reason") or "")
    if gt_err:
        bits.append(str(gt_err))
    if rpc_err:
        bits.append(str(rpc_err))
    if _rate_limited(*bits):
        return "Trades tape unavailable (rate-limited)."
    for b in bits:
        if b:
            return str(b)[:300]
    return "Trades tape unavailable right now."


def _assemble(rows, note, source, stale=False, stale_reason=None):
    rows = sorted(rows, key=lambda r: -int(r["t"]))
    agg = {}
    for r in rows:
        if not r.get("w") or r.get("anom"):
            continue
        e = agg.setdefault(r["w"], {"w": r["w"], "buys": 0, "sells": 0, "buy_usd": 0.0, "sell_usd": 0.0,
                                    "first": r["t"], "last": r["t"]})
        if r["k"] == "buy":
            e["buys"] += 1
            e["buy_usd"] += float(r["usd"] or 0)
        else:
            e["sells"] += 1
            e["sell_usd"] += float(r["usd"] or 0)
        e["first"] = min(e["first"], r["t"])
        e["last"] = max(e["last"], r["t"])
    traders = sorted(agg.values(), key=lambda e: -(e["buy_usd"] + e["sell_usd"]))[:20]
    for e in traders:
        e["buy_usd"] = round(e["buy_usd"], 2)
        e["sell_usd"] = round(e["sell_usd"], 2)
        e["net_usd"] = round(e["sell_usd"] - e["buy_usd"], 2)
    out = {"available": True, "trades": rows[:80], "n_seen": len(rows), "traders": traders,
           "span_s": (rows[0]["t"] - rows[-1]["t"]) if len(rows) > 1 else 0,
           "fetched_at": int(time.time()), "note": note, "source": source}
    if stale:
        out["stale"] = True
        if stale_reason:
            out["stale_reason"] = stale_reason
    return out


def _ui(bal):
    ui = (bal or {}).get("uiTokenAmount") or {}
    if ui.get("uiAmount") is not None:
        try:
            return float(ui["uiAmount"])
        except (TypeError, ValueError):
            pass
    try:
        return float(ui.get("uiAmountString"))
    except (TypeError, ValueError):
        return 0.0


def _deltas(meta):
    pre, post = {}, {}
    for key, dest in (("preTokenBalances", pre), ("postTokenBalances", post)):
        for b in meta.get(key) or []:
            owner, mint = b.get("owner"), b.get("mint")
            if not owner or not mint:
                continue
            dest.setdefault(owner, {})
            dest[owner][mint] = dest[owner].get(mint, 0.0) + _ui(b)
    out = {}
    for owner in set(pre) | set(post):
        mints = set((pre.get(owner) or {})) | set((post.get(owner) or {}))
        out[owner] = {m: (post.get(owner) or {}).get(m, 0.0) - (pre.get(owner) or {}).get(m, 0.0) for m in mints}
    return out


def _keys(tx):
    return (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])


def _pub(key):
    if isinstance(key, dict):
        return key.get("pubkey")
    return key if isinstance(key, str) else None


def _signers(tx):
    out = []
    for i, key in enumerate(_keys(tx)):
        if isinstance(key, dict) and key.get("signer") and key.get("pubkey"):
            out.append(key["pubkey"])
        elif isinstance(key, str) and i == 0:
            out.append(key)
    return out


def _native_sol(tx, meta, user):
    """Absolute SOL the signer swapped, with the tx fee taken out of the change."""
    keys = _keys(tx)
    idx = None
    for i, key in enumerate(keys):
        if _pub(key) == user:
            idx = i
            break
    pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
    if idx is None or idx >= len(pre_b) or idx >= len(post_b):
        return None
    delta = int(post_b[idx]) - int(pre_b[idx])
    if idx == 0:
        delta += int(meta.get("fee") or 0)
    sol = abs(delta) / 1e9
    return sol if sol > 0 else None


def parse_swap(tx, pool, token, signature, sol_usd):
    """One successful transaction -> one tape row, or None when it is not a priced swap.

    Dollar size comes from a USDC/USDT delta, or from a WSOL / native SOL delta
    times a SOL/USD price that was actually fetched. No price, no row.
    """
    if not isinstance(tx, dict):
        return None
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None
    ts = tx.get("blockTime")
    if not ts:
        return None
    deltas = _deltas(meta)
    signers = [s for s in _signers(tx) if s and s != pool]
    user = None
    for s in signers:
        d = deltas.get(s) or {}
        if token and d.get(token):
            user = s
            break
        if any(m not in QUOTES and abs(v) > 0 for m, v in d.items()):
            user = s
            break
    if user is None:
        for owner, d in deltas.items():
            if owner == pool:
                continue
            if token and d.get(token):
                user = owner
                break
            if any(m not in QUOTES and abs(v) > 0 for m, v in d.items()):
                user = owner
                break
    if not user:
        return None
    d = deltas.get(user) or {}
    base = token
    if base:
        amt = d.get(base) or 0.0
    else:
        cands = [(m, v) for m, v in d.items() if m not in QUOTES and abs(v) > 0]
        if not cands:
            return None
        base, amt = max(cands, key=lambda kv: abs(kv[1]))
    if not amt:
        return None
    usd = None
    for mint, v in d.items():
        if mint in STABLES and v:
            usd = abs(float(v))
            break
    if usd is None:
        for mint, v in d.items():
            if mint == WSOL and v and sol_usd:
                usd = abs(float(v)) * float(sol_usd)
                break
    if usd is None and sol_usd:
        sol = _native_sol(tx, meta, user)
        if sol:
            usd = sol * float(sol_usd)
    if usd is None or usd <= 0:
        return None
    amt_abs = abs(float(amt))
    return {"t": int(ts), "k": "buy" if amt > 0 else "sell", "usd": round(usd, 2),
            "px": round(usd / amt_abs, 12) if amt_abs else None, "amt": amt_abs,
            "w": user, "sig": signature, "anom": False, "tok": base}


def _default_sol_usd():
    now = time.time()
    if _sol["usd"] and now - _sol["t"] < 120:
        return _sol["usd"]
    try:
        import pulse
        px, ts = pulse._sol.get("usd"), pulse._sol.get("t") or 0
        if px and now - ts < 600:
            _sol.update(t=now, usd=float(px))
            return _sol["usd"]
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://lite-api.jup.ag/price/v3?ids=" + WSOL,
            headers={"Accept": "application/json", "User-Agent": "xScanner read-only"})
        with urllib.request.urlopen(req, timeout=4) as r:
            import json
            d = json.loads(r.read().decode())
        px = float((d.get(WSOL) or {}).get("usdPrice") or 0)
        if px > 0:
            _sol.update(t=now, usd=px)
            return px
    except Exception:
        pass
    return _sol["usd"]


def _default_gt(pool, token):
    import trader
    return trader.trades(pool, token)


def _default_rpc(method, params):
    import sol_rpc
    # gap=0: this is a person waiting. sol_rpc's default gap is for the background scan.
    return sol_rpc.call(method, params, timeout=8, gap=0)


def _rpc_tape(pool, token, rpc, sol_usd):
    if sol_usd is None:
        sol_usd = _default_sol_usd()
    sigs = rpc("getSignaturesForAddress", [pool, {"limit": SIG_LIMIT}]) or []
    if not isinstance(sigs, list):
        return None
    rows = []
    for s in sigs:
        if len(rows) >= TX_LIMIT:
            break
        if not isinstance(s, dict) or s.get("err") or not s.get("signature"):
            continue
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        row = parse_swap(tx, pool, token, s["signature"], sol_usd)
        if row:
            rows.append(row)
    if not rows:
        return None
    return _assemble(rows, NOTE_RPC, "solana_rpc")


def _mark_user():
    try:
        import pace
        return pace.PRIORITY.set("user")
    except Exception:
        return None


def _unmark(token):
    if token is None:
        return
    try:
        import pace
        pace.PRIORITY.reset(token)
    except Exception:
        pass


def _load(pool, token, gt, rpc, sol_usd):
    gt_payload, gt_err = None, None
    try:
        gt_payload = (gt or _default_gt)(pool, token)
    except Exception as e:
        gt_err = e
    if isinstance(gt_payload, dict) and gt_payload.get("available") and not gt_payload.get("stale"):
        out = dict(gt_payload)
        out["source"] = out.get("source") or "geckoterminal"
        return out
    rpc_err = None
    rpc_payload = None
    try:
        rpc_payload = _rpc_tape(pool, token, rpc or _default_rpc, sol_usd)
    except Exception as e:
        rpc_err = e
    if isinstance(rpc_payload, dict) and rpc_payload.get("available") and rpc_payload.get("trades"):
        return rpc_payload
    if isinstance(gt_payload, dict) and gt_payload.get("trades"):
        out = dict(gt_payload)
        out["stale"] = True
        out["available"] = True
        out["source"] = out.get("source") or "geckoterminal"
        out.setdefault("stale_reason", (gt_payload.get("stale_reason") or (str(gt_err) if gt_err else "refresh failed")))
        return out
    return _unavailable(_reason(gt_payload, gt_err, rpc_err))


def _store(pool, payload, now):
    fresh = bool(payload.get("available") and payload.get("trades") and not payload.get("stale"))
    retry = now + (FRESH_S if fresh or payload.get("trades") else NEGATIVE_S)
    with _lock:
        _cache[pool] = {"ts": now, "payload": payload, "retry_at": retry}
        if len(_cache) > 300:
            for k in sorted(_cache, key=lambda k: _cache[k]["ts"])[:100]:
                _cache.pop(k, None)


def trades(pool, token=None, *, gt=None, rpc=None, sol_usd=None, allow_kick=True):
    """One tape for `pool`. See the module docstring for the JSON shape.

    gt(pool, token) and rpc(method, params) are injection points for tests.
    sol_usd, when passed, prices WSOL-quoted swaps without another HTTP call.
    allow_kick is False on the stale-while-revalidate thread so it fetches
    instead of scheduling another copy of itself.
    """
    now = time.time()
    with _lock:
        c = _cache.get(pool)
        old = c["payload"] if c and c["payload"].get("trades") else None
        if c and now - c["ts"] < FRESH_S:
            return c["payload"]
        if allow_kick and c and old and now - c["ts"] < SWR_S:
            if now >= c.get("retry_at", 0) and pool not in _kicked:
                c["retry_at"] = now + FRESH_S
                _kick_locked(pool, token, gt, rpc, sol_usd)
            return {**c["payload"], "stale": True, "cached": True}
        slot = _flights.get(pool)
        if slot is None:
            ev, box = threading.Event(), {}
            _flights[pool] = (ev, box)
            leader = True
        else:
            ev, box = slot
            leader = False
    if not leader:
        ev.wait(12.0)
        if "data" in box:
            return box["data"]
        with _lock:
            c = _cache.get(pool)
        if c and c["payload"].get("trades"):
            return {**c["payload"], "stale": True, "cached": True}
        return box.get("data") or _unavailable("Trades tape unavailable right now.")
    try:
        try:
            data = _load(pool, token, gt, rpc, sol_usd)
        except Exception as e:
            data = _unavailable("Trades tape unavailable right now (%s)." % type(e).__name__)
        if not data.get("trades") and old:
            data = {**old, "stale": True, "cached": True, "available": True,
                    "stale_reason": data.get("reason") or "refresh failed"}
        box["data"] = data
        _store(pool, data, time.time())
        return data
    finally:
        with _lock:
            _flights.pop(pool, None)
        ev.set()


def _kick_locked(pool, token, gt, rpc, sol_usd):
    """Caller holds _lock. Start one refresh if nobody is already fetching."""
    if pool in _flights or pool in _kicked:
        return
    _kicked.add(pool)

    def run():
        mark = _mark_user()
        try:
            trades(pool, token, gt=gt, rpc=rpc, sol_usd=sol_usd, allow_kick=False)
        except Exception:
            pass
        finally:
            _unmark(mark)
            with _lock:
                _kicked.discard(pool)

    threading.Thread(target=run, name="trades-refresh", daemon=True).start()
