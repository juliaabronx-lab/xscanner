"""Plain, server-rendered views for crawlers, AI tools and text browsers: /robots.txt, /sitemap.xml, /llms.txt, /text.
No JavaScript, no external calls: everything is built from the already-computed coin list (app.build_state) and is public market data.
Every dynamic value is HTML-escaped (token names, flags and X text come from outside)."""
import html, time
from datetime import datetime, timedelta, timezone

MYT = timezone(timedelta(hours=8))
SITE = "xScanner"
TAGLINE = "Free, read-only Solana / pump.fun memecoin scanner: scores, rug and bundle risk, data confidence, charts. Analysis only, no trading."
ENDPOINTS = [
    ("/", "Interactive app (JavaScript). Coin feed, paste-a-CA scanner, charts, risk radar."),
    ("/text", "This page's server-rendered view: every coin in the latest scan as plain HTML, no JavaScript. Add ?format=txt for markdown text."),
    ("/llms.txt", "This file."),
    ("/api/summary", "JSON: scan time, counts per tier, verdict, themes, live-scan status."),
    ("/api/coins", "JSON: the coin list (slim). Query: min_score, min_liq, pump_only, hide_paid, sort, desc, full=1 for every nested field."),
    ("/api/coins/{mint}", "JSON: one coin from the latest scan with story, bundle detail, data-confidence detail, RugCheck and X data."),
    ("/api/lookup/{mint}", "JSON: score any Solana token by its mint address (live scan if it is not in the feed; rate limited to about 10 per minute)."),
    ("/api/ohlcv/{pool}?tf=5m&token={mint}", "JSON: candles for a pool. tf = 1m, 5m, 15m, 1h, 4h, 1d. Fields: candles[{t,o,h,l,c,v}], source, limited, n_candles."),
    ("/pulse", "Pulse board (JavaScript): live New pairs / Final stretch / Migrated columns for pump.fun tokens with filters and presets. Token pages live at /token/{mint}."),
    ("/api/pulse?since={ver}&h={hash}", "JSON: the Pulse board, incremental (rows changed since `ver`). Fields per row: mint m, symbol s, name n, mcap mc, liquidity lq, volume v1/v24, buys/sells b1/s1, holders h, top-10 % t10, dev % dv, curve % cv (cvs = chain or est), stage st = new / final / migrated, bundle bd, sniper % sn. Missing fields are unknown, not safe."),
    ("/api/token/{mint}", "JSON: trader token page data: market, security (mint / freeze authority, LP, top-10, dev, insiders), holders table, bonding curve %, early-block snipers / same-block bundle, deep links (Jupiter / Axiom / Photon open the other site; xScanner never trades)."),
    ("/api/trades/{pool}", "JSON: last trades of a pool (live tape) and top traders summed from them."),
    ("/api/dev/{wallet}", "JSON: other pump.fun tokens a dev wallet launched and how they ended (background job: poll while status is pending)."),
    ("/api/wallet/{addr}", "JSON: watch-only recent token buys and sells of a public wallet (poll while status is pending)."),
    ("/api/quotes?mints=a,b", "JSON: prices for up to 30 mints (watchlist)."),
    ("/api/narratives", "JSON: X narratives and themes for the coins in the scan."),
    ("/api/risk", "JSON: risk-radar axes (paid shill, copycat, bundle, dump, rug, thin liquidity) per coin."),
    ("/api/health", "JSON: liveness, uptime, scan loop status."),
    ("/api/docs", "OpenAPI / Swagger UI for the JSON API."),
]


def _abs(base, path):
    return (base.rstrip("/") + path) if base else path


def robots(base=None):
    out = "User-agent: *\nAllow: /\n"
    if base:
        out += f"\nSitemap: {_abs(base, '/sitemap.xml')}\n"
    return out


def sitemap(base=None):
    now = datetime.now(MYT).strftime("%Y-%m-%d")
    urls = ["/", "/pulse", "/text", "/llms.txt"]
    rows = "".join(f"<url><loc>{html.escape(_abs(base, u))}</loc><lastmod>{now}</lastmod><changefreq>hourly</changefreq></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'


def llms(base=None):
    L = [f"# {SITE}", "", f"> {TAGLINE}", "",
         "xScanner scans new and trending Solana tokens every ~2 minutes and scores each one 0-100 on X narrative (40), market (35) and safety (25), "
         "then adds a bundle / insider-cluster check (RugCheck + on-chain holder analysis) and a data-confidence check that cross-references DexScreener, "
         "GeckoTerminal, RugCheck and Solana RPC. Verdicts: **Worth a look** (score 65+), **Watch** (50-64), **Skip** (below 50) and **Blocked (hard flag)**. "
         "Missing data is never treated as safe: an unavailable bundle check or low data confidence caps the score.", "",
         "## Open to everyone", "",
         "- No login, no API key, GET only. Crawlers and AI tools are welcome (see /robots.txt).",
         "- Optional accounts (/login: username or email + password) only sync a person's own watchlist and saved wallets across devices. Every page and API listed here stays public; /api/auth/* and /api/user/data are for the web app and are not needed to read data.",
         "- The main page is JavaScript-rendered. Use the plain views below, which need no JavaScript.", "",
         "## Read this first", ""]
    L += [f"- [{_abs(base, '/text')}]({_abs(base, '/text')}): every coin in the latest scan as readable HTML (name, ticker, mint, verdict, score, why it is moving, bundle, confidence).",
          f"- [{_abs(base, '/text?format=txt')}]({_abs(base, '/text?format=txt')}): the same as markdown text.",
          f"- [{_abs(base, '/api/coins')}]({_abs(base, '/api/coins')}): the same data as JSON.", "", "## Endpoints", ""]
    L += [f"- `{p}`: {d}" for p, d in ENDPOINTS]
    L += ["", "## Notes", "",
          "- Read-only market analysis. Not financial advice. Scores can be wrong or stale; memecoins are extremely risky.",
          "- Data comes from free public APIs that rate-limit (DexScreener, GeckoTerminal, RugCheck, Jupiter, Solana RPC). Fields can be `null` or marked unavailable; that means unknown, not safe.",
          "- Times are Malaysia time (MYT, UTC+8) unless a field says otherwise.",
          "- The site runs on a free host and may take up to ~30 seconds to answer after a long idle period; retry once.", ""]
    return "\n".join(L)


def _s(v):
    return html.escape("" if v is None else str(v))


def _coin_rows(coins, usd, pct):
    rows = []
    for c in coins:
        b, d = c.get("bundle") or {}, c.get("data_confidence") or {}
        probs = {**(d.get("warnings") or {}), **(d.get("problems") or {})}
        if b.get("linked_wallets") and b.get("status") in ("ok", "elevated", "flagged"):
            share = f"{b['share_pct']}%" if b.get("share_pct") is not None else "unknown share"
            cluster = f"{b['linked_wallets']} ({b.get('basis') or 'cluster'}), {share}"
        else:
            cluster = None
        rows.append({
            "name": c.get("name") or c.get("symbol") or "?", "symbol": c.get("symbol") or "", "mint": c.get("mint") or "",
            "verdict": c.get("verdict") or "", "score": c.get("score"), "tier": c.get("tier"), "status": c.get("status"),
            "why": c.get("story_short") or c.get("story") or "", "story": c.get("story") or "",
            "mcap": usd(c.get("mcap")), "liq": usd(c.get("liq")), "vol24": usd(c.get("vol24")), "pc1": pct(c.get("pc1")), "pc24": pct(c.get("pc24")),
            "age": c.get("age_h"), "pump": bool(c.get("pump")),
            "bundle_status": b.get("status") or "unknown", "bundle_reason": b.get("reason") or "", "cluster": cluster,
            "conf_level": d.get("level") or "unknown", "conf_score": d.get("score"), "conf_label": d.get("label") or "",
            "conf_notes": "; ".join(f"{k}: {v}" for k, v in probs.items()), "uncertain": ", ".join(d.get("uncertain") or []),
            "flags": [f for f in (c.get("flags") or []) if f], "url": c.get("url") or "", "pool": c.get("pool") or ""})
    return rows


def _summary_lines(s, n):
    tiers = s.get("tiers") or {}
    return [("Scan time", s.get("generated") or "unknown"), ("Verdict", s.get("verdict") or "n/a"), ("Coins in this scan", n),
            ("Worth a look / Watch / Skip", f"{tiers.get('look', 0)} / {tiers.get('watch', 0)} / {tiers.get('skip', 0)}"),
            ("Coins checked / deep-checked", f"{s.get('coins_checked')} / {s.get('deep_checked')}")]


def render_text(coins, summary, usd, pct, base=None, fmt="html"):
    """-> (body, content_type). One section per coin; plain HTML (default) or markdown text (fmt='txt')."""
    rows = _coin_rows(coins, usd, pct)
    now = datetime.now(MYT).strftime("%Y-%m-%d %H:%M MYT")
    lines = _summary_lines(summary, len(rows))
    if fmt == "txt":
        L = [f"# {SITE}: latest scan", "", f"> {TAGLINE}", "", f"Page generated {now}.", ""] + [f"- **{k}:** {v}" for k, v in lines] + [""]
        for i, r in enumerate(rows, 1):
            L += [f"## {i}. {r['name']} (${r['symbol']})", "",
                  f"- Mint: `{r['mint']}`", f"- Verdict: **{r['verdict']}**, score {r['score']}/100" + (f" ({r['status']})" if r["status"] else ""),
                  f"- Why it's moving: {r['why'] or 'n/a'}",
                  f"- Market cap {r['mcap']}, liquidity {r['liq']}, 24h volume {r['vol24']}, 1h {r['pc1']}, 24h {r['pc24']}",
                  f"- Bundle check: {r['bundle_status']}: {r['bundle_reason']}" + (f" Cluster: {r['cluster']}." if r["cluster"] else ""),
                  f"- Data confidence: {r['conf_level']} ({r['conf_score']}/100)" + (f"; {r['conf_notes']}" if r["conf_notes"] else "")]
            if r["flags"]: L.append("- Flags: " + " | ".join(r["flags"]))
            if r["url"]: L.append(f"- DexScreener: {r['url']}")
            L.append("")
        L += ["Not financial advice. Analysis only.", ""]
        return "\n".join(L), "text/plain; charset=utf-8"
    h = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
         f"<title>{SITE} · latest Solana memecoin scan ({_s(summary.get('generated') or now)})</title>",
         f"<meta name=\"description\" content=\"{_s(TAGLINE)} {len(rows)} coins in the latest scan, scored with bundle and data-confidence checks.\">",
         "<meta name=\"robots\" content=\"index,follow\">",
         f"<link rel=\"alternate\" type=\"text/plain\" href=\"{_s(_abs(base, '/llms.txt'))}\" title=\"llms.txt\">",
         "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#111}h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem;border-top:1px solid #ccc;padding-top:1rem}"
         "code{font-size:.85em;word-break:break-all}dt{font-weight:600;margin-top:.4rem}dd{margin:0 0 .2rem 0}.v{font-weight:700}</style></head><body>",
         f"<h1>{SITE}: latest Solana memecoin scan</h1><p>{_s(TAGLINE)}</p>",
         f"<p>Plain-HTML view, no JavaScript needed. Interactive app: <a href=\"{_s(_abs(base, '/'))}\">/</a>. JSON: <a href=\"{_s(_abs(base, '/api/coins'))}\">/api/coins</a>. "
         f"Site guide for AI tools: <a href=\"{_s(_abs(base, '/llms.txt'))}\">/llms.txt</a>. Markdown version of this page: <a href=\"{_s(_abs(base, '/text?format=txt'))}\">/text?format=txt</a>.</p>",
         "<h2 style=\"border:0\">Summary</h2><ul>"]
    h += [f"<li><b>{_s(k)}:</b> {_s(v)}</li>" for k, v in lines] + [f"<li><b>Page generated:</b> {_s(now)}</li></ul>"]
    for i, r in enumerate(rows, 1):
        h.append(f"<article><h2>{i}. {_s(r['name'])} (${_s(r['symbol'])})</h2><dl>")
        h.append(f"<dt>Mint (contract address)</dt><dd><code>{_s(r['mint'])}</code></dd>")
        h.append(f"<dt>Verdict and score</dt><dd><span class=\"v\">{_s(r['verdict'])}</span>, score {_s(r['score'])}/100" + (f" ({_s(r['status'])})" if r["status"] else "") + "</dd>")
        h.append(f"<dt>Why it's moving</dt><dd>{_s(r['why'] or 'n/a')}</dd>")
        h.append(f"<dt>Market</dt><dd>Market cap {_s(r['mcap'])}, liquidity {_s(r['liq'])}, 24h volume {_s(r['vol24'])}, 1h {_s(r['pc1'])}, 24h {_s(r['pc24'])}</dd>")
        h.append(f"<dt>Bundle / insider check</dt><dd>{_s(r['bundle_status'])}: {_s(r['bundle_reason'])}" + (f" Cluster: {_s(r['cluster'])}." if r["cluster"] else "") + "</dd>")
        h.append(f"<dt>Data confidence</dt><dd>{_s(r['conf_level'])} ({_s(r['conf_score'])}/100)" + (f". {_s(r['conf_notes'])}" if r["conf_notes"] else "") + "</dd>")
        if r["flags"]: h.append("<dt>Flags</dt><dd>" + " &middot; ".join(_s(f) for f in r["flags"]) + "</dd>")
        if r["url"].startswith("https://"): h.append(f"<dt>Links</dt><dd><a href=\"{_s(r['url'])}\" rel=\"nofollow noopener\">DexScreener</a></dd>")
        h.append("</dl></article>")
    if not rows: h.append("<p>No coins in the latest scan yet. Try again in a minute.</p>")
    h.append("<p><small>Not financial advice. Analysis only; memecoins are extremely risky. Missing data means unknown, not safe.</small></p></body></html>")
    return "".join(h), "text/html; charset=utf-8"
