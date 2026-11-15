#!/usr/bin/env bash
# Refresh the hosted xScanner snapshot: rebuild the clean bundle into the deploy repo, scan it for secrets,
# commit and push. NOTE: the Render service (srv-dbsfsc0n74is739ojmkg) was added by public repo URL, so it does NOT
# auto-deploy on push: after pushing, an agent must call Render trigger_deploy (or click Manual Deploy). Not scheduled.
# Usage: deploy/push-snapshot.sh [repo_dir]   (default /workspace/xscanner-deploy, must have an 'origin' remote)
set -euo pipefail
REPO=${1:-/workspace/xscanner-deploy}
OUT="$REPO" /workspace/platform/deploy/build-bundle.sh
cd "$REPO"
# secret scan: abort if anything key-like slipped in
if grep -rIlE --exclude=push-snapshot.sh --exclude-dir=.git -e '-----BEGIN [A-Z ]*PRIVATE KEY|ssh-ed25519 AAAA|ghp_[A-Za-z0-9]{20,}|github_pat_|rnd_[A-Za-z0-9]{20,}|AAAAAAAAAAAAAAAAAAAAA[A-Za-z0-9%]{30,}|xox[bp]-|sk-[A-Za-z0-9]{20,}'  . ; then echo "ABORT: possible secret in bundle (files listed above)"; exit 1; fi
for bad in .ssh .venv bin supervise.pid tunnel.log public.url; do [ -e "$bad" ] && { echo "ABORT: $bad in bundle"; exit 1; }; done
git add -A
if git diff --cached --quiet; then echo "snapshot unchanged, nothing to push"; exit 0; fi
git commit -q -m "snapshot $(date '+%Y-%m-%d %H:%M %Z')"
git push -q origin HEAD
echo "pushed; now trigger a Render deploy for srv-dbsfsc0n74is739ojmkg (no auto-deploy)"
