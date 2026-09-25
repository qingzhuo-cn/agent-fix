# AGENTS.md — agent-fix skill (universal hook)

This repository contains the **agent-fix** skill: a cross-agent knowledge base and
CLI for diagnosing and repairing AI coding agents (Claude Code, Codex, OpenCode,
Hermes, Kimi Code, MiniMax Code, Pi, ZCode, Cursor, Gemini CLI, Aider, Qwen Code,
Amp, Droid, and any npm-distributed CLI) on Windows / macOS / Linux.

Any agent that reads AGENTS.md (Codex, OpenCode, Cursor, Kimi Code, Pi, Claude Code
via /init, …) should follow these instructions when asked to fix an agent, or when
the user points it at this repo.

## What's here

| Path | Purpose |
|------|---------|
| `fixes/*.md` | Human/agent-readable knowledge base — one doc per problem class, with Check / Fix / Verify sections |
| `catalog.json` | Machine-readable catalog (single source of truth) **including the agent registry** |
| `agentfix/` | The engine package: `catalog.py` (registry/paths), `engine.py` (checks/fixes/diagnostics), `hooks.py` (legacy hook cleanup/MCP/install), `mcp.py` (MCP server), `cli.py`, `report.py` (secret masking) |
| `scripts/fix.py` | CLI entry point (thin launcher over `agentfix.cli`; stdlib only) |
| `scripts/fix` | Shell launcher for the CLI |
| `SKILL.md` | Skill manifest (loadable by Hermes, Claude Code, OpenCode, Kimi Code, Pi, …) |
| `mcp/server.py` | Zero-dependency MCP stdio server — 12 generic verb tools (`check`/`apply`/`info`/`agents`/`versions`/`net`/`logs`/`audit`/`backup`/`restore`/`provider`/`hooks`); `python mcp/smoke_test.py` regresses every tool |
| `install/` | Thin bootstrap launchers that forward one explicit `--agent` target to `fix install` / `fix uninstall` |

## The fix protocol (follow in this order)

Only diagnose, repair, and verify the target the user explicitly named. Never
probe another installed agent or model as collateral verification.

1. **Diagnose the explicit target first.** Run:
   ```bash
   ./scripts/fix agents   # inventory only
   ./scripts/fix check <issue-id> --agent <agent-id>
   ```
   If Python isn't available, read `fixes/README.md`, pick the matching doc, and run
   its `## Check` commands only for the named target.

2. **Map the symptom to an issue id:**

   | id | symptom |
   |----|---------|
   | `agent-broken-generic` | the explicitly selected agent's binary fails |
   | `npm-postinstall-skipped` | "postinstall script was not run" / "native binary not installed" (opencode, claude-code, pi, kimi-code, minimax-code, any npm CLI) |
   | `gui-path-blind` | GUI tool such as CC-Switch says "installed · cannot run" while the terminal works; use it only for targets listed by the issue catalog. For ZCode Desktop, follow `fixes/zcode.md` |
   | `node-version-too-old` | `EBADENGINE` / startup crash from old Node |
   | `npm-registry-mirror` | npm install/upgrade hangs or `ETIMEDOUT` / `ECONNRESET` |
   | `agent-auth-broken` | "Not logged in" / 401 / missing API key |
   | `provider-config` | no provider configured — set key/base URL/model for the named provider and agent |
   | `net-connectivity` | an explicitly selected API endpoint is unreachable (TCP/DNS/proxy) |
   | `opencode-mcp-schema` | `opencode.json` MCP entry invalid (`type: stdio` / string `command` / missing `enabled`) → `ConfigInvalidError` on every opencode run incl. history |
   | `deepseek-harness-broken` | `dsh` (DeepSeek Harness) won't boot — binary missing / Node too old / incomplete plugin bundles |

3. **Apply the fix.** `./scripts/fix apply <id> --agent <agent-id> --yes` applies
   only to that target. Manual fixes (node install, interactive login, native agent
   reinstall) print the exact command — run it. Or follow the doc's `## Fix`
   section directly.

4. **Verify only that same target with a REAL command, not just `--version`.**
   ```bash
   fix check agent-auth-broken --agent claude-code
   claude "ping" --print 2>&1 | head -5    # only when claude-code is the requested target
   ```

## MCP

If an MCP-capable agent (Claude Code, OpenCode, Cursor, Codex, Kimi Code, MiniMax Code) is asking
questions about this repo, register the server only for that agent with
`python scripts/fix.py mcp register <agent-id>`. The 12 tools are `check`, `apply`,
`info`, `agents`, `versions`, `net`, `logs`, `audit`, `backup`, `restore`,
`provider`, and `hooks` (see `mcp/README.md`). Except for inventory (`agents`),
calls operate on an explicit `agent_id` or `host`. `apply` and `restore` require
explicit confirmation; `provider` is read-only unless `apply=true`, which writes
Claude Code settings only. The `hooks` tool is retained only for targeted
cleanup of legacy hooks; `hooks(action="uninstall", ...)` also requires
`confirm=true`; do not install startup repair hooks.

## Rules

- Never reinstall an agent when the issue is environmental (`gui-path-blind` is a
  PATH/registry problem; `npm-postinstall-skipped` is fixed by re-running the
  package's lifecycle script, not by reinstalling).
- New agents are registry **data**: add them to `catalog.json` → `agents` (bin,
  config home, skills dir, npm package). The CLI can then target them explicitly —
  no code changes.
- Kimi Code and MiniMax Code are current npm-distributed CLIs with documented
  Node requirements; use their package lifecycle and data-directory contracts.
  Native/desktop agents (`zcode`, `cursor`, `amp`, `droid`) are not npm packages:
  the npm postinstall fix does not apply to them.
- Use `npm root -g` instead of hardcoding the npm global path.
- Install does not register startup repair hooks or MCP with multiple agents.
- Legacy startup hooks may only be removed for the explicitly named agent.
- On Windows, when spawning commands yourself, never invoke bare `bash` (it resolves
  to the WSL launcher before git-bash); use the absolute Git bash path or let the
  CLI handle it.
- Keep changes minimal and verifiable; do not modify agent config files beyond the
  documented fix.
