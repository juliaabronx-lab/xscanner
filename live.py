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
import json, os, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

MYT = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only; +https://xscanner-c8jo.onrender.com)", "Accept": "application/json"}
ENABLED = os.environ.get("LIVE_SCAN", "").strip().lower() in ("1", "true", "yes", "on")
LIVE_DIR = os.environ.get("LIVE_DIR", "/tmp/xscanner-live")
INTERVAL = max(60, min(int(os.environ.get("LIVE_SCAN_INTERVAL", "120")), 900))
X_INTERVAL = max(300, int(os.environ.get("LIVE_X_INTERVAL", "1800")))
SCAN_TIMEOUT = int(os.environ.get("LIVE_SCAN_TIMEOUT", "150"))
WORK, SERVE = os.path.join(LIVE_DIR, "work"), os.path.join(LIVE_DIR, "serve")

status = {"enabled": ENABLED, "interval_s": INTERVAL, "running": False, "scans_ok": 0, "scans_failed": 0,
          "last_scan_at": None, "last_scan_epoch": None, "last_attempt_at": None, "last_error": None,
          "last_duration_s": None, "next_scan_epoch": None, "seeds": {}, "source_errors": {},
          "snapshot_time": None, "x": {"live": False, "reason": "X_BEARER_TOKEN not set; showing stored X posts",
                                       "last_fetch_at": None, "last_error": None}}
_gecko_backoff_until = 0.0
_started = False


def _now():
    return datetime.now(MYT).isoformat(timespec="seconds")


def _get(url, timeout=12, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def prepare(snapshot_dir, xfeed_path):
    """Create work/serve dirs seeded from the bundled snapshot. Returns (serve_dir, xfeed_path_to_serve)."""
    os.makedirs(WORK, exist_ok=True); os.makedirs(SERVE, exist_ok=True)
    for f in ("scan.py", "config.json", "x_raw.json"):
        src = os.path.join(snapshot_dir, f)
        if os.path.exists(src): shutil.copy2(src, os.path.join(WORK, f))
    for f in ("report.json", "dashboard_data.json"):
        src, dst = os.path.join(snapshot_dir, f), os.path.join(SERVE, f)
        if os.path.exists(src) and not os.path.exists(dst): shutil.copy2(src, dst)
    try:
        status["snapshot_time"] = open(os.path.join(snapshot_dir, "SNAPSHOT_TIME")).read().strip()
    except Exception:
        pass
    xf = os.path.join(LIVE_DIR, "x_feed.json")
    if os.path.exists(xfeed_path) and not os.path.exists(xf): shutil.copy2(xfeed_path, xf)
    return SERVE, xf


# ---------------------------------------------------------------- seed sources (all public, no key)
def _seeds_gecko():
    global _gecko_backoff_until
    if time.time() < _gecko_backoff_until:
        raise RuntimeError(f"rate limited, backing off {int(_gecko_backoff_until - time.time())}s")
    out = []
    for path in ("trending_pools?page=1", "new_pools?page=1"):
        try:
            d = _get("https://api.geckoterminal.com/api/v2/networks/solana/" + path)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _gecko_backoff_until = time.time() + 600   # GeckoTerminal often 429s cloud IPs: skip 10 min
                if out: break
                raise RuntimeError("429 rate limited (skipping GeckoTerminal for 10 min)")
            raise
        for p in d.get("data") or []:
            tid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id") or ""
            if tid.startswith("solana_"): out.append(tid[7:])
    return out


def _seeds_pump():
    # pump.fun's public frontend API sits behind Cloudflare and often refuses server IPs; best effort only.
    out = []
    for path in ("coins/currently-live?limit=30&offset=0&includeNsfw=false",
                 "coins?offset=0&limit=30&sort=last_trade_timestamp&order=DESC&includeNsfw=false"):
        try:
            d = _get("https://frontend-api-v3.pump.fun/" + path, timeout=8)
        except Exception as e:
            if not out and path.startswith("coins?"): raise RuntimeError(f"unavailable ({getattr(e, 'code', type(e).__name__)})")
            continue
        for c in (d if isinstance(d, list) else d.get("coins") or []):
            if isinstance(c, dict) and c.get("mint"): out.append(c["mint"])
    return out


def _seeds_dexscreener():
    # scan.py already pulls boosts + profiles; add DexScreener's pump.fun/pumpswap search for fresh pairs.
    out = []
    for q in ("pumpswap", "pump.fun"):
        d = _get("https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(q))
        for p in d.get("pairs") or []:
            if p.get("chainId") == "solana":
                a = (p.get("baseToken") or {}).get("address") or ""  # noqa
                if a.endswith("pump"): out.append(a)
    return out


def gather_seeds():
    seeds, counts, errs = [], {}, {}
    for name, fn in (("geckoterminal", _seeds_gecko), ("pumpfun", _seeds_pump), ("dexscreener_search", _seeds_dexscreener)):
        try:
            got = [m for m in fn() if 32 <= len(m) <= 44]
            counts[name] = len(got); seeds += got
        except Exception as e:
            counts[name] = 0; errs[name] = str(e)[:160]
    uniq = list(dict.fromkeys(seeds))[:90]   # keep DexScreener lookups small (30 mints per request)
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
        for f in ("dashboard_data.json", "report.json"):
            src = os.path.join(WORK, f)
            if os.path.exists(src):
                tmp = os.path.join(SERVE, f + ".tmp"); shutil.copy2(src, tmp); os.replace(tmp, os.path.join(SERVE, f))
        status.update(scans_ok=status["scans_ok"] + 1, last_scan_at=_now(), last_scan_epoch=int(time.time()), last_error=None)
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
        feed["updated_at"] = fetched
        for t in xi.TOPICS:
            info = feed["topics"].setdefault(t, {})
            info.update(last_ingest=fetched, count=per.get(t, 0),
                        newest=next((p.get("created_at") for p in feed["posts"] if p.get("topic") == t), None))
        _atomic_write(xfeed_path, feed)
        status["x"].update(live=True, reason="live X API", last_fetch_at=_now(), last_error=None)
    except Exception as e:
        msg = f"{type(e).__name__}: {getattr(e, 'code', '')} {e}"
        status["x"].update(last_error=msg[:300].replace(tok, "***"))


def _loop(xfeed_path, queries_path):
    last_x = 0.0
    while True:
        scan_once()
        if os.environ.get("X_BEARER_TOKEN") and time.time() - last_x >= X_INTERVAL:
            last_x = time.time(); x_once(xfeed_path, queries_path)
        status["next_scan_epoch"] = int(time.time() + INTERVAL)
        time.sleep(INTERVAL)


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
