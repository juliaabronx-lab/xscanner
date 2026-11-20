"""Data for the trader token page: one read-only aggregation of free public sources, with honest "pending / unavailable" parts.

Sources: DexScreener (pairs, market data, socials), Jupiter lite token stats (holders, top-10, dev %), the Solana RPC
(mint / freeze authority, pump.fun bonding curve, early-block buyers), RugCheck via bundle.py (holders table, LP lock, insiders).
Never blocks longer than ~8 s: slow parts come back as `pending` and the page asks again. No keys, no signing, no orders.
"""
import json, threading, time

import bundle as bd
import pulse
import trader as tr

_jup_cache = {}
_tok_cache = {}


def _jup_one(mint):
    c = _jup_cache.get(mint)
    if c and time.time() - c[0] < 90: return c[1]
    try:
        d = pulse._jup("search?query=" + mint)
        t = next((x for x in d if x.get("id") == mint), None) if isinstance(d, list) else None
    except Exception:
        t = None
    if t is not None: _jup_cache[mint] = (time.time(), t)
    return t or (c[1] if c else None)


def deep_links(mint, pool):
    p = pool or mint
    return [
        {"k": "jupiter", "label": "Jupiter", "url": "https://jup.ag/swap/SOL-" + mint},
        {"k": "axiom", "label": "Axiom", "url": "https://axiom.trade/meme/%s?chain=sol" % p},
        {"k": "photon", "label": "Photon", "url": "https://photon-sol.tinyastro.io/en/lp/" + p},
        {"k": "dexscreener", "label": "DexScreener", "url": "https://dexscreener.com/solana/" + p},
        {"k": "solscan", "label": "Solscan", "url": "https://solscan.io/token/" + mint},
        {"k": "rugcheck", "label": "RugCheck", "url": "https://rugcheck.xyz/tokens/" + mint},
    ]


def _pair_summary(p):
    return {"pool": p.get("pairAddress"), "dex": p.get("dexId"), "quote": (p.get("quoteToken") or {}).get("symbol"),
            "liq": tr.num((p.get("liquidity") or {}).get("usd")), "vol24": tr.num((p.get("volume") or {}).get("h24")),
            "created": int(p["pairCreatedAt"] / 1000) if p.get("pairCreatedAt") else None}


def detail(mint, deadline_s=8.0):
    """Everything the token page shows except our own score / story (those come from /api/lookup). Cached 6 s."""
    c = _tok_cache.get(mint)
    if c and time.time() - c[0] < 6: return c[1]
    t0 = time.time()
    out = {"mint": mint, "pending": [], "notes": [], "fetched_at": int(t0)}
    box = {}

    def job(name, fn):
        def run():
            try: box[name] = fn()
            except Exception as e: box[name] = ("__err__", "%s: %s" % (type(e).__name__, e))
        th = threading.Thread(target=run, daemon=True); th.start(); return th

    row = pulse.get_row(mint)
    fresh_ds = row and time.time() - (row.get("_ds") or 0) < 20
    ths = [job("ds", lambda: tr.ds_tokens([mint])),
           job("jup", lambda: _jup_one(mint)),
           job("mint", lambda: tr.mint_state(mint)),
           job("rc", lambda: (bd.fetch_raw(mint, 6), bd.get_raw(mint)))]
    curve_acct = row.get("cp") if row and row.get("cp") else (tr.bonding_curve_pda(mint) if mint.endswith("pump") else None)
    if curve_acct:
        ths.append(job("curve", lambda: tr.curves([curve_acct], ttl=3.0).get(curve_acct)))
    for th in ths: th.join(max(0.1, deadline_s - (time.time() - t0)))
    for k in ("ds", "jup", "mint", "rc", "curve"):
        if isinstance(box.get(k), tuple) and box[k] and box[k][0] == "__err__": out["notes"].append("%s unavailable (%s)" % (k, box[k][1]))
    pairs = box.get("ds") if isinstance(box.get("ds"), list) else []
    best = tr.best_pair(pairs, mint)
    jt = box.get("jup") if isinstance(box.get("jup"), dict) else None
    raw, raw_age = (box.get("rc") or (None, (None, None)))[1] if isinstance(box.get("rc"), tuple) and box["rc"][0] != "__err__" else (None, None)
    ms = box.get("mint") if isinstance(box.get("mint"), dict) else {}
    cv = box.get("curve") if isinstance(box.get("curve"), dict) else None

    # ---- header + market
    if best is None and row is None and jt is None:
        out["found"] = False
        out["notes"].append("No market found for this token yet (DexScreener has no pair and it is not on our board).")
        return out
    out["found"] = True
    bt = (best or {}).get("baseToken") or {}
    out["name"] = bt.get("name") or (jt or {}).get("name") or (row or {}).get("n")
    out["symbol"] = bt.get("symbol") or (jt or {}).get("symbol") or (row or {}).get("s")
    out["img"] = ((best or {}).get("info") or {}).get("imageUrl") or (jt or {}).get("icon") or (row or {}).get("img")
    pool = (best or {}).get("pairAddress") or (row or {}).get("p") or curve_acct
    out["pool"] = pool
    tx, vol, pc = ((best or {}).get("txns") or {}), ((best or {}).get("volume") or {}), ((best or {}).get("priceChange") or {})
    s5, s1 = ((jt or {}).get("stats5m") or {}), ((jt or {}).get("stats1h") or {})
    mc = tr.num((best or {}).get("marketCap"), tr.num((best or {}).get("fdv"))) or (jt or {}).get("mcap") or (row or {}).get("mc")
    out["market"] = {
        "px": tr.num((best or {}).get("priceUsd")) or (jt or {}).get("usdPrice") or (row or {}).get("px"), "mc": mc,
        "lq": tr.num(((best or {}).get("liquidity") or {}).get("usd")) or (jt or {}).get("liquidity") or (row or {}).get("lq"),
        "v5": tr.num(vol.get("m5")), "v1": tr.num(vol.get("h1")), "v6": tr.num(vol.get("h6")), "v24": tr.num(vol.get("h24")),
        "b5": (tx.get("m5") or {}).get("buys"), "s5": (tx.get("m5") or {}).get("sells"), "b1": (tx.get("h1") or {}).get("buys"), "s1": (tx.get("h1") or {}).get("sells"),
        "b24": (tx.get("h24") or {}).get("buys"), "s24": (tx.get("h24") or {}).get("sells"),
        "pc5": tr.num(pc.get("m5")), "pc1": tr.num(pc.get("h1")), "pc6": tr.num(pc.get("h6")), "pc24": tr.num(pc.get("h24")),
        "dex": (best or {}).get("dexId") or (row or {}).get("d"), "quote": ((best or {}).get("quoteToken") or {}).get("symbol") or (row or {}).get("q"),
        "created": (row or {}).get("cr") or (_iso(jt, "createdAt")) or (int(best["pairCreatedAt"] / 1000) if best and best.get("pairCreatedAt") else None),
        "migrated_at": (row or {}).get("mg"), "ds_url": (best or {}).get("url"),
    }
    out["pairs"] = sorted([_pair_summary(p) for p in pairs if (p.get("baseToken") or {}).get("address") == mint], key=lambda x: -(x["liq"] or 0))[:6]
    info = (best or {}).get("info") or {}
    soc = {s.get("type"): s.get("url") for s in info.get("socials") or [] if isinstance(s, dict)}
    webs = [w.get("url") for w in info.get("websites") or [] if isinstance(w, dict)]
    out["socials"] = {"twitter": soc.get("twitter") or (jt or {}).get("twitter") or (row or {}).get("tw"), "telegram": soc.get("telegram") or (jt or {}).get("telegram") or (row or {}).get("tg"),
                      "website": (webs[0] if webs else None) or (jt or {}).get("website") or (row or {}).get("web")}
    out["links"] = deep_links(mint, pool)
    # ---- bonding curve
    dex = out["market"]["dex"]
    if cv and dex in pulse.CURVE_DEX:
        out["curve"] = {"progress": cv["progress"], "complete": cv["complete"], "real_sol": cv["real_sol"], "source": "chain",
                        "note": "Read from the pump.fun bonding-curve account on chain. A curve completes when its real token reserve hits zero, then the pool moves to PumpSwap."}
    elif cv and dex in tr.AMM_DEX:
        out["curve"] = {"progress": 100.0, "complete": True, "source": "chain", "note": "Bonding curve complete: the token has migrated to an AMM pool."}
    elif dex in pulse.CURVE_DEX:
        est = tr.curve_from_mcap(mc, pulse._sol["usd"])
        out["curve"] = {"progress": est, "complete": False, "source": "est" if est is not None else None,
                        "note": "Estimated from market cap (RPC unavailable)." if est is not None else "Curve progress unavailable."}
    # ---- security
    holders_ct = (jt or {}).get("holderCount") if jt else None
    au = (jt or {}).get("audit") or {}
    sec = {}
    ma = ms.get("mint_auth") if "mint_auth" in ms else (raw or {}).get("mint_auth") if raw else None
    fa = ms.get("freeze_auth") if "freeze_auth" in ms else (raw or {}).get("freeze_auth") if raw else None
    known_auth = "mint_auth" in ms or raw is not None or au.get("mintAuthorityDisabled") is not None
    sec["mint_authority"] = {"known": known_auth, "disabled": (ma is None) if ("mint_auth" in ms or raw) else au.get("mintAuthorityDisabled"), "who": ma,
                             "source": "chain" if "mint_auth" in ms else "rugcheck" if raw else "jupiter" if au else None}
    sec["freeze_authority"] = {"known": "freeze_auth" in ms or raw is not None or au.get("freezeAuthorityDisabled") is not None,
                               "disabled": (fa is None) if ("freeze_auth" in ms or raw) else au.get("freezeAuthorityDisabled"), "who": fa,
                               "source": "chain" if "freeze_auth" in ms else "rugcheck" if raw else "jupiter" if au else None}
    sec["token2022"] = ms.get("token2022")
    tf = (raw or {}).get("transfer_fee") or {}
    sec["transfer_fee_pct"] = tf.get("pct") if tf else None
    lp_note, lp_val = None, None
    if dex in pulse.CURVE_DEX:
        lp_note = "Still on the pump.fun bonding curve: there is no LP token to pull. The liquidity sits in the curve until migration."
    elif dex == "pumpswap" or (raw and (raw.get("launchpad") or "").lower().startswith("pump")):
        lp_note = "pump.fun burns the LP tokens when a token migrates to PumpSwap."
    if raw:
        lps = [m["lp"].get("lpLockedPct") for m in raw.get("markets") or [] if m.get("lp") and m["lp"].get("lpLockedPct") is not None]
        lp_val = max(lps) if lps else None
    sec["lp"] = {"locked_pct": lp_val, "note": lp_note, "source": "rugcheck" if lp_val is not None else None}
    sec["top10_pct"] = au.get("topHoldersPercentage") if au.get("topHoldersPercentage") is not None else (row or {}).get("t10")
    sec["dev"] = {"wallet": (jt or {}).get("dev") or (raw or {}).get("creator"), "balance_pct": au.get("devBalancePercentage"), "launched": au.get("devMints"),
                  "creator_rugged": (raw or {}).get("rugged") if raw else None}
    sec["holders"] = holders_ct if holders_ct is not None else (raw or {}).get("holders") if raw else (row or {}).get("h")
    sec["insiders"] = {"networks": len((raw or {}).get("insider_networks") or []), "wallets": (raw or {}).get("insiders_detected"),
                       "top_holders_flagged": sum(1 for h in (raw or {}).get("top") or [] if h.get("insider"))} if raw else None
    sec["risks"] = (raw or {}).get("risks") if raw else None
    sec["rugcheck_score"] = (raw or {}).get("score") if raw else None
    out["security"] = sec
    # ---- holders table (RugCheck top holders)
    if raw:
        lpa = set(raw.get("lp_accts") or []) | ({curve_acct} if curve_acct else set())
        rows = []
        for i, h in enumerate(raw.get("top") or [], 1):
            pool_like = (h.get("address") in lpa) or (h.get("owner") in lpa)
            rows.append({"rank": i, "owner": h.get("owner") or h.get("address"), "pct": round(h.get("pct") or 0, 3), "insider": h.get("insider"), "pool": pool_like,
                         "dev": bool(sec["dev"]["wallet"] and (h.get("owner") == sec["dev"]["wallet"]))})
        out["holders"] = {"rows": rows, "age_s": int(raw_age or 0), "source": "rugcheck", "total": sec["holders"]}
    else:
        out["holders"] = None; out["pending"].append("holders")
    # ---- early buyers: snipers / bundle (pump.fun curve only; runs in the background, the page polls)
    if curve_acct and dex in (pulse.CURVE_DEX | tr.AMM_DEX):
        e = tr.early_cached(mint)
        if e is None:
            def run_early(): 
                try: tr.early_scan(mint, curve_acct, sec["dev"]["wallet"], max_pages=5, max_tx=16, budget_s=14.0)
                except Exception: pass
            if not _early_running.get(mint) or time.time() - _early_running[mint] > 40:
                _early_running[mint] = time.time(); threading.Thread(target=run_early, daemon=True).start()
            out["pending"].append("early")
        out["early"] = e
    else:
        out["early"] = {"status": "n/a", "reason": "Early-block buyer scan only works for pump.fun launches."}
    if not best: out["pending"].append("market")
    if not jt: out["notes"].append("Jupiter token stats unavailable right now (holders, top-10 and dev % may be missing).")
    out["took_s"] = round(time.time() - t0, 2)
    _tok_cache[mint] = (time.time(), out)
    if len(_tok_cache) > 200:
        for k in sorted(_tok_cache, key=lambda k: _tok_cache[k][0])[:80]: _tok_cache.pop(k, None)
    return out


_early_running = {}


def _iso(t, k):
    try: return pulse._iso_epoch((t or {}).get(k))
    except Exception: return None
