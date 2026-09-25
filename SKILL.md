---
name: agent-fix
description: "Use when an explicitly named AI coding agent (Claude Code, Codex, OpenCode, Hermes, Kimi Code, Pi, ZCode, Cursor, Gemini CLI, ...) is broken or misconfigured: native binary missing after npm install/upgrade, GUI tools (CC-Switch) report 'installed but cannot run', Node too old, npm registry slow/unreachable, agent auth failures, or pointing that agent at an API provider. Run `fix check <issue-id> --agent <agent-id>`, read the matching fixes/*.md doc, apply with the same explicit agent, then verify only that target with a real command. The MCP server exposes 12 tools: check/apply/info/agents/versions/net/logs/audit/backup/restore/provider/hooks."
version: 2.0.0
author: agent-fix contributors
license: MIT
metadata:
  hermes:
    tags: [agents, fix, repair, npm, path, auth, deepseek, claude-code, codex, opencode, hermes, kimi-code, pi, zcode]
    related_skills: []
---

# agent-fix — universal repair skill for AI coding agents

One knowledge base + one CLI that repairs **any explicitly selected** AI coding
agent — Claude Code, Codex, OpenCode, Hermes, Kimi Code, Pi, ZCode, Cursor, Gemini
CLI, Aider, Qwen Code, Amp, Droid, and any npm-distributed CLI — on Windows,
macOS, and Linux. The registry in `catalog.json` resolves one explicit target for
each check or repair. It never scans, repairs, or verifies other installed agents
or models. This SKILL.md is loadable by Hermes, Claude Code, OpenCode, Kimi Code,
Pi and others; Codex reads the AGENTS.md hook / `~/.codex/skills` instead.

## When to Use

Use this skill when the user reports any of these symptoms for a named agent:

- Its `--version` fails with *"postinstall script was not run"* /
  *"native binary not installed"* (usually right after an upgrade) — covers
  `claude`, `codex`, `opencode`, `pi`, `gemini`, `qwen-code`, and npm CLIs.
- A GUI tool (CC-Switch, ZCode Desktop, launchers, VS Code) shows it as
  *"installed · cannot run"* even though it works in a terminal.
- It crashes at startup with `EBADENGINE` / `SyntaxError` (Node too old).
- Its npm install hangs or fails with `ETIMEDOUT` / `ECONNRESET`.
- It reports `Not logged in`, `401 Unauthorized`, or a missing API key.
- The user wants to point it at an API provider such as DeepSeek.

Don't use for application bugs inside a healthy agent, or feature questions.

## The Fix Protocol

Always: **diagnose → read the doc → apply → verify with a real command.** Perform
every step only for the agent or host explicitly named by the user. Never use an
unrelated installed agent or model as collateral verification. Never stop at
`--version`; auth/provider issues only show up on a real call.

1. **Diagnose.**
   ```bash
   fix agents   # inventory only; does not diagnose or repair
   ./scripts/fix check <issue-id> --agent <agent-id>
   ```
   If the CLI can't run (Python missing), read the matching `fixes/<id>.md` and
   run its `## Check` section only for the named target.
2. **Map symptom → issue.** Known issue ids:
   | id | symptom |
   |----|---------|
   | `agent-broken-generic` | the explicitly selected agent's binary fails |
   | `npm-postinstall-skipped` | npm lifecycle script skipped (Claude Code, OpenCode, Pi, Kimi Code, MiniMax Code, any npm CLI) |
   | `gui-path-blind` | GUI/CC-Switch can't run a supported named agent although it works in a terminal (Windows); for ZCode Desktop, follow `fixes/zcode.md` |
   | `node-version-too-old` | engine errors, startup crash |
   | `npm-registry-mirror` | install/upgrade slow or failing |
   | `agent-auth-broken` | not logged in / 401 / missing key |
   | `provider-config` | no provider configured — set key/base URL/model for the named provider and agent |
   | `net-connectivity` | an explicitly selected API endpoint is unreachable — TCP/DNS/proxy layer |
   | `opencode-mcp-schema` | opencode.json MCP entry invalid (`type: stdio` / string `command` / missing `enabled`) → `ConfigInvalidError` incl. history |
   | `deepseek-harness-broken` | `dsh` (DeepSeek Harness) won't boot — binary missing / Node too old / install incomplete |
3. **Apply.** `./scripts/fix apply <id> --agent <agent-id> --yes`, or follow the
   doc's `## Fix` section manually. For manual fixes (node install, interactive
   login, native agent reinstall) the CLI prints the exact command — run it.
4. **Verify.** The CLI runs `verify` steps for that same target. When doing it by
   hand, verify only the named target with a **real command**:
   ```bash
   fix check agent-auth-broken --agent claude-code
   claude "ping" --print 2>&1 | head -5   # only for a requested claude-code target
   ```

## Adding an agent (universal by design)

New agents are **data**, not code. Add one line to `catalog.json` → `agents`
(bin, config home, skills dir, npm package), then target it with
`fix check <issue-id> --agent <agent-id>`. See `fixes/agent-matrix.md`.

## MCP server (any agent can call the toolbox directly)

`mcp/server.py` is a zero-dependency MCP stdio server exposing 12 generic verb
tools (see `mcp/README.md`): `check`, `apply`, `info`, `agents`, `versions`, `net`,
`logs`, `audit`, `backup`, `restore`, `provider`, and `hooks`. Except for inventory
(`agents`), calls operate on an explicit `agent_id` or `host`. The catalog's issue
ids are arguments, so new catalog issues are callable without new tool code.
Mutating tools (`apply`, `restore`, `provider`) are **dry-run by default** and need
an explicit `confirm`/`apply` flag. Register it for one named agent:

```bash
python scripts/fix.py mcp register claude-code
claude mcp list | grep agent-fix   # verify the requested registration
```

See `mcp/README.md` for the tool table and examples such as "check
npm-postinstall-skipped for opencode" or "net host=api.deepseek.com". Installers
do not register MCP automatically or in bulk. The `hooks` tool is retained only
to remove legacy startup hooks for one explicitly named agent.

## Per-agent installation (how this skill gets loaded)

| Agent | Install path | Loader |
|-------|-------------|--------|
| Hermes | `~/.local/share/hermes/skills/agent-fix/` (Win: `%LOCALAPPDATA%\hermes\skills\agent-fix\`) | `SKILL.md` |
| Claude Code | `~/.claude/skills/agent-fix/` | `SKILL.md` |
| Codex CLI | `~/.codex/skills/agent-fix/` | `SKILL.md` |
| OpenCode | `~/.config/opencode/skills/agent-fix/` | `SKILL.md` |
| Kimi Code | `~/.kimi-code/skills/agent-fix/` | `SKILL.md` (auto-discovered) |
| MiniMax Code | `~/.minimax/skills/agent-fix/` (or active `MINIMAX_DATA_DIR`) | `SKILL.md` (data-dir scoped) |
| Pi | `~/.pi/agent/skills/agent-fix/` | `SKILL.md` |
| ZCode & shared | `~/.agents/skills/agent-fix/` | `SKILL.md` |
| Cursor / others | repo root | `AGENTS.md` (auto-read) |

One command deploys skill copies, AGENTS.md instructions, and the `fix` CLI shim.
It does not register startup repair hooks or MCP servers:

```bash
install/install.sh --agent <id>        # POSIX (or Git Bash on Windows)
python scripts/fix.py uninstall --agent <id>
```

```powershell
& .\install\install.ps1 --agent <id>
```

Re-run after `git pull` to update.

## Common Pitfalls

1. **Fixing the wrong layer for GUI issues.** `gui-path-blind` is an *environment*
   problem, not a broken install — do not reinstall the agent; add its bin dir to
   the Windows user PATH (registry) and restart the GUI app (PATH is cached at
   launch). Use the automated issue only for targets listed in the catalog; for
   ZCode Desktop, follow the manual PATH steps in `fixes/zcode.md`.
2. **Verifying only with `--version`.** `--version` proves the binary exists, not
   that auth/provider works. Use a real one-line prompt for the named target when
   auth/provider is the reported issue.
3. **Hardcoding npm's global root.** Always use `npm root -g` — it differs between
   Windows (`%APPDATA%\npm\node_modules`), nvm, and system installs.
4. **On Windows, don't invoke bare `bash` from programs.** `CreateProcess` resolves
   `C:\Windows\System32\bash.exe` (WSL) before git-bash, and WSL bash can't run
   node/npm. The CLI already handles this; if you call commands yourself, use the
   absolute git-bash path.
5. **`setx` truncates PATH at 1024 chars** — use
   `[Environment]::SetEnvironmentVariable('Path', $new, 'User')` instead.
6. **Forgetting package vs desktop boundaries.** Kimi Code and MiniMax Code are current npm-distributed CLIs with their own Node requirements; use the package lifecycle path when applicable. ZCode, Cursor, Amp, and Droid are native/desktop targets, so the npm postinstall fix does not apply.

## Verification Checklist

- [ ] `./scripts/fix check <issue-id> --agent <agent-id>` checks only the requested target
- [ ] A real command verifies only that target: binary `--version` AND, for auth/provider, a model call
- [ ] For `npm-postinstall-skipped`/`agent-broken-generic`: `npm config get ignore-scripts` shows `false` or the target package's lifecycle script was re-run manually
- [ ] For `gui-path-blind`: registry PATH contains the target agent dir and the relevant GUI app was restarted
