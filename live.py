"""xScanner live mode (LIVE_SCAN=1): a server-side loop that keeps the hosted site fresh by itself.

* Every LIVE_SCAN_INTERVAL seconds (default 120) it gathers new/trending Solana tokens from public no-key
  sources (GeckoTerminal trending + new pools, pump.fun public lists when Cloudflare lets us through;
  scan.py itself adds DexScreener boosts/profiles), then runs scan.py (DexScreener + RugCheck, reusing the
  stored X posts in x_raw.json) in a private work dir and atomically publishes report.json and
  dashboard_data.json into the dir the API serves.
* Until the first scan finishes the API serves the bundled snapshot (copied into the serve dir at startup).
* Optional live X feed: only if X_BEARER_TOKEN is set in the host env (never in the repo). Without it the
  bundled X feed snapshot is shown with its own updated_at time.
Read-only toward the outside world: GET requests only. No wallets, keys, signing, trading or posting.
"""
import json, os, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

MYT = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
ENABLED = os.environ.get("LIVE_SCAN", "").strip().lower() in ("1", "true", "yes", "on")
LIVE_DIR = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
INTERVAL = max(60, min(int(os.environ.get("LIVE_SCAN_INTERVAL", "120")), 900))
X_INTERVAL = max(300, int(os.environ.get("LIVE_X_INTERVAL", "1800")))
SCAN_TIMEOUT = int(os.environ.get("LIVE_SCAN_TIMEOUT", "150"))
WORK, SERVE = os.path.join(LIVE_DIR, "work"), os.path.join(LIVE_DIR, "serve")
THIN_KEEP_S = int(os.environ.get("LIVE_THIN_KEEP_S", "1800"))   # keep a full list up to 30 min over a thin scan

status = {"enabled": ENABLED, "interval_s": INTERVAL, "running": False, "scans_ok": 0, "scans_failed": 0,
          "last_scan_at": None, "last_scan_epoch": None, "last_attempt_at": None, "last_error": None,
          "last_duration_s": None, "next_scan_epoch": None, "seeds": {}, "source_errors": {},
          "snapshot_time": None, "bundle_snapshot_time": None, "ledger_as_of": None, "x": {"live": False, "reason": "X_BEARER_TOKEN not set; showing stored X posts",
                                       "last_fetch_at": None, "last_error": None,
                                       "last_ingest_at": None, "last_ingest": None}}
_gecko_backoff_until = 0.0
_pump_backoff_until = 0.0
_dex_backoff_until = 0.0
_last_good_seeds = {"geckoterminal": [], "pumpfun": [], "dexscreener_search": [], "dexscreener_boosts": [], "dexpaprika": []}
_seed_cache = {}          # name -> (fetched_at, mints)
SEED_CACHE_S = 600        # reuse a successful seed fetch for 10 min so scan.py pair calls don't 429 the next seed pass
_started = False
_kick = threading.Event()   # set by an X ingest to start the next scan right away
_ingest_lock = threading.Lock()


def _now():
    return datetime.now(MYT).isoformat(timespec="seconds")


def _get(url, timeout=12, headers=None, tries=1):
    for i in range(tries):
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1: raise
            time.sleep(2.0 * 2 ** i)


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


LEDGER = os.path.join(LIVE_DIR, "ledger.json")


def prepare_ledger(path):
    """Serve the paper ledger from the live dir so pushes (and each scan's as-of stamp) can update it."""
    os.makedirs(LIVE_DIR, exist_ok=True)
    if os.path.exists(path) and not os.path.exists(LEDGER): shutil.copy2(path, LEDGER)
    return LEDGER


def _touch_ledger():
    # The paper trades themselves are made on the agent box (pushed via /api/ingest/x "ledger");
    # each live scan stamps the time the served data was last refreshed.
    try:
        L = json.load(open(LEDGER))
        if isinstance(L, dict):
            L["data_as_of"] = _now(); _atomic_write(LEDGER, L); status["ledger_as_of"] = L["data_as_of"]
    except Exception:
        pass


def prepare(snapshot_dir, xfeed_path):
    """Create work/serve dirs seeded from the bundled snapshot. Returns (serve_dir, xfeed_path_to_serve)."""
    os.makedirs(WORK, exist_ok=True); os.makedirs(SERVE, exist_ok=True)
    status["published_epoch"] = time.time()   # the bundled snapshot counts as freshly published at startup
    for f in ("scan.py", "config.json", "x_raw.json"):
        src = os.path.join(snapshot_dir, f)
        if os.path.exists(src): shutil.copy2(src, os.path.join(WORK, f))
    try:
        status["snapshot_time"] = status["bundle_snapshot_time"] = open(os.path.join(snapshot_dir, "SNAPSHOT_TIME")).read().strip()
    except Exception:
        status["snapshot_time"] = status["bundle_snapshot_time"] = _now()
    for f in ("report.json", "dashboard_data.json"):
        src, dst = os.path.join(snapshot_dir, f), os.path.join(SERVE, f)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            try:  # bundled files can be days old; stamp dates so a cold start isn't stuck on e.g. 14 Nov
                d = json.load(open(dst, encoding="utf-8"))
                raw = status.get("bundle_snapshot_time") or _now()
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=MYT)
                    label = dt.astimezone(MYT).strftime("%Y-%m-%d %H:%M MYT")
                except Exception:
                    label = _now()
                if f == "report.json":
                    d["generated_myt"] = label
                if f == "dashboard_data.json":
                    d["scanTime"] = label
                _atomic_write(dst, d)
            except Exception:
                pass
    xf = os.path.join(LIVE_DIR, "x_feed.json")
    if os.path.exists(xfeed_path) and not os.path.exists(xf): shutil.copy2(xfeed_path, xf)
    bundled = os.path.join(snapshot_dir, "last_seeds.json")
    if os.path.exists(bundled) and not os.path.exists(_seeds_path()):
        try:
            os.makedirs(LIVE_DIR, exist_ok=True)
            shutil.copy2(bundled, _seeds_path())
        except Exception:
            pass
    _load_last_seeds([bundled, _BUNDLED_SEEDS])
    return SERVE, xf


# ---------------------------------------------------------------- seed sources (all public, no key)
def _seeds_path():
    return os.path.join(LIVE_DIR, "last_seeds.json")


_BUNDLED_SEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "data", "last_seeds.json")


def _merge_seed_list(name, values):
    got = [m for m in values if isinstance(m, str) and 32 <= len(m) <= 44]
    if not got:
        return
    cur = _last_good_seeds.setdefault(name, [])
    _last_good_seeds[name] = list(dict.fromkeys(list(cur) + got))[-120:]


def _load_last_seeds(extra_paths=None):
    paths = [_seeds_path(), _BUNDLED_SEEDS]
    if extra_paths:
        paths = list(extra_paths) + paths
    for path in paths:
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if isinstance(v, list):
                _merge_seed_list(k, v)


def _save_last_seeds():
    try:
        os.makedirs(LIVE_DIR, exist_ok=True)
        _atomic_write(_seeds_path(), {k: v[-120:] for k, v in _last_good_seeds.items() if v})
    except Exception:
        pass


def _seeds_gecko():
    global _gecko_backoff_until
    if time.time() < _gecko_backoff_until:
        raise RuntimeError(f"rate limited, backing off {int(_gecko_backoff_until - time.time())}s")
    out = []
    # One page each, with a pause between calls so we don't burst the shared IP (charts use this host too).
    for i, path in enumerate(("trending_pools?page=1", "new_pools?page=1")):
        if i:
            time.sleep(1.6)
        try:
            d = _get("https://api.geckoterminal.com/api/v2/networks/solana/" + path)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _gecko_backoff_until = time.time() + 180
                if out: break
                raise RuntimeError("429 rate limited (skipping GeckoTerminal for 3 min)")
            raise
        for p in d.get("data") or []:
            tid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id") or ""
            if tid.startswith("solana_"): out.append(tid[7:])
    return out


def _seeds_pump():
    # pump.fun's public frontend API sits behind Cloudflare and often refuses server IPs; best effort only.
    global _pump_backoff_until
    if time.time() < _pump_backoff_until:
        raise RuntimeError(f"unavailable, backing off {int(_pump_backoff_until - time.time())}s")
    out = []
    for path in ("coins/currently-live?limit=30&offset=0&includeNsfw=false",
                 "coins?offset=0&limit=30&sort=last_trade_timestamp&order=DESC&includeNsfw=false"):
        try:
            d = _get("https://frontend-api-v3.pump.fun/" + path, timeout=8)
        except Exception as e:
            code = getattr(e, "code", None)
            if code in (403, 429, 503):
                _pump_backoff_until = time.time() + (1800 if code == 403 else 300)
            if not out and path.startswith("coins?"):
                raise RuntimeError(f"unavailable ({code or type(e).__name__})")
            continue
        for c in (d if isinstance(d, list) else d.get("coins") or []):
            if isinstance(c, dict) and c.get("mint"): out.append(c["mint"])
    return out


def _seeds_dexscreener():
    global _dex_backoff_until
    if time.time() < _dex_backoff_until:
        raise RuntimeError(f"rate limited, backing off {int(_dex_backoff_until - time.time())}s")
    # scan.py already pulls boosts + profiles; add DexScreener's pump.fun/pumpswap search for fresh pairs.
    out = []
    for i, q in enumerate(("pumpswap", "pump.fun")):
        if i:
            time.sleep(1.0)
        try:
            d = _get("https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(q), tries=2)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _dex_backoff_until = time.time() + 180
                if out: break
                raise
            raise
        for p in d.get("pairs") or []:
            if p.get("chainId") == "solana":
                a = (p.get("baseToken") or {}).get("address") or ""  # noqa
                if a.endswith("pump"): out.append(a)
    return out


def _seeds_dex_boosts():
    """Cheap DexScreener lists (60/min cap, separate from the search endpoint). Solana mints only."""
    global _dex_backoff_until
    if time.time() < _dex_backoff_until:
        raise RuntimeError(f"rate limited, backing off {int(_dex_backoff_until - time.time())}s")
    out = []
    for i, path in enumerate(("token-boosts/top/v1", "token-boosts/latest/v1", "token-profiles/latest/v1")):
        if i:
            time.sleep(0.8)
        try:
            d = _get("https://api.dexscreener.com/" + path, tries=2)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _dex_backoff_until = time.time() + 180
                if out: break
                raise
            continue
        rows = d if isinstance(d, list) else (d.get("items") or d.get("pairs") or [])
        for it in rows or []:
            if not isinstance(it, dict):
                continue
            chain = (it.get("chainId") or it.get("chain") or "").lower()
            if chain and chain not in ("solana", "sol"):
                continue
            a = it.get("tokenAddress") or (it.get("token") or {}).get("address") or ""
            if 32 <= len(a) <= 44:
                out.append(a)
    return out


_DP_QUOTES = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


def _seeds_dexpaprika():
    """Public DexPaprika search (different host from DexScreener/Gecko). Solana mints only."""
    out = []
    for i, q in enumerate(("pump", "SOL")):
        if i:
            time.sleep(0.6)
        d = _get("https://api.dexpaprika.com/search?query=" + urllib.parse.quote(q), tries=2)
        for p in d.get("pools") or []:
            if not isinstance(p, dict) or p.get("chain") != "solana":
                continue
            for tok in p.get("tokens") or []:
                a = (tok or {}).get("id") or ""
                if 32 <= len(a) <= 44 and a not in _DP_QUOTES:
                    out.append(a)
        for tok in d.get("tokens") or []:
            if not isinstance(tok, dict) or tok.get("chain") != "solana":
                continue
            a = tok.get("id") or ""
            if 32 <= len(a) <= 44 and a not in _DP_QUOTES:
                out.append(a)
    return out


def _fresh_seed_cache(name):
    hit = _seed_cache.get(name)
    if hit and time.time() - hit[0] < SEED_CACHE_S and hit[1]:
        return list(hit[1])
    return None


def _remember_seeds(name, got):
    got = list(dict.fromkeys(m for m in got if 32 <= len(m) <= 44))
    if got:
        _seed_cache[name] = (time.time(), got)
        _merge_seed_list(name, got)
    return got


def gather_seeds():
    seeds, counts, errs = [], {}, {}
    srcs = [("geckoterminal", _seeds_gecko), ("pumpfun", _seeds_pump),
            ("dexpaprika", _seeds_dexpaprika)]
    # DexScreener seed lists share an IP with scan.py pair lookups. Use a 10-min cache,
    # and only hit the network when not in backoff; otherwise reuse last-good / bundled lists.
    http_ds = (status.get("http") or {}).get("api.dexscreener.com", {})
    ds_calm = time.time() >= _dex_backoff_until and not any(k == "429" for k in http_ds)
    if ds_calm:
        srcs.append(("dexscreener_boosts", _seeds_dex_boosts))
        srcs.append(("dexscreener_search", _seeds_dexscreener))

    def add(name, got, note=None):
        got = [m for m in got if 32 <= len(m) <= 44]
        counts[name] = len(got)
        seeds.extend(got)
        if note:
            errs[name] = note

    for i, (name, fn) in enumerate(srcs):
        cached = _fresh_seed_cache(name)
        if cached:
            add(name, cached, f"cached {int(time.time() - _seed_cache[name][0])}s")
            continue
        if i:
            time.sleep(1.2)
        try:
            got = _remember_seeds(name, fn())
            add(name, got)
        except Exception as e:
            reused = [m for m in _last_good_seeds.get(name) or [] if 32 <= len(m) <= 44]
            if reused:
                add(name, reused, f"{str(e)[:120]} (reused {len(reused)} last-good seeds)")
            else:
                add(name, [], str(e)[:160])

    # Sources we didn't fetch this pass (e.g. DexScreener in backoff) still contribute last-good lists
    for name, vals in _last_good_seeds.items():
        if name not in counts and vals:
            add(name, vals, f"reused {len(vals)} last-good seeds")

    uniq = list(dict.fromkeys(seeds))[:90]
    if len(uniq) < 60:
        extra = []
        for v in _last_good_seeds.values():
            extra += v
        uniq = list(dict.fromkeys(list(uniq) + extra))[:90]
        if uniq and "reused_all" not in errs:
            errs["reused_fill"] = f"filled to {len(uniq)} with last-good seeds"
    _save_last_seeds()
    return uniq, counts, errs


# ---------------------------------------------------------------- one scan cycle
def scan_once():
    status.update(running=True, last_attempt_at=_now())
    t0 = time.time()
    try:
        seeds, counts, errs = gather_seeds()
        status["seeds"], status["source_errors"] = {**counts, "total_unique": len(seeds)}, errs
        _atomic_write(os.path.join(WORK, "candidates.json"), [{"mint": m} for m in seeds])
        for f in ("report.json", "dashboard_data.json"):
            try: os.remove(os.path.join(WORK, f))
            except FileNotFoundError: pass
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_runner.py")
        p = subprocess.run([sys.executable, runner, WORK, "--allow-non-pump", "--top", "15"],
                           cwd=WORK, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
        rep = os.path.join(WORK, "report.json")
        if p.returncode != 0 or not os.path.exists(rep):
            raise RuntimeError(f"scan.py exit {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
        try: status["http"] = json.load(open(os.path.join(WORK, "http_stats.json")))
        except Exception: pass
        r = json.load(open(rep))  # validate before publishing; never replace good data with an empty scan
        if not r.get("top") or not r.get("coins_checked"):
            raise RuntimeError(f"scan came back empty (upstream APIs rate-limited: {status.get('http')}); kept previous data")
        status["last_counts"] = {"coins_checked": r.get("coins_checked"), "deep_checked": r.get("deep_checked"), "top": len(r["top"])}
        # thin scan guard: when DexScreener rate-limits us, a 2-coin result must not replace a full list
        try: prev_top = len(json.load(open(os.path.join(SERVE, "report.json"))).get("top") or [])
        except Exception: prev_top = 0
        pub_age = time.time() - (status.get("published_epoch") or 0)
        if len(r["top"]) < 5 and prev_top >= 5 and pub_age < THIN_KEEP_S:
            status["thin_skipped"] = status.get("thin_skipped", 0) + 1
            raise RuntimeError(f"thin scan ({len(r['top'])} coins, upstream rate-limited); kept the previous {prev_top}-coin list")
        for f in ("dashboard_data.json", "report.json"):
            src = os.path.join(WORK, f)
            if os.path.exists(src):
                tmp = os.path.join(SERVE, f + ".tmp"); shutil.copy2(src, tmp); os.replace(tmp, os.path.join(SERVE, f))
        _touch_ledger()
        status["published_epoch"] = time.time()
        status.update(snapshot_time=_now(), scans_ok=status["scans_ok"] + 1, last_scan_at=_now(), last_scan_epoch=int(time.time()), last_error=None)
        try:
            import images as im
            im.warm([{"mint": c.get("mint"), "symbol": c.get("symbol")} for c in (r.get("top") or []) if c.get("mint")])
        except Exception:
            pass
    except Exception as e:
        status.update(scans_failed=status["scans_failed"] + 1, last_error=f"{type(e).__name__}: {e}"[:400])
    finally:
        status.update(running=False, last_duration_s=round(time.time() - t0, 1))


# ---------------------------------------------------------------- optional live X feed (needs X_BEARER_TOKEN)
def x_once(xfeed_path, queries_path):
    tok = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not tok:
        return
    import xfeed_ingest as xi
    try:
        qs = json.load(open(queries_path)).get("queries") or []
        start = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new, fetched = [], xi.now_iso()
        for q in qs:
            params = {"query": q["query"], "max_results": "25", "start_time": start,
                      "sort_order": q.get("sort_order", "recency"),
                      "tweet.fields": "created_at,public_metrics,author_id,lang,entities",
                      "expansions": "author_id", "user.fields": "username,name,public_metrics,verified"}
            r = _get("https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(params), timeout=15,
                     headers={"Authorization": "Bearer " + tok})
            new += xi.normalise_api(r, q["topic"], fetched)
        feed = xi.load_feed(xfeed_path)
        _, per = xi.merge(feed, new, 300, 48)
        ingested = xi.now_iso()
        feed["updated_at"] = ingested
        for t in xi.TOPICS:
            info = feed["topics"].setdefault(t, {})
            info.update(last_ingest=ingested, count=per.get(t, 0),
                        newest=next((p.get("created_at") for p in feed["posts"] if p.get("topic") == t), None))
        _atomic_write(xfeed_path, feed)
        status["x"].update(live=True, reason="live X API", last_fetch_at=_now(), last_error=None)
    except Exception as e:
        msg = f"{type(e).__name__}: {getattr(e, 'code', '')} {e}"
        status["x"].update(last_error=msg[:300].replace(tok, "***"))


PUSH_COPIES = [os.environ.get("LIVE_PUSH_BUNDLED", "deploy/data/last_push.json")]
PUSH_URL = os.environ.get("LIVE_PUSH_URL", "")   # optional remote copy (e.g. raw GitHub URL) that survives sleep/wake


def restore_last_push(xfeed_path):
    """On startup (/tmp is wiped by every deploy and wake on Render free) re-apply the newest pushed X data,
    but only if it is newer than the X feed the bundle ships with. Sources: the copy bundled at deploy time,
    plus PUSH_URL if set. Never fatal."""
    import xfeed_ingest as xi
    cands = []
    for pth in PUSH_COPIES:
        try: cands.append(("bundled", json.load(open(pth))))
        except Exception: pass
    if PUSH_URL:
        try: cands.append(("remote", _get(PUSH_URL + ("&" if "?" in PUSH_URL else "?") + f"t={int(time.time())}", timeout=20)))
        except Exception as e: status["x"]["restore_error"] = f"remote copy: {type(e).__name__}"[:120]
    cands = [(src, d) for src, d in cands if isinstance(d, dict) and d.get("pushed_at")]
    status["x"]["copies_seen"] = {src: d["pushed_at"] for src, d in cands}   # raw.githubusercontent may lag a push by ~5 min
    if not cands:
        status["x"]["restored_from"] = None; return
    src, best = max(cands, key=lambda c: (c[1]["pushed_at"], c[0] == "remote"))   # tie -> remote
    cur = xi.load_feed(xfeed_path).get("updated_at") or ""
    if best["pushed_at"] <= cur:   # both ISO UTC 'Z' strings
        status["x"]["restored_from"] = f"none (bundled feed {cur} is newer than last push {best['pushed_at']})"; return
    try:
        ingest_x(best, xfeed_path, fetched=best["pushed_at"])
        status["x"].update(restored_from=src, restored_push_at=best["pushed_at"])
    except Exception as e:
        status["x"]["restore_error"] = f"{type(e).__name__}: {e}"[:200]


def _loop(xfeed_path, queries_path):
    last_x = 0.0
    _load_last_seeds()
    restore_last_push(xfeed_path); _kick.clear()
    while True:
        scan_once()
        if os.environ.get("X_BEARER_TOKEN") and time.time() - last_x >= X_INTERVAL:
            last_x = time.time(); x_once(xfeed_path, queries_path)
        status["next_scan_epoch"] = int(time.time() + INTERVAL)
        _kick.wait(INTERVAL); _kick.clear()


def start(xfeed_path, queries_path):
    global _started
    if not ENABLED or _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(xfeed_path, queries_path), daemon=True, name="xscanner-live").start()


def public_status():
    s = json.loads(json.dumps(status))
    if s["last_scan_epoch"]:
        s["age_s"] = int(time.time() - s["last_scan_epoch"])
    s["now_epoch"] = int(time.time())
    return s


# ---------------------------------------------------------------- pushed X data (POST /api/ingest/x)
_ID = __import__("re").compile(r"^[0-9]{1,25}$")


def _as_map(v, what):
    """posts/users may come as {id: obj} (x_raw.json) or [obj, ...] (X API 'data' / 'includes.users')."""
    if v is None: return {}
    if isinstance(v, dict): items = list(v.values())
    elif isinstance(v, list): items = v
    else: raise ValueError(f"{what} must be an object or a list")
    out = {}
    for o in items:
        if not isinstance(o, dict): raise ValueError(f"every {what[:-1]} must be an object")
        i = str(o.get("id") or "")
        if not _ID.match(i): raise ValueError(f"{what[:-1]} with bad id {i[:30]!r}")
        out[i] = o
    return out


def _clean_post(p):
    if not isinstance(p.get("text", ""), str): raise ValueError(f"post {p.get('id')}: text must be a string")
    keep = ("id", "author_id", "created_at", "text", "public_metrics", "entities", "url", "conversation_id",
            "in_reply_to_user_id", "lang", "note_tweet", "referenced_tweets", "_label")
    q = {k: p[k] for k in keep if k in p}
    q["id"] = str(q["id"]); q["text"] = (q.get("text") or "")[:4000]
    q.setdefault("_label", "push")
    return q


def _clean_user(u):
    keep = ("id", "username", "name", "public_metrics", "created_at", "verified")
    q = {k: u[k] for k in keep if k in u}; q["id"] = str(q["id"])
    return q


def ingest_x(payload, xfeed_path, fetched=None):
    """Validate pushed X data, write work/x_raw.json (used by the next scan) and merge the X Live feed.
    payload = {"x_raw": {posts, users, counts?, trends?}, "feed": [{"topic", "response": <X API search response>}]?,
               "mode": "replace"|"merge"}. Without "feed", x_raw posts are shown in the memecoins feed."""
    import xfeed_ingest as xi
    if not isinstance(payload, dict): raise ValueError("payload must be a JSON object")
    raw_in = payload.get("x_raw") if isinstance(payload.get("x_raw"), dict) else payload
    posts = {i: _clean_post(p) for i, p in _as_map(raw_in.get("posts"), "posts").items()}
    users = {i: _clean_user(u) for i, u in _as_map(raw_in.get("users"), "users").items()}
    counts = raw_in.get("counts") or {}
    trends = raw_in.get("trends") or []
    if not isinstance(counts, dict) or not all(isinstance(v, list) for v in counts.values()): raise ValueError("counts must map key -> list of buckets")
    if not isinstance(trends, list) or not all(isinstance(t, str) for t in trends): raise ValueError("trends must be a list of strings")
    feed_in = payload.get("feed") or []
    if not isinstance(feed_in, list): raise ValueError("feed must be a list of {topic, response}")
    if not posts and not feed_in: raise ValueError("no posts in payload")
    if len(posts) > 3000 or len(users) > 5000: raise ValueError("too many posts/users")
    mode = payload.get("mode", "replace")
    with _ingest_lock:
        rawp = os.path.join(WORK, "x_raw.json")
        if mode == "merge":
            try: old = json.load(open(rawp))
            except Exception: old = {}
            posts = {**(old.get("posts") or {}), **posts}; users = {**(old.get("users") or {}), **users}
            counts = {**(old.get("counts") or {}), **counts}; trends = list(dict.fromkeys((old.get("trends") or []) + trends))
        now = time.time()
        if posts:
            _atomic_write(rawp, {"created": now, "posts": posts, "users": users, "counts": counts, "trends": trends[:200],
                                 "searches": [{"label": "push", "t": now}]})
        fetched = fetched or xi.now_iso(); new = []
        for f in feed_in:
            if not isinstance(f, dict) or f.get("topic") not in xi.TOPICS or not isinstance(f.get("response"), dict):
                raise ValueError(f"feed items need topic in {xi.TOPICS} and a response object")
            new += xi.normalise_api(f["response"], f["topic"], fetched)
        if not feed_in and posts:   # no separate feed: show the scan posts as the memecoins feed
            new = xi.normalise_api({"data": list(posts.values()), "includes": {"users": list(users.values())}}, "memecoins", fetched)
        received = len(new)
        uniq = {(p["topic"], p["id"]): p for p in new}
        touched = {t for t, _ in uniq}
        feed = xi.load_feed(xfeed_path)
        if mode == "replace":   # fresh push: drop every older post of the topics it covers (e.g. the bundled Nov 14 posts)
            feed["posts"] = [p for p in feed["posts"] if p.get("topic") not in touched]
        _, per = xi.merge(feed, list(uniq.values()), 300, 48)
        ingested = xi.now_iso()
        feed["updated_at"] = ingested   # ingest time, not the original push/fetch stamp
        for t in xi.TOPICS:
            info = feed["topics"].setdefault(t, {})
            if t in touched: info["last_ingest"] = ingested
            info.update(count=per.get(t, 0), newest=next((p.get("created_at") for p in feed["posts"] if p.get("topic") == t), None))
        _atomic_write(xfeed_path, feed)
        led = payload.get("ledger")
        if led is not None:
            if not isinstance(led, dict) or not isinstance(led.get("closed_trades", []), list):
                raise ValueError("ledger must be an object with a closed_trades list")
            led = {**led, "data_as_of": _now(), "pushed_at": _now()}
            _atomic_write(LEDGER, led); status["ledger_as_of"] = led["data_as_of"]
    new_by_topic = {t: sum(1 for k in uniq if k[0] == t) for t in sorted(touched)}
    summary = {"posts": len(posts), "users": len(users), "counts": len(counts), "trends": len(trends), "mode": mode,
               "feed_posts_received": received, "feed_posts_unique": len(uniq), "feed_new_by_topic": new_by_topic,
               "feed_counts": {t: per.get(t, 0) for t in xi.TOPICS}, "ledger": led is not None}
    status["x"].update(last_ingest_at=_now(), last_ingest=summary, reason="X posts pushed from the agent box")
    _kick.set()   # rescan now with the fresh posts
    return summary
