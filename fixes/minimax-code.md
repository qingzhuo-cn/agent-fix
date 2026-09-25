# MiniMax Code

- **Agent id:** `minimax-code` · bin: `mcode` · package: `@minimax-ai/code`
- **Official docs:** [Quick start](https://agent.minimax.cn/docs/cli/quick-start) · [Configuration](https://agent.minimax.cn/docs/cli/configuration) · [Features](https://agent.minimax.cn/docs/cli/features) · [Command reference](https://agent.minimax.cn/docs/cli/reference)
- **MCP:** [MCP services](https://agent.minimax.cn/docs/code/agents/mcp)

MiniMax Code is MiniMax's terminal coding agent. The current CLI is published
as `@minimax-ai/code` and exposes `mcode` (with `mcode-tools` as a helper in the
package). Its documented Node range is `>=22.19 <23 || >=24 <27`.

## Data and skills

The default data root is `~/.minimax`; set `MINIMAX_DATA_DIR` to move it.
`MAVIS_DATA_DIR` is the documented compatibility fallback (MINIMAX wins when
both are set). The runtime configuration is `<data-dir>/config.yaml`, and local
MCP configuration is normally `<data-dir>/mcp.json` (its JSON object uses the
`mcpServers` container). A legacy
`<data-dir>/mcp/mcp.json` location is retained for compatibility. Skills are
kept under the data directory (`<data-dir>/skills`), so an installed skill
belongs at `~/.minimax/skills/agent-fix/` by default.

## Common failures & fixes

1. **`mcode: command not found`** — the package bin directory is not on PATH.
   Add it to the shell and GUI-visible PATH, then restart the application.
2. **`EBADENGINE` / startup failure** — install a supported Node version from
   the range above and reinstall/update the package.
3. **Authentication or provider errors** — use `mcode login` (including
   `--region global` when appropriate) or configure the provider through the
   documented `mcode provider` commands. If an API key is supplied through the
   environment, MiniMax documents `MCODE_PROVIDER_API_KEY` and
   `mcode provider set-minimax-key`; never print the value.
4. **Skill or MCP not discovered** — check the active data directory, the
   `<data-dir>/skills` and `<data-dir>/mcp.json` paths, and test only the named
   target. Do not install a startup repair hook.

## Verification

```bash
mcode --version
mcode exec --output-format json "reply with exactly OK"
fix check agent-broken-generic --agent minimax-code
```

`--version` proves only the launcher. Use the real `mcode exec` round trip for
auth/provider verification and keep the target explicit throughout.
