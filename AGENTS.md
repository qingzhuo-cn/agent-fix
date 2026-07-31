# AGENTS.md — agent-fix skill (universal hook)

This repository contains the **agent-fix** skill: a cross-agent knowledge base and
CLI for diagnosing and repairing AI coding agents (Claude Code, Codex, OpenCode,
Hermes, Kimi Code, Pi, ZCode, Cursor, Gemini CLI, Aider, Qwen Code, Amp, Droid, and
any npm-distributed CLI) on Windows / macOS / Linux.

Any agent that reads AGENTS.md (Codex, OpenCode, Cursor, Kimi Code, Pi, Claude Code
via /init, …) should follow these instructions when asked to fix an agent, or when
the user points it at this repo.

## What's here

| Path | Purpose |
|------|---------|
| `fixes/*.md` | Human/agent-readable knowledge base — one doc per problem class, with Check / Fix / Verify sections |
| `catalog.json` | Machine-readable catalog (single source of truth) **including the agent registry** |
| `scripts/fix.py` | Cross-platform diagnostic/repair CLI + importable Python API (stdlib only) |
| `scripts/fix` | Shell launcher for the CLI |
| `SKILL.md` | Skill manifest (loadable by Hermes, Claude Code, OpenCode, Kimi Code, Pi, …) |
| `mcp/server.py` | Zero-dependency MCP stdio server — 12 tools (core fix set + 7 branch skills) callable by ANY MCP-capable agent |
| `scripts/mcp_register.py` | register/unregister the MCP server with Claude Code / OpenCode / Cursor / Codex |
| `install/` | One-command installers that deploy the skill into every detected agent |

## The fix protocol (follow in this order)

1. **Diagnose first.** Run:
   ```bash
   ./scripts/fix agents            # which agents are installed (registry-driven)
   ./scripts/fix doctor            # or: ./scripts/fix check <issue-id>
   ```
   If Python isn't available, read `fixes/README.md`, pick the matching doc, and run
   its `## Check` commands by hand.

2. **Map the symptom to an issue id:**

   | id | symptom |
   |----|---------|
   | `agent-broken-generic` | ANY detected agent's binary fails (dynamic; checks every installed agent) |
   | `npm-postinstall-skipped` | "postinstall script was not run" / "native binary not installed" (opencode, claude-code, pi, any npm CLI) |
   | `gui-path-blind` | GUI tool (CC-Switch, ZCode Desktop) says "installed · cannot run"; works in terminal (Windows) |
   | `node-version-too-old` | `EBADENGINE` / startup crash from old Node |
   | `npm-registry-mirror` | npm install/upgrade hangs or `ETIMEDOUT` / `ECONNRESET` |
   | `agent-auth-broken` | "Not logged in" / 401 / missing API key |
   | `deepseek-provider` | point any agent (incl. Kimi Code / Pi / ZCode) at the DeepSeek API |

3. **Apply the fix.** `./scripts/fix apply <id> --yes` auto-applies; manual fixes
   (node install, interactive login, native agent reinstall) print the exact
   command — run them. Or follow the doc's `## Fix` section directly.

4. **Verify with a REAL command, not just `--version`.**
   ```bash
   fix doctor                              # all agents + all issue classes
   claude "ping" --print 2>&1 | head -5    # auth/provider: needs a real model call
   ```

## MCP

If an MCP-capable agent (Claude Code, OpenCode, Cursor, ZCode, Codex) is asking
questions about this repo, it can also call the toolbox directly: register the
server with `python scripts/mcp_register.py all`, then use tools `fix_doctor`,
`fix_apply`, `net_diagnose`, `version_check`, `config_audit`, `log_triage`,
`backup_configs`, `restore_configs`, `deepseek_setup` (see `mcp/README.md`).

## Rules

- Never reinstall an agent when the issue is environmental (`gui-path-blind` is a
  PATH/registry problem; `npm-postinstall-skipped` is fixed by re-running the
  package's lifecycle script, not by reinstalling).
- New agents are registry **data**: add them to `catalog.json` → `agents` (bin,
  config home, skills dir, npm package). The CLI then detects and checks them
  automatically — no code changes.
- Native/desktop agents (`kimi`, `zcode`, `cursor`, `amp`, `droid`) are NOT npm
  packages: the npm postinstall fix doesn't apply to them; use `fixes/agent-matrix.md`
  and the per-agent docs instead.
- Use `npm root -g` instead of hardcoding the npm global path.
- `npm-postinstall-skipped` recurs on every upgrade when scripts were ever skipped —
  after fixing, suggest the watchdog: `./scripts/fix auto` on a cron/CI schedule.
- On Windows, when spawning commands yourself, never invoke bare `bash` (it resolves
  to the WSL launcher before git-bash); use the absolute Git bash path or let the
  CLI handle it.
- Keep changes minimal and verifiable; do not modify agent config files beyond the
  documented fix.
