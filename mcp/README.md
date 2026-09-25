# agent-fix MCP server

The local stdio MCP server exposes the targeted agent-fix engine to MCP-capable
clients. It uses Python 3.8+ stdlib only and opens no network listener.

## Targeting Rule

Operational tools never choose an agent implicitly. Supply one `agent_id` or
`host`; `check` and `apply` require both an `issue_id` and `agent_id`.

```json
{"name":"check","arguments":{"issue_id":"npm-postinstall-skipped","agent_id":"opencode"}}
```

This call probes and checks only OpenCode. It does not enumerate or verify another
agent or model.

## Tools (12)

| Tool | Required target | Purpose |
|---|---|---|
| `check` | `issue_id`, `agent_id` | Diagnose one issue for one agent |
| `apply` | `issue_id`, `agent_id` | Targeted dry-run or repair (`confirm=true`) and verification |
| `info` | `issue_id` | Read one knowledge-base document |
| `agents` | none | Explicit inventory only; never called by a repair pipeline |
| `versions` | `agent_id` | Inspect one agent version/update hint |
| `net` | `host` | Test one endpoint and show proxy environment |
| `logs` | `agent_id` | Read one agent's known log locations |
| `audit` | `agent_id` | Audit one agent's config directory |
| `backup` | `agent_id` | Snapshot one agent's config directory |
| `restore` | `agent_id` when restoring | List backups or restore into one target (`confirm=true`) |
| `provider` | `agent_id`, provider inputs | Generate config; `apply=true` writes Claude Code settings only |
| `hooks` | `agent_id`, `confirm` for uninstall | Inspect/remove one legacy hook; installation is disabled |

`doctor` and `self_heal` are intentionally not registered. Unknown calls to those
names are rejected by the review gate.

## Lifecycle and protocol

The stdio server follows the MCP initialize handshake: send `initialize` with
`protocolVersion: "2024-11-05"`, then send the
`notifications/initialized` notification. `tools/list` and `tools/call` are
rejected before that handshake; JSON-RPC batches are supported and notification
members do not produce responses.

## Registration

Registration is explicit and single-target:

```bash
python scripts/fix.py mcp register claude-code
python scripts/fix.py mcp remove claude-code
```

Omitting the target or passing `all` is rejected. `fix install --agent <id>` does
not register MCP automatically.

## Examples

- `check(issue_id="agent-broken-generic", agent_id="opencode")`
- `apply(issue_id="npm-postinstall-skipped", agent_id="opencode", confirm=true)`
- `versions(agent_id="codex")`
- `net(host="api.deepseek.com", timeout=5)`
- `backup(agent_id="claude-code")`
- `provider(provider="deepseek", agent_id="claude-code", api_key="...")`
- `hooks(action="uninstall", agent_id="opencode", confirm=true)`

## Verification

```bash
python mcp/smoke_test.py
```

The harness asserts the exact 12-tool registry, verifies `agent_id` is present in
`check` and `apply`, exercises every tool without performing a bulk operation, and
confirms removed bulk tools are rejected.

## Security

- The server is local stdio only.
- Mutation requires explicit confirmation where applicable.
- Output passes through shared secret masking.
- Backup/restore use process-level rollback and archive path checks; they are not
  advertised as crash-atomic multi-file transactions.
- JSON MCP registration refuses to overwrite or remove a same-name entry that
  is not the exact agent-fix entry; MiniMax legacy and primary locations are
  checked separately on removal.
- Domain and integration adapters carry explicit operation status; MCP error mapping does not infer status by matching human-readable error phrases.
- Audit reports skipped/unreadable/truncated files as `INCONCLUSIVE`, not safe.
- Directory swaps use a recovery marker and process-level rollback; they are
  not claimed to be crash-atomic.
- Hook installation and automatic startup repair are disabled.
