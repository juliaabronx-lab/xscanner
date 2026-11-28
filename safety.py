"""fix19 public-launch hardening: security headers, request-size caps, per-IP API rate limits, client-IP resolution
and sanitizers for third-party text / links. Pure ASGI (works with SSE streaming). No secrets, no wallets, read-only.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from typing import Optional

# ------------------------------------------------------------------------------------------------ client IP

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def on_render() -> bool:
    return bool(_env("RENDER"))


def _valid_ip(v: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address((v or "").strip()))
    except ValueError:
        return None


def trust_cf() -> bool:
    """Render's edge is Cloudflare, which overwrites CF-Connecting-IP with the real visitor address, and the service is
    only reachable through that edge. Off Render (local / tests) the header is ignored unless TRUST_CF_IP=1."""
    v = _env("TRUST_CF_IP").lower()
    if v in ("0", "false", "no", "off"): return False
    if v in ("1", "true", "yes", "on"): return True
    return on_render()


def ip_from(headers, peer: Optional[str]) -> tuple[str, str]:
    """-> (ip, source). headers: mapping with lower-case keys (Starlette Headers or dict)."""
    if trust_cf():
        ip = _valid_ip(headers.get("cf-connecting-ip") or "")
        if ip: return ip, "cf"
    return (peer or "unknown"), "peer"


def scope_headers(scope) -> dict:
    out = {}
    for k, v in scope.get("headers") or []:
        try: out[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception: pass
    return out


# ------------------------------------------------------------------------------------------------ sanitizers

_CTRL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_URL_BAD = re.compile(r"[\s<>\"'`\\]")


def clean_text(v, n: int = 64):
    """Token names / symbols come from anyone who launches a coin. Strip control / bidi characters and angle brackets,
    cap the length. The front end escapes too; this is defence in depth."""
    if v is None: return None
    s = _CTRL.sub("", str(v)).replace("<", "").replace(">", "").strip()
    return s[:n] or None


def clean_url(v, n: int = 500):
    """Only plain http(s) links survive (no javascript:, data:, quotes or whitespace)."""
    if not isinstance(v, str): return None
    u = v.strip()
    if not u or len(u) > n or _URL_BAD.search(u): return None
    if not re.match(r"^https?://[A-Za-z0-9.-]+(:\d+)?(/|$|\?|#)", u, re.I): return None
    return u


# ------------------------------------------------------------------------------------------------ security headers

def csp(https: bool) -> str:
    # Inline handlers / inline styles are used across the UI, so script-src keeps 'unsafe-inline'; everything else is
    # locked to this origin plus the few hosts the page really uses (chart CDN fallback, DexScreener embed,
    # GeckoTerminal direct candles, token images from any https host via <img>).
    parts = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self' https://api.geckoterminal.com",
        "frame-src https://dexscreener.com",
        "worker-src 'self' blob:",
        "media-src 'self' data: blob:",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self' https://accounts.google.com",
        "frame-ancestors 'none'",
    ]
    if https: parts.append("upgrade-insecure-requests")
    return "; ".join(parts)


PERMISSIONS = ("accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), "
               "usb=(), interest-cohort=(), browsing-topics=()")


def security_headers(https: bool) -> list[tuple[bytes, bytes]]:
    h = [
        ("content-security-policy", csp(https)),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("permissions-policy", PERMISSIONS),
        ("cross-origin-opener-policy", "same-origin"),
        ("x-permitted-cross-domain-policies", "none"),
    ]
    if https: h.append(("strict-transport-security", "max-age=31536000; includeSubDomains"))
    return [(k.encode(), v.encode()) for k, v in h]


def _is_https(scope, headers) -> bool:
    if scope.get("scheme") == "https": return True
    if (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() == "https": return True
    return on_render()


class SecurityHeaders:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        hdrs = scope_headers(scope)
        extra = security_headers(_is_https(scope, hdrs))
        names = {k for k, _ in extra}

        async def send2(msg):
            if msg.get("type") == "http.response.start":
                cur = [(k, v) for k, v in (msg.get("headers") or []) if k.lower() not in names and k.lower() != b"server"]
                msg = {**msg, "headers": cur + extra}
            await send(msg)
        return await self.app(scope, receive, send2)


# ------------------------------------------------------------------------------------------------ body size cap

def body_limit(path: str) -> int:
    if path.startswith("/api/ingest/"): return 2 * 1024 * 1024 + 64 * 1024
    if path.startswith("/api/paper/"): return 300 * 1024
    return int(_env("MAX_BODY_BYTES", str(64 * 1024)) or 65536)


def _json_resp(status: int, body: dict, extra: Optional[list] = None):
    raw = json.dumps(body).encode()
    hdr = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()), (b"cache-control", b"no-store")]
    return {"type": "http.response.start", "status": status, "headers": hdr + (extra or [])}, {"type": "http.response.body", "body": raw}


class BodyLimit:
    """413 for request bodies over the per-path cap (declared Content-Length or streamed)."""
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        lim = body_limit(scope.get("path", ""))
        hdrs = scope_headers(scope)
        try:
            declared = int(hdrs.get("content-length") or 0)
        except ValueError:
            declared = lim + 1
        if declared > lim:
            a, b = _json_resp(413, {"detail": "Request body too large."})
            await send(a); await send(b); return
        seen = {"n": 0}

        async def recv2():
            msg = await receive()
            if msg.get("type") == "http.request":
                seen["n"] += len(msg.get("body") or b"")
                if seen["n"] > lim:
                    raise _TooLarge()
            return msg
        try:
            return await self.app(scope, recv2, send)
        except _TooLarge:
            a, b = _json_resp(413, {"detail": "Request body too large."})
            try:
                await send(a); await send(b)
            except Exception:
                pass


class _TooLarge(Exception):
    pass


# ------------------------------------------------------------------------------------------------ per-IP API rate limits

class Buckets:
    """Token buckets keyed by (class, ip). In memory, one process."""
    def __init__(self):
        self._b: dict = {}
        self._lock = threading.Lock()

    def take(self, key, rate_per_min: float, burst: float) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            tok, last = self._b.get(key, (burst, now))
            tok = min(burst, tok + (now - last) * rate_per_min / 60.0)
            if tok >= 1.0:
                self._b[key] = (tok - 1.0, now)
                return True, 0
            self._b[key] = (tok, now)
            if len(self._b) > 50000:
                for k in list(self._b)[:10000]: self._b.pop(k, None)
            need = (1.0 - tok) * 60.0 / max(rate_per_min, 0.001)
            return False, max(1, int(need + 0.999))

    def clear(self):
        with self._lock: self._b.clear()


BUCKETS = Buckets()

# path prefix -> class. Heavy = endpoints that fan out to several upstream APIs per call.
CLASSES = (
    ("/api/lookup/", "heavy"), ("/api/token/", "heavy"), ("/api/dev/", "heavy"), ("/api/wallet/", "heavy"),
    ("/api/trades/", "tape"), ("/api/ohlcv/", "chart"), ("/api/paper/book/act", "write"), ("/api/auth/", "auth"),
    ("/api/user/", "write"), ("/api/img/", "img"),
)
EXEMPT = ("/api/health", "/api/ingest/")


def limits() -> dict:
    """(per-minute rate, burst). Generous for one person on one or two tabs; a script hammering gets 429."""
    def pair(name, rate, burst):
        try: r = float(_env("RL_API_" + name.upper(), str(rate)))
        except ValueError: r = rate
        return r, max(1.0, float(_env("RL_API_" + name.upper() + "_BURST", str(burst)) or burst))
    return {"heavy": pair("heavy", 40, 20), "tape": pair("tape", 60, 20), "chart": pair("chart", 90, 30),
            "write": pair("write", 120, 30), "auth": pair("auth", 60, 20), "img": pair("img", 900, 400), "api": pair("api", 600, 120)}


def enabled() -> bool:
    return _env("RL_API", "on").lower() not in ("0", "off", "false", "no")


def classify(path: str) -> Optional[str]:
    if not path.startswith("/api/") or path.startswith(EXEMPT): return None
    for pre, c in CLASSES:
        if path.startswith(pre): return c
    return "api"


class RateLimit:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled():
            return await self.app(scope, receive, send)
        cls = classify(scope.get("path", ""))
        if not cls:
            return await self.app(scope, receive, send)
        ip, _ = ip_from(scope_headers(scope), (scope.get("client") or ("unknown",))[0])
        lim = limits()
        rate, burst = lim[cls]
        ok, wait = BUCKETS.take((cls, ip), rate, burst)
        if ok and cls not in ("api", "img"):
            ok, wait = BUCKETS.take(("api", ip), *lim["api"])
        if not ok:
            a, b = _json_resp(429, {"detail": "Too many requests. Slow down and try again in a few seconds.", "retry_after": wait},
                              [(b"retry-after", str(wait).encode())])
            await send(a); await send(b); return
        return await self.app(scope, receive, send)
