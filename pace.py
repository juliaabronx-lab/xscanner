"""Shared per-host pacing for read-only market-data HTTP.

Pulse, the live scan, chart pre-warm and the price tracker each used to keep
their own gap and their own 429 clock. On a cold start they all spoke at once,
the shared IP got HTTP 429, and that cooldown was not the same clock the trades
tape and lookups use — so background 429s starved the people waiting.

pace.install() wraps urllib.request.urlopen once. Background callers take
tokens from a per-host bucket and leave a reserve for interactive requests.
A 429 on a background call cools only the background clock. Interactive
callers (the /api middleware sets that) skip the background cooldown.

scan_runner is a separate process. It calls before_request() and shares the
same file-backed bucket under LIVE_DIR. Read-only: this never signs, sends,
or trades. It only delays GETs.
"""
import contextvars, fcntl, json, os, socket, threading, time, urllib.request
from urllib.error import HTTPError

PRIORITY = contextvars.ContextVar("xscanner_pace_priority", default="bg")

STARTUP_S = 120.0
USER_COOL_MAX_S = 8.0
BG_COOL_MAX_S = 40.0

# rate = steady tokens per second. capacity = burst size. reserve = tokens a
# background caller must leave for an interactive caller. startup_scale slows
# background refill during the first STARTUP_S after arm()/install().
# Steady DexScreener / RugCheck rates are high enough that one scan (a few
# dozen GETs) still finishes inside the live scan timeout. GeckoTerminal stays
# scarce: it is the host the trades tape and the charts already lose to 429s.
HOSTS = {
    "api.geckoterminal.com": {"rate": 10 / 60, "capacity": 3, "reserve": 1, "startup_scale": 0.5},
    "api.dexscreener.com": {"rate": 60 / 60, "capacity": 5, "reserve": 2, "startup_scale": 0.7},
    "lite-api.jup.ag": {"rate": 30 / 60, "capacity": 4, "reserve": 2, "startup_scale": 0.55},
    "datapi.jup.ag": {"rate": 20 / 60, "capacity": 3, "reserve": 1, "startup_scale": 0.5},
    "api.rugcheck.xyz": {"rate": 40 / 60, "capacity": 4, "reserve": 1, "startup_scale": 0.75},
    "solana-rpc.publicnode.com": {"rate": 40 / 60, "capacity": 8, "reserve": 4, "startup_scale": 0.75},
    "api.mainnet-beta.solana.com": {"rate": 20 / 60, "capacity": 4, "reserve": 2, "startup_scale": 0.6},
    "frontend-api-v3.pump.fun": {"rate": 8 / 60, "capacity": 2, "reserve": 1, "startup_scale": 0.5},
}

# Applied once, to the first background request of that thread, and only inside
# the startup window. Spreads the loops that all used to start together.
THREAD_OFFSET_S = {
    "ohlcv-worker": 0.5,
    "price-tracker": 4.0,
    "state-refresh": 6.0,
    "pulse": 8.0,
    "xscanner-live": 13.0,
    "bundle-worker": 18.0,
    "confidence-worker": 21.0,
}

_tls = threading.local()
_install_lock = threading.Lock()
_installed = False
_orig_urlopen = None
_now = time.time
_sleep = time.sleep
_jitter = True
_use_memory = False
_memory = {"hosts": {}, "started_at": None}
_memory_lock = threading.Lock()
counters = {"bg_grant": 0, "user_grant": 0, "bg_wait_s": 0.0, "user_wait_s": 0.0, "bg_429": 0, "user_429": 0}


def _clock():
    return float(_now())


def host_of(url):
    try:
        return str(url).split("/")[2].lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def _policy(host):
    return HOSTS.get(host)


def _skip(host):
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return True
    if host.endswith(".onrender.com"):
        return True
    return False


def state_path():
    live = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
    return os.environ.get("PACE_STATE", os.path.join(live, "pace_state.json"))


def reset(*, memory=True, now=None, sleep=None, jitter=False, startup_s=None):
    """Drop bucket state. Tests use an in-memory bucket and a fake clock."""
    global _memory, _use_memory, _jitter, _now, _sleep, STARTUP_S, counters
    _memory = {"hosts": {}, "started_at": None}
    _use_memory = bool(memory)
    _jitter = bool(jitter)
    _now = now or time.time
    _sleep = sleep or time.sleep
    if startup_s is not None:
        STARTUP_S = float(startup_s)
    counters = {"bg_grant": 0, "user_grant": 0, "bg_wait_s": 0.0, "user_wait_s": 0.0, "bg_429": 0, "user_429": 0}
    _tls.offset_done = False


def _with_state(mutator):
    if _use_memory:
        with _memory_lock:
            return mutator(_memory)
    path = state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            state = json.loads(raw) if raw.strip() else {"hosts": {}, "started_at": None}
        except Exception:
            state = {"hosts": {}, "started_at": None}
        if "hosts" not in state or not isinstance(state["hosts"], dict):
            state["hosts"] = {}
        result = mutator(state)
        f.seek(0)
        f.truncate()
        json.dump(state, f)
        f.flush()
        return result
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _in_startup(state, now):
    started = state.get("started_at")
    return started is not None and (now - float(started)) < STARTUP_S


def _bucket(state, host, pol, now):
    b = state["hosts"].get(host)
    if not b:
        b = {"tokens": float(pol["capacity"]), "t": now, "bg_cool": 0.0, "user_cool": 0.0,
             "bg_strikes": 0, "user_strikes": 0}
        state["hosts"][host] = b
        return b
    rate = float(pol["rate"])
    if _in_startup(state, now):
        rate *= float(pol.get("startup_scale", 1.0))
    elapsed = max(0.0, now - float(b.get("t") or now))
    b["tokens"] = min(float(pol["capacity"]), float(b.get("tokens") or 0) + elapsed * rate)
    b["t"] = now
    return b


def _bg_rate(state, pol, now):
    rate = float(pol["rate"])
    if _in_startup(state, now):
        rate *= float(pol.get("startup_scale", 1.0))
    return rate or 1e-6


def arm():
    """Start the cold-start window if it is not already started."""
    def mut(state):
        if state.get("started_at") is None:
            state["started_at"] = _clock()
        return state["started_at"]
    return _with_state(mut)


def _startup_now():
    def mut(state):
        if state.get("started_at") is None:
            state["started_at"] = _clock()
        return _in_startup(state, _clock())
    return _with_state(mut)


def effective_priority(priority=None):
    """Background threads stay background even if a request started them.

    Python copies the caller's context into Thread. A health check that queues
    the confidence or bundle worker would otherwise mark that worker interactive
    for its whole life, and it would skip the bucket. The thread name wins.
    """
    name = threading.current_thread().name
    if name in THREAD_OFFSET_S or name.startswith("ThreadPoolExecutor"):
        return "bg"
    if priority is None:
        priority = PRIORITY.get()
    return priority or "bg"


def _offset_once(priority):
    if priority != "bg" or not _jitter:
        return 0.0
    if getattr(_tls, "offset_done", False):
        return 0.0
    _tls.offset_done = True
    try:
        if not _startup_now():
            return 0.0
    except Exception:
        return 0.0
    return float(THREAD_OFFSET_S.get(threading.current_thread().name, 0.0))


def _plan(host, priority):
    """Take one token, or return how many seconds the caller should wait.

    A background caller may spend a token only when at least `reserve` tokens
    would remain. An interactive caller may spend the reserve, and does not
    wait on a background 429 cooldown. An empty bucket does not block an
    interactive caller: the reserve is what keeps one free, and a person
    waiting on a lookup must not queue behind a sweep.
    """
    pol = _policy(host)

    def mut(state):
        if state.get("started_at") is None:
            state["started_at"] = _clock()
        now = _clock()
        b = _bucket(state, host, pol, now)
        if priority == "user":
            cool = float(b.get("user_cool") or 0)
            if now < cool:
                return min(cool - now, USER_COOL_MAX_S)
            if b["tokens"] >= 1:
                b["tokens"] -= 1
            return 0.0
        cool = float(b.get("bg_cool") or 0)
        if now < cool:
            return cool - now
        if b["tokens"] >= float(pol["reserve"]) + 1 - 1e-9:
            b["tokens"] -= 1
            return 0.0
        need = (float(pol["reserve"]) + 1) - b["tokens"]
        return max(0.05, need / _bg_rate(state, pol, now))

    return float(_with_state(mut) or 0.0)


def before_request(url, priority=None):
    """Block until `url` may be opened. No-op for hosts that are not paced."""
    priority = effective_priority(priority)
    host = host_of(url)
    if _skip(host) or _policy(host) is None:
        return
    off = _offset_once(priority)
    if off > 0:
        _sleep(off)
        counters["bg_wait_s"] += off
    cap = 5.0 if priority == "bg" else 1.0
    for _ in range(8):
        wait = _plan(host, priority)
        if wait <= 0:
            counters["bg_grant" if priority == "bg" else "user_grant"] += 1
            return
        step = min(wait, cap)
        _sleep(step)
        counters["bg_wait_s" if priority == "bg" else "user_wait_s"] += step
    wait = _plan(host, priority)
    if wait > 0 and priority == "bg":
        step = min(wait, 8.0)
        _sleep(step)
        counters["bg_wait_s"] += step
        if _plan(host, priority) <= 0:
            counters["bg_grant"] += 1
            return
    counters["bg_grant" if priority == "bg" else "user_grant"] += 1


def note_status(url, code, priority=None):
    """Record a 200 (clear that side's cooldown) or a 429/503 (cool that side only)."""
    priority = effective_priority(priority)
    host = host_of(url)
    if _skip(host) or _policy(host) is None:
        return
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        return

    def mut(state):
        now = _clock()
        b = _bucket(state, host, _policy(host), now)
        if code in (429, 503):
            if priority == "user":
                b["user_strikes"] = min(int(b.get("user_strikes") or 0) + 1, 4)
                delay = min(2 * (2 ** (b["user_strikes"] - 1)), USER_COOL_MAX_S)
                b["user_cool"] = now + delay
                counters["user_429"] += 1
            else:
                b["bg_strikes"] = min(int(b.get("bg_strikes") or 0) + 1, 4)
                delay = min(8 * (2 ** (b["bg_strikes"] - 1)), BG_COOL_MAX_S)
                b["bg_cool"] = now + delay
                counters["bg_429"] += 1
        elif code == 200:
            if priority == "user":
                b["user_strikes"] = 0
                b["user_cool"] = 0.0
            else:
                b["bg_strikes"] = 0
                b["bg_cool"] = 0.0
        return None

    _with_state(mut)


def snapshot():
    """Copy of the bucket clocks, for tests and logs. Times are seconds from now."""
    def mut(state):
        return json.loads(json.dumps(state))
    state = _with_state(mut)
    now = _clock()
    hosts = {}
    for host, b in (state.get("hosts") or {}).items():
        hosts[host] = {
            "tokens": round(float(b.get("tokens") or 0), 4),
            "bg_cool_s": round(max(0.0, float(b.get("bg_cool") or 0) - now), 3),
            "user_cool_s": round(max(0.0, float(b.get("user_cool") or 0) - now), 3),
        }
    return {"started_at": state.get("started_at"), "startup": _in_startup(state, now),
            "hosts": hosts, "counters": dict(counters)}


class UserPriorityMiddleware:
    """Mark /api requests interactive so a background 429 does not stall them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and str(scope.get("path") or "").startswith("/api/"):
            token = PRIORITY.set("user")
            try:
                await self.app(scope, receive, send)
            finally:
                PRIORITY.reset(token)
        else:
            await self.app(scope, receive, send)


def install():
    """Wrap urllib.request.urlopen once. Further calls do nothing."""
    global _installed, _orig_urlopen
    with _install_lock:
        if _installed:
            return
        _orig_urlopen = urllib.request.urlopen

        def wrapped(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
            req = url if isinstance(url, urllib.request.Request) else None
            u = req.full_url if req is not None else str(url)
            pri = effective_priority()
            before_request(u, pri)
            try:
                resp = _orig_urlopen(url, data, timeout, *args, **kwargs)
            except HTTPError as e:
                note_status(u, getattr(e, "code", 0) or 0, pri)
                raise
            code = getattr(resp, "status", None)
            if code is None:
                code = getattr(resp, "code", None)
            if code == 200 or code is None:
                note_status(u, 200, pri)
            return resp

        urllib.request.urlopen = wrapped
        _installed = True
    try:
        arm()
    except Exception:
        pass


def _uninstalled_for_tests():
    """Restore urlopen after a test called install(). Not used by the app."""
    global _installed
    if _installed and _orig_urlopen is not None:
        urllib.request.urlopen = _orig_urlopen
    _installed = False
