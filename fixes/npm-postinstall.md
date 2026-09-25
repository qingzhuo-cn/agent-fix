# npm postinstall skipped → native binary missing

- **ID:** `npm-postinstall-skipped`
- **Affects:** npm-distributed agents including Claude Code, OpenCode, Kimi Code,
  MiniMax Code, and other CLIs whose package uses a lifecycle script.
- **Tags:** `claude-code`, `opencode`, `codex`, `kimi-code`, `minimax-code`, `npm`

## Symptom

The npm package is installed (the shim exists on PATH), but the CLI fails:

```
opencode --version
# → Error: postinstall script was not run
#   Run `cd "$(npm root -g)/opencode-ai" && node postinstall.mjs` to fix.
```

```
claude --version
# → native binary not installed
```

Or the command exits non-zero without any obvious message. A GUI tool (e.g. CC-Switch)
may then report the agent as "installed · cannot run": the shim is found (installed),
but `--version` fails (cannot run).

## Root cause

`npm` skipped the package's lifecycle scripts during install/upgrade. That happens when:

1. `npm config get ignore-scripts` returns `true` (set in `~/.npmrc`, user config, or
   `NPM_CONFIG_IGNORE_SCRIPTS` env var), or
2. the install/upgrade was run with an explicit `--ignore-scripts` flag, or
3. a wrapper/parent process (CI, GUI, package manager UI) passed `--ignore-scripts`.

The package's JS code is on disk, but the native binary it downloads/links in
`postinstall` never ran, so the CLI cannot start.

## Check

```bash
npm config get ignore-scripts        # must be "false"; "true" means scripts are skipped
env | grep -i npm_config_ignore      # must be empty
opencode --version; echo "exit=$?"   # non-zero exit + "postinstall" hint = broken
claude --version;  echo "exit=$?"    # non-zero exit + "native binary" hint = broken
```

## Fix

Re-run the skipped lifecycle script manually **inside the package directory**:

```bash
# OpenCode
cd "$(npm root -g)/opencode-ai" && node postinstall.mjs

# Claude Code
cd "$(npm root -g)/@anthropic-ai/claude-code" && node install.cjs

# Generic (any npm package that skipped its scripts):
PKG=your-package-name
cd "$(npm root -g)/$PKG" && node -e "const s=require('./package.json').scripts||{}; const f=s.postinstall||s.install; if(!f){console.error('no install/postinstall script');process.exit(1)}; console.log('running:',f); require('child_process').execSync(f,{stdio:'inherit'})"
```

> Use `npm root -g` (not a hardcoded path): on Windows the global root is
> `%APPDATA%\npm\node_modules`, on POSIX `/usr/local/lib/node_modules` or the nvm
> prefix — the command above works everywhere.

## Verify

```bash
opencode --version   # prints e.g. v1.18.10, exit 0
claude --version     # prints e.g. 2.1.220 (Claude Code), exit 0
```

## Prevention

- Keep `ignore-scripts=false` (the npm default). Only flip it to `true` if you
  deliberately audit every package; then expect to re-run postinstalls manually.
- Never pass `--ignore-scripts` when installing/upgrading agent CLIs.
- If you must keep scripts disabled globally, run
  `fix check npm-postinstall-skipped --agent <id>` after every upgrade and apply
  only that target on failure. The CLI exits non-zero when broken, so an external
  CI or wrapper may automate the explicit check; agent-fix does not install a
  startup watchdog.

## History (real-world case)

This exact failure recurred across multiple sessions:

| Date | Version | Root cause | Fix |
|------|---------|-----------|-----|
| 7/27 | claude-code v2.1.220, opencode v1.18.6 | postinstall skipped | `node install.cjs` / `node postinstall.mjs` |
| 7/27 | opencode v1.18.7 | same | `node postinstall.mjs` |
| 7/28 | opencode v1.18.8 | same | `node postinstall.mjs` |
| 7/29 | opencode v1.18.9 | same | `node postinstall.mjs` |
| 7/31 | opencode v1.18.10 | `--ignore-scripts` used explicitly | `node postinstall.mjs` |

Lesson: this is a *recurring* problem for npm-distributed agents on machines where
scripts are ever disabled — the fix must be re-appliable and automatable.
