#!/usr/bin/env bash
# Assemble a clean, secret-free deploy bundle of xScanner into $OUT (default /workspace/xscanner-deploy).
# Allow-list only: never copies .ssh, keys, tokens, .venv, logs, pid files, bin/, screenshots or wallet files.
# Reads /workspace/scanner and /workspace/paper read-only.
set -euo pipefail
SRC=/workspace/platform
OUT=${OUT:-/workspace/xscanner-deploy}
mkdir -p "$OUT/static/vendor" "$OUT/data/img" "$OUT/deploy/data"
cp -p "$SRC"/{app.py,live.py,scan_runner.py,lookup.py,chart.py,images.py,xfeed_ingest.py,render.yaml} "$OUT/"
rm -rf "$OUT/static" && mkdir -p "$OUT/static"
cp -rp "$SRC"/static/. "$OUT/static/"   # whole front end (html/js/css/fonts/vendor); no secrets live in static/
cp -p "$SRC"/data/{x_feed.json,xfeed_queries.json,img_cache.json} "$OUT/data/"
cp -p "$SRC"/data/img/*.webp "$OUT/data/img/" 2>/dev/null || true
# snapshot of Advisor's scanner (read-only copy). scan.py+config.json+x_raw.json let the CA lookup score coins.
for f in report.json dashboard_data.json scan.py config.json x_raw.json; do
  cp -p "/workspace/scanner/$f" "$OUT/deploy/data/" 2>/dev/null || echo "warn: missing scanner/$f"
done
cp /workspace/paper/ledger.json "$OUT/deploy/data/ledger.json" 2>/dev/null || echo "warn: missing ledger"
date -Iseconds > "$OUT/deploy/data/SNAPSHOT_TIME"
cp -p "$SRC"/deploy/{build-bundle.sh,push-snapshot.sh,github-create-and-push.sh} "$OUT/deploy/"   # deploy scripts
# GitHub Actions keep-awake ping (pushing it needs a token with the `workflow` scope)
# Copied only when the gh token has the `workflow` scope (GitHub rejects the push otherwise). Once the file is in
# the repo, later bundles without it are still fine: build-bundle never deletes .github/, so it stays committed.
if gh auth status 2>&1 | grep -q "'workflow'"; then
  mkdir -p "$OUT/.github/workflows" && cp -p "$SRC"/deploy/github-workflows/*.yml "$OUT/.github/workflows/"
else
  echo "note: gh token lacks the 'workflow' scope; keep-awake workflow not included (run: gh auth refresh -h github.com -s workflow)"
fi
# last successful X push (public posts + paper ledger, no token): the live server restores it on startup if newer
[ -f "$SRC/data/push/last_push.json" ] && cp -p "$SRC/data/push/last_push.json" "$OUT/deploy/data/last_push.json" || true
