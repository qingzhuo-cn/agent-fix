# DeepSeek Harness (dsh) broken → CLI won't boot

- **ID:** `deepseek-harness-broken`
- **Affects:** `@deepseek-ai/dsh` (the `dsh` CLI — the DeepSeek Harness product
  launcher that boots the web GUI, the headless runner, and profiles).
- **Tags:** `dsh`, `deepseek-harness`, `npm`, `node`, `cli`

## Symptom

- `dsh --version` fails: `not recognized` / `command not found` /
  `Cannot find module '@deepseek-ai/dsh-base'` /
  `node:internal/modules/cjs/loader`.
- `dsh web` (or `dsh --profile web`) fails to boot with `EBADENGINE` or a missing
  plugin bundle (`@deepseek-ai/dsh-web-app`, `@deepseek-ai/dsh-headless`, …).
- After `npm install -g @deepseek-ai/dsh`, the `dsh` command is still not on
  PATH, or the global `node_modules/@deepseek-ai/dsh` tree is incomplete.

## Root cause

`dsh` is a thin launcher whose `bin` is `lib/bin.js`. The real runtime is the
tree of `@deepseek-ai/dsh-*` plugin bundles installed as its npm dependencies.
It breaks when:

1. **Node is too old** — commander ^15 and the dsh bundles require Node >= 20
   (22+ recommended). Older Node throws `EBADENGINE` or a startup `SyntaxError`.
2. **The global install is incomplete** — `npm install -g` was interrupted, or a
   dependency bundle is missing, so `lib/bin.js` cannot `require` its plugins.
3. **The shim is missing from PATH** — npm's global bin dir was removed from
   PATH, or the install ran under a different prefix (`npm root -g` differs
   between nvm / system / `%APPDATA%\npm`).

## Check

```bash
dsh --version; echo "exit=$?"          # prints e.g. 0.1.0-rc.6, exit 0
node -v                                # must be v20+ (22+ recommended)
npm root -g                            # e.g. %APPDATA%\npm\node_modules
node -e "const{execSync}=require('child_process');const root=execSync('npm root -g').toString().trim();require('fs').accessSync(root+'/@deepseek-ai/dsh/package.json');console.log('FOUND '+root+'/@deepseek-ai/dsh/package.json')"
```

Or run the agent-fix engine:

```bash
fix check deepseek-harness-broken        # diagnose
fix apply deepseek-harness-broken        # prints the manual reinstall + verifies
```

## Fix

Reinstall the package globally (re-runs the install and rebuilds the dependency
bundles):

```bash
npm install -g @deepseek-ai/dsh
```

If the package directory exists but bundles are incomplete, rebuild in place:

```bash
npm rebuild --prefix "$(npm root -g)/@deepseek-ai/dsh"
```

If Node is too old, upgrade first, then reinstall:

```bash
# Windows (nvm-windows / fnm):
nvm install lts && nvm use lts
# POSIX (nvm):
nvm install --lts
npm install -g @deepseek-ai/dsh
```

## Verify

```bash
dsh --version          # prints a version, exit 0
dsh --help             # prints the launcher usage (boot smoke, not just --version)
dsh web --help         # the web profile's own flags — proves the web bundle loads
```

> `--version` proves the binary exists; `--help` proves `lib/bin.js` loads its
> arg grammar; `dsh web --help` proves the web app bundle is resolvable.

## Prevention

- Keep `ignore-scripts=false` (the npm default) so npm runs the install hooks of
  any package that has them.
- After upgrading, verify with `dsh --help` (not just `--version`).
- `npm root -g` differs between nvm and the system install — reinstall under the
  same prefix you launch from, and make sure that prefix's bin dir is on PATH.
