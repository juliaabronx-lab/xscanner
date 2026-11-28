"""xScanner accounts: username (and optional email) + password sign-up / login, and per-user synced watchlist / wallets.

* Passwords: argon2id (argon2-cffi, OWASP-style parameters). Never stored or logged in clear text. Unknown users still cost one hash check
  so a login attempt does not reveal whether a name exists.
* Storage: DATABASE_URL (Render Postgres, durable) or, when unset, SQLite at $ACCOUNTS_DB / $LIVE_DIR/accounts.db (local dev and tests;
  ephemeral on Render's free filesystem, and the API says so).
* Session: the existing signed HttpOnly cookie (Secure on HTTPS, SameSite=Lax). CSRF: a per-session token that every POST / PUT / DELETE must echo in
  the X-CSRF-Token header, plus an Origin check.
* Rate limits (in memory, per process): login and sign-up per IP, failed logins per account, sync writes per user.
* Sign-up policy: SIGNUP_MODE = open (default) | invite (needs a code from INVITE_CODES) | closed.
No wallets, keys, signing or trading.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("xscanner.accounts")
HERE = os.path.dirname(os.path.abspath(__file__))

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,23}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")
KINDS = ("watch", "wallets", "prefs")
MAX_BODY = 48_000
MIN_PW, MAX_PW = 8, 128

# A small list of the most common passwords (all that pass the length rule matter; shorter ones are caught by length anyway).
COMMON = set("""password password1 password12 password123 password1234 passw0rd p@ssw0rd p@ssword pa55word 12345678 123456789 1234567890 123123123 11111111
111111111 1111111111 00000000 000000000 87654321 987654321 9876543210 12341234 12344321 12121212 11223344 1q2w3e4r 1q2w3e4r5t 1qaz2wsx 1qaz2wsx3edc
q1w2e3r4 qwerty123 qwertyui qwertyuiop qwerty12 qwerty1234 qwerty12345 asdfghjk asdfghjkl asdf1234 zxcvbnm1 zxcvbnm123 abcd1234 abcd12345 abc12345
abc123456 abcdefgh abcdefg1 iloveyou iloveyou1 iloveyou2 iloveu12 letmein1 letmein12 letmein123 welcome1 welcome12 welcome123 admin123 admin1234 administrator
adminadmin administrator1 changeme changeme1 changeme123 default1 default123 football football1 baseball baseball1 basketball superman superman1 batman123
trustno1 trustno12 monkey12 monkey123 dragon12 dragon123 master12 master123 sunshine sunshine1 princess princess1 shadow12 shadow123 michael1 jordan23 jennifer
jessica1 charlie1 charlie123 daniel12 thomas12 hunter12 hunter123 harley12 ranger12 buster12 soccer12 killer12 pepper12 joshua12 maggie12 freedom1 freedom123
whatever whatever1 starwars starwars1 pokemon1 pokemon123 cheese123 computer computer1 internet internet1 mustang1 access14 flower12 flower123 hello123 hello1234
hellohello helloworld helloworld1 testtest test1234 test12345 testing1 testing123 tester123 guest123 guest1234 login123 login1234 user1234 user12345 username
username1 qazwsxedc qazwsx123 zaq12wsx 1qazxsw2 passpass passpass1 pass1234 pass12345 mypassword mypassword1 mypass123 secret123 secret12 supersecret
solana123 solana1234 bitcoin1 bitcoin123 ethereum crypto123 crypto1234 memecoin moonshot moon1234 hodlhodl letsgomoon pumpfun1 pumpfun123 xscanner xscanner1
xscanner123 aaaaaaaa aaaaaaa1 aaaa1111 aa123456 a1234567 a12345678 a123456789 aaaaaa11 bbbbbbbb cccccccc qqqqqqqq zzzzzzzz asdasdasd asdasd123 qweasdzxc
qweqweqwe qwe12345 qwe123456 1234qwer 1234abcd 123abc123 123qweasd 123456aa 123456ab 123456a 12345a 123456q 1234567a 12345678a 123456789a 1234567890a
147258369 159753159 741852963 963852741 123654789 123321123 321321321 135792468 0123456789 1357924680 2468013579 11112222 22223333 12345679 123456780
66666666 88888888 77777777 55555555 99999999 69696969 12312312 13131313 14141414 20202020 21212121 31313131 mamamama papapapa lovelove love1234 lovelove1
iloveyou! babygirl babygirl1 lovely123 football123 liverpool arsenal1 chelsea1 barcelona realmadrid manchester manutd123 yankees1 cowboys1 steelers
""".split())

# ------------------------------------------------------------------------------------------------ config

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def signup_mode() -> str:
    m = _env("SIGNUP_MODE", "open").lower()
    return m if m in ("open", "invite", "closed") else "open"


def invite_codes() -> set[str]:
    return {c.strip() for c in _env("INVITE_CODES").split(",") if c.strip()}


def local_enabled() -> bool:
    """Local accounts need a session secret (the cookie is signed with it) and are on by default; ACCOUNTS=off disables them."""
    return bool(_env("SESSION_SECRET")) and _env("ACCOUNTS", "on").lower() not in ("0", "off", "false", "no")


# ------------------------------------------------------------------------------------------------ database

class DB:
    """Tiny portable layer over SQLite (stdlib) and Postgres (psycopg 3). SQL uses ? placeholders."""

    def __init__(self, url: str = "", path: str = ""):
        self.url = url.strip()
        self.pg = self.url.startswith(("postgres://", "postgresql://"))
        self.path = path
        self._lock = threading.RLock()
        self._conn = None
        self.ready = False
        self.last_error: Optional[str] = None

    @property
    def kind(self) -> str:
        return "postgres" if self.pg else "sqlite"

    def _connect(self):
        if self.pg:
            import psycopg
            return psycopg.connect(self.url, autocommit=True, connect_timeout=8)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def run(self, sql: str, params: tuple = (), fetch: str = "") -> Any:
        """fetch: '' (nothing), 'one' or 'all'. Reconnects once on a dropped connection."""
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    cur = self._conn.cursor()
                    cur.execute(self._sql(sql), params)
                    out = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
                    try: cur.close()
                    except Exception: pass
                    return out
                except Exception as e:
                    integrity = isinstance(e, sqlite3.IntegrityError) or type(e).__name__ in ("UniqueViolation", "IntegrityError")
                    if integrity: raise
                    self.last_error = type(e).__name__
                    try:
                        if self._conn is not None: self._conn.close()
                    except Exception: pass
                    self._conn = None
                    if attempt == 1: raise

    def init(self) -> None:
        ai = "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self.run(f"""CREATE TABLE IF NOT EXISTS users (
            id {ai}, username TEXT NOT NULL, username_l TEXT NOT NULL UNIQUE, email TEXT, email_l TEXT UNIQUE,
            pw_hash TEXT NOT NULL, created_at BIGINT NOT NULL, last_login BIGINT, pw_changed BIGINT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)""")
        self.run("""CREATE TABLE IF NOT EXISTS user_data (
            user_id BIGINT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, ver BIGINT NOT NULL, updated_at BIGINT NOT NULL, PRIMARY KEY (user_id, kind))""")
        self.run("""CREATE TABLE IF NOT EXISTS paper_books (
            user_id BIGINT PRIMARY KEY, body TEXT NOT NULL, updated_at BIGINT NOT NULL)""")
        self.ready = True


_db: Optional[DB] = None
_db_lock = threading.Lock()


def db() -> DB:
    """Lazily opened from the environment (tests switch ACCOUNTS_DB / DATABASE_URL and call reset_db())."""
    global _db
    with _db_lock:
        if _db is None:
            url = _env("DATABASE_URL")
            path = _env("ACCOUNTS_DB") or os.path.join(_env("LIVE_DIR") or os.path.join(HERE, "data"), "accounts.db")
            d = DB(url, path)
            d.init()
            _db = d
            log.info("accounts database: %s", d.kind)
        return _db


def reset_db() -> None:
    global _db
    with _db_lock:
        if _db is not None and _db._conn is not None:
            try: _db._conn.close()
            except Exception: pass
        _db = None


def durable() -> bool:
    """True when users live in Postgres (survives restarts); the SQLite fallback is wiped by Render's free filesystem."""
    try:
        return db().pg
    except Exception:
        return False


# ------------------------------------------------------------------------------------------------ password hashing

_hasher = None
_hash_slots = threading.BoundedSemaphore(int(_env("HASH_CONCURRENCY", "2") or 2))
_DUMMY = None


def hasher():
    global _hasher
    if _hasher is None:
        from argon2 import PasswordHasher
        # argon2id; 19 MiB x 2 passes is OWASP's low-memory profile, sized for a 512 MB free instance
        _hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
    return _hasher


def hash_password(pw: str) -> str:
    with _hash_slots:
        return hasher().hash(pw)


def verify_password(stored: Optional[str], pw: str) -> bool:
    """Constant-ish time: an unknown user is checked against a dummy hash."""
    global _DUMMY
    from argon2.exceptions import VerificationError, InvalidHashError
    if not stored:
        if _DUMMY is None: _DUMMY = hash_password("not-a-real-password-" + secrets.token_hex(8))
        stored, ok_if = _DUMMY, False
    else:
        ok_if = True
    try:
        with _hash_slots:
            good = hasher().verify(stored, pw)
        return bool(good and ok_if)
    except (VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def password_problem(pw: str, username: str = "") -> Optional[str]:
    if not isinstance(pw, str) or len(pw) < MIN_PW: return f"Use at least {MIN_PW} characters."
    if len(pw) > MAX_PW: return f"Use at most {MAX_PW} characters."
    low = pw.lower()
    if low in COMMON or re.sub(r"[^a-z0-9]", "", low) in COMMON: return "That password is too common. Pick something less guessable."
    if len(set(pw)) < 4: return "That password is too repetitive."
    if username and username.lower() in low and len(username) >= 4 and len(pw) < len(username) + 6: return "Do not build your password from your username."
    if pw.isdigit() and (pw in "01234567890123456789" or pw in "98765432109876543210"): return "That password is a number sequence."
    if pwned_count(pw) > 0: return "That password has appeared in a public data breach. Pick a different one."
    return None


def pwned_check_on() -> bool:
    """Have I Been Pwned range check (k-anonymity: only the first 5 hex chars of the SHA-1 leave the server).
    On by default on Render, off locally/tests; PWNED_CHECK=0/1 overrides."""
    v = _env("PWNED_CHECK").lower()
    if v in ("0", "off", "false", "no"): return False
    if v in ("1", "on", "true", "yes"): return True
    return bool(_env("RENDER"))


def pwned_count(pw: str) -> int:
    """How often the password appears in known breaches. Fails open (0) on any network problem so sign-up never breaks."""
    if not pwned_check_on() or not pw: return 0
    import hashlib, urllib.request
    h = hashlib.sha1(pw.encode("utf-8")).hexdigest().upper()
    try:
        req = urllib.request.Request("https://api.pwnedpasswords.com/range/" + h[:5],
                                     headers={"User-Agent": "xScanner-signup-check", "Add-Padding": "true"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            body = r.read(2_000_000).decode("utf-8", "replace")
    except Exception:
        return 0
    for line in body.splitlines():
        suf, _, n = line.strip().partition(":")
        if suf == h[5:]:
            try: return int(n)
            except ValueError: return 1
    return 0


# ------------------------------------------------------------------------------------------------ rate limits

class Limiter:
    """Sliding-window counters keyed by string. In memory (one process); resets on restart."""

    def __init__(self):
        self._h: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _clean(self, key: str, window: float, now: float) -> deque:
        q = self._h.get(key)
        if q is None:
            q = self._h[key] = deque()
            if len(self._h) > 20000:
                for k in list(self._h)[:5000]: self._h.pop(k, None)
        while q and now - q[0] > window: q.popleft()
        return q

    def count(self, key: str, window: float) -> int:
        with self._lock:
            return len(self._clean(key, window, time.time()))

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record an attempt; False when over the limit (the attempt is still recorded so hammering keeps you locked)."""
        with self._lock:
            now = time.time()
            q = self._clean(key, window, now)
            q.append(now)
            return len(q) <= limit

    def blocked(self, key: str, limit: int, window: float) -> bool:
        return self.count(key, window) >= limit

    def retry_after(self, key: str, window: float) -> int:
        with self._lock:
            q = self._h.get(key)
            return max(1, int(window - (time.time() - q[0]))) if q else 1

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None: self._h.clear()
            else: self._h.pop(key, None)


LIM = Limiter()
RL = {  # (limit, window seconds)
    "login_ip": (int(_env("RL_LOGIN_IP", "40")), 600),
    "login_ip_fail": (int(_env("RL_LOGIN_IP_FAIL", "15")), 600),
    "login_acct_fail": (int(_env("RL_LOGIN_ACCT_FAIL", "6")), 900),
    "signup_ip": (int(_env("RL_SIGNUP_IP", "6")), 3600),
    "signup_all": (int(_env("RL_SIGNUP_ALL", "150")), 3600),
    "sync_user": (int(_env("RL_SYNC_USER", "90")), 60),
}


def reset_limits() -> None:
    LIM.clear()


# ------------------------------------------------------------------------------------------------ users

def _now() -> int:
    return int(time.time())


def _row_user(r) -> Optional[dict]:
    if not r: return None
    return {"id": int(r[0]), "username": r[1], "email": r[2] or "", "pw_hash": r[3], "pw_changed": int(r[4]), "disabled": int(r[5] or 0)}


_COLS = "id, username, email, pw_hash, pw_changed, disabled"


def get_user_by_id(uid: int) -> Optional[dict]:
    return _row_user(db().run(f"SELECT {_COLS} FROM users WHERE id = ?", (int(uid),), "one"))


def find_user(ident: str) -> Optional[dict]:
    ident = (ident or "").strip().lower()
    if not ident: return None
    col = "email_l" if "@" in ident else "username_l"
    return _row_user(db().run(f"SELECT {_COLS} FROM users WHERE {col} = ?", (ident,), "one"))


class SignupError(Exception):
    def __init__(self, msg: str, status: int = 400, field: str = ""):
        super().__init__(msg); self.msg, self.status, self.field = msg, status, field


def create_user(username: str, password: str, email: str = "") -> dict:
    username = (username or "").strip()
    email = (email or "").strip()
    if not USERNAME_RE.match(username):
        raise SignupError("Username: 3-24 letters, digits, dot, dash or underscore, starting with a letter or digit.", field="username")
    if email and not EMAIL_RE.match(email):
        raise SignupError("That email address does not look right (or leave it empty).", field="email")
    pb = password_problem(password, username)
    if pb: raise SignupError(pb, field="password")
    ph = hash_password(password)
    t = _now()
    try:
        r = db().run("INSERT INTO users (username, username_l, email, email_l, pw_hash, created_at, last_login, pw_changed) VALUES (?,?,?,?,?,?,?,?) RETURNING id",
                     (username, username.lower(), email or None, email.lower() or None, ph, t, t, t), "one")
    except Exception as e:
        if type(e).__name__ in ("IntegrityError", "UniqueViolation"):
            msg = str(e).lower()
            if "email" in msg: raise SignupError("That email is already registered.", 409, "email")
            raise SignupError("That username is taken.", 409, "username")
        raise
    return {"id": int(r[0]), "username": username, "email": email, "pw_changed": t}


def delete_user(uid: int) -> None:
    db().run("DELETE FROM user_data WHERE user_id = ?", (int(uid),))
    db().run("DELETE FROM paper_books WHERE user_id = ?", (int(uid),))
    db().run("DELETE FROM users WHERE id = ?", (int(uid),))


def set_password(uid: int, new_pw: str) -> int:
    cur = db().run("SELECT pw_changed FROM users WHERE id = ?", (int(uid),), "one")
    t = max(_now(), int(cur[0]) + 1 if cur else 0)       # strictly newer, so every older session cookie stops matching
    db().run("UPDATE users SET pw_hash = ?, pw_changed = ? WHERE id = ?", (hash_password(new_pw), t, int(uid)))
    return t


# ------------------------------------------------------------------------------------------------ synced data

def _check_body(kind: str, body: Any) -> Any:
    """Whitelist-validate what a client may store. Returns the cleaned body or raises ValueError."""
    def s(v, n=80): return str(v)[:n]
    if kind == "watch":
        if not isinstance(body, dict): raise ValueError("watch must be an object")
        lst = body.get("list", [])
        if not isinstance(lst, list) or len(lst) > 300: raise ValueError("watch.list must be a list of at most 300")
        out = []
        for it in lst:
            if not isinstance(it, dict) or not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", str(it.get("m", ""))): continue
            e = {"m": it["m"], "s": s(it.get("s", ""), 32), "n": s(it.get("n", ""), 64)}
            for k in ("ref", "px0", "added"):
                if isinstance(it.get(k), (int, float)) and not isinstance(it.get(k), bool): e[k] = it[k]
            out.append(e)
        cfg = body.get("cfg") if isinstance(body.get("cfg"), dict) else {}
        return {"list": out, "cfg": {"pct": min(1000, max(1, float(cfg.get("pct", 20) or 20))), "notify": bool(cfg.get("notify"))}}
    if kind == "wallets":
        if not isinstance(body, dict): raise ValueError("wallets must be an object")
        lst = body.get("list", [])
        if not isinstance(lst, list) or len(lst) > 25: raise ValueError("wallets.list must be a list of at most 25")
        out = []
        for it in lst:
            if isinstance(it, dict) and re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", str(it.get("a", ""))):
                out.append({"a": it["a"], "l": s(it.get("l", ""), 24)})
        return {"list": out}
    if kind == "prefs":
        if not isinstance(body, dict): raise ValueError("prefs must be an object")
        pres = body.get("presets", {})
        if not isinstance(pres, dict) or len(pres) > 30: raise ValueError("presets must be an object of at most 30")
        clean = {}
        for name, f in pres.items():
            if isinstance(f, dict) and len(json.dumps(f)) < 1500:
                clean[s(name, 32)] = {str(k)[:24]: v for k, v in f.items() if isinstance(v, (int, float, str, bool))}
        return {"presets": clean}
    raise ValueError("unknown kind")


def get_data(uid: int) -> dict:
    out = {}
    for kind, body, ver, upd in db().run("SELECT kind, body, ver, updated_at FROM user_data WHERE user_id = ?", (int(uid),), "all") or []:
        try: out[kind] = {"body": json.loads(body), "v": int(ver), "updated": int(upd)}
        except Exception: continue
    return out


def put_data(uid: int, kind: str, body: Any) -> dict:
    clean = _check_body(kind, body)
    raw = json.dumps(clean, separators=(",", ":"))
    if len(raw) > MAX_BODY: raise ValueError("too large")
    t = _now()
    cur = db().run("SELECT ver FROM user_data WHERE user_id = ? AND kind = ?", (int(uid), kind), "one")
    ver = (int(cur[0]) + 1) if cur else 1
    if cur: db().run("UPDATE user_data SET body = ?, ver = ?, updated_at = ? WHERE user_id = ? AND kind = ?", (raw, ver, t, int(uid), kind))
    else:
        try: db().run("INSERT INTO user_data (user_id, kind, body, ver, updated_at) VALUES (?,?,?,?,?)", (int(uid), kind, raw, ver, t))
        except Exception as e:
            if type(e).__name__ not in ("IntegrityError", "UniqueViolation"): raise
            db().run("UPDATE user_data SET body = ?, ver = ?, updated_at = ? WHERE user_id = ? AND kind = ?", (raw, ver + 1, t, int(uid), kind)); ver += 1
    return {"v": ver, "updated": t, "body": clean}



# ------------------------------------------------------------------------------------------------ paper book (simulation only; same DB as accounts)

PAPER_MAX_BODY = 200_000


def get_paper(uid: int):
    """The signed-in user's play-money book, or None. Body is already JSON-object shaped."""
    row = db().run("SELECT body FROM paper_books WHERE user_id = ?", (int(uid),), "one")
    if not row:
        return None
    try:
        raw = json.loads(row[0])
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def put_paper(uid: int, account: dict) -> None:
    if not isinstance(account, dict):
        raise ValueError("paper book must be an object")
    raw = json.dumps(account, separators=(",", ":"), ensure_ascii=False)
    if len(raw) > PAPER_MAX_BODY:
        raise ValueError("That paper book is too large.")
    t = _now()
    cur = db().run("SELECT user_id FROM paper_books WHERE user_id = ?", (int(uid),), "one")
    if cur:
        db().run("UPDATE paper_books SET body = ?, updated_at = ? WHERE user_id = ?", (raw, t, int(uid)))
        return
    try:
        db().run("INSERT INTO paper_books (user_id, body, updated_at) VALUES (?,?,?)", (int(uid), raw, t))
    except Exception as e:
        if type(e).__name__ not in ("IntegrityError", "UniqueViolation"):
            raise
        db().run("UPDATE paper_books SET body = ?, updated_at = ? WHERE user_id = ?", (raw, t, int(uid)))


# ------------------------------------------------------------------------------------------------ session / CSRF helpers

def _session(request: Request) -> Optional[dict]:
    try:
        return request.session
    except AssertionError:
        return None


def csrf_token(request: Request) -> Optional[str]:
    s = _session(request)
    if s is None: return None
    t = s.get("csrf")
    if not isinstance(t, str) or len(t) < 20:
        t = s["csrf"] = secrets.token_urlsafe(32)
    return t


def csrf_ok(request: Request) -> bool:
    """POST / PUT / DELETE guard: matching X-CSRF-Token (session bound) and, when the browser sends one, a same-site Origin."""
    s = _session(request)
    want = s.get("csrf") if s is not None else None
    got = request.headers.get("x-csrf-token", "")
    if not want or not got or not secrets.compare_digest(str(want), str(got)): return False
    origin = request.headers.get("origin")
    if origin and origin != "null":
        host = request.headers.get("host", "")
        pub = _env("PUBLIC_URL")
        okh = {host}
        if pub: okh.add(urlparse(pub).netloc)
        if urlparse(origin).netloc not in okh: return False
    elif origin == "null":
        return False
    return True


def client_ip(request: Request) -> str:
    """The visitor's address: Cloudflare's CF-Connecting-IP on Render (fix19), else the proxy-resolved peer."""
    import safety
    return safety.ip_from(request.headers, request.client.host if request.client else "unknown")[0]


SESSION_MAX_AGE = int(_env("SESSION_ABSOLUTE_DAYS", "30") or 30) * 86400


def session_user(request: Request) -> Optional[dict]:
    """The signed-in LOCAL account for this request (re-checked against the database), or None."""
    s = _session(request)
    u = s.get("user") if s is not None else None
    if not isinstance(u, dict) or u.get("provider") != "local" or not u.get("uid"): return None
    try:
        row = get_user_by_id(int(u["uid"]))
    except Exception:
        return None
    if not row or row["disabled"] or int(s.get("sv") or 0) != row["pw_changed"]:
        return None
    iat = s.get("iat")
    if iat is None:
        s["iat"] = _now()                       # sessions from before fix19 start their absolute lifetime now
    elif _now() - int(iat or 0) > SESSION_MAX_AGE:
        s.clear()                               # absolute lifetime: sign in again after SESSION_ABSOLUTE_DAYS
        return None
    return row


def public_user(row: dict) -> dict:
    return {"name": row["username"], "email": row["email"], "provider": "local"}


def start_session(request: Request, row: dict) -> None:
    s = request.session
    s.clear()                                   # drop anything pre-login (session fixation)
    s["user"] = {"uid": row["id"], "name": row["username"], "email": row["email"] or row["username"], "provider": "local"}
    s["sv"] = row["pw_changed"]
    s["iat"] = _now()
    s["csrf"] = secrets.token_urlsafe(32)


def bump_sessions(uid: int) -> int:
    """Invalidate every existing session cookie of this user (sign out everywhere) by moving its session version."""
    cur = db().run("SELECT pw_changed FROM users WHERE id = ?", (int(uid),), "one")
    t = max(_now(), int(cur[0]) + 1 if cur else 0)
    db().run("UPDATE users SET pw_changed = ? WHERE id = ?", (t, int(uid)))
    return t


def export_user(row: dict) -> dict:
    """Everything stored about one account (privacy: data portability). Never includes the password hash."""
    r = db().run("SELECT created_at, last_login FROM users WHERE id = ?", (int(row["id"]),), "one") or (None, None)
    return {"exported_at": _now(), "account": {"username": row["username"], "email": row["email"] or None,
            "created_at": r[0], "last_login": r[1]}, "synced": get_data(row["id"]), "paper_book": get_paper(row["id"])}


# ------------------------------------------------------------------------------------------------ routes

def _err(status: int, msg: str, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg, **extra}, status_code=status, headers={"Cache-Control": "no-store"})


def _ok(**kw) -> JSONResponse:
    return JSONResponse({"ok": True, **kw}, headers={"Cache-Control": "no-store"})


async def _json(request: Request) -> Optional[dict]:
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY + 4000: return None
        raw = await request.body()
        if len(raw) > MAX_BODY + 4000: return None
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


GENERIC_LOGIN = "Wrong username or password."
BUSY = "Too many attempts. Wait a few minutes and try again."


def status_payload(request: Request) -> dict:
    row = session_user(request) if local_enabled() else None
    return {"local": local_enabled(), "signup": signup_mode() if local_enabled() else "closed",
            "storage": ("durable" if durable() else "ephemeral") if local_enabled() else None,
            "user": public_user(row) if row else None, "csrf": csrf_token(request) if local_enabled() else None}


def install_routes(app: FastAPI) -> None:
    def need_local():
        return None if local_enabled() else _err(503, "Accounts are not enabled on this server.")

    @app.post("/api/auth/signup", include_in_schema=False)
    async def signup(request: Request):
        if (r := need_local()): return r
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        ip = client_ip(request)
        if not LIM.hit("su:" + ip, *RL["signup_ip"]) or not LIM.hit("su:all", *RL["signup_all"]):
            return _err(429, "Too many sign-ups from here. Try again later.", retry_after=LIM.retry_after("su:" + ip, RL["signup_ip"][1]))
        mode = signup_mode()
        if mode == "closed": return _err(403, "Sign-up is closed on this server.")
        d = await _json(request)
        if d is None: return _err(400, "Bad request.")
        if mode == "invite":
            code = str(d.get("invite") or "").strip()
            if not any(secrets.compare_digest(code.encode(), c.encode()) for c in invite_codes()):
                return _err(403, "A valid invite code is required.", field="invite")
        try:
            from starlette.concurrency import run_in_threadpool   # hashing + breach check off the event loop
            row = await run_in_threadpool(create_user, str(d.get("username") or ""), str(d.get("password") or ""), str(d.get("email") or ""))
        except SignupError as e:
            return _err(e.status, e.msg, field=e.field)
        except Exception:
            log.exception("signup failed")
            return _err(503, "Accounts are temporarily unavailable. Try again shortly.")
        start_session(request, row)
        log.info("account created (id %s)", row["id"])
        return _ok(user=public_user(row), csrf=request.session["csrf"])

    @app.post("/api/auth/login", include_in_schema=False)
    async def login(request: Request):
        if (r := need_local()): return r
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        ip = client_ip(request)
        if not LIM.hit("li:" + ip, *RL["login_ip"]) or LIM.blocked("lif:" + ip, *RL["login_ip_fail"]):
            return _err(429, BUSY, retry_after=LIM.retry_after("li:" + ip, RL["login_ip"][1]))
        d = await _json(request)
        if d is None: return _err(400, "Bad request.")
        ident = str(d.get("id") or d.get("username") or "").strip()[:200]
        pw = str(d.get("password") or "")[:MAX_PW + 1]
        akey = "lia:" + ident.lower()
        if LIM.blocked(akey, *RL["login_acct_fail"]):
            return _err(429, BUSY, retry_after=LIM.retry_after(akey, RL["login_acct_fail"][1]))
        try:
            row = find_user(ident)
        except Exception:
            log.exception("login lookup failed")
            return _err(503, "Accounts are temporarily unavailable. Try again shortly.")
        from starlette.concurrency import run_in_threadpool
        ok = (await run_in_threadpool(verify_password, row["pw_hash"] if row else None, pw)) and bool(row) and not row["disabled"] and len(pw) <= MAX_PW
        if not ok:
            LIM.hit(akey, *RL["login_acct_fail"]); LIM.hit("lif:" + ip, *RL["login_ip_fail"])
            return _err(401, GENERIC_LOGIN)
        LIM.clear(akey)
        try: db().run("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
        except Exception: pass
        start_session(request, row)
        return _ok(user=public_user(row), csrf=request.session["csrf"])

    @app.post("/api/auth/logout", include_in_schema=False)
    async def logout_api(request: Request):
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        s = _session(request)
        if s is not None: s.clear()
        return _ok(csrf=csrf_token(request))             # a fresh anonymous session token for the next sign-in

    @app.post("/api/auth/password", include_in_schema=False)
    async def change_password(request: Request):
        if (r := need_local()): return r
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        row = session_user(request)
        if not row: return _err(401, "Sign in first.")
        d = await _json(request)
        if d is None: return _err(400, "Bad request.")
        if not LIM.hit("pw:%s" % row["id"], 6, 900): return _err(429, BUSY)
        if not verify_password(row["pw_hash"], str(d.get("current") or "")): return _err(401, "Current password is wrong.", field="current")
        pb = password_problem(str(d.get("new") or ""), row["username"])
        if pb: return _err(400, pb, field="new")
        t = set_password(row["id"], str(d["new"]))
        request.session["sv"] = t
        return _ok()

    @app.post("/api/auth/delete", include_in_schema=False)
    async def delete_account(request: Request):
        if (r := need_local()): return r
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        row = session_user(request)
        if not row: return _err(401, "Sign in first.")
        d = await _json(request)
        if d is None: return _err(400, "Bad request.")
        if not LIM.hit("del:%s" % row["id"], 5, 900): return _err(429, BUSY)
        if not verify_password(row["pw_hash"], str(d.get("password") or "")): return _err(401, "Password is wrong.", field="password")
        delete_user(row["id"])
        request.session.clear()
        return _ok(csrf=csrf_token(request))

    @app.post("/api/auth/logout-all", include_in_schema=False)
    async def logout_all(request: Request):
        """Sign out on every device (e.g. after using a shared computer or a suspected leak)."""
        if (r := need_local()): return r
        if not csrf_ok(request): return _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        row = session_user(request)
        if not row: return _err(401, "Sign in first.")
        if not LIM.hit("lo:%s" % row["id"], 10, 900): return _err(429, BUSY)
        bump_sessions(row["id"])
        request.session.clear()
        return _ok(csrf=csrf_token(request))

    @app.get("/api/user/export", include_in_schema=False)
    def user_export(request: Request):
        if (r := need_local()): return r
        row = session_user(request)
        if not row: return _err(401, "Sign in to download your data.", signin=True)
        if not LIM.hit("ex:%s" % row["id"], 20, 3600): return _err(429, BUSY)
        try:
            data = export_user(row)
        except Exception:
            log.exception("export failed")
            return _err(503, "Export is temporarily unavailable.")
        return JSONResponse(data, headers={"Cache-Control": "no-store",
                                           "Content-Disposition": 'attachment; filename="xscanner-%s.json"' % row["username"]})

    def gate(request: Request, write: bool):
        """-> (row, error response). Personal data needs a signed-in local account; writes also need CSRF."""
        if (r := need_local()): return None, r
        row = session_user(request)
        if not row: return None, _err(401, "Sign in to sync.", signin=True)
        if write and not csrf_ok(request): return None, _err(403, "Session expired. Reload the page and try again.", csrf_failed=True)
        if write and not LIM.hit("sy:%s" % row["id"], *RL["sync_user"]): return None, _err(429, "Syncing too fast. Slow down.")
        return row, None

    @app.get("/api/user/data", include_in_schema=False)
    def user_data(request: Request):
        row, err = gate(request, False)
        if err: return err
        try:
            return _ok(data=get_data(row["id"]), user=public_user(row))
        except Exception:
            log.exception("sync read failed")
            return _err(503, "Sync is temporarily unavailable.")

    @app.put("/api/user/data/{kind}", include_in_schema=False)
    async def user_data_put(kind: str, request: Request):
        row, err = gate(request, True)
        if err: return err
        if kind not in KINDS: return _err(404, "Unknown data kind.")
        d = await _json(request)
        if d is None or "body" not in d: return _err(400, "Bad request.")
        try:
            res = put_data(row["id"], kind, d["body"])
        except ValueError as e:
            return _err(400, str(e))
        except Exception:
            log.exception("sync write failed")
            return _err(503, "Sync is temporarily unavailable.")
        return _ok(**res)
