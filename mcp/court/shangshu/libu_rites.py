#!/usr/bin/env python3
"""礼部 · Ministry of Rites — 治交: the protocol with external providers.

Like court protocol with foreign states (邦交), this ministry establishes
proper relations between the agents and model providers: it knows each
provider's etiquette (base URL, model, env vars) and drafts the correct
greetings (config snippets) — or writes them into Claude's settings.

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .common import _detect_agents, _mask, _sanitize_id, _shellq, _tomlq

MINISTRY = {
    "id": "libu-rites",
    "name": "礼部",
    "en": "Ministry of Rites",
    "motto": "治交 · the protocol with external providers",
}

_KNOWN_PROVIDERS = {
    "deepseek": {"base": "https://api.deepseek.com", "model": "deepseek-chat", "anthropic_path": "/anthropic"},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o", "anthropic_path": None},
    "anthropic": {"base": "https://api.anthropic.com", "model": "claude-sonnet-4-5", "anthropic_path": None},
    "google": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-pro", "anthropic_path": None},
    "moonshot": {"base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "anthropic_path": None},
    "zhipu": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6", "anthropic_path": None},
    "qwen": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "anthropic_path": None},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o", "anthropic_path": None},
    "ollama": {"base": "http://localhost:11434/v1", "model": "qwen2.5-coder:latest", "anthropic_path": None},
}


def provider_setup(
    provider: str = "deepseek",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    apply: bool = False,
    show_key: bool = False,
) -> str:
    """Generate per-agent config snippets for ANY provider.

    provider: deepseek|openai|anthropic|google|moonshot|zhipu|qwen|openrouter|
              ollama|custom. api_key required for cloud providers (empty for
              Ollama). base_url/model default from the provider table.
    apply=True also writes Claude's ~/.claude/settings.json.
    """
    info = _KNOWN_PROVIDERS.get((provider or "").lower(), {"base": "", "model": ""})
    base = base_url or info.get("base", "")
    model = model or info.get("model", "deepseek-chat")
    anthropic_base = base
    if info.get("anthropic_path"):
        anthropic_base = base.rstrip("/") + info["anthropic_path"]
    if not base:
        return "error: unknown provider — pass base_url explicitly (see fixes/provider-config.md)"
    if not api_key and provider.lower() != "ollama":
        return "error: api_key is required (leave empty only for ollama)"

    shown = api_key if show_key else (_mask(api_key) if api_key else "(local)")
    prov_id = _sanitize_id(provider) or "custom"
    model_id = _sanitize_id(model) or "custom"

    lines = [
        f"PROVIDER SETUP: {provider}  (base={base}, model={model}, key={_mask(api_key) if api_key else '(local)'})",
        "",
    ]
    for agent in _detect_agents():
        aid = agent.get("id")
        name = agent.get("name", aid)
        lines.append(f"== {name}")
        if aid == "claude-code":
            lines.append(f"  export ANTHROPIC_BASE_URL={_shellq(anthropic_base)}")
            lines.append(f"  export ANTHROPIC_AUTH_TOKEN={_shellq(shown)}")
            lines.append(f"  export ANTHROPIC_MODEL={_shellq(model)}")
            lines.append("  # or persist in ~/.claude/settings.json env block (apply=true does this)")
        elif aid in ("codex", "opencode", "pi", "qwen-code"):
            lines.append(f"  export OPENAI_BASE_URL={_shellq(base)}")
            lines.append(f"  export OPENAI_API_KEY={_shellq(shown)}")
            if aid == "qwen-code":
                lines.append(f"  # or DASHSCOPE_API_KEY + --dashscope-url {_shellq(base)}")
        elif aid == "kimi-code":
            lines.append(f"  # ~/.kimi-code/config.toml:")
            lines.append(f"  [provider.{prov_id}]")
            lines.append(f"  base_url = \"{_tomlq(base)}\"")
            lines.append(f"  api_key = \"{_tomlq(shown)}\"")
            lines.append(f"  [model.{model_id}]")
            lines.append(f"  provider = \"{prov_id}\"")
        elif aid == "hermes":
            lines.append(f"  hermes config set provider {prov_id}")
            lines.append(f"  hermes config set model {model_id}")
            lines.append(f"  # key via provider config / .env (e.g. {prov_id.upper()}_API_KEY)")
        elif aid == "zcode":
            lines.append("  # ZCode app provider settings:")
            lines.append(f"  Base URL: {base}")
            lines.append(f"  API key:  {shown}")
            lines.append(f"  Model:    {model}")
        elif aid == "gemini":
            lines.append(f"  export GEMINI_API_KEY={_shellq(shown)}")
        elif aid == "aider":
            lines.append(f"  export OPENAI_API_KEY={_shellq(shown)}")
            lines.append(f"  aider --openai-api-base {_shellq(base)} --model {_shellq(model)}")
        else:
            lines.append(f"  set provider env for this agent (see fixes/provider-config.md)")
        lines.append("")
    if apply:
        written = _apply_provider_settings(provider, base, anthropic_base, api_key, model)
        lines.append(f"APPLIED: {written}")
    if api_key and not show_key:
        lines.append("NOTE: key masked in output — pass show_key=true to reveal, or apply=true to write config files.")
    lines.append("Note: verify with a real model prompt; run config_audit before pushing keys to git.")
    return "\n".join(lines)


def _apply_provider_settings(provider: str, base: str, anthropic_base: str, api_key: str, model: str) -> str:
    target = Path.home() / ".claude" / "settings.json"
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return f"could not parse existing {target} — apply manually"
    env = dict(data.get("env", {}))
    env.update(
        {
            "ANTHROPIC_BASE_URL": anthropic_base,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_MODEL": model,
        }
    )
    data["env"] = env
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)  # a key lives here — restrict perms (best-effort)
    except OSError:
        pass
    return f"wrote {target} ({provider}, model {model})"


def deepseek_setup(key: str, apply: bool = False, show_key: bool = False) -> str:
    """DeepSeek-specific shortcut for provider_setup(provider='deepseek')."""
    return provider_setup(provider="deepseek", api_key=key, apply=apply, show_key=show_key)


TOOLS: Dict[str, Dict[str, Any]] = {
    "deepseek_setup": {
        "description": "DeepSeek-specific shortcut: generate per-agent DeepSeek config snippets. Key is MASKED in output by default; pass show_key=true to reveal it, or apply=true to write ~/.claude/settings.json. For ANY provider use provider_setup.",
        "args": {
            "key": {"type": "string", "description": "DeepSeek API key (sk-...)"},
            "apply": {"type": "boolean", "description": "also write Claude settings.json (default false)"},
            "show_key": {"type": "boolean", "description": "print the full key in snippets (default false — masked)"},
        },
        "fn": lambda a: deepseek_setup(key=a.get("key", ""), apply=bool(a.get("apply", False)), show_key=bool(a.get("show_key", False))),
    },
    "provider_setup": {
        "description": "Generate per-agent config snippets for ANY provider (deepseek|openai|anthropic|google|moonshot|zhipu|qwen|openrouter|ollama|custom). Pass provider + api_key (optional base_url/model overrides). Key is MASKED in output by default; pass show_key=true to reveal it, or apply=true to write ~/.claude/settings.json.",
        "args": {
            "provider": {"type": "string", "description": "provider id: deepseek, openai, anthropic, google, moonshot, zhipu, qwen, openrouter, ollama, or custom"},
            "api_key": {"type": "string", "description": "API key (empty only for ollama/local)"},
            "base_url": {"type": "string", "description": "override base URL (optional; defaults from provider table)"},
            "model": {"type": "string", "description": "override model name (optional)"},
            "apply": {"type": "boolean", "description": "also write Claude settings.json (default false)"},
            "show_key": {"type": "boolean", "description": "print the full key in snippets (default false — masked)"},
        },
        "fn": lambda a: provider_setup(
            provider=a.get("provider", "deepseek"),
            api_key=a.get("api_key", ""),
            base_url=a.get("base_url", ""),
            model=a.get("model", ""),
            apply=bool(a.get("apply", False)),
            show_key=bool(a.get("show_key", False)),
        ),
    },
}
