# Kimi Code

- **Agent id:** `kimi-code` · bin: `kimi` · package: `@moonshot-ai/kimi-code`
- **Official docs:** [Getting started](https://moonshotai.github.io/kimi-code/zh/guides/getting-started.html) · [Configuration](https://moonshotai.github.io/kimi-code/zh/configuration/config-files.html) · [Data locations](https://moonshotai.github.io/kimi-code/zh/configuration/data-locations.html)
- **MCP:** [MCP configuration](https://moonshotai.github.io/kimi-code/zh/customization/mcp.html)

Kimi Code is Moonshot AI's terminal coding agent. The current distribution is
published as `@moonshot-ai/kimi-code`; use the package's documented Node
requirement (`>=22.19.0`) and the `kimi` binary exposed by the package. A bare
shell `KIMI_API_KEY` is not an automatic provider credential: use managed login,
`[providers.<name>].api_key_env`, or the documented `[providers.<name>.env]`
field.

## Install / layout

| Path | Purpose |
|------|---------|
| `$KIMI_CODE_HOME/` (default `~/.kimi-code/`) | Kimi data root |
| `$KIMI_CODE_HOME/config.toml` (default `~/.kimi-code/config.toml`) | provider/model and runtime configuration |
| `$KIMI_CODE_HOME/mcp.json` (default `~/.kimi-code/mcp.json`) | local MCP server configuration |
| `$KIMI_CODE_HOME/skills/` (default `~/.kimi-code/skills/`) | user skills; keep `agent-fix/SKILL.md` here |

Install or update the package using the current Kimi instructions, then make
sure the directory containing `kimi` is visible to the shell and to GUI launchers.

## Common failures & fixes

1. **`kimi: command not found`** — the package binary directory is not on PATH.
   Add the package's bin directory to PATH and restart the launcher.
2. **`EBADENGINE` / startup syntax error** — install a supported Node release
   (`>=22.19.0`) and reinstall/update the package.
3. **Login or model errors** — inspect `${KIMI_CODE_HOME:-$HOME/.kimi-code}/config.toml`, use Kimi's
   managed login flow, and verify with a real prompt from the explicitly named
   target. `--version` alone does not verify authentication.
4. **Skill not loading** — confirm
   `${KIMI_CODE_HOME:-$HOME/.kimi-code}/skills/<name>/SKILL.md` exists and is readable by the target.
5. **MCP not discovered** — validate `${KIMI_CODE_HOME:-$HOME/.kimi-code}/mcp.json` and test the one
   named server; do not register startup repair hooks.

## Verification

```bash
kimi --version
kimi -p "reply with exactly OK" 2>&1
fix check agent-broken-generic --agent kimi-code
```

The final model command is the authoritative auth/provider check. A successful
presence check is not a substitute for that round trip.
