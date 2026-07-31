# Agent auth broken (not logged in / expired OAuth / missing API key)

- **ID:** `agent-auth-broken`
- **Affects:** `claude-code`, `codex` (and by extension any agent configured with a
  provider key: `opencode`, `hermes`, …).
- **Tags:** `auth`, `login`, `oauth`, `api-key`

## Symptom

- The CLI starts but refuses to work: `Not logged in`, `Please log in`, `401
  Unauthorized`, `Authentication required`, `MFA required`, or a popup asking you to
  authenticate every launch.
- `claude --version` works (binary fine) but any real prompt fails.
- Switching providers (e.g. with CC-Switch) leaves the agent with no valid credential.

## Root cause

1. OAuth session expired (Claude Code / Codex browser login).
2. Credential file missing/corrupt (`~/.claude/.credentials.json`,
   `~/.codex/auth.json`).
3. API key not set / wrong key / key for the wrong provider (DeepSeek vs Anthropic vs
   OpenAI — see [deepseek-provider.md](deepseek-provider.md)).
4. A provider switcher wrote an env-var config that isn't actually being loaded.

## Check

```bash
# Claude Code
claude doctor 2>&1 | head -40          # built-in diagnostic: auth, versions, PATH
ls -la ~/.claude/.credentials.json 2>/dev/null || echo "no credentials file"

# Codex
codex login status 2>&1 | head -5 || true
ls -la ~/.codex/auth.json 2>/dev/null || echo "no codex auth file"

# Env-var based auth (what most provider switchers set)
env | grep -iE "ANTHROPIC|OPENAI|DEEPSEEK|API_KEY" | sed 's/=.*/=<set>/'
```

## Fix

```bash
# Claude Code — interactive login
claude /login          # browser OAuth, or:
claude /logout && claude /login

# Codex — interactive login
codex login            # browser OAuth (ChatGPT account), or:

# API-key style (any agent):
export ANTHROPIC_AUTH_TOKEN="sk-..."    # Claude Code with a proxy/DeepSeek key
export OPENAI_API_KEY="sk-..."          # Codex/OpenCode with an OpenAI-compatible key
```

For provider switching (DeepSeek / proxies), the fix is usually the **env vars**, not
the agent's own login — see [deepseek-provider.md](deepseek-provider.md).

## Verify

```bash
claude "ping" --print 2>&1 | head -5    # a real model response, not an auth error
codex exec "ping" 2>&1 | head -5
```

## Prevention

- Know which auth mechanism your agent uses (OAuth vs API key) and which env vars it
  reads — don't mix them.
- Store keys in the agent's config file (`.claude/settings.json`,
  `~/.codex/config.toml`) or a shell profile, not just one terminal session.
- After using a provider switcher, verify with a one-line prompt, not `--version`.
