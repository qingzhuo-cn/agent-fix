# Agent Matrix — every AI coding agent at a glance

- **ID:** `agent-broken-generic`
- **Affects:** every agent in the registry below — this doc is the master table.

The `fix` CLI keeps a **registry** of known AI coding agents in `catalog.json`.
`fix agents` shows which are installed on your machine; `fix doctor` then runs a
binary check for **every detected agent** (not just the big four), so a broken
Pi, Kimi Code, or ZCode install is caught automatically.

## Registry

| Agent | Bin | Install / package | Config home | Skills dir | Provider env (OpenAI/Anthropic-compatible) | Docs |
|-------|-----|-------------------|-------------|------------|---------------------------------------------|------|
| Claude Code | `claude` | npm `@anthropic-ai/claude-code` | `~/.claude` | `~/.claude/skills` | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` | [docs](https://docs.anthropic.com/claude-code) |
| Codex CLI | `codex` | npm `@openai/codex` | `~/.codex` | `~/.codex/skills` | `OPENAI_BASE_URL`, `OPENAI_API_KEY` | [github](https://github.com/openai/codex) |
| OpenCode | `opencode` | npm `opencode-ai` | `~/.config/opencode` | `~/.config/opencode/skills` | `OPENAI_BASE_URL`, `OPENAI_API_KEY` | [opencode.ai](https://opencode.ai) |
| Hermes Agent | `hermes` | pip/venv `hermes-agent` | `~/.local/share/hermes` | `~/.local/share/hermes/skills` | provider config in hermes | [docs](https://hermes-agent.nousresearch.com/docs) |
| Kimi Code | `kimi` | native (`~/.kimi-code/bin`) | `~/.kimi-code` | `~/.kimi-code/skills` | `KIMI_API_KEY` (+ `--skills-dir`, agent profiles) | [kimi.com/code](https://kimi.com/code) |
| Pi | `pi` | npm `@earendil-works/pi-coding-agent` | `~/.pi` | `~/.pi/agent/skills` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | [pi.dev](https://pi.dev) |
| ZCode | `zcode` | desktop app + `zcode-cli` | `~/.zcode` | `~/.agents/skills` (shared) | OpenAI-compatible (GLM-based) | [zcode.ai](https://zcode.ai) |
| Cursor | `cursor` | desktop app | `~/.cursor` | `~/.cursor/skills` | account login | [cursor.com](https://cursor.com) |
| Gemini CLI | `gemini` | npm `@google/gemini-cli` | `~/.gemini` | — | `GEMINI_API_KEY` | [github](https://github.com/google-gemini/gemini-cli) |
| Aider | `aider` | pip/uv `aider-chat` | `~/.config/aider` | — | `OPENAI_API_KEY` | [aider.chat](https://aider.chat) |
| Qwen Code | `qwen-code` | npm `@qwen-code/qwen-code` | `~/.qwen-code` | — | `DASHSCOPE_API_KEY` | [qwen.ai](https://qwen.ai) |
| Amp | `amp` | native installer | `~/.config/amp` | `~/.config/amp/tools` | account login | [amp.dev](https://amp.dev) |
| Droid | `droid` | native installer | `~/.factory` | `~/.factory/skills` | account login | [getdroid.ai](https://getdroid.ai) |

> Skills ecosystem note: the **same SKILL.md format** is shared by Claude Code,
> Codex CLI, OpenCode, Hermes, Kimi Code, Pi, Amp, Droid, ZCode (`~/.agents/skills`)
> and most 2025+ agents. `install/install.sh` and `install/install.ps1` deploy
> `agent-fix` into every detected agent's skills dir — one clone, all agents.

## The universal failure pattern

Almost every agent breaks the same three ways:

1. **npm-installed agents** (`claude`, `codex`, `opencode`, `pi`, `gemini`,
   `qwen-code` …): `postinstall`/`install` skipped → native binary missing.
   - Check: `fix check agent-broken-generic`
   - Fix: `fix apply agent-broken-generic --yes` re-runs the package's lifecycle
     script inside `$(npm root -g)/<pkg>` automatically.
2. **Native/desktop agents** (`kimi`, `zcode`, `cursor`, `amp`, `droid` …):
   version file checks pass but the app can't start (GUI PATH, broken install).
   - Check: `fix check agent-broken-generic` (runs `--version`)
   - Fix: reinstall per the agent's own installer; for PATH/GUI issues use
     `gui-path-blind` (see [gui-path.md](gui-path.md)).
3. **Any agent**: auth/provider misconfig after switching models.
   - See [agent-auth.md](agent-auth.md) and [deepseek-provider.md](deepseek-provider.md).

## Adding a new agent to the registry

1. Add one line to `catalog.json` → `agents`:
   ```json
   "my-agent": {
     "name": "My Agent", "bin": ["my-agent"],
     "config": "~/.config/my-agent", "skills": "~/.config/my-agent/skills",
     "npm_pkg": "my-agent-npm-package", "provider_env": ["OPENAI_API_KEY"]
   }
   ```
2. `fix agents` will now detect it; `fix doctor` will check it automatically.
3. Optional: write `fixes/my-agent.md` with agent-specific repair notes and add a
   row to the table above.

## Verification

```bash
fix agents          # shows detected vs known agents
fix doctor          # includes per-agent binary checks (exit 0 = all healthy)
fix apply agent-broken-generic --yes   # repairs any broken npm-installed agent
```
