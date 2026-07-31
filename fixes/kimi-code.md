# Kimi Code

- **Agent id:** `kimi-code` · bin: `kimi` · config: `~/.kimi-code`
- **Home:** <https://kimi.com/code> · Moonshot AI (Kimi)

Kimi Code is Moonshot AI's terminal coding agent. It ships as **native binaries**
(`~/.kimi-code/bin/kimi` + helper tools like `fd`), not an npm package, so the
npm-postinstall fix does **not** apply — but the generic binary check in
`fix doctor` covers it.

## Install / layout

| Path | Purpose |
|------|---------|
| `~/.kimi-code/bin/kimi` | the CLI (add to PATH: `~/.kimi-code/bin`) |
| `~/.kimi-code/config.toml` | provider/model config (login populates managed Kimi entries) |
| `~/.kimi-code/sessions/` | session storage |
| `~/.kimi-code/skills/` | user skills (auto-discovered; `--skills-dir` overrides) |

## Common failures & fixes

1. **`kimi: command not found`** — `~/.kimi-code/bin` not on PATH.
   - Fix: add `~/.kimi-code/bin` to PATH (see [gui-path.md](gui-path.md) for the
     GUI-visible registry PATH on Windows).
2. **`--version` works but the agent can't start / login loop** —
   - Fix: check `~/.kimi-code/config.toml`; re-run `kimi` login; delete a corrupt
     `session_index.jsonl` if session loading hangs (sessions are re-indexed).
3. **Missing API key / model errors** — Kimi uses `KIMI_API_KEY` and managed
   providers in `config.toml`.
   - Fix: set `KIMI_API_KEY`, or add a custom provider block (OpenAI-compatible).
4. **Skill not loading** — Kimi auto-discovers user skills; confirm the folder is
   at `~/.kimi-code/skills/<name>/SKILL.md` or pass `--skills-dir`.

## Using DeepSeek with Kimi Code

Kimi supports custom OpenAI-compatible providers in `~/.kimi-code/config.toml`:

```toml
[provider.deepseek]
base_url = "https://api.deepseek.com"
api_key = "sk-<your-deepseek-key>"

[model.deepseek-chat]
provider = "deepseek"
```

```bash
export KIMI_API_KEY="sk-<your-deepseek-key>"   # or set in config.toml
kimi -m deepseek-chat "hello"
```

## Verification

```bash
kimi --version                                   # binary OK
kimi -p "say hi" 2>&1 | head -5                  # real model round-trip
fix check agent-broken-generic                   # included in doctor
```
