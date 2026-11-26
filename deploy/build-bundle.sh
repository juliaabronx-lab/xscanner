#!/usr/bin/env bash
# Assemble a clean, secret-free deploy bundle of xScanner into $OUT (default /workspace/xscanner-deploy).
# Allow-list only: never copies .ssh, keys, tokens, .venv, logs, pid files, bin/, screenshots or wallet files.
# Reads /workspace/scanner and /workspace/paper read-only.
set -euo pipefail
SRC=/workspace/platform
OUT=${OUT:-/workspace/xscanner-deploy}
mkdir -p "$OUT/static/vendor" "$OUT/data/img" "$OUT/deploy/data"
cp -p "$SRC"/{app.py,auth.py,bundle.py,confidence.py,story.py,pricehist.py,seo.py,accounts.py,pulse.py,trader.py,tokenpage.py,sol_rpc.py,net.py,live.py,scan_runner.py,lookup.py,chart.py,images.py,xfeed_ingest.py,papertrade.py,paperbook.py,pace.py,trades_tape.py,render.yaml,requirements.txt} "$OUT/"
# guard: every local module the app imports must be in the bundle (a missing one crashes the deploy at start-up)
python3 - "$SRC" "$OUT" <<'PY' || { echo "ABORT: a local module imported by the app is missing from the bundle"; exit 1; }
import ast, os, sys
src, out = sys.argv[1:3]
miss = set()
for f in os.listdir(out):
    if not f.endswith(".py"): continue
    for n in ast.walk(ast.parse(open(os.path.join(out, f), encoding="utf-8").read())):
        names = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else [n.module.split(".")[0]] if isinstance(n, ast.ImportFrom) and n.module and not n.level else []
        for m in names:
            if os.path.exists(os.path.join(src, m + ".py")) and not os.path.exists(os.path.join(out, m + ".py")): miss.add(m + ".py")
if miss: print("missing from bundle:", ", ".join(sorted(miss))); sys.exit(1)
PY
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
# last-good live-scan seed lists (public mints only) so a cold start isn't stuck with 0 DexScreener seeds
[ -f "$SRC/deploy/data/last_seeds.json" ] && cp -p "$SRC/deploy/data/last_seeds.json" "$OUT/deploy/data/last_seeds.json" || true

# Cache seed (bundle / confidence / OHLCV / price snapshots, public market data only): exported from the LIVE site just before
# it is replaced, so a deploy (which wipes /tmp) does not reset every coin to "unavailable" and every chart to empty.
# Falls back to the last good export on the box. The ingest token is passed via a 0600 header file, never on a command line.
SEED_BOX=/workspace/platform/data/push/seed_caches.json
TOKF=/workspace/platform/deploy/.ingest_token
if [ -s "$TOKF" ]; then
  HDR=$(mktemp); chmod 600 "$HDR"; printf 'X-Ingest-Token: %s\n' "$(cat "$TOKF")" > "$HDR"
  if curl -fsS -m 90 -H @"$HDR" "${SEED_URL:-https://xscanner-c8jo.onrender.com}/api/ingest/cache-export" -o "$SEED_BOX.tmp" 2>/dev/null \
     && python3 -c "import json,sys; d=json.load(open('$SEED_BOX.tmp')); sys.exit(0 if len(d.get('bundle',{}))+len(d.get('ohlcv',{}))>0 else 1)"; then
    mkdir -p "$(dirname "$SEED_BOX")"; mv "$SEED_BOX.tmp" "$SEED_BOX"; echo "seed: exported live caches ($(python3 -c "import json;d=json.load(open('$SEED_BOX'));print({k:len(v) for k,v in d.items() if isinstance(v,dict)})"))"
  else
    rm -f "$SEED_BOX.tmp"; echo "warn: live cache export failed; using the last good seed if any"
  fi
  rm -f "$HDR"
fi
[ -f "$SEED_BOX" ] && cp -p "$SEED_BOX" "$OUT/deploy/data/seed_caches.json" || echo "warn: no cache seed"
