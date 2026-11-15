#!/usr/bin/env python3
"""Memecoin narrative + X scanner (data only, no trading).

  python3 scan.py --candidates   # after X searches: list tickers to run X counts on
  python3 scan.py                # full scan -> report.md + report.json
Options: --no-x (DexScreener-only), --top N (default 5)
"""
import json, math, os, re, sys, time, hashlib, urllib.request, urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
MYT = timezone(timedelta(hours=8))
NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "Mozilla/5.0 (scanner)", "Accept": "application/json"}

B58 = r"[1-9A-HJ-NP-Za-km-z]"
MINT_RE = re.compile(rf"(?<![1-9A-HJ-NP-Za-km-z])({B58}{{31,44}})(?![1-9A-HJ-NP-Za-km-z])")
TAG_RE = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{1,14})\b")
SHILL_PATS = [
    r"called at", r"call(?:ed)? (?:on|in) (?:my )?(?:private )?(?:tg|telegram)", r"callout on tg",
    r"dm (?:me )?to join", r"join (?:my |our )?(?:private |alpha |vip )?(?:tg|telegram|discord)",
    r"entry (?:mc|still)", r"\b\d+(?:\.\d+)?\s?x (?:gain|profit|up|on|from|move|rise)", r"printed \d",
    r"#memecoin1000x", r"alpha telegram", r"cto signal", r"wallet flow check", r"ai signal",
    r"detect paid", r"follow for more", r"early calls?", r"went from \$[\d.]+k", r"from \$[\d.]+k to \$[\d.]+k",
    r"current mc", r"cto alpha", r"breakout ⚡", r"spotlight", r"gem alert", r"next 100x", r"100x gem",
    r"research your entry", r"bull case:", r"\bsniper\b", r"my call", r"tg fam",
    r"(?<![\w.])\d+(?:\.\d+)?\s?x\b", r"\d+(?:\.\d+)?k\s*(?:→|->|to|➡️?)\s*\$?\d+(?:\.\d+)?[km]",
    r"watch(?:ing)?\s+(?:your entry|\$)", r"\bentry\s+\$", r"eyes on (?:this|\$)", r"don.?t sleep", r"call(?:ed)?\s+\$\w+\s+at",
    r"my alpha", r"/pnl", r"private\s+tg", r"the group", r"next one", r"\bdm me\b", r"\bbio\b", r"winner.?s circle",
    r"\balert\b", r"momentum board", r"cto audit", r"coordinated launch", r"trending dex", r"mcap:\s*\$",
    r"market cap:\s*\$", r"is live on", r"race to the moon", r"called around", r"successful call", r"migration intel",
    r"profits? secured", r"let.?s send", r"live now on pump", r"liquidity:\s*\$", r"had \$\w+ at", r"we had", r"calls? speak", r"still cooking",
]
SHILL_RE = re.compile("|".join(SHILL_PATS), re.I)
PHISH_RE = re.compile(r"allocation|connect (?:your )?wallet|claim (?:your|now)|airdrop is live|check eligib|eligible|whitelist spot", re.I)
KNOWN_SHILL = {a.lower() for a in CFG["known_shill_accounts"]}
KOLS = {a.lower() for a in CFG["kol_accounts"]}
IGNORE = {t.upper() for t in CFG["ignore_tickers"]}
BL_NAMES = [n.upper() for n in CFG["blacklist_names"]]
BL_MINTS = set(CFG["blacklist_mints"])


def get(url, timeout=12, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            err = e
            time.sleep(0.6 * (i + 1))
    return {"_error": str(err)}


def ptime(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------- X side
def load_x():
    p = os.path.join(HERE, "x_raw.json")
    if "--no-x" in sys.argv or not os.path.exists(p):
        return {"posts": {}, "users": {}, "counts": {}}
    return json.load(open(p))


def template_key(text):
    t = text.lower()
    t = re.sub(r"https?://\S+", "", t)
    t = MINT_RE.sub("<m>", t)
    t = TAG_RE.sub("<t>", t)
    t = re.sub(r"@\w+", "", t)
    t = re.sub(r"[\d.,]+", "#", t)
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.md5(t[:120].encode()).hexdigest()


def analyse_x(raw):
    posts = list(raw.get("posts", {}).values())
    users = raw.get("users", {})
    tmpl_count = defaultdict(set)
    for p in posts:
        tmpl_count[template_key(p.get("text", ""))].add(p.get("author_id"))
    author_flags = defaultdict(int)
    enriched = []
    for p in posts:
        txt = p.get("text", "")
        u = users.get(p.get("author_id"), {})
        uname = (u.get("username") or (p.get("url", "").split("/")[3] if p.get("url", "").count("/") > 3 else "")).lower()
        mints = set(m for m in MINT_RE.findall(txt) if len(m) >= 32 and not m.isalpha())
        tags = set(t.upper() for t in TAG_RE.findall(txt)) - IGNORE
        reasons = []
        if uname in KNOWN_SHILL: reasons.append("known call-group acct")
        if SHILL_RE.search(txt): reasons.append("templated call text")
        if len(tmpl_count[template_key(txt)]) >= 1 and sum(1 for q in posts if q.get("author_id") == p.get("author_id") and template_key(q.get("text","")) == template_key(txt)) >= 2:
            reasons.append("same text repeated")
        mentioned = set(re.findall(r"@(\w+)", txt.lower()))
        if p.get("in_reply_to_user_id") and (mints or tags) and (mentioned & KOLS or txt.startswith("@")):
            reasons.append("reply-spam with CA")
        bare = re.sub(r"https?://\S+|@\w+|#\w+|\$\w+", " ", MINT_RE.sub(" ", txt))
        words = re.findall(r"[^\W\d_]{2,}", bare)
        if (mints or tags) and len([w for w in words if w.lower() not in ("ca", "solana", "sol", "pump", "fun")]) < 5:
            reasons.append("bare CA/ticker drop")
        if len(re.findall(r"#\w+", txt)) >= 4: reasons.append("hashtag spam")
        if any(t.lower() in uname for t in tags if len(t) >= 4): reasons.append("project's own account")
        phish = bool(PHISH_RE.search(txt))
        if phish: reasons.append("phishing wording")
        pm = p.get("public_metrics", {})
        fol = (u.get("public_metrics") or {}).get("followers_count", 0)
        eng = pm.get("like_count", 0) + 2 * pm.get("retweet_count", 0) + pm.get("reply_count", 0) + 2 * pm.get("quote_count", 0)
        if reasons: author_flags[p.get("author_id")] += 1
        enriched.append(dict(id=p["id"], author=p.get("author_id"), uname=uname, followers=fol, eng=eng,
                             t=ptime(p.get("created_at", "")), mints=mints, tags=tags, shill=bool(reasons),
                             reasons=reasons, phish=phish, text=txt, url=p.get("url")))
    coins_by_author = defaultdict(set)
    for e in enriched:
        coins_by_author[e["author"]] |= e["mints"] | e["tags"]
    for e in enriched:
        if len(coins_by_author[e["author"]]) >= 4 and not e["shill"]:
            e["shill"] = True; e["reasons"].append("multi-coin caller")
    # author-level: 2+ flagged posts => every post from that author is shill
    for e in enriched:
        if author_flags[e["author"]] >= 2 and not e["shill"]:
            e["shill"] = True; e["reasons"].append("call-group author")
    return enriched


def x_stats(enriched, keys_mints, ticker, name):
    """Stats for posts mentioning any mint in keys_mints, or $ticker, or the coin name."""
    tk = (ticker or "").upper()
    nm = (name or "").lower().strip()
    hits = []
    for e in enriched:
        if e["mints"] & keys_mints or (tk and tk in e["tags"]) or (nm and len(nm) >= 5 and nm in e["text"].lower()):
            hits.append(e)
    tl = re.sub(r"[^a-z0-9]", "", tk.lower())
    own = lambda e: len(tl) >= 4 and tl in e["uname"].replace("_", "")
    org = [e for e in hits if not e["shill"] and not own(e)]
    sh = [e for e in hits if e["shill"] or own(e)]
    t1 = NOW - timedelta(hours=1)
    reasons = defaultdict(int)
    for e in sh:
        for r in e["reasons"]: reasons[r] += 1
    return dict(
        mentions=len(hits), authors=len({e["author"] for e in hits}),
        organic_authors=len({e["author"] for e in org}), shill_authors=len({e["author"] for e in sh}),
        organic_eng=sum(e["eng"] for e in org), organic_followers=sum(sorted([e["followers"] for e in org], reverse=True)[:10]),
        last1h=sum(1 for e in hits if e["t"] and e["t"] >= t1),
        shill_share=round(len(sh) / len(hits), 2) if hits else 0.0,
        phishing=sum(1 for e in hits if e["phish"]),
        shill_reasons=dict(reasons),
        shill_accts=sorted({e["uname"] for e in sh if e["uname"]})[:6],
        top_organic=sorted(org, key=lambda e: -e["eng"])[:2],
    )


def velocity(counts, key):
    """counts = list of {start,end,tweet_count} (hour granularity). ratio = last hour / avg of previous hours."""
    b = counts.get(key) or counts.get(key.upper())
    if not b: return None
    b = sorted(b, key=lambda x: x.get("start", ""))
    vals = [x.get("tweet_count", 0) for x in b]
    if len(vals) < 2: return dict(last=vals[-1] if vals else 0, prev_avg=0, ratio=None, total=sum(vals))
    # last bucket may be partial: scale it to a full hour
    last = vals[-1]
    st, en = ptime(b[-1].get("start", "")), ptime(b[-1].get("end", ""))
    if st and en:
        frac = max(0.1, min(1.0, (min(NOW, en) - st).total_seconds() / 3600))
        last = vals[-1] / frac
    prev = vals[-4:-1] if len(vals) >= 4 else vals[:-1]
    pa = sum(prev) / len(prev) if prev else 0
    return dict(last=round(last), prev_avg=round(pa, 1), ratio=round(last / pa, 2) if pa else (99.0 if last else None), total=sum(vals))


# ---------------------------------------------------------------- DexScreener side
def ds_pairs_for_mints(mints):
    out = defaultdict(list)
    mints = list(mints)
    want = set(mints)
    chunks = [mints[i:i + 30] for i in range(0, len(mints), 30)]
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(lambda c: get("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(c)), chunks):
            for p in (res.get("pairs") or []) if isinstance(res, dict) else []:
                if p.get("chainId") == "solana" and p["baseToken"]["address"] in want:
                    out[p["baseToken"]["address"]].append(p)
    return out


def ds_search(q):
    r = get("https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(q))
    return [p for p in (r.get("pairs") or []) if p.get("chainId") == "solana"] if isinstance(r, dict) else []


def summarize_token(mint, pairs):
    if not pairs: return None
    main = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0, (p.get("txns", {}).get("h1", {}).get("buys", 0))))
    def tx(k):
        b = sum(p.get("txns", {}).get(k, {}).get("buys", 0) for p in pairs)
        s = sum(p.get("txns", {}).get(k, {}).get("sells", 0) for p in pairs)
        return b, s
    busiest = max(pairs, key=lambda p: p.get("txns", {}).get("h1", {}).get("buys", 0) + p.get("txns", {}).get("h1", {}).get("sells", 0))
    created = min((p.get("pairCreatedAt") or 9e15) for p in pairs)
    bt = main["baseToken"]
    pc = busiest.get("priceChange") or {}
    mcap = max([(p.get("marketCap") or p.get("fdv") or 0) for p in pairs] or [0])
    liq = sum(((p.get("liquidity") or {}).get("usd") or 0) for p in pairs)
    # DexScreener can't price some pools (e.g. xStock-quoted pump pools). If an unpriced pool is the
    # busiest one, treat liquidity/mcap as unknown so RugCheck's numbers are used instead.
    act = lambda p: p.get("txns", {}).get("h1", {}).get("buys", 0) + p.get("txns", {}).get("h1", {}).get("sells", 0)
    if not (busiest.get("liquidity") or {}).get("usd") and act(busiest) > 3 * max([act(p) for p in pairs if (p.get("liquidity") or {}).get("usd")] or [0]):
        liq, mcap = 0, 0
    vol24 = sum((p.get("volume") or {}).get("h24", 0) or 0 for p in pairs)
    boosts = max(((p.get("boosts") or {}).get("active") or 0) for p in pairs)
    socials = []
    for p in pairs:
        for s in (p.get("info") or {}).get("socials") or []:
            socials.append(s.get("url"))
    pool = busiest if not (main.get("liquidity") or {}).get("usd") else main
    return dict(
        mint=mint, name=bt.get("name"), symbol=bt.get("symbol"), pool=pool["pairAddress"], dex=pool.get("dexId"),
        url=f"https://dexscreener.com/solana/{pool['pairAddress'].lower()}",
        age_h=round((time.time() * 1000 - created) / 3.6e6, 1) if created < 9e15 else None,
        liq=liq, mcap=mcap, vol24=vol24, m5=tx("m5"), h1=tx("h1"), h24=tx("h24"),
        pc5=pc.get("m5"), pc1=pc.get("h1"), pc6=pc.get("h6"), pc24=pc.get("h24"),
        boosts=boosts, quote=pool.get("quoteToken", {}).get("symbol"), socials=list(dict.fromkeys(socials))[:3],
        is_pump=mint.endswith("pump"), n_pairs=len(pairs),
    )


def rugcheck(mint, pair_addrs):
    try:
        return _rugcheck(mint, pair_addrs)
    except Exception as e:
        return {"error": f"parse: {e}"}


def _rugcheck(mint, pair_addrs):
    r = get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=15)
    if "_error" in r or not isinstance(r, dict) or "mint" not in r:
        s = get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary", timeout=10)
        if "_error" in s: return {"error": s["_error"]}
        return dict(score=s.get("score_normalised"), risks=[x.get("name") for x in s.get("risks") or []], top10=None)
    excl = set(pair_addrs)
    for m in r.get("markets") or []:
        for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount"):
            v = m.get(k)
            if isinstance(v, str): excl.add(v)
            elif isinstance(v, dict) and v.get("pubkey"): excl.add(v["pubkey"])
        lp = m.get("lp") or {}
        for k in ("lpMint", "baseVault", "quoteVault"):
            if isinstance(lp.get(k), str): excl.add(lp[k])
    for a, info in (r.get("knownAccounts") or {}).items():
        if (info or {}).get("type") in ("AMM", "LOCKER", "PUMP_FUN"): excl.add(a)
    th = [h for h in (r.get("topHolders") or []) if h.get("owner") not in excl and h.get("address") not in excl]
    top10 = round(sum(h.get("pct", 0) for h in th[:10]), 1) if th else None
    supply = ((r.get("token") or {}).get("supply") or 0) / 10 ** ((r.get("token") or {}).get("decimals") or 6)
    ct = r.get("creatorTokens") or []
    return dict(
        score=r.get("score_normalised"), risks=[x.get("name") for x in r.get("risks") or []],
        top10=top10, top1=round(th[0]["pct"], 1) if th else None, insiders=sum(1 for h in th[:20] if h.get("insider")),
        holders=r.get("totalHolders"), liq=r.get("totalMarketLiquidity"), price=r.get("price"),
        mcap=(r.get("price") or 0) * supply, rugged=r.get("rugged"), mint_auth=(r.get("token") or {}).get("mintAuthority"),
        freeze_auth=(r.get("token") or {}).get("freezeAuthority"), creator=r.get("creator"),
        creator_tokens=len(ct), creator_rugged=sum(1 for t in ct if t.get("rugged")),
        insider_networks=len(r.get("insiderNetworks") or []),
        bundle_pct=min(100.0, round(100 * sum((n.get("currentHolding") or 0) for n in (r.get("insiderNetworks") or [])) / max(1, (r.get("token") or {}).get("supply") or 1), 1)),
        bundle_wallets=sum((n.get("activeAccounts") or n.get("size") or 0) for n in (r.get("insiderNetworks") or [])), launchpad=(r.get("launchpad") or {}).get("name"),
    )


# ---------------------------------------------------------------- scoring
STOP = {"coin", "token", "the", "solana", "inu", "official", "meme", "with", "this", "that", "and", "for"}


def trend_match(c, trends):
    words = {w for w in re.findall(r"[a-z0-9]{4,}", f"{c.get('name','')} {c.get('symbol','')}".lower()) if w not in STOP}
    for t in trends:
        tl = t.lower().lstrip("#$")
        tw = set(re.findall(r"[a-z0-9]{4,}", tl))
        if words & tw or (len(tl) >= 4 and tl.replace(" ", "") in words):
            return t
    return None


def norm_sym(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def is_blacklisted(c):
    if c["mint"] in BL_MINTS: return True
    s = f"{c.get('name','')} {c.get('symbol','')}".upper().replace(" ", "")
    return any(b.replace(" ", "") in s for b in BL_NAMES)


def score(c):
    x, rc, flags = c["x"], c.get("rc") or {}, []
    b5, s5 = c["m5"]; b1, s1 = c["h1"]
    # X narrative (0-40)
    xs = 0.0
    xs += min(16, 6 * math.log2(1 + x["organic_authors"]))
    xs += min(8, 2 * math.log10(1 + x["organic_eng"]) * 2)
    v = c.get("vel")
    if v and v.get("ratio"):
        xs += min(10, max(0, (v["ratio"] - 1) * 4))
        if v["ratio"] < 0.6 and v["prev_avg"] >= 5: xs -= 5; flags.append(f"X chatter cooling ({v['last']}/h vs {v['prev_avg']}/h)")
    elif x["last1h"]:
        xs += min(6, x["last1h"])
    xs += min(6, math.log10(1 + x["organic_followers"]) * 1.2)
    xs *= (1 - 0.6 * x["shill_share"])
    xs += min(6, 1.5 * math.log2(1 + x["shill_authors"]))  # caller attention: small, capped (paid shills often precede dumps)
    # Market (0-35)
    ms = 0.0
    liq = c["liq"] or 0
    ms += 8 if liq >= 50000 else 6 if liq >= 25000 else 3 if liq >= CFG["min_liquidity_usd"] else 0
    r1 = b1 / max(1, s1); r5 = b5 / max(1, s5)
    ms += max(0, min(9, (r1 - 0.9) * 15)); ms += max(0, min(7, (r5 - 0.9) * 10))
    pc1 = c.get("pc1") or 0; pc5 = c.get("pc5") or 0
    n1 = b1 + s1
    ms = ms * min(1.0, n1 / 400)  # buy pressure only counts with real activity
    if n1 < 150: flags.append(f"no momentum ({n1} trades/1h)")
    ms += 6 if 0 < pc1 <= 150 else 3 if pc1 > 150 else 0
    ms += 5 if pc5 > 0 else 0
    # Safety (0-25)
    ss = 25.0
    sc = rc.get("score")
    if sc is None: ss -= 6; flags.append("RugCheck unavailable")
    elif sc >= 50: ss -= 25; flags.append(f"RugCheck DANGER {sc}")
    elif sc >= 10: ss -= 6; flags.append(f"RugCheck score {sc}")
    for rk in rc.get("risks") or []:
        ss -= 4
    if rc.get("risks"): flags.append("risks: " + ", ".join(rc["risks"][:3]))
    if any("rug" in (r or "").lower() for r in rc.get("risks") or []): flags.append("creator rugged before (RugCheck)")
    if rc.get("top10") is not None and rc["top10"] > 30: ss -= 6; flags.append(f"top10 hold {rc['top10']}%")
    if rc.get("insiders"): ss -= 4; flags.append(f"{rc['insiders']} insider wallets in top20")
    bp = rc.get("bundle_pct") or 0
    if bp >= 10: ss -= 10; flags.append(f"BUNDLE: {rc['bundle_wallets']} linked wallets hold {bp}%")
    elif bp >= 4: ss -= 5; flags.append(f"bundle: {rc['bundle_wallets']} linked wallets hold {bp}%")
    if rc.get("creator_rugged"): ss -= 15; flags.append(f"creator rugged {rc['creator_rugged']} before")
    elif rc.get("creator_tokens", 0) >= 5: ss -= 5; flags.append(f"creator launched {rc['creator_tokens']} tokens")
    if rc.get("mint_auth") or rc.get("freeze_auth"): ss -= 15; flags.append("mint/freeze authority ON")
    if rc.get("rugged"): ss = 0; flags.append("RUGGED")
    # hard flags
    if s5 >= 40 and s5 >= 2.5 * max(1, b5): flags.append(f"DUMP: 5m {b5} buys / {s5} sells")
    if pc1 <= -15: flags.append(f"fading {pc1}% 1h (stale signal)")
    if liq and liq < CFG["min_liquidity_usd"]: flags.append(f"thin liq ${liq/1e3:.0f}K")
    if c.get("fees_est") is not None and c["fees_est"] < CFG["min_fees_sol"] / 2: flags.append(f"est fees ~{c['fees_est']} SOL (<{CFG['min_fees_sol']})")
    if c["boosts"]: flags.append(f"paid DexScreener boost ({c['boosts']})")
    if x["shill_share"] >= 0.6 and x["mentions"] >= 2: flags.append(f"{int(x['shill_share']*100)}% of X posts are paid shills ({', '.join('@'+a for a in x['shill_accts'][:3])})")
    if x["phishing"]: flags.append("phishing posts use its CA")
    if c.get("copycats"): flags.append(f"{c['copycats']} same-name copycats exist")
    if c.get("is_copy"): flags.append("COPYCAT of bigger/older same-name coin")
    if c.get("blacklisted"): flags.append("BLACKLISTED (axiom/filter list)")
    if c.get("trend"): xs += 8; flags.append(f"matches X trend '{c['trend']}' (good)")
    if not c["is_pump"]: flags.append("not a pump.fun mint")
    xs = min(40, xs)
    total = xs + ms + max(0, ss)
    accel = bool(v and v.get("ratio") and v["ratio"] >= 1.5 and v["last"] >= 10)
    if x["organic_authors"] < 3 and not accel and not c.get("trend"):
        total = min(total, 50); flags.append("weak X narrative (<3 organic accounts, no acceleration)")
    elif accel:
        flags.append(f"X mentions accelerating {v['ratio']}x (good)")
    if x["shill_share"] >= 0.7 and x["mentions"] >= 5:
        total = min(total, 60); flags.append("mostly paid-shilled: capped at 60 (CASHTAG lesson)")
    hard = any(f.startswith(("DUMP", "RugCheck DANGER", "RUGGED", "BLACKLISTED", "COPYCAT", "mint/freeze")) or f.startswith("creator rugged") for f in flags)
    if hard: total *= 0.35
    elif any(f.startswith(("fading", "thin liq", "phishing", "no momentum", "BUNDLE")) for f in flags): total *= 0.7
    flags = [f for f in flags if f]
    return round(min(100, total)), dict(x=round(xs, 1), market=round(ms, 1), safety=round(max(0, ss), 1)), flags


def reason(c):
    x, b1, s1 = c["x"], c["h1"][0], c["h1"][1]
    bits = []
    v = c.get("vel")
    if v and v.get("ratio"): bits.append(f"X {v['last']}/h vs {v['prev_avg']}/h avg")
    bits.append(f"{x['organic_authors']} organic / {x['shill_authors']} shill X accts")
    bits.append(f"1h {b1}b/{s1}s, {c.get('pc1')}%")
    bits.append(f"mcap ${(c['mcap'] or 0)/1e3:.0f}K, liq ${(c['liq'] or 0)/1e3:.0f}K, age {c['age_h']}h")
    return "; ".join(bits)


# ---------------------------------------------------------------- dashboard export
def export_dashboard(ranked, coins, raw, blacklisted_seen, hot_themes, out):
    reasons_cfg = {k.upper(): v for k, v in (CFG.get("blacklist_reasons") or {}).items()}
    HARD = ("DUMP", "RugCheck DANGER", "RUGGED", "BLACKLISTED", "COPYCAT", "mint/freeze", "creator rugged")
    rows = []
    for c in ranked:
        rc = c.get("rc") or {}
        flags = c.get("flags") or []
        hard = any(f.startswith(HARD) for f in flags)
        status = "blocked" if hard else ("buy" if c["score"] >= 65 else "watch" if c["score"] >= 50 else "skip")
        v = c.get("vel") or {}
        rows.append(dict(
            name=c["name"], ticker=c["symbol"], mint=c["mint"], pool=c["pool"], dex=c["dex"], url=c["url"],
            score=c["score"], parts=c["parts"], status=status, pump=c["is_pump"],
            ageH=c["age_h"], mcap=round(c["mcap"] or 0), liq=round(c["liq"] or 0), pc1=c.get("pc1"), pc5=c.get("pc5"), pc24=c.get("pc24"),
            b5=c["m5"][0], s5=c["m5"][1], b1=c["h1"][0], s1=c["h1"][1],
            real=c["x"]["organic_authors"], paid=c["x"]["shill_authors"], mentions=c["x"]["mentions"], paidShare=c["x"]["shill_share"],
            paidAccts=c["x"]["shill_accts"], examples=[e["url"] for e in c["x"]["top_organic"] if e.get("url")],
            velNow=v.get("last"), velAvg=v.get("prev_avg"), velRatio=v.get("ratio"),
            bundlePct=rc.get("bundle_pct"), bundleWallets=rc.get("bundle_wallets"), top10=rc.get("top10"),
            rugScore=rc.get("score"), risks=rc.get("risks") or [], holders=rc.get("holders"),
            feesEst=c.get("fees_est"), boosts=c["boosts"], copycats=c.get("copycats", 0), flags=flags, reason=c["reason"],
        ))
    # narratives: every coin with X counts, plus hourly series
    narr = []
    for key, buckets in (raw.get("counts") or {}).items():
        c = coins.get(key) or next((x for x in coins.values() if norm_sym(x["symbol"]) == key and x.get("primary", True)), None)
        if not c: continue
        b = sorted(buckets, key=lambda x: x.get("start", ""))
        series = [dict(label=datetime.fromisoformat(x["start"].replace("Z", "+00:00")).astimezone(MYT).strftime("%-I%p").lower(), value=x.get("tweet_count", 0)) for x in b]
        v = velocity(raw["counts"], key) or {}
        narr.append(dict(theme=c["name"], ticker=c["symbol"], mint=c["mint"], series=series, velNow=v.get("last"), velAvg=v.get("prev_avg"),
                         velRatio=v.get("ratio"), real=c["x"]["organic_authors"], paid=c["x"]["shill_authors"], mentions=c["x"]["mentions"],
                         blacklisted=c.get("blacklisted", False)))
    narr.sort(key=lambda n: -(n["velNow"] or 0))
    blocked = []
    for r in rows:
        hard = [f for f in r["flags"] if f.startswith(HARD) or f.startswith(("BUNDLE", "bundle", "fading"))]
        if hard:
            blocked.append(dict(ticker=r["ticker"], name=r["name"], mint=r["mint"], kind=("bundle" if any("bundle" in f.lower() for f in hard) else "dump" if any(f.startswith(("DUMP", "fading")) for f in hard) else "rug" if any("rug" in f.lower() for f in hard) else "copycat" if any(f.startswith("COPYCAT") for f in hard) else "other"), why="; ".join(hard)))
    for c in coins.values():
        if c.get("blacklisted") and c.get("primary", True):
            key = next((k for k in reasons_cfg if k in (norm_sym(c["symbol"]) + norm_sym(c["name"]))), None)
            blocked.append(dict(ticker=c["symbol"], name=c["name"], mint=c["mint"], kind="blacklist", why=reasons_cfg.get(key, "on blacklist") if key else ("blacklisted mint" if c["mint"] in BL_MINTS else "on blacklist")))
    paper = {}
    try:
        L = json.load(open("/workspace/paper/ledger.json"))
        paper = dict(cash=L.get("cash_rm"), usdcProfit=L.get("usdc_profit_rm"), open=L.get("open_position"),
                     rules=L.get("rules_v2") or "sell half at +10%; sell rest at +20%; stop -10% before half sold; after half sold, stop on rest at breakeven",
                     trades=[dict(symbol=t.get("symbol"), entry=t.get("entry_time"), exit=t.get("exit_time"), size=t.get("size_rm"),
                                  gain=t.get("gain_pct"), pnl=t.get("pnl_rm"), reason=t.get("exit_reason")) for t in L.get("closed_trades", [])])
    except Exception as e:
        paper = {"error": str(e)}
    top = [r for r in rows if r["status"] == "buy" and r["pump"]]
    verdict = (f"Buy-worthy: {', '.join(r['ticker'] for r in top[:3])}" if top else "Nothing worth buying right now")
    D = dict(scanTime=out["generated_myt"], verdict=verdict, xPosts=out["x_posts"], xPaid=out["x_shill_posts"],
             coinsChecked=out["coins_checked"], deepChecked=out["deep_checked"], runtimeS=out["elapsed_s"],
             coins=rows, narratives=narr, themes=[dict(word=w, tickers=t) for w, t in hot_themes], blocked=blocked, paper=paper)
    json.dump(D, open(os.path.join(HERE, "dashboard_data.json"), "w"), indent=1, default=str, ensure_ascii=False)


# ---------------------------------------------------------------- main
def main():
    top_n = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 5
    raw = load_x()
    enriched = analyse_x(raw)
    # seeds from X
    mint_hits = defaultdict(int); tag_hits = defaultdict(int); tag_org = defaultdict(set)
    for e in enriched:
        for m in e["mints"]: mint_hits[m] += 1
        for t in e["tags"]:
            tag_hits[t] += 1
            if not e["shill"]: tag_org[t].add(e["author"])
    # seeds from DexScreener (latest boosts/profiles = what is being promoted right now)
    seeds_ds = set()
    lists = {}
    with ThreadPoolExecutor(4) as ex:
        for k, r in zip(["boost_latest", "boost_top", "profiles"], ex.map(get, [
                "https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1"])):
            lists[k] = r if isinstance(r, list) else []
            for it in lists[k]:
                if it.get("chainId") == "solana" and it.get("tokenAddress", "").endswith("pump"):
                    seeds_ds.add(it["tokenAddress"])
    boosted = {it.get("tokenAddress") for k in ("boost_latest", "boost_top") for it in lists[k]}

    # ticker-only mentions -> DexScreener search (top tickers by organic authors, then raw hits)
    tickers = sorted(tag_hits, key=lambda t: (-len(tag_org[t]), -tag_hits[t]))[:14]
    with ThreadPoolExecutor(8) as ex:
        search_res = dict(zip(tickers, ex.map(ds_search, tickers)))
    # ticker -> all same-symbol solana tokens (for copycat detection)
    sym_groups = defaultdict(dict)
    for t, pairs in search_res.items():
        for p in pairs:
            if (p["baseToken"].get("symbol") or "").upper() == t:
                sym_groups[t].setdefault(p["baseToken"]["address"], []).append(p)

    all_mints = set(mint_hits) | seeds_ds | {k for k in (raw.get("counts") or {}) if len(k) >= 32}
    try:
        all_mints |= {p["mint"] for p in json.load(open(os.path.join(HERE, "candidates.json")))}
    except Exception:
        pass
    for t, g in sym_groups.items(): all_mints |= set(g)
    all_mints = {m for m in all_mints if len(m) >= 32}
    pairs_by_mint = ds_pairs_for_mints(all_mints)
    for t, g in sym_groups.items():
        for m, ps in g.items():
            if m not in pairs_by_mint: pairs_by_mint[m] = ps

    coins = {}
    for m, ps in pairs_by_mint.items():
        s = summarize_token(m, ps)
        if s: coins[m] = s

    # copycat detection: group same-symbol coins; the "narrative coin" is the one X posts name by CA,
    # else the most active (1h txns) with real liquidity, else highest mcap; tie -> earliest.
    by_sym = defaultdict(list)
    for c in coins.values(): by_sym[norm_sym(c["symbol"])].append(c)
    for sym, group in by_sym.items():
        def rank_key(c):
            b1, s1 = c["h1"]
            return (mint_hits.get(c["mint"], 0), c["is_pump"], (b1 + s1) >= 100 and ((c["liq"] or 0) >= 10000 or not c["liq"]),
                    (c["mcap"] or 0), -(c["age_h"] or 0))
        group.sort(key=rank_key, reverse=True)
        orig = group[0]
        orig["primary"] = True
        orig["copycats"] = len(group) - 1
        for c in group[1:]:
            c["primary"] = False
            c["is_copy"] = (c["mcap"] or 0) < 0.5 * (orig["mcap"] or 0) or (c["age_h"] or 0) < (orig["age_h"] or 0)

    for c in coins.values():
        if c.get("primary", True):
            c["x"] = x_stats(enriched, {c["mint"]}, c["symbol"], c["name"])
        else:  # copycats only get posts that name their own CA
            c["x"] = x_stats(enriched, {c["mint"]}, None, None)
        c["vel"] = (velocity(raw.get("counts", {}), c["mint"]) or
                    (velocity(raw.get("counts", {}), (c["symbol"] or "").upper()) if c.get("primary", True) else None))
        c["boosts"] = max(c["boosts"], 1 if c["mint"] in boosted else 0)
        c["blacklisted"] = is_blacklisted(c)
        c["trend"] = trend_match(c, raw.get("trends", []))
        n24 = c["h24"][0] + c["h24"][1]
        c["fees_est"] = round(n24 * CFG["fee_per_trade_sol_est"], 1) if n24 else None

    def pre(c):
        b1, s1 = c["h1"]
        return (c["x"]["organic_authors"] * 3 + c["x"]["mentions"] + (2 if c["vel"] else 0)
                + min(5, (b1 + s1) / 400) + (2 if (c["liq"] or 0) >= 20000 else 0 if not c["liq"] else -3) + (1 if c["is_pump"] else -2))

    blacklisted_seen = sorted({c["symbol"] for c in coins.values() if c["blacklisted"]})
    pool_coins = [c for c in coins.values() if not c["blacklisted"] and c.get("primary", True)
                  and (c["is_pump"] or "--allow-non-pump" in sys.argv)]
    if "--candidates" in sys.argv:
        cands = sorted(pool_coins, key=pre, reverse=True)[:10]
        start6 = (NOW - timedelta(hours=6)).strftime("%Y-%m-%dT%H:00:00Z")
        start3 = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:00Z")
        plan = []
        print(f"For EACH coin below make 2 X calls (namespace x, write_to_file=true):")
        print(f"  A) get_posts_counts_recent query=<COUNT_Q> granularity=hour start_time={start6}  -> python3 ingest.py --counts <KEY> <file>")
        print(f"  B) search_posts_all query='<SEARCH_Q>' start_time={start3} max_results=25 sort_order=relevancy (+fields) -> python3 ingest.py --label ticker <file>")
        for c in cands:
            sym = norm_sym(c["symbol"])
            plain = bool(re.fullmatch(r"[A-Z][A-Z0-9]{4,14}", sym)) and sym not in IGNORE
            cq = sym if plain else c["mint"]
            sq = f"({sym} OR {c['mint']}) -is:retweet" if plain else f"{c['mint']} -is:retweet"
            plan.append(dict(symbol=sym, key=cq, count_query=cq, search_query=sq, mint=c["mint"]))
            print(f"  {sym:12s} KEY/COUNT_Q={cq}  SEARCH_Q='{sq}'  (now: {c['x']['mentions']} mentions, {c['x']['organic_authors']} organic)")
        json.dump(plan, open(os.path.join(HERE, "candidates.json"), "w"), indent=1)
        print(f"[{time.time()-T0:.1f}s]")
        return

    cands = sorted(pool_coins, key=pre, reverse=True)[:CFG["max_candidates"]]
    with ThreadPoolExecutor(8) as ex:
        rcs = list(ex.map(lambda c: rugcheck(c["mint"], [p["pairAddress"] for p in pairs_by_mint[c["mint"]]]), cands))
    for c, rc in zip(cands, rcs):
        c["rc"] = rc
        # DexScreener can't price some quotes (e.g. xStock-quoted pump pools) -> fall back to RugCheck
        if not c["liq"] and rc.get("liq"): c["liq"] = rc["liq"]; c["liq_src"] = "rugcheck"
        if not c["mcap"] and rc.get("mcap"): c["mcap"] = rc["mcap"]; c["mcap_src"] = "rugcheck"
        c["score"], c["parts"], c["flags"] = score(c)
        c["reason"] = reason(c)

    # one coin per narrative: keep best per symbol
    ranked, seen = [], set()
    for c in sorted(cands, key=lambda c: -c["score"]):
        key = norm_sym(c["symbol"])
        if key in seen: continue
        seen.add(key); ranked.append(c)

    # theme words shared by several coins (e.g. a news narrative spawning many tickers)
    themes = defaultdict(set)
    for c in coins.values():
        for w in re.findall(r"[a-z]{4,}", (c["name"] or "").lower()):
            if w not in STOP:
                themes[w].add(c["symbol"])
    hot_themes = sorted([(w, sorted(s)) for w, s in themes.items() if len(s) >= 3], key=lambda x: -len(x[1]))[:6]

    elapsed = round(time.time() - T0, 1)
    out = dict(
        generated_myt=datetime.now(MYT).strftime("%Y-%m-%d %H:%M MYT"), elapsed_s=elapsed,
        x_posts=len(enriched), x_shill_posts=sum(1 for e in enriched if e["shill"]),
        coins_checked=len(coins), deep_checked=len(cands), themes=hot_themes, blacklisted_seen=blacklisted_seen,
        top=[{k: c.get(k) for k in ("score", "parts", "name", "symbol", "mint", "pool", "dex", "url", "age_h", "liq", "mcap",
                                    "vol24", "m5", "h1", "pc5", "pc1", "pc24", "fees_est", "boosts", "reason", "flags", "copycats")}
             | {"x": {k: v for k, v in c["x"].items() if k != "top_organic"}, "vel": c.get("vel"),
                "rugcheck": c.get("rc"), "organic_examples": [e["url"] for e in c["x"]["top_organic"]]}
             for c in ranked[:max(top_n, 10)]],
    )
    json.dump(out, open(os.path.join(HERE, "report.json"), "w"), indent=1, default=str, ensure_ascii=False)
    try:
        export_dashboard(ranked, coins, raw, blacklisted_seen, hot_themes, out)
    except Exception as e:
        print("dashboard export failed:", e)

    L = [f"# Memecoin narrative scan — {out['generated_myt']}",
         f"X posts analysed: {out['x_posts']} ({out['x_shill_posts']} paid-shill/call-group). Coins checked: {out['coins_checked']}, deep-checked: {out['deep_checked']}. Run time {elapsed}s.",
         "Data only, not trade advice. Juliaa gives the final yes.", ""]
    for i, c in enumerate(ranked[:top_n], 1):
        L.append(f"**{i}. {c['name']} (${c['symbol']}) — {c['score']}/100**  ")
        L.append(f"Mint `{c['mint']}` · pool `{c['pool']}` ({c['dex']}) · {c['url']}  ")
        L.append(f"Why: {c['reason']}  ")
        L.append(f"Red flags: {'; '.join(c['flags']) if c['flags'] else 'none found'}")
        L.append("")
    if hot_themes:
        L.append("Themes spawning many tickers: " + "; ".join(f"{w} ({len(s)})" for w, s in hot_themes))
    if blacklisted_seen:
        L.append("Skipped (blacklisted): " + ", ".join(str(x) for x in blacklisted_seen))
    L.append("\nNot checked: honeypot/sell simulation, exact fees paid (estimate = 24h trades x 0.001 SOL), bundle wallets beyond RugCheck insider flags.")
    open(os.path.join(HERE, "report.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
