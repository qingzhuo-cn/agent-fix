---
name: agent-fix
description: "Use when ANY AI coding agent (Claude Code, Codex, OpenCode, Hermes, Kimi Code, Pi, ZCode, Cursor, Gemini CLI, ...) is broken or misconfigured: native binary missing after npm install/upgrade, GUI tools (CC-Switch) report 'installed but cannot run', Node too old, npm registry slow/unreachable, agent auth failures, or pointing any agent at the DeepSeek API. Run `fix agents` to see installed agents, `fix doctor` to diagnose, read the matching fixes/*.md doc, apply the fix, then verify with a real command. An MCP server (mcp/server.py) exposes the same tools plus net_diagnose/version_check/config_audit/log_triage/backup/restore/deepseek_setup to any MCP-capable agent."
version: 1.4.0
author: agent-fix contributors
license: MIT
metadata:
  hermes:
    tags: [agents, fix, repair, npm, path, auth, deepseek, claude-code, codex, opencode, hermes, kimi-code, pi, zcode]
    related_skills: []
---

# agent-fix — universal repair skill for AI coding agents

One knowledge base + one CLI that repairs **any** AI coding agent — Claude Code,
Codex, OpenCode, Hermes, Kimi Code, Pi, ZCode, Cursor, Gemini CLI, Aider, Qwen
Code, Amp, Droid, and any npm-distributed CLI — on Windows, macOS, and Linux. The
agent registry in `catalog.json` drives a **dynamic** check: `fix doctor` verifies
the binary of *every agent actually installed on the machine*, not just the big
four. Works by hand, from a terminal, from a cron job, or from inside another agent
(this SKILL.md is loadable by Hermes, Claude Code, OpenCode, Kimi Code, Pi and
others; Codex reads the AGENTS.md hook / `~/.codex/skills` instead).

## When to Use

Use this skill when the user reports any of these symptoms:

- Any agent's `--version` fails with *"postinstall script was not run"* /
  *"native binary not installed"* (usually right after an upgrade) — covers
  `claude`, `codex`, `opencode`, `pi`, `gemini`, `qwen-code`, and every npm CLI.
- A GUI tool (CC-Switch, ZCode Desktop, launchers, VS Code) shows an agent as
  *"installed · cannot run"* even though it works in a terminal.
- An agent crashes at startup with `EBADENGINE` / `SyntaxError` (Node too old).
- `npm install -g …` hangs or fails with `ETIMEDOUT` / `ECONNRESET`.
- `Not logged in`, `401 Unauthorized`, or missing API key on every prompt.
- The user wants to point any agent (including Kimi Code / Pi / ZCode) at the
  DeepSeek API (e.g. `deepseek-chat`).

Don't use for: application bugs inside a healthy agent, or feature questions.

## The Fix Protocol

Always: **diagnose → read the doc → apply → verify with a real command.** Never stop
at `--version`; auth/network issues only show up on a real call.

1. **Diagnose.**
   ```bash
   fix agents          # which agents are installed (registry-driven)
   ./scripts/fix doctor          # or: fix check [issue-id]
   ```
   If the CLI can't run (python missing), fall back to the docs: read the matching
   `fixes/<id>.md` and run its `## Check` section by hand.
2. **Map symptom → issue.** Known issue ids:
   | id | symptom |
   |----|---------|
   | `agent-broken-generic` | ANY detected agent's binary fails (dynamic, registry-driven) |
   | `npm-postinstall-skipped` | postinstall/native binary missing (Claude Code, OpenCode, any npm CLI) |
   | `gui-path-blind` | GUI/CC-Switch/ZCode Desktop can't run agents that work in terminal (Windows) |
   | `node-version-too-old` | engine errors, startup crash |
   | `npm-registry-mirror` | install/upgrade slow or failing |
   | `agent-auth-broken` | not logged in / 401 / missing key |
   | `provider-config` | no provider configured — set key/base URL/model for ANY provider (DeepSeek/OpenAI/Anthropic/Google/Ollama/...) |
   | `net-connectivity` | API endpoints unreachable — TCP/DNS/proxy layer under all agents |
3. **Apply.** `./scripts/fix apply <id> --yes` (auto), or follow the doc's `## Fix`
   section manually. For `manual` fixes (node install, interactive login, native
   agent reinstall) the CLI prints the exact command — run it.
4. **Verify.** The CLI runs `verify` steps automatically. When doing it by hand,
   verify with a **real command**:
   ```bash
   fix doctor                                   # all agents + all issue classes
   claude "ping" --print 2>&1 | head -5         # auth / provider (real model call)
   ```

## Adding an agent (universal by design)

New agents are **data**, not code. Add one line to `catalog.json` → `agents`
(bin, config home, skills dir, npm package), then `fix agents` detects it and
`fix doctor` checks it automatically. See `fixes/agent-matrix.md`.

## MCP server (any agent can call the toolbox directly)

`mcp/server.py` is a zero-dependency MCP stdio server exposing 12 tools: the core
inspect/fix set (`fix_agents`, `fix_doctor`, `fix_check`, `fix_apply`, `fix_info`)
plus 7 branch skills (`net_diagnose`, `version_check`, `config_audit`,
`log_triage`, `backup_configs`, `restore_configs`, `deepseek_setup`). Register it
once, and Claude Code / OpenCode / Cursor / ZCode / Codex can call any tool as a
native function — no SKILL.md loading needed:

```bash
python scripts/mcp_register.py all          # register with every installed agent
claude mcp list | grep agent-fix            # verify: ✔ Connected
```

See `mcp/README.md` for the tool table, manual registration per agent, and example
prompts ("run fix_doctor", "net_diagnose — is DeepSeek reachable?", "backup_configs
before upgrading"). The installers register MCP automatically.

## Recurring real-world case

`npm-postinstall-skipped` recurs **every upgrade** of npm-installed agents when npm
scripts were ever skipped (`ignore-scripts` or an explicit `--ignore-scripts`). Do
not stop at fixing once — set up a watchdog:

```bash
# cron / task scheduler, e.g. daily:
cd /path/to/agent-fix-skill && ./scripts/fix auto >> fix.log 2>&1
```

`fix auto` checks everything (including every detected agent's binary) and
auto-repairs what's broken; it exits non-zero when something is still broken.

## Per-agent installation (how this skill gets loaded)

| Agent | Install path | Hook |
|-------|-------------|------|
| Hermes | `~/.local/share/hermes/skills/agent-fix/` (Win: `%LOCALAPPDATA%\hermes\skills\agent-fix\`) | `SKILL.md` |
| Claude Code | `~/.claude/skills/agent-fix/` | `SKILL.md` |
| Codex CLI | `~/.codex/skills/agent-fix/` | `SKILL.md` |
| OpenCode | `~/.config/opencode/skill/agent-fix/` | `SKILL.md` |
| Kimi Code | `~/.kimi-code/skills/agent-fix/` | `SKILL.md` (auto-discovered) |
| Pi | `~/.pi/agent/skills/agent-fix/` | `SKILL.md` |
| ZCode & shared | `~/.agents/skills/agent-fix/` | `SKILL.md` |
| Cursor / others | repo root | `AGENTS.md` (auto-read) |

The installers in `install/` do this for you: `install/install.sh` (POSIX) and
`install/install.ps1` (Windows) detect installed agents and copy the skill + CLI
into each. Re-run after `git pull` to update.

## Common Pitfalls

1. **Fixing the wrong layer for GUI issues.** `gui-path-blind` is an *environment*
   problem, not a broken install — do not reinstall the agent; add its bin dir to
   the Windows user PATH (registry) and restart the GUI app (PATH is cached at
   launch). Applies to ZCode Desktop and any GUI launcher too.
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
6. **Forgetting native/desktop agents.** `kimi`, `zcode`, `cursor` are NOT npm
   packages — the npm postinstall fix doesn't apply; use the generic binary check
   + reinstall / GUI PATH fixes (`fixes/kimi-code.md`, `fixes/zcode.md`).

## Verification Checklist

- [ ] `./scripts/fix doctor` exits 0 (all issues + all detected agents healthy)
- [ ] `fix agents` lists every installed agent as INSTALLED
- [ ] Real command verified: binary `--version` AND (for auth/provider) a model call
- [ ] For `npm-postinstall-skipped`/`agent-broken-generic`: `npm config get
      ignore-scripts` shows `false` or the postinstall was re-run manually
- [ ] For `gui-path-blind`: registry PATH contains the agent dirs AND the GUI app
      was restarted
- [ ] Watchdog suggested/set up if the problem is recurrent (npm upgrades)
