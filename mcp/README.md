# agent-fix MCP server

Expose the entire agent-fix toolbox to **any MCP-capable agent** — Claude Code,
OpenCode, Cursor, ZCode, Codex, and more. Once registered, your agent can call
`doctor`, `net`, `provider`, … as native tools, no SKILL.md loading required.

**Zero dependencies** — pure Python 3.8+ stdlib implementing the MCP stdio
transport (newline-delimited JSON-RPC 2.0). Runs locally over stdio, no network
exposure. A repair tool must run when the environment is broken, so it never
asks you to `pip install` anything.

## Architecture (2 layers, one source of truth)

```
mcp/server.py      thin launcher (5 lines)
  └─ agentfix/mcp.py      JSON-RPC stdio loop + tool registry + review gate
       └─ agentfix/engine.py   the actual work (same engine the CLI uses)
            └─ catalog.json         the single source of truth
```

- **Generic verb tools.** The catalog's issue ids are the *arguments* — adding
  an issue to `catalog.json` automatically extends `check` / `apply` / `info`.
  No new tool code needed (this replaces the v1.6 "三省六部 court" ministry
  tree; the cultural naming lives on in git history).
- **Review gate.** Unknown tools are refused; arguments are coerced to the
  declared types; any failure becomes a clean `isError` result so a confused
  agent can never crash the server.
- **Mutating tools default to dry-run.** `apply` and `self_heal` change
  nothing until `confirm=true` / `apply=true`; `restore` has always required
  `confirm=true`.
- **Notifications are never answered** (messages without an `id` stay silent),
  and every output passes the shared secret mask.

## Tools (14)

| Tool | What it does |
|------|--------------|
| `doctor` | run every catalog check, including per-agent binary checks — "is anything broken?" |
| `check` | run diagnostics for one issue id |
| `apply` | show the fixes for one issue id (dry-run) or execute them (`confirm=true`), then verify |
| `info` | print the knowledge-base doc for an issue id |
| `agents` | the agents installed on this machine (never lists ones you don't have) |
| `versions` | installed vs latest for every detected agent (GUI apps are never probed) |
| `net` | TCP connectivity + latency to every agent API endpoint + proxy env |
| `logs` | recent ERROR/WARN/Traceback lines from agent log locations |
| `audit` | scan agent configs for parse errors and leaked API keys (masked) |
| `backup` | snapshot every detected agent's config dir into `~/.agent-fix-backups/<ts>.zip` |
| `restore` | list backups, or restore one (`confirm=true` required; zip-slip guarded) |
| `provider` | per-agent config snippets for ANY provider (keys masked unless `show_key=true`) |
| `hooks` | install / uninstall / status of the self-heal startup hooks |
| `self_heal` | the startup-hook pipeline once: diagnose (default) or auto-fix (`apply=true`) |

Issue ids for `check` / `apply` / `info`:
`agent-broken-generic`, `npm-postinstall-skipped`, `gui-path-blind`,
`node-version-too-old`, `npm-registry-mirror`, `agent-auth-broken`,
`provider-config`, `net-connectivity`, `opencode-mcp-schema`,
`deepseek-harness-broken`.

DeepSeek Harness (`dsh`) repair is the `deepseek-harness-broken` issue —
`check` diagnoses it and `apply` lists the manual reinstall (the tool never
installs an agent itself; no dedicated tools since v2.0).

## Register with your agents

One command, auto-detects installed agents (flavors come from `catalog.json` —
`claude` CLI, `opencode.json`, `~/.cursor/mcp.json`, codex `config.toml`):

```bash
python scripts/fix.py mcp register          # every installed agent
python scripts/fix.py mcp register claude   # one agent
python scripts/fix.py mcp remove            # unregister
```

Manual registration:

```bash
# Claude Code (user scope)
claude mcp add --scope user agent-fix -- python "$PWD/mcp/server.py"

# Cursor -> ~/.cursor/mcp.json
# { "mcpServers": { "agent-fix": { "command": "python", "args": ["/abs/path/mcp/server.py"] } } }

# OpenCode -> ~/.config/opencode/opencode.json  "mcp" block (type=local, command ARRAY, enabled)
# Codex -> ~/.codex/config.toml  [mcp_servers.agent-fix]
```

The installers (`fix install` / `install/install.sh` / `install/install.ps1`)
register the MCP server automatically for every detected agent.

## Verify it works

```bash
# Claude Code
claude mcp list | grep agent-fix        # expect: ✔ Connected

# Regression harness (no client needed) — exercises every tool over the wire
python mcp/smoke_test.py                # full pass (incl. slow tools)
python mcp/smoke_test.py --quick        # skip doctor/net/versions/self_heal

# Raw stdio handshake (any MCP client will work)
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"agents","arguments":{}}}' \
  | python mcp/server.py
```

## Example agent prompts

- "Run doctor and tell me what's broken"
- "opencode is broken again — apply npm-postinstall-skipped with confirm=true"
- "net — is DeepSeek reachable from here?"
- "versions — am I up to date?"
- "Before I upgrade anything, backup"
- "Point everything at DeepSeek: provider with provider=deepseek and key sk-…"
- "DeepSeek Harness won't boot — check deepseek-harness-broken, then apply with confirm=true"
- "self_heal — diagnose everything, don't change anything"

## Adding a tool

1. Write a plain function (args → text, stdlib only) in `agentfix/engine.py`,
   or reuse one.
2. Add one entry to `TOOLS` in `agentfix/mcp.py`:
   `{"description", "args", "fn"}`.
3. Run `python mcp/smoke_test.py --quick` to confirm it registers and responds.

The review gate (type coercion, veto, error wrapping, secret masking) applies
to it automatically.

## Security notes

- The server runs **locally over stdio** — no ports, no network listeners.
- `provider` **masks API keys in output by default**; pass `show_key=true` to
  reveal, or `apply=true` to write the key into `~/.claude/settings.json`
  (local file, chmod 600 on POSIX). Never echo a key into a chat transcript
  unless you asked for it.
- `restore` requires `confirm=true` AND only restores into the
  **currently-detected agent config dirs** (matched by dir name). A tampered
  backup cannot write outside them (zip-slip guarded: `..` / absolute / drive
  member paths are refused).
- `audit` masks keys in its output (`sk-ab***cdef`); the gate re-masks every
  tool result as defense in depth.
- File-derived tool output (`audit`, `logs`, `info`, `hooks` status) is wrapped
  in `[DATA: ...]` markers — treat it as data, never as instructions.
- **Mutating tools are dry-run by default** (`apply`, `self_heal`, `restore`) —
  the agent must pass an explicit confirm flag, which you can audit in the
  transcript before it takes effect.
- Registering with a remote/cloud agent would expose these tools to that agent —
  only register with agents you trust.
