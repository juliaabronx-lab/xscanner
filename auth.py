"""Google OAuth 2.0 / OIDC sign-in for xScanner.

Authorization code flow with state + PKCE (S256) and a nonce. Sessions live in a
signed HttpOnly SameSite=Lax cookie (Secure on HTTPS / Render).

Disabled when GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET or SESSION_SECRET is unset:
the rest of the app keeps working exactly as before and /login explains that
sign-in is not configured. No wallets, keys, signing or trading.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Any, Optional
from urllib.parse import urlencode

from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_HTML = os.path.join(HERE, "static", "login.html")
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/userinfo"
SCOPE = "openid email profile"
# Auth routes and the endpoints the brief leaves open when AUTH_REQUIRED=true.
_OPEN_EXACT = {"/login", "/auth/google", "/auth/callback", "/logout", "/favicon.ico", "/api/health"}
_OPEN_PREFIX = ("/static/", "/api/ingest/")
_NEXT_OK = re.compile(r"^/[^\s\\]*$")


def _truthy(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def settings() -> dict[str, Any]:
    """Read auth config from the environment on every call (tests can change env)."""
    emails = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
    return {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        "session_secret": os.environ.get("SESSION_SECRET", "").strip(),
        "required": _truthy("AUTH_REQUIRED"),
        "allowed": emails,
        "redirect_uri": os.environ.get("AUTH_REDIRECT_URI", "").strip(),
    }


def configured(cfg: Optional[dict[str, Any]] = None) -> bool:
    cfg = cfg or settings()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["session_secret"])


def https_only() -> bool:
    """Secure cookie on Render / HTTPS. Off for local http:// unless SESSION_SECURE=1."""
    flag = os.environ.get("SESSION_SECURE", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return bool(os.environ.get("RENDER"))


def current_user(request: Request) -> Optional[dict[str, Any]]:
    try:
        user = request.session.get("user")
    except AssertionError:
        return None
    return user if isinstance(user, dict) and user.get("email") else None


def safe_next(value: Optional[str]) -> str:
    """Only allow same-origin relative paths. Blocks protocol-relative and open redirects."""
    if not value:
        return "/"
    value = value.strip()
    if not _NEXT_OK.match(value) or value.startswith("//") or "://" in value or "\\" in value:
        return "/"
    return value


def redirect_uri_for(request: Request, cfg: Optional[dict[str, Any]] = None) -> str:
    cfg = cfg or settings()
    if cfg["redirect_uri"]:
        return cfg["redirect_uri"]
    return str(request.url.replace(path="/auth/callback", query=""))


def _me_payload(request: Request) -> dict[str, Any]:
    cfg = settings()
    user = current_user(request)
    public = None
    if user:
        public = {k: user.get(k) for k in ("email", "name", "picture") if user.get(k)}
    return {
        "configured": configured(cfg),
        "required": bool(cfg["required"] and configured(cfg)),
        "user": public,
    }


class AuthGateMiddleware(BaseHTTPMiddleware):
    """When AUTH_REQUIRED=true and Google is configured, demand a signed-in session.

    HTML pages redirect to /login; /api/* returns 401. /api/health, /api/ingest/*,
    /api/me, static assets and the auth routes stay reachable.
    """

    async def dispatch(self, request: Request, call_next):
        cfg = settings()
        if not (cfg["required"] and configured(cfg)):
            return await call_next(request)
        path = request.url.path
        if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIX):
            return await call_next(request)
        if current_user(request):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "sign in required", "required": True}, status_code=401)
        nxt = path
        if request.url.query:
            nxt += "?" + request.url.query
        return RedirectResponse("/login?next=" + _enc_next(nxt), status_code=302)


def _enc_next(path: str) -> str:
    from urllib.parse import quote
    return quote(safe_next(path), safe="/")


def _login_html(request: Request) -> str:
    cfg = settings()
    ready = configured(cfg)
    nxt = safe_next(request.query_params.get("next"))
    err = (request.query_params.get("error") or "").strip()
    errors = {
        "state": "Sign-in was cancelled or expired. Please try again.",
        "denied": "Google sign-in was cancelled.",
        "forbidden": "This Google account is not allowed on xScanner.",
        "oauth": "Google sign-in failed. Please try again.",
        "unverified": "That Google account has no verified email.",
    }
    err_html = f'<p class="login-err" role="alert">{errors[err]}</p>' if err in errors else ""
    if ready:
        href = "/auth/google" + (f"?next={_enc_next(nxt)}" if nxt != "/" else "")
        button = (
            f'<a class="google-btn" href="{href}">'
            f'{_GOOGLE_G}<span>Sign in with Google</span></a>'
        )
        note = ""
    else:
        button = (
            f'<button type="button" class="google-btn" disabled aria-disabled="true">'
            f'{_GOOGLE_G}<span>Sign in with Google</span></button>'
        )
        note = '<p class="login-note">Google sign-in is not configured on this server.</p>'
    back = "" if (cfg["required"] and ready) else '<a class="ghost login-back" href="/">Back to the scanner</a>'
    required_note = (
        '<p class="muted">This copy of xScanner requires a sign-in to view the scanner.</p>'
        if (cfg["required"] and ready) else ""
    )
    try:
        raw = open(LOGIN_HTML, encoding="utf-8").read()
    except OSError:
        raw = "<!doctype html><title>Sign in · xScanner</title><p>{{auth_note}}</p>"
    return (raw.replace("{{auth_button}}", button)
               .replace("{{auth_note}}", note)
               .replace("{{auth_error}}", err_html)
               .replace("{{auth_required}}", required_note)
               .replace("{{back_link}}", back))


# Official multicolour G, inlined so the button works offline.
_GOOGLE_G = (
    '<svg class="gicon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 8 3.1l5.7-5.7C34.2 6.1 29.4 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z"/>'
    '<path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 12 24 12c3.1 0 5.8 1.2 8 3.1l5.7-5.7C34.2 6.1 29.4 4 24 4 16.3 4 9.6 8.3 6.3 14.7z"/>'
    '<path fill="#4CAF50" d="M24 44c5.2 0 10-2 13.5-5.2l-6.2-5.2C29.3 35.1 26.8 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/>'
    '<path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.1 3.1-3.5 5.6-6.6 7.1l6.2 5.2C37.2 38.3 44 33 44 24c0-1.2-.1-2.3-.4-3.5z"/>'
    '</svg>'
)


def install(app: FastAPI) -> None:
    """Attach middleware and auth routes. Safe to call once at import time."""
    app.add_middleware(AuthGateMiddleware)
    secret = settings()["session_secret"]
    if secret:
        app.add_middleware(
            SessionMiddleware,
            secret_key=secret,
            session_cookie="xs_session",
            same_site="lax",
            https_only=https_only(),
            max_age=60 * 60 * 24 * 14,
        )
    # Render (and any proxy) terminates TLS; honour X-Forwarded-Proto so the
    # callback URL is https://…/auth/callback rather than http://.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    @app.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
    def login_page(request: Request):
        return HTMLResponse(_login_html(request), headers={"Cache-Control": "no-store"})

    @app.get("/auth/google", include_in_schema=False)
    async def auth_google(request: Request):
        cfg = settings()
        if not configured(cfg):
            return RedirectResponse("/login", status_code=302)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = _code_challenge(verifier)
        request.session["oauth_state"] = state
        request.session["oauth_nonce"] = nonce
        request.session["oauth_verifier"] = verifier
        request.session["oauth_next"] = safe_next(request.query_params.get("next"))
        params = {
            "client_id": cfg["client_id"],
            "redirect_uri": redirect_uri_for(request, cfg),
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        return RedirectResponse(GOOGLE_AUTH + "?" + urlencode(params), status_code=302)

    @app.get("/auth/callback", include_in_schema=False)
    async def auth_callback(request: Request):
        cfg = settings()
        if not configured(cfg):
            return RedirectResponse("/login", status_code=302)
        if request.query_params.get("error"):
            return RedirectResponse("/login?error=denied", status_code=302)
        state = request.query_params.get("state") or ""
        code = request.query_params.get("code") or ""
        want = request.session.get("oauth_state")
        nonce = request.session.get("oauth_nonce")
        verifier = request.session.get("oauth_verifier")
        nxt = safe_next(request.session.get("oauth_next"))
        if not want or not state or not secrets.compare_digest(str(state), str(want)):
            return HTMLResponse("Invalid or expired sign-in state.", status_code=400)
        if not code or not verifier:
            return RedirectResponse("/login?error=oauth", status_code=302)
        try:
            userinfo = await _exchange(cfg, request, code, verifier, nonce)
        except Exception:
            return RedirectResponse("/login?error=oauth", status_code=302)
        email = (userinfo.get("email") or "").strip().lower()
        if not email:
            return RedirectResponse("/login?error=unverified", status_code=302)
        if userinfo.get("email_verified") is False:
            return RedirectResponse("/login?error=unverified", status_code=302)
        if cfg["allowed"] and email not in cfg["allowed"]:
            request.session.clear()
            return RedirectResponse("/login?error=forbidden", status_code=302)
        # Rotate the session to drop one-time OAuth values (and session fixation).
        request.session.clear()
        request.session["user"] = {
            "sub": userinfo.get("sub") or "",
            "email": email,
            "name": (userinfo.get("name") or email).strip(),
            "picture": userinfo.get("picture") or "",
        }
        return RedirectResponse(nxt, status_code=302)

    @app.get("/logout", include_in_schema=False)
    async def logout(request: Request):
        try:
            request.session.clear()
        except AssertionError:
            pass
        return RedirectResponse("/", status_code=302)

    @app.get("/api/me")
    def me(request: Request):
        # Allowed while signed out so the header chip can render a Sign in link.
        return _me_payload(request)


def _code_challenge(verifier: str) -> str:
    import base64
    import hashlib
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _exchange(cfg: dict[str, Any], request: Request, code: str, verifier: str, nonce: Optional[str]) -> dict[str, Any]:
    """Trade the auth code for tokens (PKCE) and return Google userinfo. Authlib client."""
    redirect_uri = redirect_uri_for(request, cfg)
    async with AsyncOAuth2Client(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        token_endpoint=GOOGLE_TOKEN,
        code_challenge_method="S256",
    ) as client:
        token = await client.fetch_token(
            GOOGLE_TOKEN,
            code=code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            grant_type="authorization_code",
        )
        # Prefer userinfo; fall back to claims already on the token if present.
        try:
            resp = await client.get(GOOGLE_USERINFO)
            resp.raise_for_status()
            info = resp.json()
        except Exception:
            info = token.get("userinfo") or {}
        if nonce and info.get("nonce") and info["nonce"] != nonce:
            raise ValueError("nonce mismatch")
        return info
