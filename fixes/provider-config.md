# Agent provider not configured — configure ANY provider

- **ID:** `provider-config`
- **Affects:** every agent — this is the auth/config layer above the network.
- **Tags:** `provider`, `api-key`, `base-url`, `model`, `deepseek`, `openai`,
  `anthropic`, `google`, `ollama`, `openrouter`

## Symptom

- The agent binary runs (`--version` works) but **every prompt fails**: `401
  Unauthorized`, `Authentication required`, `model not found`, `no provider
  configured`, or an endless login popup.
- You switched providers (with CC-Switch or by hand) and the agent kept the old
  credential.
- You just installed a new agent and it has no key at all.

## Root cause

The agent has no valid **provider configuration** — a reachable base URL + a valid
API key + a model name. Every agent reads its provider config from env vars, a
config file, or an interactive login. The fix is provider-agnostic: set the right
three values for the provider you want, in the place that agent reads.

## Check

```bash
fix check provider-config --agent <id>   # key env or config home for one target
# MCP: the `provider` tool generates snippets for the same explicit target
```

## The provider table

| Provider | Base URL (OpenAI-compatible unless noted) | Default model | Key env var |
|----------|------------------------------------------|---------------|-------------|
| DeepSeek | `https://api.deepseek.com` (Claude Code: `https://api.deepseek.com/anthropic`) | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` | `OPENAI_API_KEY` |
| Anthropic | `https://api.anthropic.com` | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-pro` | `GEMINI_API_KEY` |
| Moonshot/Kimi Code | `https://api.moonshot.ai/v1` (managed Kimi service may use its configured endpoint) | `kimi-code/k3` or configured model | managed login / explicit `api_key_env` |
| MiniMax | provider-specific | provider-specific | `MCODE_PROVIDER_API_KEY` or managed login |
| Zhipu (GLM) | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.6` | `ZHIPU_API_KEY` |
| Alibaba (Qwen) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` | `DASHSCOPE_API_KEY` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o` | `OPENROUTER_API_KEY` |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5-coder:latest` | (none; local) |
| Custom proxy (one-api/new-api) | whatever your gateway gives you | your model | `OPENAI_API_KEY` etc. |

## Fix (per agent — any provider)

The pattern is identical for every provider: **base URL + key + model**, placed
where the agent reads it.

| Agent | Mechanism | Where the values go |
|-------|-----------|---------------------|
| Claude Code | env / settings | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` in `~/.claude/settings.json` or shell env |
| Codex CLI | env / config.toml | `OPENAI_BASE_URL`, `OPENAI_API_KEY` (or `model_provider` block in `~/.codex/config.toml`) |
| OpenCode | env / opencode.json | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, or `provider.<name>` block |
| Hermes | provider config | `hermes config set provider <name>`, `hermes config set model <m>`; key in provider config/.env |
| Kimi Code | `config.toml` | managed login or `[providers.<name>]` / `[models.<alias>]` in `$KIMI_CODE_HOME/config.toml` |
| MiniMax Code | `config.yaml` / provider commands | `mcode login`, `mcode provider set-minimax-key`, or documented custom provider |
| Pi | env | `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` + base URL (pi-ai supports OpenAI/Anthropic/Google) |
| ZCode | app settings | OpenAI-compatible custom provider: base URL + key + model (GLM-based) |
| Gemini CLI | env | `GEMINI_API_KEY` |
| Aider | env | `OPENAI_API_KEY` (+ `--openai-api-base`) |
| Qwen Code | env | `DASHSCOPE_API_KEY` |

Kimi Code uses `[providers.<name>]` and `[models.<alias>]`, not the older
`[provider.*]`/`[model.*]` spelling. The provider table requires `type` and the
model table requires a provider reference; set `api_key_env` or use managed login
instead of pasting a key into the config or a transcript.


```bash
# any OpenAI-compatible provider (DeepSeek, Moonshot, Zhipu, Qwen, Ollama, OpenRouter, custom):
export OPENAI_BASE_URL="https://<provider-base-url>"
export OPENAI_API_KEY="sk-..."
# Claude Code with an Anthropic-compatible provider:
export ANTHROPIC_BASE_URL="https://<provider-anthropic-base-url>"
export ANTHROPIC_AUTH_TOKEN="sk-..."
```

MCP users: the `provider` tool generates snippets for one explicit target.
With `apply=true`, it writes Claude Code settings; other targets receive
manual/config-file steps and are not modified.
DeepSeek-specific details: [deepseek-provider.md](deepseek-provider.md).

## Verify

Always verify with a **real model round-trip**, not `--version`:

```bash
fix check provider-config --agent claude-code   # presence probe; inconclusive until round-trip
claude "ping" --print 2>&1                    # replace with the target's real call
```

## Prevention

- Keep one canonical key per provider in your shell profile / agent config; never
  paste keys into chat or commit them (run the `audit` MCP tool before pushing).
- After any provider switch, verify with one real prompt — `--version` cannot see
  auth problems.
- If prompts fail with 401, check [agent-auth.md](agent-auth.md); if they fail with
  timeouts, check [net-connectivity.md](net-connectivity.md) first.
