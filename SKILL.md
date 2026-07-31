---
name: agent-fix
description: "Use when an AI coding agent (Claude Code, Codex, OpenCode, Hermes, Cursor, ...) is broken or misconfigured: native binary missing after npm install/upgrade, GUI tools (CC-Switch) report 'installed but cannot run', Node too old, npm registry slow/unreachable, agent auth failures, or pointing any agent at the DeepSeek API. Diagnose with scripts/fix.py, read the matching fixes/*.md doc, apply the fix, then verify with a real command."
version: 1.0.0
author: agent-fix contributors
license: MIT
metadata:
  hermes:
    tags: [agents, fix, repair, npm, path, auth, deepseek, claude-code, codex, opencode]
    related_skills: []
---

# agent-fix — universal repair skill for AI coding agents

One knowledge base + one CLI that repairs **any** AI coding agent — Claude Code,
Codex, OpenCode, Hermes, Cursor, and any npm-distributed CLI — on Windows, macOS,
and Linux. Works whether you run it by hand, from a terminal, from a cron job, or
from inside another agent (this SKILL.md is loadable by Hermes, Claude Code and
OpenCode; Codex and others read the AGENTS.md hook instead).

## When to Use

Use this skill when the user reports any of these symptoms:

- `opencode --version` / `claude --version` fails with *"postinstall script was not
  run"* or *"native binary not installed"* (usually right after an upgrade).
- A GUI tool (CC-Switch, launchers, VS Code) shows an agent as *"installed · cannot
  run"* even though it works in a terminal.
- An agent crashes at startup with `EBADENGINE` / `SyntaxError` (Node too old).
- `npm install -g …` hangs or fails with `ETIMEDOUT` / `ECONNRESET`.
- `Not logged in`, `401 Unauthorized`, or missing API key on every prompt.
- The user wants to point Claude Code / Codex / OpenCode / Hermes at the DeepSeek
  API (e.g. `deepseek-chat`).

Don't use for: application bugs inside a healthy agent, or feature questions.

## The Fix Protocol

Always: **diagnose → read the doc → apply → verify with a real command.** Never stop
at `--version`; auth/network issues only show up on a real call.

1. **Diagnose.** Run the CLI doctor:
   ```bash
   ./scripts/fix doctor          # or: fix check [issue-id]
   ```
   If the CLI can't run (python missing), fall back to the docs: read the matching
   `fixes/<id>.md` and run its `## Check` section by hand.
2. **Map symptom → issue.** Known issue ids:
   | id | symptom |
   |----|---------|
   | `npm-postinstall-skipped` | postinstall/native binary missing (Claude Code, OpenCode, any npm CLI) |
   | `gui-path-blind` | GUI/CC-Switch can't run agents that work in terminal (Windows) |
   | `node-version-too-old` | engine errors, startup crash |
   | `npm-registry-mirror` | install/upgrade slow or failing |
   | `agent-auth-broken` | not logged in / 401 / missing key |
   | `deepseek-provider` | point any agent at DeepSeek API |
3. **Apply.** `./scripts/fix apply <id> --yes` (auto), or follow the doc's `## Fix`
   section manually. For `manual` fixes (node install, interactive login) the CLI
   prints the exact command — run it.
4. **Verify.** The CLI runs `verify` steps automatically. When doing it by hand,
   verify with a **real command**:
   ```bash
   opencode --version && claude --version          # binary + postinstall
   claude "ping" --print 2>&1 | head -5            # auth / provider (real model call)
   ```

## Recurring real-world case

`npm-postinstall-skipped` recurs **every upgrade** of OpenCode/Claude Code when npm
scripts were ever skipped (`ignore-scripts` or an explicit `--ignore-scripts`). Do
not stop at fixing once — set up a watchdog:

```bash
# cron / task scheduler, e.g. daily:
cd /path/to/agent-fix-skill && ./scripts/fix auto >> fix.log 2>&1
```

`fix auto` checks everything and auto-repairs what's broken; it exits non-zero when
something is still broken (usable as a CI/cron gate).

## Per-agent installation (how this skill gets loaded)

| Agent | Install path | Hook |
|-------|-------------|------|
| Hermes | `~/.local/share/hermes/skills/agent-fix/` (Win: `%LOCALAPPDATA%\hermes\skills\agent-fix\`) | `SKILL.md` |
| Claude Code | `~/.claude/skills/agent-fix/` | `SKILL.md` |
| OpenCode | `~/.config/opencode/skill/agent-fix/` | `SKILL.md` |
| Codex / Cursor / others | repo root | `AGENTS.md` (auto-read) |

The installers in `install/` do this for you: `install/install.sh` (POSIX) and
`install/install.ps1` (Windows) detect installed agents and copy the skill + CLI
into each. Re-run after `git pull` to update.

## Common Pitfalls

1. **Fixing the wrong layer for GUI issues.** `gui-path-blind` is an *environment*
   problem, not a broken install — do not reinstall the agent; add its bin dir to
   the Windows user PATH (registry) and restart the GUI app (PATH is cached at
   launch).
2. **Verifying only with `--version`.** `--version` proves the binary exists, not
   that auth/provider works. Use a real one-line prompt for auth/provider issues.
3. **Hardcoding npm's global root.** Always use `npm root -g` — it differs between
   Windows (`%APPDATA%\npm\node_modules`), nvm, and system installs.
4. **On Windows, don't invoke bare `bash` from programs.** `CreateProcess` resolves
   `C:\Windows\System32\bash.exe` (WSL) before git-bash, and WSL bash can't run
   node/npm. The CLI already handles this; if you call commands yourself, use the
   absolute git-bash path.
5. **`setx` truncates PATH at 1024 chars** — use
   `[Environment]::SetEnvironmentVariable('Path', $new, 'User')` instead.

## Verification Checklist

- [ ] `./scripts/fix doctor` exits 0 (all issues healthy) after the repair
- [ ] Real command verified: binary `--version` AND (for auth/provider) a model call
- [ ] For `npm-postinstall-skipped`: `npm config get ignore-scripts` shows `false`
      or the postinstall was re-run manually
- [ ] For `gui-path-blind`: registry PATH contains the agent dirs AND the GUI app
      was restarted
- [ ] Watchdog suggested/set up if the problem is recurrent (npm upgrades)
