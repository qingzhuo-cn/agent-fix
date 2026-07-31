#!/usr/bin/env bash
# install.sh — install the agent-fix skill + CLI into every detected agent.
# Works on POSIX (macOS/Linux) and inside Git Bash on Windows.
# Idempotent: safe to re-run after `git pull` to update.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="agent-fix"
NOW="$(date +%Y-%m-%d)"

info()  { printf '\033[1;34m[agent-fix]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[agent-fix]\033[0m %s\n' "$*"; }
skip()  { printf '\033[1;33m[agent-fix]\033[0m %s\n' "$*"; }

install_skill() { # $1 = target dir
  local target="$1"
  mkdir -p "$target"
  cp -r "$REPO/SKILL.md" "$REPO/fixes" "$REPO/catalog.json" "$REPO/scripts" "$target/"
  ok "skill installed -> $target"
}

install_agents_md_hook() { # $1 = AGENTS.md path
  local file="$1" marker_begin="# --- agent-fix (installed $NOW) ---" marker_end="# --- /agent-fix ---"
  mkdir -p "$(dirname "$file")"
  if [ -f "$file" ] && grep -q "# --- /agent-fix ---" "$file"; then
    skip "hook already present in $file"
    return
  fi
  {
    printf '\n%s\n' "$marker_begin"
    printf 'When asked to fix a broken AI coding agent, use the agent-fix skill at %s\n' "$REPO"
    printf '  - Read %s/fixes/*.md (knowledge base) and %s/SKILL.md\n' "$REPO" "$REPO"
    printf '  - Run: %s/scripts/fix doctor   (then: fix apply <id> --yes)\n' "$REPO"
    printf '%s\n' "$marker_end"
  } >> "$file"
  ok "AGENTS.md hook appended -> $file"
}

install_cli() { # $1 = bin dir
  local bindir="$1"
  mkdir -p "$bindir"
  rm -f "$bindir/fix"   # remove any stale copy/symlink from a previous install
  if ln -s "$REPO/scripts/fix" "$bindir/fix" 2>/dev/null && [ -L "$bindir/fix" ]; then
    :   # real symlink
  else
    # git-bash / MSYS without symlink support (ln silently copies): write an
    # exec shim embedding the absolute repo path. (A plain copy of scripts/fix
    # would break: the wrapper resolves its own location, so ~/bin/fix would
    # look for ~/bin/fix.py.)
    rm -f "$bindir/fix"
    cat > "$bindir/fix" <<EOF
#!/usr/bin/env bash
exec "$REPO/scripts/fix" "\$@"
EOF
  fi
  chmod +x "$bindir/fix"
  ok "CLI installed -> $bindir/fix"
}

info "installing agent-fix from $REPO"

# --- skill targets -----------------------------------------------------
if [ -n "${LOCALAPPDATA:-}" ] && [ -d "$LOCALAPPDATA" ]; then          # Windows (git-bash)
  install_skill "$LOCALAPPDATA/hermes/skills/$NAME"
elif [ -d "$HOME/.local/share/hermes" ] || [ -d "$HOME/.local/share/hermes/skills" ]; then
  install_skill "$HOME/.local/share/hermes/skills/$NAME"
else
  mkdir -p "$HOME/.local/share/hermes/skills" && install_skill "$HOME/.local/share/hermes/skills/$NAME"
fi

[ -d "$HOME/.claude" ] && install_skill "$HOME/.claude/skills/$NAME" || skip "Claude Code not detected (~/.claude missing)"
[ -d "$HOME/.config/opencode" ] && install_skill "$HOME/.config/opencode/skill/$NAME" || skip "OpenCode not detected (~/.config/opencode missing)"
[ -d "$HOME/.codex" ] && install_skill "$HOME/.codex/skills/$NAME" || skip "Codex not detected (~/.codex missing)"
[ -d "$HOME/.kimi-code" ] && install_skill "$HOME/.kimi-code/skills/$NAME" || skip "Kimi Code not detected (~/.kimi-code missing)"
[ -d "$HOME/.pi" ] && install_skill "$HOME/.pi/agent/skills/$NAME" || skip "Pi not detected (~/.pi missing)"
# shared skills dir used by ZCode and others — always install
install_skill "$HOME/.agents/skills/$NAME"

# --- AGENTS.md hooks (Codex, etc.) ------------------------------------
install_agents_md_hook "$HOME/.codex/AGENTS.md"
install_agents_md_hook "$HOME/.config/opencode/AGENTS.md"

# --- MCP server registration -----------------------------------------
info "registering MCP server with detected agents..."
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -n "$PY" ]; then
  PYREPO="$REPO"
  if command -v cygpath >/dev/null 2>&1; then PYREPO="$(cygpath -m "$REPO")"; fi
  "$PY" "$PYREPO/scripts/mcp_register.py" all || skip "MCP registration incomplete (see mcp/README.md)"
else
  skip "python not found — register MCP manually (see mcp/README.md)"
fi

# --- Self-heal startup hooks (claude/codex/opencode/hermes) ----------
info "registering self-heal startup hooks (agents check+repair themselves on start)..."
if [ -n "$PY" ]; then
  "$PY" "$PYREPO/scripts/heal_hooks.py" install || skip "self-heal hooks incomplete (see scripts/heal_hooks.py status)"
else
  skip "python not found — self-heal hooks skipped (run scripts/heal_hooks.py install later)"
fi

# --- CLI --------------------------------------------------------------
if [ -d "$HOME/bin" ] && [[ ":$PATH:" == *":$HOME/bin:"* ]]; then
  install_cli "$HOME/bin"
elif [ -d "$HOME/.local/bin" ]; then
  install_cli "$HOME/.local/bin"
else
  mkdir -p "$HOME/bin" && install_cli "$HOME/bin" && skip "add ~/bin to PATH to use the 'fix' command"
fi

ok "done. Try: fix doctor"
