# Point any agent at the DeepSeek API

- **ID:** `deepseek-provider`
- **Affects:** `claude-code`, `codex`, `opencode`, `hermes`, and any OpenAI- or
  Anthropic-compatible client. Get a key at <https://platform.deepseek.com/>.
- **Tags:** `deepseek`, `provider`, `api-key`, `base-url`

## Symptom

- You want to run your favorite agent on DeepSeek models (e.g. `deepseek-chat`,
  `deepseek-reasoner`, or DeepSeek's hosted `deepseek-v4` line) instead of the
  vendor's default model.
- After switching provider, the agent errors with `401`, `connection refused`, or
  `model not found`.

## Root cause

Each agent talks to a *specific* base URL by default. To use DeepSeek you must point
the agent at DeepSeek's endpoint **and** set a DeepSeek API key. DeepSeek exposes two
compatible endpoints:

| Endpoint | Compatible with | Base URL |
|----------|----------------|----------|
| OpenAI-compatible | Codex, OpenCode, Hermes, most tools | `https://api.deepseek.com` |
| Anthropic-compatible | Claude Code | `https://api.deepseek.com/anthropic` |

## Fix (per agent)

### Claude Code

```bash
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-<your-deepseek-key>"
export ANTHROPIC_MODEL="deepseek-chat"        # or deepseek-reasoner
claude
```

Persist in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "sk-<your-deepseek-key>",
    "ANTHROPIC_MODEL": "deepseek-chat"
  }
}
```

### Codex

```bash
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_API_KEY="sk-<your-deepseek-key>"
codex
```

or in `~/.codex/config.toml`:

```toml
model_provider = "deepseek"
[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com"
env_key = "DEEPSEEK_API_KEY"
wire_api = "chat"
```

### OpenCode

```bash
opencode auth login   # choose "Other: custom", then:
# base URL:  https://api.deepseek.com
# API key:   sk-<your-deepseek-key>
# model:     deepseek-chat
```

or in `~/.config/opencode/opencode.json`:

```json
{
  "provider": {
    "deepseek": {
      "npm": "@ai-sdk/deepseek",
      "options": { "apiKey": "{env:DEEPSEEK_API_KEY}" },
      "models": { "deepseek-chat": {} }
    }
  },
  "model": "deepseek/deepseek-chat"
}
```

### Hermes

```bash
hermes config set provider deepseek   # or the provider name you configured
hermes config set model deepseek-chat
# keys go in the provider config / .env, e.g. DEEPSEEK_API_KEY=sk-...
```

## Verify

```bash
# real model round-trip (not just --version)
claude "say hi" --print 2>&1 | head -5
codex exec "say hi" 2>&1 | head -5
opencode run "say hi" 2>&1 | head -5
```

## Prevention

- Keep the API key in config files/env, never paste it in chat or commit it.
- `ANTHROPIC_AUTH_TOKEN` (Claude Code w/ DeepSeek) ≠ `ANTHROPIC_API_KEY` — if your
  switcher sets the wrong var, the agent silently falls back to the vendor auth.
- After switching providers, always verify with a **model round-trip**, because
  `--version` checks the binary, not the auth.

## Related

- [agent-auth.md](agent-auth.md) — 401 / "not logged in" diagnosis.
- [gui-path.md](gui-path.md) — GUI switchers (CC-Switch) may also need PATH entries.
