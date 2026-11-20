"""Per-user paper book: persistence and price lookup. No upstream HTTP.

Prices are read from caches the rest of xScanner already filled (the token
page, the quote batch, the Pulse board, the SOL/USD figure Pulse stores).
This module never calls DexScreener, GeckoTerminal, Jupiter or the RPC.

Logged-in books are stored as JSON under ``PAPER_BOOK_DIR`` (default
``data/paper_books``), one file per account, named by a hash of the email.
The disk on a host like Render is ephemeral, so the browser also keeps a copy.
Signed-out books are not written here; the browser holds them and sends the
snapshot with each action.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque

import papertrade as pt

HERE = os.path.dirname(os.path.abspath(__file__))
FRESH_S = 45.0
SOL_FRESH_S = 900.0

_lock = threading.Lock()
_file_locks = {}
_hits = {}


class BadRequest(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.msg = msg
        self.status = status


def book_dir() -> str:
    return os.environ.get("PAPER_BOOK_DIR", os.path.join(HERE, "data", "paper_books"))


def _user_lock(key: str):
    with _lock:
        lk = _file_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _file_locks[key] = lk
        return lk


def user_key(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return digest[:32]


def _path(email: str) -> str:
    key = user_key(email)
    if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
        raise BadRequest("Bad account id.")
    return os.path.join(book_dir(), key + ".json")


def load_user(email: str):
    path = _path(email)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return pt.normalize(raw)


def save_user(email: str, account: dict) -> None:
    path = _path(email)
    os.makedirs(book_dir(), exist_ok=True)
    blob = json.dumps(account, separators=(",", ":"), ensure_ascii=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _num(v):
    return pt._finite(v)


def sol_price(hint=None, *, now=None):
    """SOL/USD from Pulse's in-memory figure, else a sane client hint. Never fetches."""
    import pulse
    now = time.time() if now is None else now
    usd = _num(pulse._sol.get("usd"))
    age = now - float(pulse._sol.get("t") or 0)
    if usd and 5.0 < usd < 5000.0 and 0 <= age <= SOL_FRESH_S:
        return usd, "pulse"
    hint_n = _num(hint)
    if hint_n and 5.0 < hint_n < 5000.0:
        return hint_n, "displayed"
    if usd and 5.0 < usd < 5000.0:
        return usd, "pulse-stale"
    return None, None


def cached_px(mint, *, now=None):
    """Token USD price from a cache that is still fresh. Never fetches."""
    import pulse
    import tokenpage as tkp
    import trader as tr
    now = time.time() if now is None else now
    hit = tr._quote_cache.get(mint)
    if hit and now - hit[0] <= FRESH_S:
        px = _num((hit[1] or {}).get("px"))
        if px and px > 0:
            return px, "quote-cache"
    hit = tkp._tok_cache.get(mint)
    if hit and now - hit[0] <= FRESH_S:
        px = _num(((hit[1] or {}).get("market") or {}).get("px"))
        if px and px > 0:
            return px, "token-cache"
    try:
        row = pulse.get_row(mint)
    except Exception:
        row = None
    if row and now - float(row.get("_ds") or 0) <= FRESH_S:
        px = _num(row.get("px"))
        if px and px > 0:
            return px, "pulse"
    return None, None


def resolve_px(mint, hint, *, now=None):
    """Prefer a fresh server cache. Otherwise use the price the page is already showing."""
    px, src = cached_px(mint, now=now)
    if px:
        return px, src
    hint_n = _num(hint)
    if hint_n and hint_n > 0:
        return hint_n, "displayed"
    return None, None


def _allow(ip: str) -> bool:
    raw = os.environ.get("PAPER_ACT_PER_10S", "40").strip()
    try:
        limit = int(raw)
    except ValueError:
        limit = 40
    if limit <= 0:
        return True
    now = time.time()
    with _lock:
        q = _hits.setdefault(ip or "?", deque())
        while q and now - q[0] > 10:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
    return True


def _prices_for(account, body):
    """Merge fresh caches over the quotes the page already has. No network."""
    hints = {}
    raw = body.get("quotes") if isinstance(body.get("quotes"), dict) else {}
    for mint, val in list(raw.items())[:80]:
        if isinstance(val, dict):
            hints[mint] = val.get("px")
        else:
            hints[mint] = val
    if body.get("mint") and body.get("px_usd"):
        hints.setdefault(body["mint"], body.get("px_usd"))
    mints = set(hints)
    mints.update((account.get("positions") or {}))
    mints.update(o.get("mint") for o in account.get("orders") or [])
    out = {}
    sources = {}
    for mint in list(mints)[:80]:
        if not pt._mint_ok(mint):
            continue
        px, src = resolve_px(mint, hints.get(mint))
        if px:
            out[mint] = px
            sources[mint] = src
    return out, sources


def public_state(user) -> dict:
    sol, sol_src = sol_price(None)
    account = None
    if user and user.get("email"):
        account = load_user(user["email"])
    return {
        "signed_in": bool(user and user.get("email")),
        "storage": "server" if user and user.get("email") else "browser",
        "account": account,
        "blank": pt.new_account(),
        "sol_usd": sol,
        "sol_source": sol_src,
        "summary": pt.summary(account or pt.new_account(), {}, sol),
        "disclaimer": "Paper trading simulation. Not financial advice. No wallet, no real orders.",
    }


def _stamp_prices(body, prices, sources, sol, sol_src):
    """Fill px_usd / sol_usd / quotes from caches so the engine does not fetch."""
    body = dict(body)
    mint = body.get("mint")
    if mint and mint in prices:
        body["px_usd"] = prices[mint]
        body["price_source"] = sources.get(mint) or body.get("price_source") or ""
    elif body.get("px_usd") and not body.get("price_source"):
        body["price_source"] = "displayed"
    if sol:
        body["sol_usd"] = sol
    if body.get("action") == "mark":
        body["quotes"] = prices
        body["price_source"] = "mark"
        body["sol_usd"] = sol
    return body


def handle_act(user, body, *, ip="local", now=None):
    if not isinstance(body, dict):
        raise BadRequest("Expected a JSON object.")
    action_name = body.get("action")
    if not isinstance(action_name, str):
        raise BadRequest("Missing action.")
    if not _allow(ip):
        raise BadRequest("Too many paper actions. Wait a few seconds.", status=429)
    blob = json.dumps(body, default=str)
    if len(blob) > 250_000:
        raise BadRequest("That paper book is too large.")

    email = (user or {}).get("email") if isinstance(user, dict) else None
    lock = _user_lock(user_key(email)) if email else _lock
    with lock:
        if email:
            current = load_user(email) or pt.new_account(now)
            if action_name == "adopt":
                if not pt.is_empty(current):
                    account = current
                else:
                    account = pt.normalize(body.get("account"))
                    save_user(email, account)
                sol, sol_src = sol_price(body.get("sol_usd"))
                prices, _sources = _prices_for(account, body)
                return _ok(account, None, [], sol, sol_src, prices, adopted=True)
            account = current
        else:
            if action_name == "adopt":
                raise BadRequest("Sign in to keep a paper book on the server.")
            account = pt.normalize(body.get("account"))

        sol, sol_src = sol_price(body.get("sol_usd"))
        prices, sources = _prices_for(account, body)
        stamped = _stamp_prices(body, prices, sources, sol, sol_src)
        dry = bool(body.get("dry"))
        try:
            nxt, fill, fills = pt.apply(account, stamped, now=now, dry=dry)
        except pt.Replay as e:
            sol2, sol_src2 = sol_price(body.get("sol_usd"))
            return _ok(e.account, None, [], sol2, sol_src2, prices, replay=True)
        except pt.Reject as e:
            raise BadRequest(e.msg)
        if email and not dry and (fill or fills):
            save_user(email, nxt)
        return _ok(nxt, fill, fills, sol, sol_src, prices, dry=dry)


def _ok(account, fill, fills, sol, sol_src, prices, *, replay=False, dry=False, adopted=False):
    return {
        "ok": True,
        "replay": replay,
        "dry": dry,
        "adopted": adopted,
        "account": account,
        "fill": fill,
        "fills": fills,
        "summary": pt.summary(account, prices, sol),
        "sol_usd": sol,
        "sol_source": sol_src,
        "disclaimer": "Paper trading simulation. Not financial advice. No wallet, no real orders.",
    }
