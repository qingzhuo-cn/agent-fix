# Agent Matrix — every AI coding agent at a glance

- **ID:** `agent-broken-generic`
- **Affects:** every agent in the registry below — this doc is the master table.

The `fix` CLI keeps a **registry** of known AI coding agents in `catalog.json`.
`fix agents` is inventory only. For a named target, run
`fix check agent-broken-generic --agent <id>`; no command automatically diagnoses
every detected Agent.

## Registry

| Agent | Bin | Install / package | Config home | Skills dir | Provider env (OpenAI/Anthropic-compatible) | Docs |
|-------|-----|-------------------|-------------|------------|---------------------------------------------|------|
| Claude Code | `claude` | npm `@anthropic-ai/claude-code` | `~/.claude` | `~/.claude/skills` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | [docs](https://docs.anthropic.com/claude-code) |
| Codex CLI | `codex` | npm `@openai/codex` | `~/.codex` | `~/.codex/skills` | `OPENAI_API_KEY` | [github](https://github.com/openai/codex) |
| OpenCode | `opencode` | npm `opencode-ai` | `~/.config/opencode` | `~/.config/opencode/skills` | `OPENAI_API_KEY` | [opencode.ai](https://opencode.ai) |
| Hermes Agent | `hermes` | pip/venv `hermes-agent` | `~/.local/share/hermes` | `~/.local/share/hermes/skills` | provider config in hermes | [docs](https://hermes-agent.nousresearch.com/docs) |
| Kimi Code | `kimi` | npm `@moonshot-ai/kimi-code` (Node `>=22.19.0`) | `~/.kimi-code` (`$KIMI_CODE_HOME`) | `$KIMI_CODE_HOME/skills` | managed login / config.toml | [docs](https://moonshotai.github.io/kimi-code/zh/guides/getting-started.html) |
| MiniMax Code | `mcode` | npm `@minimax-ai/code` (Node `>=22.19 <23 \|\| >=24 <27`) | `~/.minimax` (`MINIMAX_DATA_DIR`, fallback `MAVIS_DATA_DIR`) | `<data-dir>/skills` | `MCODE_PROVIDER_API_KEY` / managed login | [docs](https://agent.minimax.cn/docs/cli/quick-start) |
| Pi | `pi` | npm `@earendil-works/pi-coding-agent` | `~/.pi` | `~/.pi/agent/skills` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | [pi.dev](https://pi.dev) |
| ZCode | `zcode` | desktop app + `zcode-cli` | `~/.zcode` | `~/.agents/skills` (shared) | OpenAI-compatible (GLM-based) | [zcode.ai](https://zcode.ai) |
| Cursor | `cursor` | desktop app | `~/.cursor` | `~/.cursor/skills` | account login | [cursor.com](https://cursor.com) |
| Gemini CLI | `gemini` | npm `@google/gemini-cli` | `~/.gemini` | — | `GEMINI_API_KEY` | [github](https://github.com/google-gemini/gemini-cli) |
| Aider | `aider` | pip/uv `aider-chat` | `~/.config/aider` | — | `OPENAI_API_KEY` | [aider.chat](https://aider.chat) |
| Qwen Code | `qwen-code` | npm `@qwen-code/qwen-code` | `~/.qwen-code` | — | `DASHSCOPE_API_KEY` | [qwen.ai](https://qwen.ai) |
| Amp | `amp` | native installer | `~/.config/amp` | `~/.config/amp/tools` | account login | [amp.dev](https://amp.dev) |
| Droid | `droid` | native installer | `~/.factory` | `~/.factory/skills` | account login | [getdroid.ai](https://getdroid.ai) |
| DeepSeek Harness | `dsh` | npm `@deepseek-ai/dsh` / command-only | — | — | harness runtime | [DSH](https://github.com/deepseek-ai/DeepSeek-Harness) |

> Skills ecosystem note: the **same SKILL.md format** is shared by Claude Code,
> Codex CLI, OpenCode, Hermes, Kimi Code, Pi, Amp, Droid, ZCode (`~/.agents/skills`)
> and most 2025+ agents. `install/install.sh --agent <id>` and
> `& .\install\install.ps1 --agent <id>` deploy `agent-fix` into one explicitly
> selected target.

## The universal failure pattern

Almost every agent breaks the same three ways:

1. **npm-installed agents** (`claude`, `codex`, `opencode`, `pi`, `gemini`,
   `qwen-code` …): `postinstall`/`install` skipped → native binary missing.
   - Check: `fix check npm-postinstall-skipped --agent <id>`
   - Fix: `fix apply npm-postinstall-skipped --agent <id> --yes` re-runs the
     package lifecycle script inside `$(npm root -g)/<pkg>`.
2. **Native/desktop agents** (`hermes`, `zcode`, `cursor`, `amp`, `droid` …):
   version-file checks may pass but the app can still fail to start (GUI PATH,
   broken install). Kimi Code and MiniMax Code are current npm-distributed CLIs;
   use their documented Node requirements and package lifecycle checks.
   - Check: `fix check agent-broken-generic --agent <id>`
   - Fix: reinstall per the Agent's own installer. Use `gui-path-blind` only when
     the target is listed in that issue; otherwise follow the per-Agent guide.
3. **Any agent**: auth/provider misconfig after switching models.
   - See [agent-auth.md](agent-auth.md) and [provider-config.md](provider-config.md).

## Adding a new agent to the registry

1. Add one line to `catalog.json` → `agents`:
   ```json
   "my-agent": {
     "name": "My Agent", "bin": ["my-agent"],
     "config": "~/.config/my-agent", "skills": "~/.config/my-agent/skills",
     "npm_pkg": "my-agent-npm-package", "provider_env": ["OPENAI_API_KEY"]
   }
   ```
2. `fix agents` will now show the entry when it is installed; repair commands
   still require that Agent's explicit id.
3. Optional: write `fixes/my-agent.md` with agent-specific repair notes and add a
   row to the table above.

## Verification

```bash
fix agents                                             # explicit inventory only
fix check agent-broken-generic --agent <id>             # diagnose one target
fix apply npm-postinstall-skipped --agent <id> --yes    # repair an npm target
```
