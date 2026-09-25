# Fix Catalog

This directory is the human/agent-readable knowledge base behind `agent-fix`.
Each issue documents symptoms, root cause, targeted checks, the fix, and
verification.

| ID | Problem | Doc |
|---|---|---|
| `agent-broken-generic` | The selected agent binary fails | [agent-matrix.md](agent-matrix.md) |
| `npm-postinstall-skipped` | npm lifecycle script skipped (Claude Code, Codex, OpenCode, Pi, Kimi Code, MiniMax Code, npm CLIs) | [npm-postinstall.md](npm-postinstall.md) |
| `gui-path-blind` | GUI cannot find the selected agent binary | [gui-path.md](gui-path.md) |
| `node-version-too-old` | Node is too old for the selected agent | [node-version.md](node-version.md) |
| `npm-registry-mirror` | npm registry is slow or unreachable | [npm-registry.md](npm-registry.md) |
| `agent-auth-broken` | Login or credential is missing/expired | [agent-auth.md](agent-auth.md) |
| `provider-config` | Provider key/base URL/model is missing | [provider-config.md](provider-config.md) |
| `net-connectivity` | The selected agent endpoint is unreachable | [net-connectivity.md](net-connectivity.md) |
| `opencode-mcp-schema` | OpenCode MCP schema is invalid | [opencode-mcp-schema.md](opencode-mcp-schema.md) |
| `deepseek-harness-broken` | DeepSeek Harness (`dsh`) cannot boot | [deepseek-harness.md](deepseek-harness.md) |

## Per-agent references

- [Kimi Code](kimi-code.md)
- [MiniMax Code](minimax-code.md)
- [Pi](pi.md)
- [ZCode](zcode.md)
- [Registry matrix](agent-matrix.md)

## Usage

Run only the checks and fixes for the target named by the user:

```bash
fix check <issue-id> --agent <agent-id>
fix apply <issue-id> --agent <agent-id> --yes
```

Do not use another installed agent or model as collateral verification.
