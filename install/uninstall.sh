#!/usr/bin/env bash
# uninstall.sh — remove agent-fix skill copies + CLI + AGENTS.md hooks.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="agent-fix"

rm -rf "$HOME/.claude/skills/$NAME"                      2>/dev/null || true
rm -rf "$HOME/.config/opencode/skill/$NAME"              2>/dev/null || true
rm -rf "$HOME/.codex/skills/$NAME"                       2>/dev/null || true
rm -rf "$HOME/.kimi-code/skills/$NAME"                   2>/dev/null || true
rm -rf "$HOME/.pi/agent/skills/$NAME"                    2>/dev/null || true
rm -rf "$HOME/.agents/skills/$NAME"                      2>/dev/null || true
rm -rf "$HOME/.local/share/hermes/skills/$NAME"          2>/dev/null || true
[ -n "${LOCALAPPDATA:-}" ] && rm -rf "$LOCALAPPDATA/hermes/skills/$NAME" 2>/dev/null || true

for f in "$HOME/.codex/AGENTS.md" "$HOME/.config/opencode/AGENTS.md"; do
  [ -f "$f" ] || continue
  # remove the marker block (from '# --- agent-fix (installed' to '# --- /agent-fix ---')
  awk 'BEGIN{skip=0} /^# --- agent-fix \(installed/{skip=1} !skip{print} /^# --- \/agent-fix ---/{skip=0}' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done

rm -f "$HOME/bin/fix" "$HOME/.local/bin/fix" 2>/dev/null || true

# unregister MCP server
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -n "$PY" ]; then
  PYREPO="$REPO"
  if command -v cygpath >/dev/null 2>&1; then PYREPO="$(cygpath -m "$REPO")"; fi
  "$PY" "$PYREPO/scripts/mcp_register.py" all --remove 2>/dev/null || true
fi

echo "[agent-fix] uninstalled"
