#!/usr/bin/env bash
# Assemble a clean, secret-free deploy bundle of xScanner into $OUT (default /workspace/xscanner-deploy).
# Allow-list only: never copies .ssh, keys, tokens, .venv, logs, pid files, bin/, screenshots or wallet files.
# Reads /workspace/scanner and /workspace/paper read-only.
set -euo pipefail
SRC=/workspace/platform
OUT=${OUT:-/workspace/xscanner-deploy}
mkdir -p "$OUT/static/vendor" "$OUT/data/img" "$OUT/deploy/data"
cp "$SRC"/{app.py,lookup.py,chart.py,images.py,xfeed_ingest.py,render.yaml} "$OUT/"
cp "$SRC"/static/{index.html,app.js,chart.js,motion.js,xfeed.js,style.css,logo.svg} "$OUT/static/"
cp "$SRC"/static/vendor/lightweight-charts.js "$OUT/static/vendor/"
cp "$SRC"/data/{x_feed.json,xfeed_queries.json,img_cache.json} "$OUT/data/"
cp "$SRC"/data/img/*.webp "$OUT/data/img/" 2>/dev/null || true
# snapshot of Advisor's scanner (read-only copy). scan.py+config.json+x_raw.json let the CA lookup score coins.
for f in report.json dashboard_data.json scan.py config.json x_raw.json; do
  cp "/workspace/scanner/$f" "$OUT/deploy/data/" 2>/dev/null || echo "warn: missing scanner/$f"
done
cp /workspace/paper/ledger.json "$OUT/deploy/data/ledger.json" 2>/dev/null || echo "warn: missing ledger"
date -Iseconds > "$OUT/deploy/data/SNAPSHOT_TIME"
