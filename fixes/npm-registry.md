# npm registry slow / unreachable (mirror)

- **ID:** `npm-registry-mirror`
- **Affects:** all npm-installed agents (`claude`, `codex`, `opencode`, hermes deps, …)
  in networks where registry.npmjs.org is slow or blocked.
- **Tags:** `npm`, `registry`, `mirror`, `china`

## Symptom

- `npm install -g <agent>` hangs, times out, or fails with `ETIMEDOUT` /
  `ECONNRESET` / `ERR! code EAI_AGAIN`.
- Upgrading an agent (which then misses its postinstall — see
  [npm-postinstall.md](npm-postinstall.md)) takes forever or fails mid-way.

## Root cause

`registry.npmjs.org` is unreachable or throttled from your network. The fix is to
point npm (and often the agent's own package managers) at a reachable mirror.

## Check

```bash
npm config get registry      # e.g. https://registry.npmjs.org/
npm ping 2>&1 | head -3      # fails/slow = network path problem
```

## Fix

```bash
# China-friendly mirror (npmmirror, official Taobao successor)
npm config set registry https://registry.npmmirror.com

# or the plain Chinese mirror:
# npm config set registry https://registry.npm.taobao.org   # legacy, avoid

# verify
npm config get registry
npm ping
```

Per-project override (recommended, doesn't touch global config):

```bash
# in the project dir, or use --registry for a one-off install
npm install -g opencode-ai --registry=https://registry.npmmirror.com
```

## Verify

```bash
npm ping                                   # pong, fast
npm install -g <agent>                     # completes; then run the agent's --version
```

## Prevention

- Set the mirror once in `~/.npmrc` (`registry=...`) rather than per-command.
- After any install/upgrade that may have skipped scripts, run
  `fix check npm-postinstall-skipped` to confirm the binary is present.
- GUI package managers (and some agent auto-updaters) may bypass your `~/.npmrc` —
  if an upgrade silently skips postinstall again, re-check that doc.

## Related

- [npm-postinstall.md](npm-postinstall.md) — upgrades that fail mid-way often leave
  the package half-installed; re-run the postinstall after fixing the registry.
