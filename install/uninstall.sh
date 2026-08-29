#!/usr/bin/env bash
# uninstall.sh — thin bootstrap for `fix uninstall` (the exact inverse of install).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  REPO="$(cygpath -m "$REPO")"
fi

PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "error: no python found (looked for python3/python/py)" >&2
  exit 2
fi

exec "$PY" "$REPO/scripts/fix.py" uninstall
