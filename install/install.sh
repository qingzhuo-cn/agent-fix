#!/usr/bin/env bash
# install.sh — thin bootstrap: find python, let the agent-fix CLI deploy itself.
#
# Everything (skill copies, AGENTS.md hooks, startup hooks, MCP registration,
# the `fix` CLI shim) is implemented once in agentfix/hooks.py and driven by
# catalog.json. This script only bootstraps python; `fix uninstall` reverses it.
# Idempotent: safe to re-run after `git pull`.
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

exec "$PY" "$REPO/scripts/fix.py" install
