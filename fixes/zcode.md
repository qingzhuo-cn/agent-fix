# ZCode

- **Agent id:** `zcode` · bin: `zcode` · config: `~/.zcode`
- **Home:** <https://zcode.ai> · GLM-5.x ecosystem (Zhipu-backed models)

ZCode is a desktop + CLI AI coding agent (GLM models). Its skills live in the
**shared `~/.agents/skills` directory** (also used by several other agents), and it
has its own plugin system under `~/.zcode/cli/plugins`.

## Install / layout

| Path | Purpose |
|------|---------|
| `~/.zcode/` | config home (CLI under `~/.zcode/cli`) |
| `~/.agents/skills/` | user skills (shared convention: `~/.agents/skills/<name>/SKILL.md`) |
| `~/.zcode/cli/plugins/cache/zcode-plugins-official` | official plugins |
| `zcode-cli` | terminal client (community) for the desktop runtime |

Install: download the ZCode desktop app / CLI from <https://zcode.ai> (or the
community `zcode-cli` terminal client).

## Common failures & fixes

1. **`zcode: command not found`** — CLI not on PATH.
   - Fix: add the ZCode CLI dir to PATH (see [gui-path.md](gui-path.md)).
2. **Desktop shows "installed · cannot run"** — GUI PATH issue; the app can't see
   your npm/agent dirs.
   - Fix: `fix apply gui-path-blind --yes`, then restart ZCode Desktop.
3. **Skill/plugin not loading** — skills must be at
   `~/.agents/skills/<name>/SKILL.md`; plugins follow the `.zcode-plugin` layout
   (`plugin.json`, `commands/*.md`, `.mcp.json`).
4. **Model errors after switching provider** — ZCode is GLM-based and exposes an
   OpenAI-compatible endpoint for custom providers; check the app's provider/model
   settings for a base-URL override (e.g. DeepSeek: `https://api.deepseek.com`).

## Using DeepSeek with ZCode

ZCode accepts OpenAI-compatible custom providers (set in the app's provider
settings):

```text
Base URL: https://api.deepseek.com
API key:  sk-<your-deepseek-key>
Model:    deepseek-chat   (or deepseek-reasoner)
```

or via env if supported by your zcode-cli build:

```bash
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_API_KEY="sk-<your-deepseek-key>"
```

## Verification

```bash
zcode --version 2>&1 | head -2                    # binary OK
fix check agent-broken-generic                    # included in doctor
```
