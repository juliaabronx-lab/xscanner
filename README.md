# xScanner (public read-only copy)

Memecoin narrative + X scanner dashboard. Analysis and paper trades only: no wallets, no keys, no signing, no trading, no posting.

- Runs with `PUBLIC_READONLY=1`: `POST /api/scan` returns 403. CA lookup, live charts (GeckoTerminal/DexScreener) and token images work.
- `deploy/data/` is a snapshot of the scanner output (report.json, dashboard_data.json, scan.py + config.json + x_raw.json for the CA lookup) and the paper ledger.
- `data/` holds the X feed snapshot and the token image cache.
- Start: `uvicorn app:app --host 0.0.0.0 --port $PORT` with `SCANNER_DIR=deploy/data LEDGER_PATH=deploy/data/ledger.json PUBLIC_READONLY=1`.
- Refresh: `deploy/push-snapshot.sh` (run on the source box) rebuilds the snapshot, commits and pushes; the host redeploys on push.
