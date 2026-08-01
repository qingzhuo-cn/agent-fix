# OpenCode MCP entry uses invalid schema (type "stdio") → ConfigInvalidError

- **ID:** `opencode-mcp-schema`
- **Affects:** `opencode` (any version ≥ v1.18; older versions tolerated `stdio`)
- **Tags:** `opencode`, `mcp`, `config`, `ConfigInvalidError`, `json-schema`

## Symptom

- EVERY opencode command fails at startup — including ones that don't touch MCP,
  like `opencode session list` (history) and `opencode -c` (continue last session):
  ```
  Error: ConfigInvalidError
  ```
- With more detail:
  ```
  Error: Configuration is invalid at C:\Users\<user>\.config\opencode\opencode.json
  ↳ Expected { readonly "type": "local", ... } | { readonly "type": "remote", ... },
    got {"type":"stdio",...} mcp.<name>
  ↳ Missing key mcp.<name>.enabled
  ```

## Root cause

OpenCode validates the **entire** `opencode.json` against its schema before doing
anything — including listing/resuming history. Its `mcp.<name>` entries only accept
two shapes:

```jsonc
// local (stdio subprocess): command MUST be an ARRAY of argv tokens
"<name>": { "type": "local", "command": ["python", "C:/path/to/server.py"], "enabled": true }

// remote (HTTP/SSE): 
"<name>": { "type": "remote", "url": "https://example.com/mcp", "enabled": true }
```

- `"type": "stdio"` is the **Claude Code / Cursor** spelling — OpenCode rejects it.
- `command` as a **string** is the Cursor spelling — OpenCode requires an array.
- `enabled` is **required** in the OpenCode schema.

Any single bad entry invalidates the whole config, so even `session list` dies.

## Check

```bash
opencode session list 2>&1 | head -5        # ConfigInvalidError? -> broken
grep -nE '"type"[[:space:]]*:[[:space:]]*"stdio"' "$HOME/.config/opencode/opencode.json" 2>/dev/null
# any match = the offending entry
```

## Fix

Edit `~/.config/opencode/opencode.json` (Windows: `C:\Users\<user>\.config\opencode\opencode.json`),
`mcp` block. Rewrite the entry to the `local` shape:

```jsonc
"mcp": {
  "agent-fix": {
    "type": "local",
    "command": ["C:\\Users\\<user>\\AppData\\Local\\Programs\\Python\\Python312\\python.exe", "C:/Users/<user>/agent-fix/mcp/server.py"],
    "enabled": true
  }
}
```

Notes:

- Keep other keys the entry already has (`environment`, etc.) — only fix
  `type`, `command` (string → array), and add `enabled`.
- `agent-fix` ships a registrar that writes the correct shape since v1.5.x:
  ```bash
  python scripts/mcp_register.py opencode      # fixes the entry in place (idempotent)
  ```
  If the entry was written by an older mcp_register.py (`type: stdio`), re-run it
  once after upgrading the repo, or fix by hand above.

## Verify

```bash
opencode session list 2>&1 | head -5        # sessions listed, no ConfigInvalidError
opencode run 'Respond with exactly: OK'     # MCP servers actually boot
```

## Prevention

- Never copy Claude Code / Cursor MCP snippets (`type: stdio`, string `command`)
  into `opencode.json`.
- After editing `opencode.json` by hand, run `opencode session list` once — it is
  the cheapest config-schema validation.
- When auto-registering MCP for OpenCode, write `{type: "local", command: [...], enabled: true}`.
