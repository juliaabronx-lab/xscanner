"""fix19: public-launch trust pages (Terms, Privacy, Risk disclaimer), a friendly HTML 404 / 500 page and
/.well-known/security.txt. Plain server-rendered HTML, no scripts, no third-party requests."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

UPDATED = "28 November 2026"

_CSS = """:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#06070b;color:#d7d9e0;font:15px/1.6 Geist,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
@font-face{font-family:Geist;font-weight:100 900;font-display:swap;src:url(/static/fonts/geist-latin.woff2) format("woff2")}
main{max-width:760px;margin:0 auto;padding:28px 18px 60px}a{color:#2fe3ac}h1{font-size:26px;margin:18px 0 4px;color:#fff}h2{font-size:17px;margin:26px 0 6px;color:#fff}
.top{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:13px}.top b{color:#fff;font-size:15px}.muted{color:#8b8fa3;font-size:13px}
.tag{display:inline-block;border:1px solid rgba(47,227,172,.45);color:#2fe3ac;border-radius:999px;padding:1px 9px;font-size:11px;font-weight:700;letter-spacing:.05em}
.box{border:1px solid #23262f;background:#0d0f14;border-radius:10px;padding:12px 14px;margin:14px 0}li{margin:4px 0}
footer{margin-top:40px;border-top:1px solid #1c1f27;padding-top:14px;font-size:13px;color:#8b8fa3}"""

NAV = ('<nav class="top"><a href="/"><b>xScanner</b></a><span class="tag">PAPER · SIMULATION · NFA</span>'
       '<a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/disclaimer">Risk disclaimer</a></nav>')
FOOT = ('<footer>xScanner is a read-only market scanner with a paper-trading simulator. Not financial advice. '
        'No wallet keys, no signing, no real trades. <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> · '
        '<a href="/disclaimer">Risk disclaimer</a></footer>')


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} · xScanner</title><meta name="robots" content="index,follow"><link rel="icon" href="/static/logo.svg">'
            f'<style>{_CSS}</style></head><body><main>{NAV}{body}{FOOT}</main></body></html>')
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "public, max-age=600" if status == 200 else "no-store"})


TERMS = f"""<h1>Terms of use</h1><p class="muted">Last updated {UPDATED}</p>
<div class="box"><b>Short version:</b> xScanner shows public market data about Solana tokens and lets you practise with play money.
It never touches real funds, never asks for a wallet or seed phrase, and nothing here is financial advice.</div>
<h2>1. What the service is</h2><p>xScanner is an informational dashboard. It reads public data (for example DexScreener, GeckoTerminal,
Jupiter, RugCheck, the Solana RPC and public posts on X) and shows scores, charts and risk flags. Paper trading is a
<b>simulation</b>: balances are play money ("paper SOL") with no cash value and cannot be withdrawn, sold or redeemed.</p>
<h2>2. No financial advice</h2><p>Nothing on xScanner is investment, financial, legal or tax advice, or a recommendation to buy or sell anything.
Scores and flags are automated and can be wrong, late or incomplete. You are solely responsible for any real-world decision you make.</p>
<h2>3. Your account</h2><ul><li>You must be old enough to form a binding contract where you live (at least 18 in most places).</li>
<li>Keep your password private and use one you do not use anywhere else. You are responsible for activity on your account.</li>
<li>Never enter a wallet seed phrase or private key anywhere on xScanner. We will never ask for one.</li></ul>
<h2>4. Acceptable use</h2><ul><li>No scraping or automated requests that degrade the service (rate limits apply and return HTTP 429).</li>
<li>No attempts to break security, access other people's data, or overload upstream data providers through xScanner.</li>
<li>No use of xScanner to promote scams, pump-and-dump schemes or market manipulation.</li></ul>
<h2>5. Availability and data</h2><p>The service is provided "as is" and "as available", without warranties of any kind. It may be slow,
unavailable or reset; paper balances and account data can be lost. Third-party data belongs to its providers.</p>
<h2>6. Liability</h2><p>To the fullest extent the law allows, xScanner and its operator are not liable for any loss or damage arising from
use of the service or reliance on its data, including trading losses.</p>
<h2>7. Changes and termination</h2><p>We may change these terms or the service, or suspend accounts that break these terms. You can delete
your account at any time from your account menu. Continued use after a change means you accept the updated terms.</p>"""

PRIVACY = f"""<h1>Privacy policy</h1><p class="muted">Last updated {UPDATED}</p>
<div class="box"><b>Short version:</b> you can use xScanner without an account. If you create one we store only what is needed to sign you in
and sync your lists. No ads, no trackers, no selling of data. You can download or delete your data at any time.</div>
<h2>What we collect</h2><ul>
<li><b>Account (optional):</b> username, optional email, and your password as an argon2id hash (we never store or see the password itself).</li>
<li><b>Synced data (optional):</b> your watchlist, watched public wallet addresses, Pulse filter presets and your paper-trading book.</li>
<li><b>Session cookie:</b> one signed, HttpOnly cookie (<code>xs_session</code>) that keeps you signed in and protects forms (CSRF). It is strictly necessary.</li>
<li><b>Your browser's local storage:</b> preferences and a guest paper book are kept on your device, not on our server, until you sign in.</li>
<li><b>Technical logs:</b> our host (Render) and its network edge (Cloudflare) process IP addresses and request logs for security and rate limiting.</li></ul>
<h2>What we do not do</h2><ul><li>No advertising or analytics trackers, no selling or renting of personal data.</li>
<li>No wallets, private keys, seed phrases or real transactions, ever.</li></ul>
<h2>Third parties your browser talks to</h2><p>Charts may load candles directly from GeckoTerminal, the optional chart embed comes from DexScreener,
and token logos are shown from their public image hosts. Those services can see your IP address, as with any website.</p>
<h2>Your choices</h2><ul><li><b>Download your data:</b> signed in, open <a href="/api/user/export">/api/user/export</a> (JSON).</li>
<li><b>Delete your account:</b> from the account menu ("Delete account"). This erases your account, synced lists and paper book.</li>
<li><b>Sign out everywhere:</b> from the account menu, or change your password, to end every other session.</li></ul>
<h2>Retention and security</h2><p>Account data is kept until you delete it. Passwords are hashed with argon2id; traffic is HTTPS only (HSTS);
sign-in is rate-limited against guessing. No system is perfectly secure, so use a unique password.</p>
<h2>Contact</h2><p>Questions or a security report: open an issue on the project's GitHub page or contact the site operator.</p>"""

DISCLAIMER = f"""<h1>Risk disclaimer</h1><p class="muted">Last updated {UPDATED}</p>
<div class="box"><b>Memecoins are extremely risky.</b> Most new tokens go to zero. Rug pulls, honeypots, bundled supply, fake volume and
coordinated shilling are common. Never risk money you cannot afford to lose.</div>
<ul><li><b>Not financial advice.</b> xScanner's scores, verdicts and flags are automated opinions from public data, not recommendations.</li>
<li><b>Data can be wrong.</b> Prices, market caps, holder counts and risk checks come from third parties and can be delayed, manipulated or missing.</li>
<li><b>Paper trading is a simulation.</b> Simulated fills ignore real liquidity, MEV, failed transactions and many fees; real results will differ.</li>
<li><b>No custody.</b> xScanner never holds funds or keys and cannot execute trades. Anyone asking for your seed phrase "from xScanner" is a scammer.</li>
<li><b>Do your own research.</b> Check the contract, liquidity, holders and team yourself before any real trade.</li></ul>"""

_ERR = {404: ("Page not found", "That page does not exist (or the link is broken)."),
        405: ("Not allowed", "That page cannot be opened this way."),
        500: ("Something went wrong", "An unexpected error happened on our side. Please try again in a moment.")}


def error_page(status: int) -> HTMLResponse:
    t, msg = _ERR.get(status, _ERR[500])
    return page(t, f'<h1>{t}</h1><p>{msg}</p><p><a href="/">← Back to the scanner</a> · <a href="/pulse">Pulse</a></p>', status)


SECURITY_TXT = """Contact: https://github.com/juliaabronx-lab/xscanner/issues
Expires: 2027-11-28T00:00:00.000Z
Preferred-Languages: en
Policy: https://xscanner-c8jo.onrender.com/terms
"""


def install(app: FastAPI) -> None:
    @app.api_route("/terms", methods=["GET", "HEAD"], include_in_schema=False)
    def terms(): return page("Terms of use", TERMS)

    @app.api_route("/privacy", methods=["GET", "HEAD"], include_in_schema=False)
    def privacy(): return page("Privacy policy", PRIVACY)

    @app.api_route("/disclaimer", methods=["GET", "HEAD"], include_in_schema=False)
    def disclaimer(): return page("Risk disclaimer", DISCLAIMER)

    @app.api_route("/.well-known/security.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def security_txt(): return PlainTextResponse(SECURITY_TXT, headers={"Cache-Control": "public, max-age=86400"})
