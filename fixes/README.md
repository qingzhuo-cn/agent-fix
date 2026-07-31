# Fix Catalog

This directory is the **human/agent-readable knowledge base** behind the `agent-fix` skill.
Each file documents one problem class: symptoms, root cause, diagnosis commands, and the exact fix.

Every doc is plain Markdown with a stable `id` so any agent (Claude Code, Codex, OpenCode,
Hermes, Cursor, …) can read it directly. The same content is mirrored in
[`../catalog.json`](../catalog.json) in machine-readable form, which drives the
[`fix` CLI](../scripts/fix.py).

| ID | Problem | Affected agents | Doc |
|----|---------|-----------------|-----|
| `npm-postinstall-skipped` | npm `ignore-scripts` / `--ignore-scripts` skips postinstall → native binary missing | claude-code, opencode, codex, any npm CLI | [npm-postinstall.md](npm-postinstall.md) |
| `gui-path-blind` | GUI apps (CC-Switch, VS Code, launchers) can't find agent binaries that only exist in shell PATH | all agents, CC-Switch | [gui-path.md](gui-path.md) |
| `node-version-too-old` | Node too old for the agent's engine requirement → CLI crashes at startup | claude-code, codex, opencode | [node-version.md](node-version.md) |
| `npm-registry-mirror` | npm install/upgrade slow or fails (network / mirror issues) | all npm-installed agents | [npm-registry.md](npm-registry.md) |
| `agent-auth-broken` | "Not logged in" / expired OAuth / missing API key | claude-code, codex | [agent-auth.md](agent-auth.md) |
| `deepseek-provider` | Point any agent at the DeepSeek API (Anthropic- or OpenAI-compatible endpoints) | claude-code, codex, opencode, hermes | [deepseek-provider.md](deepseek-provider.md) |

## How to use

- **Humans:** open the doc for your symptom and follow it.
- **Agents:** when a user reports a broken agent, read the matching doc, run the
  `Check` commands, apply the `Fix`, then run `Verify`.
- **Programs:** call `scripts/fix.py check|apply` (or import it) — it reads the same
  catalog. See [`../scripts/README.md`](../scripts/README.md).
