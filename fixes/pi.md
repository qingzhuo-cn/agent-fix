# Pi (pi-coding-agent)

- **Agent id:** `pi` · bin: `pi` · config: `~/.pi`
- **Home:** <https://pi.dev> · npm: `@earendil-works/pi-coding-agent`

Pi is a minimal, extensible terminal coding harness (pi-mono project). It is
**npm-distributed**, so it hits the classic npm-postinstall failure mode, and it
uses the **shared SKILL.md ecosystem** (compatible with Claude Code, Codex CLI,
Amp, Droid).

## Install / layout

| Path | Purpose |
|------|---------|
| `~/.pi/agent/skills/` | user skills (clone skill repos here, e.g. `~/.pi/agent/skills/agent-fix`) |
| `.pi/skills/` | project-level skills |
| `~/.pi/` | config & state |
| npm `@earendil-works/pi-coding-agent` | the CLI (`npx pi` or global install) |

Install: `npm install -g @earendil-works/pi-coding-agent` (or `npx
@earendil-works/pi-coding-agent`).

## Common failures & fixes

1. **`pi: command not found` / postinstall skipped** — the classic npm issue:
   - Fix: `fix apply npm-postinstall-skipped --agent pi --yes` (re-runs the package
     lifecycle script in `$(npm root -g)/@earendil-works/pi-coding-agent`), or
     manually:
     ```bash
     cd "$(npm root -g)/@earendil-works/pi-coding-agent" && node -e "const s=require('./package.json').scripts||{};const f=s.postinstall||s.install;f&&require('child_process').execSync(f,{stdio:'inherit'})"
     ```
2. **`pi` starts but says "no provider configured"** — Pi is model-agnostic
   (`pi-ai` supports OpenAI, Anthropic, Google, …).
   - Fix: set the provider env vars (see below) or configure a provider in
     `~/.pi/` per the docs at <https://pi.dev/docs/latest>.
3. **Skill not found** — confirm the skill folder layout:
   `~/.pi/agent/skills/<name>/SKILL.md`.

## Using DeepSeek with Pi

Pi's `pi-ai` layer accepts standard OpenAI/Anthropic-compatible env vars, so
DeepSeek works through either endpoint:

```bash
# OpenAI-compatible
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_API_KEY="sk-<your-deepseek-key>"

# or Anthropic-compatible (Claude-shaped providers)
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-<your-deepseek-key>"
```

## Verification

```bash
pi --version 2>&1                            # binary OK; do not pipe away exit status
fix check npm-postinstall-skipped --agent pi      # explicit target only
```
