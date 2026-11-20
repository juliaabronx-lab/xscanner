"""Shared read-only HTTP GET layer for the public market-data APIs.

Concurrent callers of the same URL share one upstream request (single-flight).
A successful body is reused for a few seconds so one coin lookup does not pay
for the same RugCheck or DexScreener call three times (score, bundle, confidence).
After HTTP 429 or 503 the host is cooled down; we do not retry it in a loop.

Read-only. No keys, signing, or trading.
"""
import json, threading, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FRESH_S = 15
MAX_ENTRIES = 128

_lock = threading.Lock()
_text = {}          # url -> (fetched_at, status, text)
_cool = {}          # host -> (until_epoch, strikes)
_flight = {}        # url -> (Event, box)
_tls = threading.local()   # cooldown scope of the call that is fetching right now (keeps _fetch's signature)
USER_COOL_MAX_S = 12       # a person waiting on a lookup is never locked out for long
stats = {"fetches": 0, "hits": 0, "joined": 0, "cooldowns": 0, "errors": 0}


class UpstreamError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _host(url):
    try:
        return url.split("/")[2].lower()
    except Exception:
        return ""


def _parse(text):
    try:
        return json.loads(text)
    except Exception:
        return {"_error": "bad json"}


def _remember(url, status, text):
    _text[url] = (time.time(), status, text)
    if len(_text) > MAX_ENTRIES:
        for k in sorted(_text, key=lambda k: _text[k][0])[:len(_text) - MAX_ENTRIES]:
            _text.pop(k, None)


def _ckey(host, scope):
    return (host, scope or "")


def _cool_for(host, headers, scope=""):
    ra = None
    try:
        if headers is not None and headers.get("Retry-After"):
            ra = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        ra = None
    k = _ckey(host, scope)
    prev = _cool.get(k, (0, 0))[1] + 1
    delay = ra if ra is not None and 0 < ra < 120 else min(4 * (2 ** (prev - 1)), 60)
    if scope == "user": delay = min(delay, USER_COOL_MAX_S)
    _cool[k] = (time.time() + delay, min(prev, 6))


def get_json(url, timeout=8, headers=None, fresh_s=FRESH_S, errors="dict", scope=""):
    """GET JSON.

    errors='dict' matches scan.py: failures are {"_error": "..."}.
    errors='raise' raises UpstreamError so callers can keep their own 429 back-off.
    scope='user' is for a person waiting on a lookup: it has its own cooldown clock (capped at USER_COOL_MAX_S), so a 429 that
    the background workers hit does not lock a user out, and the other way round. Scope '' is the background / shared clock.
    """
    now = time.time()
    host = _host(url)
    with _lock:
        hit = _text.get(url)
        if hit and hit[1] == 200 and now - hit[0] < fresh_s:
            stats["hits"] += 1
            return _parse(hit[2])
        until = _cool.get(_ckey(host, scope), (0, 0))[0]
        if now < until:
            stats["cooldowns"] += 1
            cooled = True
        else:
            cooled = False
            slot = _flight.get(url)
            if slot is None:
                ev, box = threading.Event(), {}
                _flight[url] = (ev, box)
                leader = True
            else:
                ev, box = slot
                leader = False
                stats["joined"] += 1
    if cooled:
        if errors == "raise":
            raise UpstreamError(429, "429 rate limited")
        return {"_error": "429 rate limited"}
    if not leader:
        ev.wait(max(1.0, float(timeout) + 1.0))
        if "data" in box:
            return box["data"]
        if errors == "raise":
            raise UpstreamError(box.get("code"), box.get("err") or "upstream failed")
        return {"_error": box.get("err") or "upstream failed"}
    data, code, err = None, None, None
    try:
        _tls.scope = scope
        data, code = _fetch(url, timeout, headers)
    except UpstreamError as e:
        code, err = e.code, str(e)
    with _lock:
        if data is not None and code == 200:
            box["data"] = data
        else:
            box["code"] = code
            box["err"] = err or "upstream failed"
            if errors != "raise":
                box["data"] = {"_error": box["err"]}
        _flight.pop(url, None)
    ev.set()
    if data is not None:
        return data
    if errors == "raise":
        raise UpstreamError(code, err or "upstream failed")
    return box.get("data") or {"_error": err or "upstream failed"}


def _fetch(url, timeout, headers):
    stats["fetches"] += 1
    req = Request(url, headers=headers or {"Accept": "application/json", "User-Agent": "xScanner"})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = getattr(r, "status", 200) or 200
    except HTTPError as e:
        with _lock:
            stats["errors"] += 1
            if e.code in (429, 503):
                _cool_for(_host(url), e.headers, getattr(_tls, "scope", ""))
        raise UpstreamError(e.code, "HTTP %s" % e.code)
    except URLError:
        with _lock:
            stats["errors"] += 1
        raise UpstreamError(None, "URLError")
    except Exception as e:
        with _lock:
            stats["errors"] += 1
        raise UpstreamError(None, type(e).__name__)
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except Exception:
        raise UpstreamError(code, "bad json")
    if code == 200:
        with _lock:
            _remember(url, 200, text)
            _cool.pop(_ckey(_host(url), getattr(_tls, "scope", "")), None)
    return parsed, code
