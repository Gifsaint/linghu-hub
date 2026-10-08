#!/usr/bin/env bash
# Sync YouTube video wall and push to origin/main if changed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

OUT="$(python3 "$ROOT/scripts/sync_youtube.py")"
echo "$OUT"

CHANGED="$(printf '%s\n' "$OUT" | awk -F= '/^CHANGED=/{print $2; exit}')"
if [[ "${CHANGED}" != "1" ]]; then
  echo "No video wall changes; skip commit."
  exit 0
fi

git add index.html
if git diff --cached --quiet; then
  echo "index.html staged empty after CHANGED=1; skip commit."
  exit 0
fi

git commit -m "Sync YouTube videos from @令狐投资"
git push origin main
echo "Pushed sync commit $(git rev-parse --short HEAD)"
