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
import pricehist as ph
import images as im
import bundle as bd
import confidence as cf
import story as st_
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
import auth
auth.install(app)  # Google sign-in; no-op for the rest of the app until env vars are set


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


def rewrite_text(text, mcap=None, liq=None):
    """Apply the shared money and age formatters to scan.py reason/flag strings.

    scan.py writes always-K .0f amounts (2393 -> '$2K'). When the raw mcap/liq
    numbers are available, replace those phrases with fmt_usd so the UI shows
    '$2.39K'. Remaining always-K amounts still go through rewrite_money.
    """
    if not text or not isinstance(text, str):
        return text
    if mcap is not None:
        text = re.sub(r"\bmcap \$[0-9.]+[KMBT]?\b", "mcap " + fmt_usd(mcap), text, count=1, flags=re.I)
    if liq is not None:
        text = re.sub(r"\bthin liq \$[0-9.]+[KMBT]?\b", "thin liq " + fmt_usd(liq), text, flags=re.I)
        text = re.sub(r"\bliq \$[0-9.]+[KMBT]?\b", "liq " + fmt_usd(liq), text, count=1, flags=re.I)
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
    flags = [rewrite_text(f, mcap=c.get("mcap"), liq=c.get("liq")) for f in (c.get("flags") or [])]
    paid_share = num(x.get("shill_share"))
    out = {
        "source": "report", "score": c.get("score"), "parts": c.get("parts") or {}, "tier": tier(c.get("score")),
        "name": (c.get("name") or "").strip(), "symbol": c.get("symbol"), "mint": c.get("mint"), "pool": c.get("pool"),
        "dex": c.get("dex"), "url": c.get("url"), "age_h": c.get("age_h"), "liq": c.get("liq"), "mcap": c.get("mcap"),
        "vol24": c.get("vol24"), "pc5": c.get("pc5"), "pc1": c.get("pc1"), "pc24": c.get("pc24"),
        "buys5": b5, "sells5": s5, "buys1h": b1, "sells1h": s1, "fees_est": c.get("fees_est"), "boosts": c.get("boosts"),
        "copycats": c.get("copycats"), "reason": rewrite_text(c.get("reason"), mcap=c.get("mcap"), liq=c.get("liq")), "flags": flags,
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
    out = apply_story(apply_bundle_and_confidence(out))
    out["verdict"] = verdict_of(out)
    return out


def _scan_age_s():
    """Seconds since the data the scanner copy was built from (last live scan, else the bundled snapshot)."""
    for raw in (live.status.get("last_scan_at"), live.status.get("snapshot_time"), live.status.get("bundle_snapshot_time")):
        if not raw: continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MYT)
            return max(0.0, time.time() - dt.timestamp())
        except Exception:
            continue
    return None


_SCANNER_BUNDLE = re.compile(r"^\s*(bundle[d]?\b|concentrated\b)", re.I)


def verdict_of(c):
    """The one verdict string the UI and API share (the UI renders this, it never recomputes from the score)."""
    if c.get("status") == "blocked":
        return "Blocked (hard flag)"
    return {"look": "Worth a look", "watch": "Watch", "skip": "Skip"}.get(c.get("tier") or tier(c.get("score")), "Skip")


def _bundle_flag(b):
    st, why = b.get("status"), b.get("reason") or ""
    if st == "flagged":
        head, _, rest = why.partition(": ")
        return ("BUNDLE: " if head.startswith("Bundled") else "CONCENTRATED: ") + rest
    if st == "elevated": return "Bundle risk: " + why.split(": ", 1)[-1]
    if st == "unavailable": return "Bundle check unavailable (not counted as safe)"
    return None


def apply_bundle_and_confidence(out):
    """Bundle / insider-cluster check + data-confidence cross-check. Both are non-blocking cache reads.
    Missing data is never safe: an unavailable bundle check caps the score below 'look'; low data confidence caps it
    below 'watch'; a flagged bundle caps it at 20 and zeroes the safety points."""
    mint = out.get("mint")
    rc = out.get("rugcheck") or {}
    try:
        b = bd.assess(mint, rc)
    except Exception as e:
        b = bd.assess_numbers(None); b["error"] = type(e).__name__
    try:
        cached, _ = bd.get_cached(mint)
    except Exception:
        cached = None
    rx = (cached or {}).get("rc") or {}
    try:
        conf = cf.assess(out, rc_extra=rx, scan_age_s=_scan_age_s())
    except Exception as e:
        conf = cf.evaluate(out, None); conf["error"] = type(e).__name__
    st = b.get("status")
    known = st in ("ok", "elevated", "flagged")
    out["bundle"] = {k: b.get(k) for k in ("status", "level", "flagged", "reason", "source", "networks", "top10_ex_lp", "top20_ex_lp",
                                         "insider_top_holders", "uniform_n", "uniform_of", "uniform_pct", "fund", "age_s", "stale",
                                         "pending", "partial")}
    # ONE source of truth: linked_wallets / share_pct = the cluster that explains the verdict (whichever detector found it).
    # RugCheck's own insider-network numbers are kept apart as insider_*; wallets / net_share are aliases of the cluster numbers.
    out["bundle"]["linked_wallets"] = b.get("cluster_wallets", b.get("wallets")) if known else None
    out["bundle"]["share_pct"] = b.get("cluster_share", b.get("net_share")) if known else None     # None = unknown (never 0)
    out["bundle"]["basis"] = b.get("cluster_basis") if known else None
    out["bundle"]["cluster_text"] = b.get("cluster_text") if known else None
    out["bundle"]["wallets"], out["bundle"]["net_share"] = out["bundle"]["linked_wallets"], out["bundle"]["share_pct"]
    out["bundle"]["insider_wallets"] = b.get("wallets") if known else None
    out["bundle"]["insider_share"] = b.get("net_share") if known else None
    out["bundle"]["insider_share_raw"] = b.get("net_share_raw") if known else None
    out["bundle_pct"] = out["bundle"]["share_pct"]
    out["bundle_wallets"] = out["bundle"]["linked_wallets"]
    out["data_confidence"] = {k: conf.get(k) for k in ("score", "level", "label", "uncertain", "notes", "fields", "sources",
                                                       "n_sources", "checked_age_s", "scan_stale", "cap", "problems", "warnings")}
    # --- score + safety caps (never raise a score)
    sc0 = num(out.get("score"), 0) or 0
    cap = 100
    if st in bd.CAP: cap = min(cap, bd.CAP[st])
    cap = min(cap, conf.get("cap", 100))
    parts = dict(out.get("parts") or {})
    if parts.get("safety") is not None:
        scap = bd.SAFETY_CAP.get(st, 25)
        if conf["level"] == "low": scap = min(scap, 10)
        elif conf["level"] == "unchecked": scap = min(scap, 18)
        if num(parts["safety"], 0) > scap: parts["safety"] = scap
        out["parts"] = parts
    # ONE canonical bundle chip: drop the scanner's own BUNDLE:/bundle: text (it counted wallets differently), keep ours
    flags = [f for f in (out.get("flags") or []) if not _SCANNER_BUNDLE.match(f)]
    new = []
    bf = _bundle_flag(out["bundle"] | {"status": st, "reason": b.get("reason")})
    if bf: new.append(bf)
    if conf["level"] in ("low", "unchecked") or conf["uncertain"]:
        head = "Data uncertain" if conf["level"] != "unchecked" else "Data confidence pending"
        names = ", ".join(conf["uncertain"][:4]) if conf["uncertain"] else ""
        new.append(f"{head}: {names}" if names and conf["level"] != "unchecked" else (head if conf["level"] == "unchecked" else f"{head} ({names})"))
    if cap < sc0:
        out.setdefault("score_raw", out.get("score"))
        out["score"] = cap
        out["tier"] = tier(cap)
        why = []
        if st in bd.CAP and bd.CAP[st] < sc0: why.append({"flagged": "bundle detected", "elevated": "bundle risk", "unavailable": "bundle check unavailable"}[st])
        if conf.get("cap", 100) < sc0: why.append("low data confidence" if conf["level"] == "low" else "data confidence pending")
        new.append(f"capped at {cap}: " + " + ".join(why))
    seen_f, uniq = set(), []
    for f in new + flags:                       # one chip per distinct flag (case-insensitive)
        k = re.sub(r"\s+", " ", str(f)).strip().lower()
        if k not in seen_f: seen_f.add(k); uniq.append(f)
    out["flags"] = uniq
    if st == "flagged" and out.get("status") != "blocked": out["status"] = "high risk"
    elif conf["level"] == "low" and out.get("status") in (None, "buy", "watch"): out["status"] = "data uncertain"
    # --- risk radar: a bundle risk we could not measure is shown as uncertain (0.5), never as zero
    ra = dict(out.get("risk") or {})
    ra["bundle"] = 1.0 if st == "flagged" else max(0.6, ra.get("bundle", 0)) if st == "elevated" else 0.5 if st == "unavailable" else ra.get("bundle", 0)
    out["risk"] = ra
    return out


_STORY_MEMO = {}          # mint -> (key, story fields): the story is rebuilt only when its inputs change, not on every request


def apply_story(out):
    """Attach the plain-English coin story (what it is, why it is moving). Cache reads only; any failure -> no story."""
    try:
        mint = out.get("mint")
        e = cf._cache.get(mint) or {}
        cached, _ = bd.get_cached(mint)
        rcx = (cached or {}).get("rc") or {}
        raws = [os.path.join(live.WORK, "x_raw.json"), os.path.join(HERE, "deploy", "data", "x_raw.json"), os.path.join(SCANNER, "x_raw.json")]
        posts, trends = st_.load_x(XFEED, raws)
        sig = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in [XFEED, *raws])
        key = (sig, out.get("score"), out.get("mcap"), out.get("liq"), out.get("pc1"), out.get("pc24"), out.get("age_h"),
               tuple(out.get("flags") or ()), (e.get("ds") or {}).get("price"), rcx.get("meta_desc"))
        hit = _STORY_MEMO.get(mint)
        if hit and hit[0] == key:
            out["story"], out["story_short"], out["story_detail"] = hit[1]
            return out
        s = st_.build(out, ds=e.get("ds"), gt=e.get("gt"), rc_meta={"desc": rcx.get("meta_desc")}, posts=posts, trends=trends)
        out["story"], out["story_short"] = s["text"], s["short"]
        out["story_detail"] = {k: s[k] for k in ("sentences", "theme", "highlights", "x_matches", "catalyst")}
        if len(_STORY_MEMO) > 400: _STORY_MEMO.clear()
        _STORY_MEMO[mint] = (key, (out["story"], out["story_short"], out["story_detail"]))
    except Exception as ex:
        out["story"] = out["story_short"] = None
        out["story_error"] = type(ex).__name__
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


def _build_state_uncached():
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


# The enriched coin list (bundle + confidence + story + verdict for every coin) is computed ONCE per scan / per few seconds
# and shared by every endpoint; requests only ever read it. A refresh runs in the background and the old copy keeps serving.
STATE_TTL = float(os.environ.get("STATE_TTL_S", "8"))
_ST = {"v": None, "t": 0.0, "key": None, "building": False}
_ST_LOCK = threading.Lock()


def _state_key():
    paths = [os.path.join(SCANNER, "report.json"), os.path.join(SCANNER, "dashboard_data.json"), XFEED, LEDGER]
    return tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in paths)


_RECENT = {}                 # mint -> {"t", "coin"}: coins that WERE in the feed; served if a live lookup is rate-limited
_RECENT_FILE = os.path.join(os.environ.get("LIVE_DIR", "/tmp/xscanner-live"), "recent_coins.json")
RECENT_MAX, RECENT_AGE_S = 150, 24 * 3600
_recent_flush = {"t": 0.0, "loaded": False}


def _recent_load():
    if _recent_flush["loaded"]: return
    _recent_flush["loaded"] = True
    try:
        d = json.load(open(_RECENT_FILE, encoding="utf-8"))
        for m, e in d.items():
            if isinstance(e, dict) and time.time() - e.get("t", 0) < RECENT_AGE_S: _RECENT.setdefault(m, e)
    except Exception:
        pass


def _recent_remember(coins):
    _recent_load()
    now = time.time()
    for c in coins:
        if c.get("mint") and c.get("source") != "live": _RECENT[c["mint"]] = {"t": now, "coin": c}
    for m in sorted(_RECENT, key=lambda k: _RECENT[k]["t"])[:max(0, len(_RECENT) - RECENT_MAX)]: _RECENT.pop(m, None)
    if now - _recent_flush["t"] > 60:
        _recent_flush["t"] = now
        try:
            os.makedirs(os.path.dirname(_RECENT_FILE), exist_ok=True)
            with open(_RECENT_FILE + ".tmp", "w", encoding="utf-8") as f: json.dump(_RECENT, f, default=str)
            os.replace(_RECENT_FILE + ".tmp", _RECENT_FILE)
        except Exception:
            pass


def _refresh_state():
    try:
        key = _state_key()
        v = _build_state_uncached()
        with _ST_LOCK:
            _ST.update(v=v, t=time.time(), key=key)
        try: _recent_remember(v[0])
        except Exception: pass
    finally:
        _ST["building"] = False


def build_state():
    v = _ST["v"]
    if v is None:                                   # cold start only: the first request builds it
        with _ST_LOCK:
            building = _ST["building"]; _ST["building"] = True
        if not building:
            _refresh_state()
        else:
            for _ in range(200):
                if _ST["v"] is not None: break
                time.sleep(0.05)
        return _ST["v"] if _ST["v"] is not None else _build_state_uncached()
    if (time.time() - _ST["t"] > STATE_TTL or _state_key() != _ST["key"]):
        with _ST_LOCK:
            go = not _ST["building"]; _ST["building"] = True
        if go: threading.Thread(target=_refresh_state, name="state-refresh", daemon=True).start()
    return v


_BOOT = time.time()


@app.get("/api/health")
def health():
    _, _, _, meta = build_state()
    _, lmeta = load(LEDGER, {})
    return {"ok": True, "uptime_s": int(time.time() - _BOOT), "started_at": datetime.fromtimestamp(_BOOT, MYT).isoformat(timespec="seconds"), "readonly": True, "public_readonly": PUBLIC_READONLY, "sources": {**meta, "ledger": lmeta}, "scan": scan_state_public()}


@app.get("/api/summary")
def summary():
    coins, s, _, meta = build_state()
    s = dict(s)
    s["coin_count"] = len(coins)
    s["tiers"] = {t: sum(1 for c in coins if c["tier"] == t) for t in ("look", "watch", "skip")}
    s["sources"] = meta
    s["public_readonly"] = PUBLIC_READONLY
    s["live"] = live.public_status()
    return s


_LIST_DROP = ("story", "story_detail", "rugcheck", "x", "organic_examples", "risk", "vel", "reason", "parts_detail", "data_issues_raw")


def _slim_coin(c):
    """List-endpoint shape: everything a card / table row needs; the heavy nested fields live in /api/coins/{mint} and /api/lookup."""
    o = {k: v for k, v in c.items() if k not in _LIST_DROP}
    b = c.get("bundle") or {}
    o["bundle"] = {k: b.get(k) for k in ("status", "level", "flagged", "reason", "linked_wallets", "share_pct", "basis", "cluster_text")}
    d = c.get("data_confidence") or {}
    o["data_confidence"] = {k: d.get(k) for k in ("score", "level", "label", "uncertain", "problems", "warnings")}
    x = c.get("x") or {}
    o["x"] = {"mentions": x.get("mentions")}
    return o


@app.get("/api/coins")
def coins(min_score: float = 0, min_liq: float = 0, pump_only: bool = False, hide_paid: bool = False,
          sort: str = "score", desc: bool = True, full: bool = False):
    cs, _, _, meta = build_state()
    out = [c for c in cs if (num(c.get("score"), 0) or 0) >= min_score and (num(c.get("liq"), 0) or 0) >= min_liq
           and (not pump_only or c["pump"]) and (not hide_paid or not c["paid_shilled"])]
    if sort in ("symbol", "name", "dex", "tier"):
        out.sort(key=lambda c: str(c.get(sort) or "").lower(), reverse=desc)
    else:
        out.sort(key=lambda c: num(c.get(sort), float("-inf")), reverse=desc)
    im.warm(out)
    out = [({**c} if full else _slim_coin(c)) for c in out]
    for c in out: c["img_real"] = im.has_file(c.get("mint") or "")
    return {"count": len(out), "total": len(cs), "coins": out, "sources": meta, "slim": not full}


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


def _recent_lookup(mint):
    """A coin that was in the feed within the last day: serve its last scan instead of failing when DexScreener is busy."""
    _recent_load()
    e = _RECENT.get(mint)
    if not e or time.time() - e["t"] > RECENT_AGE_S: return None
    age = int(time.time() - e["t"])
    return {**e["coin"], "narrative": None,
            "lookup": {"source": "report", "x_available": True, "cached": True, "stale": True, "cache_age_s": age,
                       "note": f"DexScreener is busy, so this is the last scan that included this coin ({age // 60} min ago)."}}


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
        stale, sage = lk.stale_cached(mint)
        rec = _recent_lookup(mint)
        if stale:
            r = {**stale, "cached": True, "stale": True, "cache_age_s": sage}
        elif rec:
            return rec
        else:
            code = 429 if e.status >= 500 or e.status == 503 else e.status
            raise HTTPException(code, e.msg if code != 429 else (
                e.msg if e.status in (429, 503) else "DexScreener is busy right now. Please try the scan again in a moment."))
    except Exception as e:
        stale, sage = lk.stale_cached(mint)
        rec = _recent_lookup(mint)
        if stale:
            r = {**stale, "cached": True, "stale": True, "cache_age_s": sage}
        elif rec:
            return rec
        else:
            raise HTTPException(429, "DexScreener is busy right now. Please try the scan again in a moment.")
    ts = [threading.Thread(target=fn, args=(mint,), daemon=True) for fn in (lambda m: bd.fetch_now(m, 6), lambda m: cf.fetch_now(m, 6))]
    for t in ts: t.start()
    for t in ts: t.join(14)
    x = {k: v for k, v in (r.get("x") or {}).items() if k != "top_organic"}
    raw = {**{k: r.get(k) for k in ("score", "parts", "name", "symbol", "mint", "pool", "dex", "url", "age_h", "liq", "mcap",
                                   "vol24", "m5", "h1", "pc5", "pc1", "pc24", "fees_est", "boosts", "reason", "flags", "copycats")},
           "x": x, "vel": r.get("vel"), "rugcheck": r.get("rc") or {},
           "organic_examples": [e.get("url") for e in (r.get("x") or {}).get("top_organic") or [] if e.get("url")]}
    out = normalise_report_coin(raw, {})
    out["source"] = "live"
    out["status"] = "blocked" if any(f.startswith(("DUMP", "RugCheck DANGER", "RUGGED", "BLACKLISTED", "COPYCAT", "mint/freeze", "creator rugged")) for f in out["flags"]) else None
    out["narrative"] = None
    out["verdict"] = verdict_of(out)
    out["lookup"] = {"source": "live", "scored_at": round(time.time() - (r.get("cache_age_s") or 0)), "x_available": r.get("x_available"), "cached": r.get("cached"), "stale": r.get("stale"), "cache_age_s": r.get("cache_age_s"),
                     "copy_of": r.get("copy_of"), "partial": bool(r.get("partial")),
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
    """Always JSON. A viewer never waits on a rate-limited upstream (see chart.py)."""
    try:
        return ch.get_ohlcv(pool.strip(), tf, token.strip() or None)
    except ch.ChartError as e:
        return JSONResponse({"detail": e.msg}, status_code=e.status if e.status in (400, 404, 503) else 503,
                            headers={"Retry-After": "20"} if e.status not in (400, 404) else None)
    except Exception as e:
        return JSONResponse({"detail": "Chart temporarily unavailable; retrying shortly.", "error": type(e).__name__}, status_code=503,
                            headers={"Retry-After": "20"})


@app.get("/api/ohlcv-status", include_in_schema=False)
def ohlcv_status():
    return ch.stats()


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


def _price_hint(mint):
    """Last price from our other caches when Jupiter is unavailable: DexScreener pull, RugCheck, else chain supply x mcap."""
    e = cf._cache.get(mint) or {}
    p = num((e.get("ds") or {}).get("price"))
    if p and p > 0: return p
    a, _ = bd.get_cached(mint)
    rcx = (a or {}).get("rc") or {}
    p = num(rcx.get("price"))
    if p and p > 0: return p
    sup, dec = num(rcx.get("supply_raw")), num(rcx.get("decimals"))
    mc = num((e.get("ds") or {}).get("mcap"))
    if sup and dec is not None and mc and mc > 0: return mc / (sup / 10 ** dec)
    return None


def _report_top():
    """Every coin the UI can show: the report's top list plus the dashboard-only coins."""
    r, _ = load(os.path.join(SCANNER, "report.json"), {})
    out = [c for c in (r.get("top") or []) if isinstance(c, dict)]
    have = {c.get("mint") for c in out}
    dd, _ = load(os.path.join(SCANNER, "dashboard_data.json"), {})
    for d in dd.get("coins") or []:
        if isinstance(d, dict) and d.get("mint") and d["mint"] not in have:
            out.append({"mint": d["mint"], "pool": d.get("pool"), "mcap": d.get("mcap"), "pc5": d.get("pc5"), "pc1": d.get("pc1"),
                        "pc24": d.get("pc24"), "age_h": d.get("ageH")})
    return out


@app.on_event("startup")
def _start_live():
    try:
        bd._load(); cf._load(); ch._load_store(); ph.load()      # disk store + bundled seed: a restart never resets to 'unavailable'
        ph.price_hint = _price_hint
        ph.start(_report_top)                                     # one price snapshot per coin per minute (feeds the charts)
    except Exception:
        pass
    live.start(XFEED, XQUERIES)   # no-op unless LIVE_SCAN=1; first scan runs immediately


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    """Every /api failure is JSON, never an HTML error page."""
    return JSONResponse({"detail": "Internal error; please retry shortly.", "error": type(exc).__name__}, status_code=500)


@app.get("/api/ingest/cache-export", include_in_schema=False)
def cache_export(request: Request):
    """Box -> repo seed: bundle / confidence / OHLCV / price-snapshot caches (public market data only). Needs INGEST_TOKEN."""
    import hmac
    want = os.environ.get("INGEST_TOKEN", "").strip()
    if not want:
        raise HTTPException(404, "Not Found")
    if not hmac.compare_digest(request.headers.get("x-ingest-token", "").encode(), want.encode()):
        raise HTTPException(401, "bad ingest token")
    return {"exported_at": time.time(), "bundle": bd.export(), "confidence": cf.export(), "ohlcv": ch.export(),
            "pricehist": ph.export()}


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
