#!/usr/bin/env bash
# One-time: after `gh auth login` succeeded on the box, create the GitHub repo and push the clean bundle.
# Usage: deploy/github-create-and-push.sh [public|private]   (default public)
set -euo pipefail
VIS=${1:-public}
REPO_DIR=/workspace/xscanner-deploy
gh auth status
OWNER=$(gh api user -q .login)
cd "$REPO_DIR"
OUT="$REPO_DIR" /workspace/platform/deploy/build-bundle.sh >/dev/null
git add -A; git -c user.name="$OWNER" -c user.email="$OWNER@users.noreply.github.com" commit -qm "snapshot $(date '+%F %H:%M %Z')" || true
git config user.name "$OWNER"; git config user.email "$OWNER@users.noreply.github.com"
gh auth setup-git
if gh repo view "$OWNER/xscanner" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/xscanner.git"
  git push -u origin main
else
  gh repo create "$OWNER/xscanner" --"$VIS" --description "xScanner: public read-only memecoin + X narrative dashboard" --source "$REPO_DIR" --remote origin --push
fi
echo "repo: https://github.com/$OWNER/xscanner ($VIS)"
