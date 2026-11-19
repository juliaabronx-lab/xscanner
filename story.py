"""Coin story for xScanner: a short plain-English account of what a coin is and why it is moving (or falling).

Built ONLY from data the app already has or caches (no extra network calls, never blocks a scan):
  * the scanner row: name, ticker, age, price change 5m/1h/24h, buys/sells, volume, liquidity, DexScreener boosts, X mention pace
  * cached DexScreener pair info (website, socials, 6h change, trade counts), GeckoTerminal info (description, socials,
    graduation time), RugCheck token metadata description (all pulled by confidence.py / bundle.py workers)
  * ingested X posts (live feed + last scan posts) that mention the cashtag or mint, and X trends that match the coin.
Nothing is invented: a catalyst is only named when the data shows it; otherwise the story says there is no clear news.
Safety/score talk is deliberately left out; it lives in its own section of the UI.
"""
import json, os, re, time
from datetime import datetime, timezone

B58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
URL = re.compile(r"https?://\S+|\bt\.co/\S+|www\.\S+", re.I)
SPAM = re.compile(r"\b(dm\b|dm me|telegram|\btg\b|vip|signals?|calls?\b|\d+x\b|entry|early entries|join (our|my|the)|follow \+|giveaway|airdrop|gem alert|ca\s*[:>]|is live|just launched|just went live|next 100|moon)", re.I)

THEMES = [  # (regex on name/ticker/description, phrase)
    (r"\b(gta|gta ?6|gta ?vi|grand theft auto|leonida|vice city|rockstar|lucia)\b", "a GTA 6 hype coin"),
    (r"\b(trump|maga|melania|donald)\b", "a Trump / US-politics meme coin"),
    (r"\b(elon|musk|tesla|spacex|doge ?father)\b", "an Elon Musk-themed meme coin"),
    (r"\b(pepe|frog|kermit|peace ?frog)\b", "a frog meme coin"),
    (r"\b(doge|shib|shiba|inu|dog|puppy|bonk|wif)\b", "a dog-themed meme coin"),
    (r"\b(cat|cats|kitty|kitten|meow|mog|popcat)\b", "a cat-themed meme coin"),
    (r"\b(ai|agent|agents|gpt|grok|llm|robot|bot)\b", "an AI-narrative coin"),
    (r"\b(christmas|xmas|wishmas|santa|holiday|advent|elf)\b", "a Christmas / holiday-season coin"),
    (r"\b(thanksgiving|turkey|halloween|pumpkin)\b", "a seasonal-holiday meme coin"),
    (r"\b(world cup|fifa|nfl|nba|messi|ronaldo|football|soccer)\b", "a sports-themed meme coin"),
    (r"\b(duck|hamster|manatee|capybara|panda|bear|bull|monkey|ape|penguin|goat|fish|whale)\b", "an animal meme coin"),
    (r"\b(usa|america|american|eagle)\b", "an America-themed meme coin"),
]
_state = {"sig": None, "posts": [], "trends": []}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _usd(v):
    v = _num(v)
    if v is None: return None
    a = abs(v)
    for d, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= d:
            x = a / d
            s = f"{x:.0f}" if x >= 100 else f"{x:.1f}".rstrip("0").rstrip(".") if x >= 10 else f"{x:.2f}".rstrip("0").rstrip(".")
            return f"${s}{u}"
    return f"${a:.0f}"


def _pct(v):
    v = _num(v)
    if v is None: return None
    a = abs(v)
    s = f"{a:.0f}" if a >= 10 else f"{a:.1f}".rstrip("0").rstrip(".")
    return f"{'+' if v >= 0 else '-'}{s}%"


def _age(h):
    h = _num(h)
    if h is None: return None
    if h < 1: return f"{max(1, round(h * 60))} minutes ago"
    if h < 48: return f"{h:.0f} hours ago" if h >= 2 else "about an hour ago"
    d = h / 24
    return f"{d:.0f} days ago" if d < 60 else f"{d / 30.4:.0f} months ago"


# ------------------------------------------------------------------ ingested X data (cached by file mtime)
def _eng(m):
    m = m or {}
    return (_num(m.get("like_count", m.get("likes"))) or 0) + 2 * (_num(m.get("retweet_count", m.get("reposts"))) or 0) + \
           (_num(m.get("reply_count", m.get("replies"))) or 0) + 2 * (_num(m.get("quote_count", m.get("quotes"))) or 0)


def load_x(feed_path=None, raw_paths=()):
    """-> (posts, trends). posts: [{id, handle, text, eng, t, url, followers}]. Reloaded only when a file changes."""
    paths = [p for p in [feed_path, *raw_paths] if p and os.path.exists(p)]
    sig = tuple((p, os.path.getmtime(p)) for p in paths)
    if sig == _state["sig"]: return _state["posts"], _state["trends"]
    posts, trends, seen = [], [], set()
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh: d = json.load(fh)
        except Exception: continue
        if isinstance(d.get("posts"), list):                       # x_feed.json
            for x in d["posts"]:
                if isinstance(x, dict) and x.get("id") and x["id"] not in seen:
                    seen.add(x["id"])
                    posts.append({"id": x["id"], "handle": x.get("handle") or x.get("author"), "text": x.get("text") or "",
                                  "eng": _eng(x.get("metrics")), "t": x.get("created_at"), "url": x.get("url"), "followers": x.get("followers") or 0})
        elif isinstance(d.get("posts"), dict):                     # x_raw.json
            users = d.get("users") or {}
            for pid, x in d["posts"].items():
                if not isinstance(x, dict) or pid in seen: continue
                seen.add(pid)
                u = users.get(x.get("author_id")) or {}
                posts.append({"id": pid, "handle": u.get("username"), "text": x.get("text") or "", "eng": _eng(x.get("public_metrics")),
                              "t": x.get("created_at"), "url": x.get("url"), "followers": (u.get("public_metrics") or {}).get("followers_count") or 0})
            trends += [t for t in (d.get("trends") or []) if isinstance(t, str)]
    _state.update(sig=sig, posts=posts, trends=list(dict.fromkeys(trends)))
    return _state["posts"], _state["trends"]


MAJORS = {"SOL", "ETH", "BTC", "DOT", "BNB", "XRP", "DOGE", "USDC", "USDT", "AVAX", "ADA", "LINK", "TRX", "TON", "SHIB", "PEPE", "WIF", "BONK"}


def patterns(c):
    """Regexes that prove a post is about this coin. Short or famous tickers ($UP, $DOT, $SOL) are too ambiguous: mint only."""
    sym, name = (c.get("symbol") or "").strip(), (c.get("name") or "").strip()
    pats = []
    if sym and len(sym) >= 4 and sym.upper() not in MAJORS:
        pats.append(re.compile(r"(?<![A-Za-z0-9])\$" + re.escape(sym) + r"(?![A-Za-z0-9])", re.I))
        if sym.isalpha() and len(sym) >= 5: pats.append(re.compile(r"(?<![A-Za-z0-9$#@])" + re.escape(sym) + r"(?![A-Za-z0-9])", re.I))
    elif sym and len(sym) == 3 and sym.upper() not in MAJORS:       # 3-letter cashtags only when written in capitals
        pats.append(re.compile(r"(?<![A-Za-z0-9])\$" + re.escape(sym.upper()) + r"(?![A-Za-z0-9])"))
    if len(name) >= 6 and name.lower() != sym.lower(): pats.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I))
    return pats


def match_posts(c, posts):
    mint, pats = c.get("mint") or "", patterns(c)
    return [p for p in posts if (mint and mint in p["text"]) or any(r.search(p["text"]) for r in pats)]


def _clean(text, limit=95):
    t = URL.sub("", text or "")
    t = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200d]", "", t)
    t = B58.sub("", t)
    t = re.sub(r"(?:^|\s)[#@]\w+", " ", t)           # drop hashtags and @mentions inside the excerpt
    t = re.sub(r"\s+", " ", t).strip(" \n-–—:|")
    t = t.replace("\u201c", "'").replace("\u201d", "'").replace('"', "'")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
    return t


def _excerpt(text, pats, mint, limit=95):
    """Clean excerpt that starts near, and contains, the mention of the coin (None if the cleaned text no longer names it)."""
    t = _clean(text, 600)
    pos = None
    for r in pats:
        m = r.search(t)
        if m and (pos is None or m.start() < pos): pos = m.start()
    if pos is None: return None
    start = max([0] + [i + 2 for i in (t.rfind(". ", 0, pos), t.rfind("! ", 0, pos), t.rfind("? ", 0, pos), t.rfind("\n", 0, pos)) if i >= 0])
    if pos - start > limit - 35:                       # match would be cut off: start a few words before it
        start = t.rfind(" ", 0, max(0, pos - 30)) + 1
    t = t[start:].strip()
    if len(t) > limit: t = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
    return t


def highlights(posts, paid, c=None, n=2):
    paid = {str(p).lower().lstrip("@") for p in (paid or [])}
    pats = patterns(c or {})
    good = []
    for p in posts:
        h = (p.get("handle") or "").lower()
        if not h or h in paid or SPAM.search(p["text"]): continue
        t = _excerpt(p["text"], pats, None)
        if not t or len(t) < 25 or sum(ch.isascii() for ch in t) < 0.9 * len(t): continue   # English, names the coin
        good.append((p["eng"], p.get("followers") or 0, p, t))
    good.sort(key=lambda g: (-g[0], -g[1]))
    out, seen = [], set()
    for _, _, p, t in good:
        k = t[:30].lower()
        if k in seen: continue
        seen.add(k); out.append({"handle": p["handle"], "text": t, "url": p.get("url")})
        if len(out) >= n: break
    return out


def theme_of(c, desc):
    blob = " ".join([c.get("name") or "", c.get("symbol") or "", desc or ""]).lower()
    for rx, phrase in THEMES:
        if re.search(rx, blob): return phrase, rx
    return None, None


def match_trend(c, rx, trends):
    toks = {t.lower() for t in re.findall(r"[A-Za-z0-9]{4,}", (c.get("name") or "") + " " + (c.get("symbol") or ""))}
    for t in trends or []:
        tl = t.lower().lstrip("#")
        if (rx and re.search(rx, tl)) or any(k in tl for k in toks): return t
    return None


def _first_sentence(d, limit=110):
    t = _clean(d, 400)
    m = re.match(r"(.{20,}?[.!?])(\s|$)", t)
    t = m.group(1) if m else t
    if len(t) > limit: t = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
    return t.rstrip(".") if not t.endswith("…") else t


def _plural(n, w):
    n = int(n)
    return f"{n:,} {w}" + ("" if n == 1 else "s")


def _q(t):
    return "\u201c" + t.rstrip(".!,;: ") + ("…" if t.endswith("…") else "") + "\u201d" if not t.endswith("…") else "\u201c" + t + "\u201d"


def build(c, ds=None, gt=None, rc_meta=None, posts=(), trends=()):
    """-> {"text": "2-4 sentences", "short": "one line", ...}. Never raises, never invents."""
    ds, gt, rc_meta = ds or {}, gt or {}, rc_meta or {}
    x = c.get("x") or {}
    sym, name = c.get("symbol") or "This coin", (c.get("name") or "").strip()
    raw_desc = next((d for d in (gt.get("desc"), rc_meta.get("desc")) if isinstance(d, str) and len(d.strip()) >= 20), None)
    desc = _first_sentence(raw_desc) if raw_desc else None
    if desc and len(desc) < 20: desc = None
    theme, rx = theme_of(c, raw_desc)
    age_h = _num(c.get("age_h")); pump = bool(c.get("pump")); dex = (c.get("dex") or "").lower()
    grad = pump and dex in ("pumpswap", "raydium", "meteora")
    pc5, pc1, pc24, pc6 = _num(c.get("pc5")), _num(c.get("pc1")), _num(c.get("pc24")), _num(ds.get("pc6"))
    b1, sl1 = _num(c.get("buys1h")), _num(c.get("sells1h"))
    vol, liq = _num(c.get("vol24")), _num(c.get("liq"))
    ratio = (b1 / max(sl1, 1)) if (b1 is not None and sl1 is not None) else None
    # ---- state of the move
    hot = (pc1 or 0) >= 10 or ((pc24 or 0) >= 50 and (pc1 or 0) >= -3 and (pc6 is None or pc6 >= 0))
    faded = (pc24 or 0) >= 50 and not hot and ((pc1 or 0) < -3 or (pc6 is not None and pc6 < 0))
    down = not hot and not faded and (pc1 or 0) < -5
    up = not hot and not faded and not down and ((pc1 or 0) > 2 or (pc24 or 0) > 20)
    # ---- sentence 1: what it is
    who = f"${sym}" + (f" ({name})" if name and name.lower() != sym.lower() else "")
    kind = theme or ("a pump.fun memecoin" if pump else "a Solana memecoin")
    s1 = f"{who} is {kind}"
    if theme and pump: s1 += " from pump.fun"
    if age_h is not None and age_h < 72: s1 += f", launched {_age(age_h)}" + (" and already graduated to PumpSwap" if grad and dex == "pumpswap" else "")
    elif grad: s1 += ", now trading on " + ("PumpSwap" if dex == "pumpswap" else dex.capitalize())
    if desc: s1 += f", pitched as \u201c{desc}\u201d"
    else:
        links = []
        if ds.get("websites") or gt.get("websites"): links.append("a website")
        soc = {str(z).lower() for z in (ds.get("socials") or [])}
        if "twitter" in soc or gt.get("twitter"): links.append("an X account")
        if links: s1 += ", with " + " and ".join(links)
    s1 += "."
    # ---- sentence 2: the move
    s2 = ""
    bits = []
    if pc5 is not None and abs(pc5) >= 1: bits.append(f"{_pct(pc5)} in 5 minutes")
    if pc1 is not None: bits.append(f"{_pct(pc1)} in the past hour")
    if pc6 is not None and pc1 is not None and abs(pc6) >= 5: bits.append(f"{_pct(pc6)} over 6 hours")
    elif pc24 is not None: bits.append(f"{_pct(pc24)} over 24 hours")
    if bits:
        s2 = "The price is " + (", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else bits[0])
        if ratio is not None and b1 + sl1 >= 20:
            s2 += f", and buyers {'lead' if ratio >= 1.4 else 'trail' if ratio <= 0.7 else 'roughly match'} sellers ({int(b1):,} buys to {int(sl1):,} sells in the past hour)"
        tx1, tx24 = _num(ds.get("tx_h1")), _num(ds.get("tx_h24"))
        if tx1 and tx24 and tx1 / (tx24 / 24) >= 2: s2 += f"; trading in the last hour is {tx1 / (tx24 / 24):.1f}x its 24-hour average"
        elif vol and liq and vol / liq >= 3: s2 += f"; about {_usd(vol)} changed hands in 24 hours, around {vol / liq:.0f}x the pool"
        s2 += "."
    # ---- sentence 3: catalysts
    matched = match_posts(c, posts)
    hl = highlights(matched, c.get("paid_accounts") or x.get("shill_accts"), c=c, n=1)
    boosts = int(_num(c.get("boosts")) or 0)
    ment = _num(x.get("mentions")); vel = c.get("vel") or {}
    vl, va = _num(vel.get("last")), _num(vel.get("prev_avg"))
    trend = match_trend(c, rx, trends)
    cats, why, weak_x, fade = [], [], 0, None
    if boosts > 0: cats.append("a paid DexScreener boost is putting it in front of more traders"); why.append("a paid DexScreener boost")
    n_x = int(ment) if ment else len(matched)
    if n_x:
        if vl is not None and va and va > 0 and vl / va >= 1.3:
            cats.append(f"X chatter is picking up (about {vl:.0f} {'post' if round(vl) == 1 else 'posts'} an hour versus {va:.0f} before)"); why.append("rising X chatter")
        elif vl is not None and va and va > 0 and vl / va <= 0.7:
            fade = f"X chatter is fading (about {vl:.0f} {'post' if round(vl) == 1 else 'posts'} an hour versus {va:.0f} before)"
        elif n_x >= 10:
            cats.append(f"{_plural(n_x, 'X post')} mention it"); why.append(_plural(n_x, "X post"))
        else:
            weak_x = n_x
        if cats and n_x and (_num(x.get("shill_share")) or 0) >= 0.5 and "X" in cats[-1]: cats[-1] += ", though most of those accounts look like paid promoters"
    if trend: cats.append(f"it overlaps with the X trend \u201c{trend}\u201d"); why.append("a matching X trend")
    clear = bool(cats) or bool(hl)
    if hl and not any("X" in w for w in why): why.append("chatter on X")
    s3 = ""
    if cats:
        body = " and ".join(cats[:2]) if len(cats) <= 2 else ", ".join(cats[:-1]) + " and " + cats[-1]
        s3 = body[0].upper() + body[1:] + "."
    if fade and cats: s3 = s3.rstrip(".") + "; " + fade[0].lower() + fade[1:] + "."
    if hl: s3 = (s3 + " " if s3 else "") + f"Top post: @{hl[0]['handle']} wrote {_q(hl[0]['text'])}."
    if not clear:
        busy = (b1 is not None and b1 + (sl1 or 0) >= 100) or (vol and liq and vol / max(liq, 1) >= 2)
        wx = (f", with only {_plural(weak_x, 'X post')} mentioning it" if weak_x else "") + (f"; {fade[0].lower() + fade[1:]}" if fade else "")
        s3 = "No clear news or catalyst found" + ("; the move looks driven by trading volume" if (hot or faded or down or up) and busy else "; trading is quiet and nothing in the data explains a move") + wx + "."
    parts = [p for p in (s1, s2, s3) if p]
    # ---- card line
    if hot: head = "Pumping" + (f": {_pct(pc1)} in 1h" if (pc1 or 0) >= 10 else f": {_pct(pc24)} on the day")
    elif faded: head = f"Cooling off after a {_pct(pc24)} run" + (f" ({_pct(pc1)} in 1h)" if pc1 is not None else "")
    elif down: head = f"Falling: {_pct(pc1)} in 1h"
    elif up: head = f"Drifting up: {_pct(pc1) if (pc1 or 0) > 2 else _pct(pc24) + ' on the day'}"
    else: head = "Quiet" + (f": {_pct(pc1)} in 1h" if pc1 is not None else "")
    rs = []
    if ratio is not None and b1 + sl1 >= 20: rs.append("buyers lead sellers" if ratio >= 1.4 else "sellers lead buyers" if ratio <= 0.7 else "")
    if theme: rs.append("rides " + re.sub(r"^an? ", "", theme).replace(" coin", "").replace("a GTA 6 hype", "GTA 6 hype"))
    rs += why[:2]
    rs = [r for r in rs if r]
    short = head + (" \u2014 " + ", ".join(rs[:3]) if clear and rs else (" \u2014 no clear news" + ("; looks volume-driven" if (hot or faded or down or up) and (rs or True) else "") if not clear else ""))
    return {"text": " ".join(parts), "short": short + ".", "sentences": parts, "theme": theme, "highlights": hl,
            "x_matches": len(matched), "catalyst": clear}
