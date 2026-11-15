"""xScanner — local read-only dashboard over Advisor's scanner output.

Reads (never writes): /workspace/scanner/report.json, /workspace/scanner/dashboard_data.json,
/workspace/scanner/config.json, /workspace/paper/ledger.json.
The only side effect is POST /api/scan, which runs `python3 scan.py` (disabled with HTTP 403 when PUBLIC_READONLY=1). That command's normal behaviour:
DexScreener + RugCheck refresh, reusing the last x_raw.json; scan.py itself never calls X).
No wallets, keys, signing, trading or posting anywhere.
"""
import json, os, re, subprocess, sys, threading, time
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException, Request
import lookup as lk
import live
import chart as ch
import images as im
from fastapi.responses import Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SCANNER = os.environ.get("SCANNER_DIR", "/workspace/scanner")
LEDGER = os.environ.get("LEDGER_PATH", "/workspace/paper/ledger.json")
HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "180"))
PUBLIC_READONLY = os.environ.get("PUBLIC_READONLY", "").strip().lower() in ("1", "true", "yes", "on")
MYT = timezone(timedelta(hours=8))

app = FastAPI(title="xScanner", docs_url="/api/docs")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=800)  # smaller pages over the tunnel / mobile


def load(path, default):
    """Read JSON; return (data, meta). Missing/empty/corrupt -> default + error note."""
    meta = {"path": path, "exists": os.path.exists(path), "error": None, "mtime": None}
    if not meta["exists"]:
        meta["error"] = "missing"; return default, meta
    try:
        meta["mtime"] = datetime.fromtimestamp(os.path.getmtime(path), MYT).isoformat(timespec="seconds")
        txt = open(path, encoding="utf-8").read()
        if not txt.strip():
            meta["error"] = "empty"; return default, meta
        data = json.loads(txt)
        if data is None or (isinstance(default, dict) and not isinstance(data, dict)):
            meta["error"] = "unexpected type"; return default, meta
        return data, meta
    except Exception as e:  # half-written file during a scan, bad JSON, etc.
        meta["error"] = f"{type(e).__name__}: {e}"; return default, meta


def num(v, d=None):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def fmt_usd(v):
    """Same K/M/B/T scale as the frontend usd() helper: $882M, not $881932K."""
    v = num(v)
    if v is None:
        return "—"
    a = abs(v)
    sign = "-" if v < 0 else ""
    for d, u in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= d:
            x = a / d
            decimals = 0 if x >= 100 else 1 if x >= 10 else 2
            s = f"{x:.{decimals}f}"
            if "." in s: s = s.rstrip("0").rstrip(".")
            return f"{sign}${s}{u}"
    if a >= 1 or a == 0:
        return f"{sign}${a:.0f}"
    return f"{sign}${a:.4g}"


_K_MONEY = re.compile(r"\$(\d+(?:\.\d+)?)K\b")


def rewrite_money(text):
    """Upgrade scan.py's always-K amounts ($881932K, $2255K) to K/M/B/T."""
    if not text or not isinstance(text, str):
        return text
    def repl(m):
        return fmt_usd(float(m.group(1)) * 1000)
    return _K_MONEY.sub(repl, text)


def fmt_age(h):
    """Same m/h/d/mo/y scale as the frontend ageTxt() helper: 34101.1h -> 3.9y."""
    h = num(h)
    if h is None:
        return "—"
    if h < 1:
        return f"{max(1, int(round(h * 60)))}m"
    if h < 48:
        return (f"{h:.1f}h" if h < 10 else f"{h:.0f}h")
    d = h / 24
    if d < 60:
        return f"{int(round(d))}d"
    if d < 730:
        return f"{int(round(d / 30.4))}mo"
    return f"{d / 365:.1f}y"


_AGE = re.compile(r"\bage (\d+(?:\.\d+)?)h\b")


def rewrite_age(text):
    if not text or not isinstance(text, str):
        return text
    return _AGE.sub(lambda m: "age " + fmt_age(float(m.group(1))), text)


def rewrite_text(text):
    """Apply the shared money and age formatters to scan.py reason/flag strings."""
    return rewrite_age(rewrite_money(text))


def pair(v):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return v[0], v[1]
    return None, None


def is_pump(c):
    mint = (c.get("mint") or "")
    lp = ((c.get("rugcheck") or {}).get("launchpad") or "").lower()
    return mint.endswith("pump") or (c.get("dex") or "").lower() in ("pumpfun", "pumpswap") or "pump" in lp


def risk_axes(c):
    """0..1 per axis, higher = riskier. Derived from report fields + flag text."""
    flags = " | ".join(c.get("flags") or []).lower()
    rc = c.get("rugcheck") or {}
    x = c.get("x") or {}
    paid = num(x.get("shill_share"), 0) or 0
    if "paid shill" in flags: paid = max(paid, 0.5)
    if "paid dexscreener boost" in flags: paid = max(paid, 0.3)
    cc = num(c.get("copycats"), 0) or 0
    copy = 1.0 if "copycat" in flags and "same-name" not in flags else min(1.0, cc / 15)
    bpct = num(rc.get("bundle_pct"), 0) or 0
    bundle = min(1.0, bpct / 20)
    if "bundle:" in flags.replace("bundle: ", "bundle:"): bundle = max(bundle, 0.5)
    pc1 = num(c.get("pc1"), 0) or 0
    b5, s5 = pair(c.get("m5"))
    dump = 0.0
    if pc1 < 0: dump = min(1.0, -pc1 / 50)
    if b5 is not None and s5 and (b5 or 0) > 0 and s5 / b5 >= 2.5: dump = 1.0
    if "dump" in flags: dump = 1.0
    if "fading" in flags: dump = max(dump, 0.6)
    rs = num(rc.get("score"), 0) or 0
    rug = min(1.0, rs / 100) if rs <= 100 else min(1.0, rs / 1000)
    if rc.get("creator_rugged") or rc.get("rugged") or "rug" in flags: rug = 1.0
    if rc.get("mint_auth") or rc.get("freeze_auth"): rug = max(rug, 0.8)
    liq = num(c.get("liq"), 0) or 0
    thin = 1.0 if liq < 5000 else 0.7 if liq < 20000 else 0.3 if liq < 50000 else 0.0
    return {"paid_shill": round(paid, 2), "copycat": round(copy, 2), "bundle": round(bundle, 2),
            "dump": round(dump, 2), "rug": round(rug, 2), "thin_liq": round(thin, 2)}


DATA_SCORE_CAP = 35   # coins with impossible/missing core data can never reach "watch" (50)


def data_issues(c):
    """Sanity-check DexScreener/RugCheck core numbers. Returns a list of human-readable problems."""
    mcap, liq, vol = num(c.get("mcap")), num(c.get("liq")), num(c.get("vol24"))
    out = []
    if mcap is None or mcap <= 0: out.append("missing market cap")
    elif mcap > 1e11: out.append(f"implausible market cap (${mcap/1e12:.1f}T)" if mcap >= 1e12 else "implausible market cap")
    if liq is None or liq <= 0: out.append("missing liquidity")
    elif liq < 100: out.append("liquidity near zero")
    elif mcap and mcap > 0 and liq > 1.0 * mcap: out.append("liquidity above market cap")
    if vol is None or vol <= 0: out.append("no 24h volume")
    return out


def tier(score):
    s = num(score, 0) or 0
    return "look" if s >= 65 else "watch" if s >= 50 else "skip"


def normalise_report_coin(c, dash):
    x = c.get("x") or {}
    rc = c.get("rugcheck") or {}
    b5, s5 = pair(c.get("m5")); b1, s1 = pair(c.get("h1"))
    flags = [rewrite_text(f) for f in (c.get("flags") or [])]
    paid_share = num(x.get("shill_share"))
    out = {
        "source": "report", "score": c.get("score"), "parts": c.get("parts") or {}, "tier": tier(c.get("score")),
        "name": (c.get("name") or "").strip(), "symbol": c.get("symbol"), "mint": c.get("mint"), "pool": c.get("pool"),
        "dex": c.get("dex"), "url": c.get("url"), "age_h": c.get("age_h"), "liq": c.get("liq"), "mcap": c.get("mcap"),
        "vol24": c.get("vol24"), "pc5": c.get("pc5"), "pc1": c.get("pc1"), "pc24": c.get("pc24"),
        "buys5": b5, "sells5": s5, "buys1h": b1, "sells1h": s1, "fees_est": c.get("fees_est"), "boosts": c.get("boosts"),
        "copycats": c.get("copycats"), "reason": rewrite_text(c.get("reason")), "flags": [rewrite_text(f) for f in flags],
        "pump": is_pump(c), "paid_share": paid_share,
        "paid_shilled": bool((paid_share or 0) >= 0.5 or any("paid shill" in f.lower() for f in flags)),
        "x": x, "vel": c.get("vel") or {}, "rugcheck": rc, "organic_examples": c.get("organic_examples") or [],
        "risk": risk_axes(c),
    }
    d = dash.get(c.get("mint")) or {}
    out["status"] = d.get("status")
    if d.get("paidAccts"): out["paid_accounts"] = d["paidAccts"]
    issues = data_issues(out)
    out["data_issues"] = issues
    if issues:   # flag + penalize: keep the raw score for reference, cap the shown one below "watch"
        out["score_raw"] = out["score"]
        sc = num(out["score"], 0) or 0
        if sc > DATA_SCORE_CAP: out["score"] = DATA_SCORE_CAP
        out["tier"] = tier(out["score"])
        out["flags"] = ["DATA: " + "; ".join(issues)] + list(flags)
        if out["status"] in (None, "buy", "watch"): out["status"] = "bad data"
    return out


def normalise_dash_coin(d):
    # dashboard_data.json coins (written by scan.py) use camelCase; map to the same shape
    c = {"score": d.get("score"), "parts": d.get("parts"), "name": d.get("name"), "symbol": d.get("ticker"),
         "mint": d.get("mint"), "pool": d.get("pool"), "dex": d.get("dex"), "url": d.get("url"), "age_h": d.get("ageH"),
         "liq": d.get("liq"), "mcap": d.get("mcap"), "pc1": d.get("pc1"), "pc5": d.get("pc5"), "pc24": d.get("pc24"),
         "m5": [d.get("b5"), d.get("s5")], "h1": [d.get("b1"), d.get("s1")], "fees_est": d.get("feesEst"),
         "boosts": d.get("boosts"), "copycats": d.get("copycats"), "reason": d.get("reason"), "flags": d.get("flags") or [],
         "x": {"mentions": d.get("mentions"), "organic_authors": d.get("real"), "shill_authors": d.get("paid"),
               "shill_share": d.get("paidShare")},
         "vel": {"last": d.get("velNow"), "prev_avg": d.get("velAvg"), "ratio": d.get("velRatio")},
         "rugcheck": {"score": d.get("rugScore"), "risks": d.get("risks") or [], "top10": d.get("top10"),
                      "holders": d.get("holders"), "bundle_pct": d.get("bundlePct"), "bundle_wallets": d.get("bundleWallets")},
         "organic_examples": d.get("examples") or []}
    out = normalise_report_coin(c, {d.get("mint"): d})
    out["source"] = "dashboard_data"
    if d.get("pump") is not None: out["pump"] = bool(d["pump"])
    return out


def _fmt_myt(iso):
    """Turn an ISO timestamp into the 'YYYY-MM-DD HH:MM MYT' label the UI expects."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MYT)
        return dt.astimezone(MYT).strftime("%Y-%m-%d %H:%M MYT")
    except Exception:
        return str(iso)


def _scan_generated(report, dashd):
    """Prefer the live loop's last successful scan, then SNAPSHOT_TIME, then the report stamp.
    Stops a cold start from showing a stale bundled generated_myt (e.g. 14 Nov)."""
    for raw in (live.status.get("last_scan_at"), live.status.get("snapshot_time"),
                live.status.get("bundle_snapshot_time")):
        label = _fmt_myt(raw)
        if label:
            return label
    return report.get("generated_myt") or dashd.get("scanTime")


def build_state():
    report, rmeta = load(os.path.join(SCANNER, "report.json"), {})
    dashd, dmeta = load(os.path.join(SCANNER, "dashboard_data.json"), {})
    dash_coins = {c.get("mint"): c for c in (dashd.get("coins") or []) if isinstance(c, dict)}
    coins, seen = [], set()
    for c in report.get("top") or []:
        if isinstance(c, dict) and c.get("mint") not in seen:
            seen.add(c.get("mint")); coins.append(normalise_report_coin(c, dash_coins))
    for m, d in dash_coins.items():
        if m not in seen:
            seen.add(m); coins.append(normalise_dash_coin(d))
    coins.sort(key=lambda c: -(num(c.get("score"), 0) or 0))
    buy = [c["symbol"] for c in coins if c["tier"] == "look" and c.get("pump") and c.get("status") != "blocked" and not c.get("data_issues")]
    summary = {
        "generated": _scan_generated(report, dashd),
        "elapsed_s": report.get("elapsed_s"), "x_posts": report.get("x_posts"), "x_shill_posts": report.get("x_shill_posts"),
        "coins_checked": report.get("coins_checked"), "deep_checked": report.get("deep_checked"),
        "verdict": (f"Buy-worthy: {', '.join(buy[:3])}" if buy else "Nothing worth buying right now") if (report or dashd) else None, "blacklisted_seen": report.get("blacklisted_seen") or [],
        "themes": [{"word": t[0], "tickers": t[1]} if isinstance(t, (list, tuple)) else t for t in (report.get("themes") or [])],
    }
    return coins, summary, dashd, {"report": rmeta, "dashboard_data": dmeta}


@app.get("/api/health")
def health():
    _, _, _, meta = build_state()
    _, lmeta = load(LEDGER, {})
    return {"ok": True, "readonly": True, "public_readonly": PUBLIC_READONLY, "sources": {**meta, "ledger": lmeta}, "scan": scan_state_public()}


@app.get("/api/summary")
def summary():
    coins, s, _, meta = build_state()
    s["coin_count"] = len(coins)
    s["tiers"] = {t: sum(1 for c in coins if c["tier"] == t) for t in ("look", "watch", "skip")}
    s["sources"] = meta
    s["public_readonly"] = PUBLIC_READONLY
    s["live"] = live.public_status()
    return s


@app.get("/api/coins")
def coins(min_score: float = 0, min_liq: float = 0, pump_only: bool = False, hide_paid: bool = False,
          sort: str = "score", desc: bool = True):
    cs, _, _, meta = build_state()
    out = [c for c in cs if (num(c.get("score"), 0) or 0) >= min_score and (num(c.get("liq"), 0) or 0) >= min_liq
           and (not pump_only or c["pump"]) and (not hide_paid or not c["paid_shilled"])]
    if sort in ("symbol", "name", "dex", "tier"):
        out.sort(key=lambda c: str(c.get(sort) or "").lower(), reverse=desc)
    else:
        out.sort(key=lambda c: num(c.get(sort), float("-inf")), reverse=desc)
    im.warm(out)
    for c in out: c["img_real"] = im.has_file(c.get("mint") or "")
    return {"count": len(out), "total": len(cs), "coins": out, "sources": meta}


@app.get("/api/coins/{mint}")
def coin(mint: str):
    cs, _, dashd, _ = build_state()
    for c in cs:
        if c.get("mint") == mint:
            n = next((n for n in (dashd.get("narratives") or []) if n.get("mint") == mint), None)
            return {**c, "narrative": n}
    raise HTTPException(404, "coin not in current report")


@app.get("/api/narratives")
def narratives():
    cs, s, dashd, meta = build_state()
    narr = dashd.get("narratives") or []
    if not narr:  # fall back to report velocity data
        narr = [{"theme": c["name"], "ticker": c["symbol"], "mint": c["mint"], "series": [],
                 "velNow": c["vel"].get("last"), "velAvg": c["vel"].get("prev_avg"), "velRatio": c["vel"].get("ratio"),
                 "real": c["x"].get("organic_authors"), "paid": c["x"].get("shill_authors"),
                 "mentions": c["x"].get("mentions"), "blacklisted": False} for c in cs if c.get("vel")]
    themes = [{"word": t["word"], "tickers": t["tickers"]} for t in s["themes"]] or (dashd.get("themes") or [])
    return {"narratives": narr, "themes": themes, "blacklisted_seen": s["blacklisted_seen"],
            "blocked": dashd.get("blocked") or [], "sources": meta}


@app.get("/api/risk")
def risk():
    cs, _, dashd, meta = build_state()
    axes = ["paid_shill", "copycat", "bundle", "dump", "rug", "thin_liq"]
    rows = [{"symbol": c["symbol"], "mint": c["mint"], "score": c["score"], "risk": c["risk"], "flags": c["flags"]} for c in cs]
    totals = {a: sum(1 for r in rows if r["risk"][a] >= 0.5) for a in axes}
    return {"axes": axes, "coins": rows, "flag_counts": totals, "blocked": dashd.get("blocked") or [], "sources": meta}


@app.get("/api/paper")
def paper():
    L, meta = load(LEDGER, {})
    trades = [t for t in (L.get("closed_trades") or []) if isinstance(t, dict)]
    trades.sort(key=lambda t: t.get("exit_time") or t.get("entry_time") or "")
    cum, curve = 0.0, []
    for t in trades:
        cum += num(t.get("pnl_rm"), 0) or 0
        curve.append({"time": t.get("exit_time"), "symbol": t.get("symbol"), "pnl": t.get("pnl_rm"), "cum": round(cum, 2)})
    wins = [t for t in trades if (num(t.get("pnl_rm"), 0) or 0) > 0]
    start = num(L.get("start_rm"))
    cash = num(L.get("cash_rm"))
    op = L.get("open_position")
    return {
        "start_rm": start, "cash_rm": cash, "usdc_profit_rm": L.get("usdc_profit_rm"), "open_position": op,
        "realised_pnl_rm": round(cum, 2), "trades": trades, "curve": curve,
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "n_trades": len(trades), "best": max(trades, key=lambda t: num(t.get("pnl_rm"), 0) or 0) if trades else None,
        "worst": min(trades, key=lambda t: num(t.get("pnl_rm"), 0) or 0) if trades else None,
        "rules": L.get("rules_v2") or L.get("rules"), "fees": L.get("cost_note"),
        "started": L.get("started"), "ends": L.get("ends"), "last_run": L.get("last_run"), "data_as_of": L.get("data_as_of") or meta.get("mtime"), "last_run_note": L.get("last_run_note"),
        "candidates_seen": (L.get("candidates_seen") or [])[-60:][::-1], "sources": {"ledger": meta},
    }


# ---------------------------------------------------------------- Paste-CA lookup (read-only, allowed in PUBLIC_READONLY)
def _client_ip(request: Request):
    # localhost.run does NOT add X-Forwarded-For (verified), so every tunnel visitor arrives as
    # 127.0.0.1 and the public site shares one rate-limit bucket. Uvicorn's proxy-headers handling
    # would otherwise let a client pick its own "IP" via a fake X-Forwarded-For, so any request
    # carrying that header is put into a single shared "forwarded" bucket instead.
    if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
        return "forwarded-header"
    return request.client.host if request.client else "unknown"


@app.get("/api/lookup/{mint}")
def lookup_coin(mint: str, request: Request):
    mint = (mint or "").strip()
    if not lk.valid_mint(mint):
        raise HTTPException(400, "That doesn't look like a Solana token address. Paste the contract (mint) address: 32-44 letters/numbers, no 0, O, I or l.")
    cs, _, dashd, _ = build_state()
    for c in cs:
        if c.get("mint") == mint:
            n = next((n for n in (dashd.get("narratives") or []) if n.get("mint") == mint), None)
            return {**c, "narrative": n, "lookup": {"source": "report", "x_available": True,
                    "note": "Found in the latest scan report."}}
    try:
        r = lk.lookup_live(mint, _client_ip(request))
    except lk.LookupError_ as e:
        raise HTTPException(e.status, e.msg)
    except Exception as e:
        raise HTTPException(502, f"Live scan failed ({type(e).__name__}). DexScreener or RugCheck may be slow; try again shortly.")
    x = {k: v for k, v in (r.get("x") or {}).items() if k != "top_organic"}
    raw = {**{k: r.get(k) for k in ("score", "parts", "name", "symbol", "mint", "pool", "dex", "url", "age_h", "liq", "mcap",
                                   "vol24", "m5", "h1", "pc5", "pc1", "pc24", "fees_est", "boosts", "reason", "flags", "copycats")},
           "x": x, "vel": r.get("vel"), "rugcheck": r.get("rc") or {},
           "organic_examples": [e.get("url") for e in (r.get("x") or {}).get("top_organic") or [] if e.get("url")]}
    out = normalise_report_coin(raw, {})
    out["source"] = "live"
    out["status"] = "blocked" if any(f.startswith(("DUMP", "RugCheck DANGER", "RUGGED", "BLACKLISTED", "COPYCAT", "mint/freeze", "creator rugged")) for f in out["flags"]) else None
    out["narrative"] = None
    out["lookup"] = {"source": "live", "x_available": r.get("x_available"), "cached": r.get("cached"), "cache_age_s": r.get("cache_age_s"),
                     "copy_of": r.get("copy_of"),
                     "checked_at": datetime.now(MYT).isoformat(timespec="seconds"),
                     "note": "Live single-coin scan (DexScreener + RugCheck). Not in the latest report; X/paid-shill data "
                             + ("from stored posts only." if r.get("x_available") else "unavailable.")}
    return out


# ---------------------------------------------------------------- Token images (proxy + cache, read-only)
@app.get("/api/img/{mint}")
def token_img(mint: str, request: Request, s: str = ""):
    mint = (mint or "").strip()
    if not im.valid_mint(mint):
        raise HTTPException(400, "bad mint")
    real = im.has_file(mint)
    if not real:
        try:
            im.resolve(mint, s or None)
            real = im.has_file(mint)
        except RuntimeError:          # upstream budget spent: letter avatar now, real image on a later load
            return Response(im.avatar_svg(mint, s), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=60"})
        except Exception:
            real = False
    if real:
        path = im.file_path(mint)
        etag = '"%x-%x"' % (int(os.path.getmtime(path)), os.path.getsize(path))
        hdr = {"Cache-Control": "public, max-age=604800, immutable", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=hdr)
        return FileResponse(path, media_type="image/webp", headers=hdr)
    return Response(im.avatar_svg(mint, s), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/img-stats")
def img_stats():
    cs, _, _, _ = build_state()
    return im.stats([c["mint"] for c in cs if c.get("mint")])


# ---------------------------------------------------------------- Live chart (OHLCV proxy, read-only, allowed in PUBLIC_READONLY)
@app.get("/api/ohlcv/{pool}")
def ohlcv(pool: str, tf: str = "5m", token: str = ""):
    try:
        return ch.get_ohlcv(pool.strip(), tf, token.strip() or None)
    except ch.ChartError as e:
        raise HTTPException(e.status, e.msg)


# ---------------------------------------------------------------- Run scan
_lock = threading.Lock()
_scan = {"running": False, "started": None, "finished": None, "returncode": None, "output": "", "error": None, "duration_s": None}


def scan_state_public():
    return {**dict(_scan), "public_readonly": PUBLIC_READONLY, "live": live.public_status()}


def _run_scan(args):
    cmd = [sys.executable if os.environ.get("SCAN_USE_VENV") else "python3", os.path.join(SCANNER, "scan.py"), *args]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=SCANNER, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
        _scan.update(returncode=p.returncode, output=(p.stdout + ("\n" + p.stderr if p.stderr else ""))[-6000:],
                     error=None if p.returncode == 0 else f"exit code {p.returncode}")
    except subprocess.TimeoutExpired:
        _scan.update(returncode=None, error=f"timed out after {SCAN_TIMEOUT}s")
    except Exception as e:
        _scan.update(returncode=None, error=f"{type(e).__name__}: {e}")
    finally:
        _scan.update(running=False, finished=datetime.now(MYT).isoformat(timespec="seconds"), duration_s=round(time.time() - t0, 1))
        _lock.release()


@app.post("/api/scan")
def run_scan(top: int = 10, allow_non_pump: bool = False):
    # Plain `scan.py` = DexScreener + RugCheck refresh reusing the last x_raw.json (scan.py never calls X;
    # the X stage is done separately by ingest.py with agent tools). We deliberately do NOT pass --no-x,
    # since that would discard the X narrative data rather than reuse it.
    if PUBLIC_READONLY:
        raise HTTPException(status_code=403, detail="Public read-only mode: running a scan is disabled.")
    if not _lock.acquire(blocking=False):
        return JSONResponse({"started": False, "reason": "a scan is already running", "scan": scan_state_public()}, status_code=409)
    args = ["--top", str(max(1, min(int(top), 50)))] + (["--allow-non-pump"] if allow_non_pump else [])
    _scan.update(running=True, started=datetime.now(MYT).isoformat(timespec="seconds"), finished=None,
                 returncode=None, output="", error=None, duration_s=None, args=args)
    threading.Thread(target=_run_scan, args=(args,), daemon=True).start()
    return {"started": True, "scan": scan_state_public()}


@app.get("/api/scan")
def scan_status():
    return scan_state_public()



# ---- X Live Feed (read-only; data/x_feed.json is written by xfeed_ingest.py from agent X searches)
XFEED = os.environ.get("XFEED_PATH", os.path.join(HERE, "data", "x_feed.json"))
XQUERIES = os.path.join(HERE, "data", "xfeed_queries.json")
if live.ENABLED:  # hosted live mode: serve data the server-side loop keeps refreshing (snapshot until first scan)
    SCANNER, XFEED = live.prepare(SCANNER, XFEED)
    LEDGER = live.prepare_ledger(LEDGER)


@app.on_event("startup")
def _start_live():
    live.start(XFEED, XQUERIES)   # no-op unless LIVE_SCAN=1; first scan runs immediately


@app.get("/api/live")
def live_status():
    return live.public_status()


# ---- X push ingest (agent box -> hosted site). Disabled (404) unless INGEST_TOKEN is set in the host env.
INGEST_MAX_BYTES = 2 * 1024 * 1024


@app.post("/api/ingest/x", include_in_schema=False)
async def ingest_x(request: Request):
    import hmac
    want = os.environ.get("INGEST_TOKEN", "").strip()
    if not want or not live.ENABLED:
        raise HTTPException(404, "Not Found")
    got = request.headers.get("x-ingest-token", "")
    if not hmac.compare_digest(got.encode(), want.encode()):
        raise HTTPException(401, "bad ingest token")
    try:
        if int(request.headers.get("content-length") or 0) > INGEST_MAX_BYTES:
            raise HTTPException(413, "payload over 2 MB")
    except ValueError:
        raise HTTPException(400, "bad content-length")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > INGEST_MAX_BYTES:
            raise HTTPException(413, "payload over 2 MB")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "body must be JSON")
    try:
        res = live.ingest_x(payload, XFEED)
    except ValueError as e:
        raise HTTPException(422, str(e)[:300])
    return {"ok": True, **res, "rescan": "started"}
XTOPICS = ("memecoins", "politics", "tech")
_xcache = {"mtime": None, "data": None}


def _xfeed_load():
    try:
        mt = os.path.getmtime(XFEED)
    except OSError:
        return {"posts": [], "topics": {}, "updated_at": None}, "missing"
    if _xcache["mtime"] != mt:
        d, meta = load(XFEED, {})
        if meta["error"]:
            return (_xcache["data"] or {"posts": [], "topics": {}, "updated_at": None}), meta["error"]
        _xcache.update(mtime=mt, data=d)
    return _xcache["data"], None


@app.get("/api/xfeed")
def xfeed(topic: str = "", q: str = "", min_followers: int = 0, ca_only: bool = False,
          sort: str = "latest", limit: int = 300):
    d, err = _xfeed_load()
    posts = [p for p in (d.get("posts") or []) if isinstance(p, dict)]
    counts = {t: sum(1 for p in posts if p.get("topic") == t) for t in XTOPICS}
    t = topic.strip().lower()
    if t:
        if t not in XTOPICS:
            raise HTTPException(status_code=400, detail=f"topic must be one of {', '.join(XTOPICS)}")
        posts = [p for p in posts if p.get("topic") == t]
    terms = [w for w in q.lower().split() if w][:8]
    if terms:
        def hay(p): return " ".join([p.get("text") or "", p.get("author") or "", p.get("handle") or "", " ".join(p.get("cas") or [])]).lower()
        posts = [p for p in posts if all(w in hay(p) for w in terms)]
    if min_followers > 0:
        posts = [p for p in posts if (p.get("followers") or 0) >= min_followers]
    if ca_only:
        posts = [p for p in posts if p.get("cas")]
    if sort == "top":
        posts.sort(key=lambda p: -((p.get("metrics") or {}).get("likes", 0) + 2 * (p.get("metrics") or {}).get("reposts", 0)))
    else:
        posts.sort(key=lambda p: (p.get("created_at") or "", p.get("id") or ""), reverse=True)
    lim = max(1, min(int(limit), 300))
    return {"topic": t or None, "q": q, "count": len(posts), "counts": counts, "updated_at": d.get("updated_at"),
            "topics": d.get("topics") or {}, "error": err, "live_api": bool(live.status["x"]["live"]),
            "refresh_note": "Feed file is refreshed by the agent's X searches (xfeed_ingest.py); the page polls this endpoint.",
            "posts": posts[:lim]}




@app.get("/api/xfeed/heads")
def xfeed_heads(n: int = 60):
    """Tiny poll endpoint for the 'new posts' badge: latest ids per topic."""
    d, err = _xfeed_load()
    posts = sorted([p for p in (d.get("posts") or []) if isinstance(p, dict)],
                   key=lambda p: (p.get("created_at") or "", p.get("id") or ""), reverse=True)
    n = max(1, min(int(n), 300))
    heads = {t: [p.get("id") for p in posts if p.get("topic") == t][:n] for t in XTOPICS}
    return {"updated_at": d.get("updated_at"), "topics": d.get("topics") or {}, "heads": heads, "error": err,
            "live_api": bool(live.status["x"]["live"])}


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(HERE, "static", "logo.svg"), media_type="image/svg+xml")


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    # no-cache so phones always pick up the latest page after a fix
    return FileResponse(os.path.join(HERE, "static", "index.html"), headers={"Cache-Control": "no-cache"})
