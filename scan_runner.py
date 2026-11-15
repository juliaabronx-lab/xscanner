#!/usr/bin/env python3
"""Run a copy of scan.py (in the live work dir) with a gentler, rate-limit-aware HTTP getter.

Hosted servers share outbound IPs, so DexScreener/RugCheck/GeckoTerminal can answer 429. This wrapper
(used only by live.py) keeps scan.py's logic unchanged and only swaps its `get` helper:
  * at most 3 concurrent requests per host, with retries + backoff on 429/5xx,
  * DexScreener /latest/dex/tokens/<mints> falls back to /tokens/v1/solana/<mints> (separate limit),
  * per-host status counts are written to http_stats.json for /api/live.
Usage: python3 scan_runner.py <work_dir> [scan.py args...]   GET requests only; no keys, no trading.
"""
import hashlib, json, os, random, sys, threading, time, urllib.request, urllib.error
from collections import Counter, defaultdict

WORK = os.path.abspath(sys.argv[1])
sys.argv = [os.path.join(WORK, "scan.py")] + sys.argv[2:]
sys.path.insert(0, WORK)
import scan  # noqa: E402  (scan.py copied into WORK; reads config.json / x_raw.json from there)

_sems = defaultdict(lambda: threading.Semaphore(3))
_sems["api.dexscreener.com"] = threading.Semaphore(2)
_pace = {"api.dexscreener.com": 0.25}          # min seconds between request starts per host
_last = defaultdict(float)
_pace_lock = threading.Lock()
CACHE = os.path.join(WORK, "cache"); os.makedirs(CACHE, exist_ok=True)
# Slow-moving DexScreener lists are cached across scans (fresh pair data still comes from the 30-mint batch endpoint)
TTL = (("/latest/dex/search", 600), ("/token-boosts/", 240), ("/token-profiles/", 240))


def _ttl(url):
    return next((t for k, t in TTL if k in url), 0)


def _cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")


def _cache_get(url, max_age):
    p = _cache_path(url)
    try:
        if time.time() - os.path.getmtime(p) <= max_age: return json.load(open(p))
    except Exception:
        pass
    return None


def _cache_put(url, obj):
    try:
        tmp = _cache_path(url) + ".tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, _cache_path(url))
    except Exception:
        pass
_stats = defaultdict(Counter)
_lock = threading.Lock()


def _host(url):
    return url.split("/")[2]


def _once(url, timeout):
    req = urllib.request.Request(url, headers=scan.UA)
    h = _host(url)
    with _sems[h]:
        if h in _pace:
            with _pace_lock:
                wait = _last[h] + _pace[h] - time.time()
                _last[h] = max(time.time(), _last[h] + _pace[h])
            if wait > 0: time.sleep(wait)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())


def get(url, timeout=12, tries=4):
    ttl = _ttl(url)
    if ttl:
        hit = _cache_get(url, ttl)
        if hit is not None:
            with _lock: _stats[_host(url)]["cache"] += 1
            return hit
    out = _get(url, timeout, tries)
    if ttl:
        if "_error" not in out: _cache_put(url, out)
        else:
            stale = _cache_get(url, 3600)   # upstream refused: last good answer (<1 h) beats nothing
            if stale is not None:
                with _lock: _stats[_host(url)]["stale"] += 1
                return stale
    return out


def _get(url, timeout=12, tries=4):
    err, h = None, _host(url)
    for i in range(tries):
        try:
            out = _once(url, timeout)
            with _lock: _stats[h]["200"] += 1
            return out
        except urllib.error.HTTPError as e:
            err = e
            with _lock: _stats[h][str(e.code)] += 1
            if e.code == 404: break
            if e.code == 429 and "/latest/dex/tokens/" in url:
                alt = url.replace("/latest/dex/tokens/", "/tokens/v1/solana/")
                try:
                    lst = _once(alt, timeout)
                    with _lock: _stats[h]["200-alt"] += 1
                    return {"pairs": lst if isinstance(lst, list) else []}
                except Exception as e2:
                    err = e2
            time.sleep((1.5 * 2 ** i) + random.random())
        except Exception as e:
            err = e
            with _lock: _stats[h][type(e).__name__] += 1
            time.sleep(0.8 * (i + 1))
    return {"_error": str(err)}


scan.get = get
try:
    scan.main()
finally:
    json.dump({h: dict(c) for h, c in _stats.items()}, open(os.path.join(WORK, "http_stats.json"), "w"))
