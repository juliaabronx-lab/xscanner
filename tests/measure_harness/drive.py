"""Cold-boot harness. Starts uvicorn with the urllib counter and records 120s.

Run from anywhere:
  MEASURE_MODE=sim-open python3 tests/measure_harness/drive.py
  MEASURE_MODE=sim-gt429 python3 tests/measure_harness/drive.py

sitecustomize.py in this directory is put on PYTHONPATH so the child scan
process is counted too. It answers synthetic JSON and, in sim-gt429, returns
HTTP 429 from GeckoTerminal after MEASURE_GT_OK successes (default 4).
It does not call the public market-data APIs. Localhost is passed through.
"""
import json, os, shutil, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PORT = int(os.environ.get("MEASURE_PORT", "8765"))
MODE = os.environ.get("MEASURE_MODE", "sim-open")
SECONDS = float(os.environ.get("MEASURE_SECONDS", "120"))
LABEL = os.environ.get("MEASURE_LABEL", MODE)
OUT_DIR = os.environ.get("MEASURE_DIR", "/tmp/xscan_measure")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.environ.get("MEASURE_OUT", os.path.join(OUT_DIR, f"{LABEL}.json"))
LOG = os.environ.get("MEASURE_LOG", os.path.join(OUT_DIR, f"{LABEL}.jsonl"))
POOL = "FcsUythNUWbfT7RP2yuFp1kiWjft15du9WtgLUrrqx3p"
MINT = "9cmXfuC26anZtH9AUWVzpUxU1UJ7PPogYxftYTepump"

if os.path.exists(LOG):
    os.remove(LOG)
shutil.rmtree(os.path.join(OUT_DIR, f"live-{LABEL}"), ignore_errors=True)

t0 = time.time()
env = os.environ.copy()
env.update({
    "PYTHONPATH": HERE + os.pathsep + env.get("PYTHONPATH", ""),
    "MEASURE_LOG": LOG,
    "MEASURE_T0": str(t0),
    "MEASURE_MODE": MODE,
    "MEASURE_GT_OK": env.get("MEASURE_GT_OK", "4"),
    "LIVE_SCAN": "1",
    "PULSE": "1",
    "PRICEHIST": "1",
    "SELF_PING": "0",
    "PUBLIC_READONLY": "1",
    "LIVE_DIR": os.path.join(OUT_DIR, f"live-{LABEL}"),
    "SCANNER_DIR": os.path.join(REPO, "deploy/data"),
    "LEDGER_PATH": os.path.join(REPO, "deploy/data/ledger.json"),
    "XFEED_PATH": os.path.join(REPO, "data/x_feed.json"),
})
err_path = os.path.join(OUT_DIR, f"{LABEL}.stderr")
err_f = open(err_path, "w")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=REPO, env=env, stdout=err_f, stderr=subprocess.STDOUT,
)

trades = []
trades_lock = threading.Lock()


def _one_trade(tag):
    url = f"http://127.0.0.1:{PORT}/api/trades/{POOL}?token={MINT}"
    t_req = time.time() - t0
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            raw = r.read().decode()
            code = getattr(r, "status", 200)
    except Exception as e:
        rec = {"tag": tag, "t": round(t_req, 3), "latency_s": round(time.perf_counter() - started, 3),
               "error": type(e).__name__ + ": " + str(e)[:300]}
    else:
        try:
            body = json.loads(raw)
        except Exception:
            body = {"_raw": raw[:400]}
        rec = {"tag": tag, "t": round(t_req, 3), "latency_s": round(time.perf_counter() - started, 3),
               "http": code, "available": body.get("available"), "reason": body.get("reason"),
               "stale": body.get("stale"), "source": body.get("source"),
               "n_trades": len(body.get("trades") or []), "n_seen": body.get("n_seen"),
               "note": (body.get("note") or "")[:180]}
    with trades_lock:
        trades.append(rec)


def burst(tag, n=6):
    ths = [threading.Thread(target=_one_trade, args=(tag,)) for _ in range(n)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(30)


health_at = None
deadline = t0 + SECONDS
next_burst = {20.0: "t20", 70.0: "t70"}
fired = set()
while time.time() < deadline:
    now = time.time() - t0
    if health_at is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:
                if r.status == 200:
                    health_at = round(now, 3)
        except Exception:
            pass
    for at, tag in list(next_burst.items()):
        if now >= at and tag not in fired and health_at is not None:
            fired.add(tag)
            burst(tag, 6)
    time.sleep(0.2)

# stop
proc.terminate()
try:
    proc.wait(timeout=8)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait(timeout=5)
err_f.close()

rows = []
if os.path.exists(LOG):
    for line in open(LOG, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

calls = [r for r in rows if r.get("host")]
by_host = {}
for r in calls:
    h = r["host"]
    sec = int(float(r["t"]))
    slot = by_host.setdefault(h, {"n": 0, "s429": 0, "per_sec": {}, "first_t": r["t"], "last_t": r["t"]})
    slot["n"] += 1
    slot["last_t"] = max(slot["last_t"], r["t"])
    slot["first_t"] = min(slot["first_t"], r["t"])
    if int(r.get("status") or 0) == 429:
        slot["s429"] += 1
    bucket = slot["per_sec"].setdefault(str(sec), 0)
    slot["per_sec"][str(sec)] = bucket + 1

summary_hosts = {}
for h, s in sorted(by_host.items(), key=lambda kv: -kv[1]["n"]):
    per = {int(k): v for k, v in s["per_sec"].items()}
    peak_sec = max(per, key=per.get) if per else None
    first30 = sum(v for k, v in per.items() if k < 30)
    summary_hosts[h] = {
        "calls": s["n"], "http_429": s["s429"],
        "first_t": s["first_t"], "last_t": s["last_t"],
        "peak_per_sec": per.get(peak_sec, 0) if peak_sec is not None else 0,
        "peak_sec": peak_sec,
        "calls_first_30s": first30,
        "per_sec": {str(k): per[k] for k in sorted(per)},
    }

pids = sorted({r.get("pid") for r in rows if r.get("pid")})
err_tail = open(err_path, encoding="utf-8", errors="replace").read()[-2500:]
out = {
    "label": LABEL,
    "mode": MODE,
    "kind": "measured",
    "clock": "wall clock seconds from uvicorn process start",
    "window_s": SECONDS,
    "base_commit": os.environ.get("MEASURE_COMMIT", ""),
    "health_ready_s": health_at,
    "server_exit": proc.returncode,
    "pids": pids,
    "upstream_calls": len(calls),
    "hosts": summary_hosts,
    "trades_bursts": trades,
    "note": "Synthetic upstream (no real market-data calls). 429s are injected by the harness when mode is sim-gt429, after MEASURE_GT_OK successful GeckoTerminal responses.",
}
json.dump(out, open(OUT, "w"), indent=2)
print(json.dumps({k: out[k] for k in ("label", "health_ready_s", "upstream_calls", "server_exit", "pids")}, indent=2))
print("--- hosts ---")
for h, s in summary_hosts.items():
    print(f"{h:40s} n={s['calls']:4d} 429={s['http_429']:4d} peak={s['peak_per_sec']}/s @{s['peak_sec']} first30={s['calls_first_30s']} first={s['first_t']}")
print("--- trades ---")
for t in trades:
    print(json.dumps(t))
if "Error" in err_tail or "Traceback" in err_tail:
    print("--- stderr tail ---")
    print(err_tail[-1500:])
