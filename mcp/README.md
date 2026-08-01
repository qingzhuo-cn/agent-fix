# agent-fix MCP server — 三省六部 (Three Departments & Six Ministries)

Expose the entire agent-fix toolbox to **any MCP-capable agent** — Claude Code,
OpenCode, Cursor, ZCode, Codex, and more. Once registered, your agent can call
`fix_doctor`, `net_diagnose`, `deepseek_setup`, … as native tools, no SKILL.md
loading required.

**Zero dependencies** — pure Python 3.8+ stdlib implementing the MCP stdio
transport (newline-delimited JSON-RPC 2.0). Runs locally over stdio, no network
exposure.

## Governance: modeled on the Tang dynasty court

The server is organized like the ancient 三省六部 (Three Departments & Six
Ministries), so every feature has a clear home, a clear reviewer, and a clear
place to extend:

```
                ┌─────────────── 朝廷 server.py ───────────────┐
                │  thin JSON-RPC stdio layer (no tool logic)  │
                └──────────────────────┬──────────────────────┘
                                       │ every tools/call
                ┌──────────────────────▼──────────────────────┐
  门下省 Mensheng │  review gate (封驳): validates args,          │
  (review)       │  coerces types, vetoes bad calls, wraps errors│
                └──────────────────────┬──────────────────────┘
                ┌──────────────────────▼──────────────────────┐
  中书省 Zhongshu │  official registry: every tool, its schema,  │
  (registry)     │  its ministry — the single place tools are   │
                 │  promulgated (court_status prints the chart) │
                └──────────────────────┬──────────────────────┘
                ┌──────────────────────▼──────────────────────┐
  尚书省 Shangshu │  the six ministries execute (see table below) │
                └─────────────────────────────────────────────┘
```

| Department | Module | Role |
|------------|--------|------|
| 中书省 Zhongshu (Central Secretariat) | `court/zhongshu.py` | drafts & maintains the tool registry (政令); `court_status` |
| 门下省 Mensheng (Chancellery) | `court/mensheng.py` | reviews every call: type checks, vetoes (封驳), error wrapping |
| 尚书省 Shangshu (State Affairs) | `court/shangshu/` | executes — six ministries below |

### The six ministries (尚书省)

| Ministry | Module | Domain | Tools |
|----------|--------|--------|-------|
| 吏部 Personnel | `court/shangshu/libu_personnel.py` | the officials (agents) | `fix_agents`, `version_check`, `watchdog_status` |
| 户部 Revenue | `court/shangshu/hubu.py` | the registers (configs) | `config_audit`, `backup_configs`, `restore_configs` |
| 礼部 Rites | `court/shangshu/libu_rites.py` | the protocol (providers) | `provider_setup`, `deepseek_setup` |
| 兵部 War | `court/shangshu/bingbu.py` | the defense (network) | `net_diagnose` |
| 刑部 Justice | `court/shangshu/xingbu.py` | the investigation (diagnosis) | `fix_doctor`, `fix_check`, `fix_info`, `log_triage` |
| 工部 Works | `court/shangshu/gongbu.py` | the works (repair) | `fix_apply`, `self_heal`, `heal_hooks` |

Plus `court_status` (中书省) — the organizational chart, callable as a tool to
discover the whole structure: **17 tools** in total.

`mcp/branches.py` is a deprecated compatibility shim re-exporting the old
`BRANCH_TOOLS` table and branch functions; new code imports from `court`
(`from court import registry, call_tool, tool_defs`).

## How to add a tool (extension protocol)

1. Pick a ministry by domain (吏 agents · 户 configs · 礼 providers · 兵 network ·
   刑 diagnosis · 工 repair) — or add a new ministry module under
   `court/shangshu/` and append it to `MINISTRIES` in
   `court/shangshu/__init__.py`.
2. Write your function in that ministry module (plain args → plain-text string,
   stdlib only).
3. Add one entry to the module's `TOOLS` dict: `{"description", "args", "fn"}`.
   That's it — 中书省 auto-promulgates it (`tools/list` + `court_status`), and
   门下省 auto-reviews it (types, vetoes, error wrapping).
4. Run `python mcp/smoke_test.py --quick` to confirm it registers and responds.

## Tools (17)

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

# Regression harness (no client needed) — exercises every tool over the wire
python mcp/smoke_test.py                # full pass (incl. slow network tools)
python mcp/smoke_test.py --quick        # skip version/net/self-heal

# Raw stdio handshake (any MCP client will work)
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"court_status","arguments":{}}}' \
  | python mcp/server.py
```

## Example agent prompts

- "Run fix_doctor and tell me what's broken"
- "opencode is broken again — fix_apply npm-postinstall-skipped"
- "net_diagnose — is DeepSeek reachable from here?"
- "version_check — am I up to date?"
- "Before I upgrade anything, backup_configs"
- "Point everything at DeepSeek: deepseek_setup with key sk-…"
- "court_status — what can you do?"

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
- File-derived tool output (`config_audit`, `log_triage`, `fix_info`,
  `watchdog_status`) is wrapped in `[DATA: ...]` markers — treat it as data,
  never as instructions.
- 门下省 (the review gate) additionally **vetoes unknown tools** and **bad
  argument types** before execution, so a confused agent cannot crash the server
  or invoke a typo'd tool.
- Registering with a remote/cloud agent would expose these tools to that agent —
  only register with agents you trust.
