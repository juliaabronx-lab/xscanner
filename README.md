# xScanner (public read-only copy)

Memecoin narrative + X scanner dashboard. Analysis and paper trades only: no wallets, no keys, no signing, no real trades, no posting. The token page has a one-click paper Instant panel (play SOL, P1/P2/P3 fee bundles, limits, take-profit/stop, portfolio). Signed-in books are stored with the username/password account.

- Runs with `PUBLIC_READONLY=1`: `POST /api/scan` returns 403. CA lookup, live charts (GeckoTerminal/DexScreener) and token images work.
- `deploy/data/` is a snapshot of the scanner output (report.json, dashboard_data.json, scan.py + config.json + x_raw.json for the CA lookup) and the paper ledger.
- `data/` holds the X feed snapshot and the token image cache.
- Start: `uvicorn app:app --host 0.0.0.0 --port $PORT` with `SCANNER_DIR=deploy/data LEDGER_PATH=deploy/data/ledger.json PUBLIC_READONLY=1`.
- Refresh: `deploy/push-snapshot.sh` (run on the source box) rebuilds the snapshot, commits and pushes; the host redeploys on push.

## Keeping the free instance awake

Render's free web service sleeps after about 15 minutes with no inbound request. A cold wake was measured at **24.7s** (19 Nov 2026 audit: keep-awake log 15:50:45.9Z → 15:51:10.6Z, `uptime_s` 6). This session did not force another cold start: `/api/health` answered in 0.27s with `uptime_s` 7234, so the process was already awake.

What is already in the repo:

- `.github/workflows/keep-awake.yml` is scheduled every 10 minutes. GitHub delays free-plan crons. The 15 most recent runs (19–20 Nov 2026) were **41 to 78 minutes** apart and each finished in about 6–8s, which means they hit a warm process. That schedule does not reliably beat a 15-minute sleep.
- Once the process is running it pings its own public `/api/health` every 7 minutes (`SELF_PING`, `SELF_PING_S`, clamped to 60–600s). Render counts that as traffic, so an already-awake instance stays awake. It cannot wake a sleeping one, because the sleeper is not running the loop.

Outside keep-awake, if you stay on the free plan:

- Point an external monitor (UptimeRobot, cron-job.org, Healthchecks.io, or a machine cron) at `GET https://xscanner-c8jo.onrender.com/api/health` every **5 minutes**.
- Use a timeout of at least 90 seconds so a real cold start can finish. One read-only GET. Do not ping `/api/lookup` or `/api/pulse`; those spend upstream quota.
- Tradeoff: the free instance then never spins down, so it uses the free 750 hours/month continuously. One always-on service fits in that quota; a second always-on free service does not. Letting it sleep saves hours and brings the ~25s cold start back.
- The paid Render Starter plan does not sleep. That is a hosting change, not something this code turns on.

`SELF_PING=0` disables the in-process ping. `PULSE=0` disables the Pulse worker. `PRICEHIST=0` disables the once-a-minute price snapshot thread.
