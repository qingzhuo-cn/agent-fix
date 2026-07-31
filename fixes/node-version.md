# Node version too old for the agent

- **ID:** `node-version-too-old`
- **Affects:** `@anthropic-ai/claude-code`, `opencode-ai`, `@openai/codex` (all
  npm-distributed agents), any npm CLI with an `engines` requirement.
- **Tags:** `node`, `nvm`, `engines`

## Symptom

- Installing the agent fails or warns with `EBADENGINE` / `Unsupported engine`.
- The CLI crashes immediately at startup with a `SyntaxError` or a message like
  `requires node >= 20`.
- `npm install -g <agent>` succeeds but the command errors with `Cannot find module`
  or engine-related failures.

## Root cause

Agent CLIs declare a minimum Node version in `package.json` `engines.node`:

| Agent | Minimum Node (approx.) |
|-------|------------------------|
| Claude Code | 18+ (newer builds require 20+) |
| Codex CLI | 18+ (recommend 20+) |
| OpenCode | 20+ |

A system Node older than the requirement (or an odd/outdated install) breaks
install and/or runtime.

## Check

```bash
node -v                                   # current version, e.g. v24.18.0
npm view @anthropic-ai/claude-code engines.node 2>/dev/null   # what claude wants
npm view opencode-ai engines.node 2>/dev/null                 # what opencode wants
npm view @openai/codex engines.node 2>/dev/null               # what codex wants
which node                               # are you even running the Node you think?
```

## Fix

1. **Install a modern Node** (18+/20+) via a version manager (keeps it per-user,
   no admin rights needed):

   - Windows: [nvm-windows](https://github.com/coreybutler/nvm-windows)
     ```powershell
     nvm install lts
     nvm use lts
     ```
   - macOS/Linux: [nvm](https://github.com/nvm-sh/nvm) or [fnm](https://fnm.vercel.app)
     ```bash
     curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
     nvm install --lts && nvm use --lts
     ```

2. **Reinstall the agent** under the new Node (important: npm global packages were
   installed by the old Node and may be stale):

   ```bash
   npm install -g @anthropic-ai/claude-code opencode-ai @openai/codex
   ```

3. If `node -v` is already new but the agent still fails, you may have **multiple
   Node installs** — check `which node` vs the PATH order; remove the stale one or
   reorder PATH.

## Verify

```bash
node -v && claude --version && opencode --version && codex --version
# all print versions, exit 0
```

## Prevention

- Use an LTS Node with a version manager; don't install Node from random binaries.
- After a Node major upgrade, reinstall global npm packages (or use `npm i -g npm`
  first).
