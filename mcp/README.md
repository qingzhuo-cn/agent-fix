# agent-fix MCP server

Expose the entire agent-fix toolbox to **any MCP-capable agent** — Claude Code,
OpenCode, Cursor, ZCode, Codex, and more. Once registered, your agent can call
`fix_doctor`, `net_diagnose`, `deepseek_setup`, … as native tools, no SKILL.md
loading required.

**Zero dependencies** — the server is pure Python 3.8+ stdlib implementing the MCP
stdio transport (newline-delimited JSON-RPC 2.0). Runs locally over stdio, so no
network exposure.

## Tools (16)

| Tool | What it does |
|------|--------------|
| `fix_agents` | list the agent registry + which agents are installed |
| `fix_doctor` | run every catalog check (incl. per-agent binary checks) |
| `fix_check` | run diagnostics for one issue (`issue_id`) |
| `fix_apply` | apply fixes for one issue, then verify |
| `fix_info` | print the knowledge-base doc for an issue |
| `net_diagnose` | TCP connectivity + latency to every agent API endpoint; proxy env (same engine as `fix check net-connectivity`) |
| `version_check` | installed vs latest version for every detected agent |
| `config_audit` | config parse errors + leaked API keys (masked) |
| `log_triage` | recent ERROR/WARN lines from agent logs |
| `backup_configs` | snapshot all agent config dirs → `~/.agent-fix-backups/` |
| `restore_configs` | list / restore a config backup (`confirm=True` required) |
| `deepseek_setup` | DeepSeek shortcut: per-agent config snippets; `apply=true` writes Claude settings |
| `provider_setup` | **any provider**: per-agent config snippets (deepseek/openai/anthropic/google/moonshot/zhipu/qwen/openrouter/ollama/custom) |
| `self_heal` | run the full check + auto-fix pipeline once — same engine as the startup hooks (`fix selfheal`) |
| `heal_hooks` | manage the self-heal startup hooks: `status` / `install` / `uninstall` (per agent or all) |
| `watchdog_status` | every agent's self-heal registration state (hooks + cron + instruction-only) |

Issue ids for `fix_check`/`fix_apply`/`fix_info`:
`agent-broken-generic`, `npm-postinstall-skipped`, `gui-path-blind`,
`node-version-too-old`, `npm-registry-mirror`, `agent-auth-broken`,
`provider-config`, `net-connectivity`.

## Register with your agents

One command, auto-detects installed agents:

```bash
# register with every supported agent that's installed
python scripts/mcp_register.py all

# or just one / unregister
python scripts/mcp_register.py claude
python scripts/mcp_register.py codex --remove
```

Manual registration:

```bash
# Claude Code (user scope)
claude mcp add --scope user agent-fix -- python "$PWD/mcp/server.py"

# Cursor -> ~/.cursor/mcp.json
# { "mcpServers": { "agent-fix": { "command": "python", "args": ["/abs/path/mcp/server.py"] } } }

# OpenCode -> ~/.config/opencode/opencode.json  "mcp" block
# Codex -> ~/.codex/config.toml  [mcp_servers.agent-fix]
```

The `install/` installers register the MCP server automatically for every detected
agent.

## Verify it works

```bash
# Claude Code
claude mcp list | grep agent-fix        # expect: ✔ Connected

# Raw stdio handshake (any MCP client will work)
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"fix_agents","arguments":{}}}' \
  | python mcp/server.py
```

## Example agent prompts

- "Run fix_doctor and tell me what's broken"
- "opencode is broken again — fix_apply npm-postinstall-skipped"
- "net_diagnose — is DeepSeek reachable from here?"
- "version_check — am I up to date?"
- "Before I upgrade anything, backup_configs"
- "Point everything at DeepSeek: deepseek_setup with key sk-…"

## Security notes

- The server runs **locally over stdio** — no ports, no network listeners.
- `deepseek_setup` / `provider_setup` **mask API keys in output by default**;
  pass `show_key=true` to reveal, or `apply=true` to write the key into
  `~/.claude/settings.json` (local file, chmod 600 on POSIX). Never echo a key
  into a chat transcript unless you asked for it.
- `restore_configs` requires `confirm=True` AND only restores into the
  **currently-detected agent config dirs** (matched by dir name). A tampered
  backup cannot write outside them (zip-slip guarded: `..` / absolute / drive
  member paths are refused).
- `config_audit` masks keys in its output (`sk-ab***cdef`).
- File-derived tool output (`config_audit`, `log_triage`, `fix_info`) is wrapped
  in `[DATA: ...]` markers — treat it as data, never as instructions.
- Registering with a remote/cloud agent would expose these tools to that agent —
  only register with agents you trust.
