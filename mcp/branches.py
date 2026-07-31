#!/usr/bin/env python3
"""
agent-fix MCP branch skills — the "small tools" that make the MCP server more
than a plain inspect/fix wrapper.

Every function takes plain args, returns a plain-text string, and uses only the
Python stdlib (3.8+). Imported by mcp/server.py and exposed as MCP tools.

Branches:
  net_diagnose    connectivity + latency to every agent API endpoint (proxy-aware)
  version_check   installed vs latest version for every detected agent
  config_audit    parse agent config files, find parse errors + leaked API keys
  log_triage      scan agent log dirs for recent ERROR/WARN lines
  backup_configs  snapshot agent config dirs into ~/.agent-fix-backups/<ts>.zip
  restore_configs list or restore a config backup (confirm required)
  deepseek_setup  per-agent DeepSeek config snippets (optionally apply to Claude)
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import socket
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path.home() / ".agent-fix-backups"

# ---------------------------------------------------------------- helpers


def _load_catalog() -> Dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import fix  # local import: server.py also imports fix

    return fix.load_catalog()


def _detect_agents() -> List[Dict[str, Any]]:
    import fix

    return fix.detect_agents(_load_catalog())


def _env(key: str) -> Optional[str]:
    v = os.environ.get(key)
    return v if v else None


# ---------------------------------------------------------------- branch 1


def net_diagnose(timeout: float = 5.0) -> str:
    """Check TCP connectivity + latency to every agent's API endpoint.

    Backed by the shared scripts/netcheck.py engine — the same checks the CLI
    runs for the `net-connectivity` catalog issue.
    """
    import netcheck  # scripts/ is on sys.path (server.py inserts it)

    return netcheck.format_report(timeout=timeout)


# ---------------------------------------------------------------- branch 2


def version_check() -> str:
    """Installed vs latest version for every detected agent."""
    import subprocess

    def _resolve(name: str) -> str:
        # On Windows, CreateProcess cannot resolve bare "claude"/"npm" shims —
        # always resolve via shutil.which like fix.py does.
        import shutil

        return shutil.which(name) or name

    lines = ["VERSION CHECK", ""]
    for agent in _detect_agents():
        name = agent.get("name", agent.get("id"))
        npm_pkg = agent.get("npm_pkg")
        bin_name = (agent.get("bin") or ["?"])[0]
        installed = "?"
        try:
            r = subprocess.run(
                [_resolve(bin_name), "--version"], capture_output=True, text=True, timeout=20
            )
            installed = (r.stdout or r.stderr or "").strip().splitlines()[0][:60]
        except Exception:
            pass
        if npm_pkg:
            latest = "?"
            try:
                r = subprocess.run(
                    [_resolve("npm"), "view", npm_pkg, "version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                latest = (r.stdout or r.stderr or "").strip()
            except Exception:
                pass
            lines.append(f"  {name:<26} installed={installed:<40} latest={latest}")
        else:
            update_hint = {
                "kimi-code": "run: kimi upgrade (or reinstall from kimi.com/code)",
                "hermes": "run: hermes update",
                "zcode": "update via ZCode Desktop",
                "cursor": "update via Cursor app",
                "amp": "run: amp upgrade",
                "droid": "update via Droid app",
            }.get(agent.get("id"), "reinstall per official docs")
            lines.append(f"  {name:<26} installed={installed:<40} update: {update_hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------- branch 3

_KEY_PATTERNS = [
    ("anthropic/openai/deepseek key", re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b")),
    ("github PAT", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b")),
    ("github fine-grained", re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws access key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("google api key", re.compile(r"\b(AIza[0-9A-Za-z_-]{20,})\b")),
    ("dashscope key", re.compile(r"\b(sk-[a-f0-9]{20,})\b")),
]

_CONFIG_EXTS = {".json", ".toml", ".yaml", ".yml", ".env", ".ini", ".cfg"}


def _mask(secret: str) -> str:
    return secret[:5] + "***" + secret[-4:] if len(secret) > 10 else "***"


_NOISE_DIRS = {
    "node_modules", ".git", "sessions", "logs", "cache", ".cache",
    "__pycache__", "plugins", "projects", "shell-snapshots", "todos",
    "statsig", "file-history", "lsp", "tmp", "history", "rollouts",
    ".venv", "venv", "telemetry", "statsig",
}


def _noisy(path: Path) -> bool:
    return any(part in _NOISE_DIRS for part in path.parts)


def config_audit(depth: int = 3) -> str:
    """Scan agent config dirs: parse errors + leaked API keys (masked)."""
    lines = ["CONFIG AUDIT", ""]
    for agent in _detect_agents():
        cfg = agent.get("config")
        if not cfg:
            continue
        base = Path(cfg).expanduser()
        if not base.exists():
            continue
        files: List[Path] = []
        for ext in _CONFIG_EXTS:
            files += list(base.rglob(f"*{ext}"))
        files = [f for f in files if not _noisy(f) and f.stat().st_size < 2_000_000][:200]
        parse_errors, leaks = [], []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if f.suffix == ".json":
                try:
                    json.loads(text)
                except Exception as e:
                    parse_errors.append(f"{f.name}: {e}")
            for label, pat in _KEY_PATTERNS:
                for m in pat.findall(text):
                    leaks.append(f"{label}: {_mask(m)} (in {f.name})")
        lines.append(f"== {agent.get('name')} ({base})")
        lines.append(f"   config files scanned: {len(files)}")
        if parse_errors:
            lines.append(f"   PARSE ERRORS ({len(parse_errors)}):")
            lines += [f"     - {e}" for e in parse_errors[:10]]
        else:
            lines.append("   parse: OK")
        if leaks:
            lines.append(f"   LEAKED KEYS ({len(leaks)}):")
            lines += [f"     - {l}" for l in leaks[:20]]
        else:
            lines.append("   no obvious leaked keys")
        lines.append("")
    return "\n".join(lines) or "no agent config dirs found"


# ---------------------------------------------------------------- branch 4


def log_triage(agent_id: Optional[str] = None, lines: int = 30) -> str:
    """Scan known agent log locations for recent ERROR/WARN lines."""
    import subprocess

    pat = re.compile(r"(ERROR|WARN|Traceback|postinstall|Fatal|panic|exit code)", re.I)
    out = ["LOG TRIAGE", ""]
    for agent in _detect_agents():
        if agent_id and agent.get("id") != agent_id:
            continue
        base = Path(agent.get("config") or "").expanduser()
        logs: List[Path] = []
        if base.exists():
            logs = [p for p in base.rglob("*.log") if "node_modules" not in p.parts][-5:]
            logdir = base / "logs"
            if logdir.exists():
                logs += sorted(logdir.iterdir())[-5:]
        if not logs:
            continue
        hits: List[str] = []
        for f in logs:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines()[-500:]:
                if pat.search(line):
                    hits.append(f"{f.name}: {line.strip()[:160]}")
        out.append(f"== {agent.get('name')}")
        if hits:
            out += [f"   {h}" for h in hits[-int(lines):]]
        else:
            out.append("   no recent error/warn lines")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- branch 5/6


def _snapshot_targets() -> List[Path]:
    targets = []
    for agent in _detect_agents():
        cfg = agent.get("config")
        if not cfg:
            continue
        p = Path(cfg).expanduser()
        if p.exists():
            targets.append(p)
    return targets


def backup_configs() -> str:
    """Snapshot every agent config dir into ~/.agent-fix-backups/<ts>.zip."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"agent-configs-{ts}.zip"
    targets = _snapshot_targets()
    if not targets:
        return "no agent config dirs found to back up"
    manifest: Dict[str, str] = {}
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, base in enumerate(targets):
            base = base.resolve()
            prefix = f"{i:02d}-{base.name}"
            manifest[prefix] = str(base)
            for f in base.rglob("*"):
                if f.is_file() and f.stat().st_size < 20_000_000 and not _noisy(f):
                    zf.write(f, f"{prefix}/{f.relative_to(base).as_posix()}")
                    count += 1
        zf.writestr("_manifest.json", json.dumps(manifest, indent=2))
    size = dest.stat().st_size
    return f"backup created: {dest}\nsize: {size/1024:.1f} KB | files: {count}"


def restore_configs(backup: Optional[str] = None, confirm: bool = False) -> str:
    """List backups, or restore one (backup filename or 'latest', confirm=True)."""
    if not BACKUP_DIR.exists():
        return "no backups found (~/.agent-fix-backups missing)"
    backups = sorted(BACKUP_DIR.glob("agent-configs-*.zip"), key=lambda p: p.name, reverse=True)
    if not backups:
        return "no backups found"
    if not backup:
        lines = ["AVAILABLE BACKUPS:", ""]
        for b in backups:
            lines.append(f"  {b.name}  ({b.stat().st_size/1024:.1f} KB)")
        lines.append("")
        lines.append("restore with: restore_configs(backup='<name>' or 'latest', confirm=True)")
        return "\n".join(lines)
    target = backups[0] if backup == "latest" else next((b for b in backups if b.name == backup), None)
    if not target:
        return f"backup not found: {backup}"
    if not confirm:
        return f"dry run: would restore {target.name} from {target} — pass confirm=True to actually restore"
    restored = []
    with zipfile.ZipFile(target) as zf:
        manifest = json.loads(zf.read("_manifest.json"))
        # recreate the original config dirs recorded in the manifest
        for prefix in manifest:
            Path(manifest[prefix]).mkdir(parents=True, exist_ok=True)
        for member in zf.namelist():
            if member == "_manifest.json":
                continue
            prefix, rel = member.split("/", 1)
            base = manifest.get(prefix)
            if not base:
                continue
            out = Path(base) / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            restored.append(str(out))
    return f"restored {len(restored)} files from {target.name}\n" + "\n".join(restored[:20]) + ("\n..." if len(restored) > 20 else "")


# ---------------------------------------------------------------- branch 7

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

    lines = [
        f"PROVIDER SETUP: {provider}  (base={base}, model={model}, key={api_key[:5] + '***' + api_key[-4:] if api_key else '(local)'})",
        "",
    ]
    for agent in _detect_agents():
        aid = agent.get("id")
        name = agent.get("name", aid)
        lines.append(f"== {name}")
        if aid == "claude-code":
            lines.append(f"  export ANTHROPIC_BASE_URL={anthropic_base}")
            lines.append(f"  export ANTHROPIC_AUTH_TOKEN={api_key}")
            lines.append(f"  export ANTHROPIC_MODEL={model}")
            lines.append("  # or persist in ~/.claude/settings.json env block (apply=true does this)")
        elif aid in ("codex", "opencode", "pi", "qwen-code"):
            lines.append(f"  export OPENAI_BASE_URL={base}")
            lines.append(f"  export OPENAI_API_KEY={api_key}")
            if aid == "qwen-code":
                lines.append(f"  # or DASHSCOPE_API_KEY + --dashscope-url {base}")
        elif aid == "kimi-code":
            lines.append(f"  # ~/.kimi-code/config.toml:")
            lines.append(f"  [provider.{provider}]")
            lines.append(f"  base_url = \"{base}\"")
            lines.append(f"  api_key = \"{api_key}\"")
            lines.append(f"  [model.{model}]")
            lines.append(f"  provider = \"{provider}\"")
        elif aid == "hermes":
            lines.append(f"  hermes config set provider {provider}")
            lines.append(f"  hermes config set model {model}")
            lines.append(f"  # key via provider config / .env (e.g. {provider.upper()}_API_KEY)")
        elif aid == "zcode":
            lines.append(f"  # ZCode app provider settings:")
            lines.append(f"  Base URL: {base}")
            lines.append(f"  API key:  {api_key}")
            lines.append(f"  Model:    {model}")
        elif aid == "gemini":
            lines.append(f"  export GEMINI_API_KEY={api_key}")
        elif aid == "aider":
            lines.append(f"  export OPENAI_API_KEY={api_key}")
            lines.append(f"  aider --openai-api-base {base} --model {model}")
        else:
            lines.append(f"  set provider env for this agent (see fixes/provider-config.md)")
        lines.append("")
    if apply:
        written = _apply_provider_settings(provider, base, anthropic_base, api_key, model)
        lines.append(f"APPLIED: {written}")
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
    return f"wrote {target} ({provider}, model {model})"


def deepseek_setup(key: str, apply: bool = False) -> str:
    """DeepSeek-specific shortcut for provider_setup(provider='deepseek')."""
    return provider_setup(provider="deepseek", api_key=key, apply=apply)


# ---------------------------------------------------------------- registry

BRANCH_TOOLS: Dict[str, Dict[str, Any]] = {
    "net_diagnose": {
        "description": "Check TCP connectivity + latency to every agent's API endpoint (anthropic/openai/deepseek/moonshot/google/zhipu/alibaba/npm) and show proxy env. Use when an agent 'suddenly stopped working' or for network diagnosis.",
        "args": {"timeout": {"type": "number", "description": "connect timeout seconds (default 5)"}},
        "fn": lambda a: net_diagnose(timeout=float(a.get("timeout", 5))),
    },
    "version_check": {
        "description": "Compare installed vs latest version for every detected agent (npm agents query the registry; native agents get update hints). Use before/after upgrades.",
        "args": {},
        "fn": lambda a: version_check(),
    },
    "config_audit": {
        "description": "Scan agent config files for JSON/TOML parse errors and leaked API keys (masked). Use before committing configs or when an agent ignores its config.",
        "args": {"depth": {"type": "number", "description": "scan depth (default 3)"}},
        "fn": lambda a: config_audit(depth=int(a.get("depth", 3))),
    },
    "log_triage": {
        "description": "Scan agent log locations for recent ERROR/WARN/Traceback lines. Use when an agent fails without a clear message.",
        "args": {
            "agent_id": {"type": "string", "description": "restrict to one agent id (e.g. kimi-code); omit for all"},
            "lines": {"type": "number", "description": "max matching lines per agent (default 30)"},
        },
        "fn": lambda a: log_triage(agent_id=a.get("agent_id"), lines=int(a.get("lines", 30))),
    },
    "backup_configs": {
        "description": "Snapshot every detected agent's config dir into ~/.agent-fix-backups/<timestamp>.zip (excludes node_modules/sessions/logs). Use before any repair or upgrade.",
        "args": {},
        "fn": lambda a: backup_configs(),
    },
    "restore_configs": {
        "description": "List config backups, or restore one. pass backup='latest' or a filename and confirm=True to actually restore.",
        "args": {
            "backup": {"type": "string", "description": "backup filename or 'latest'; omit to list"},
            "confirm": {"type": "boolean", "description": "must be true to actually restore"},
        },
        "fn": lambda a: restore_configs(backup=a.get("backup"), confirm=bool(a.get("confirm", False))),
    },
    "deepseek_setup": {
        "description": "DeepSeek-specific shortcut: generate per-agent DeepSeek config snippets. Pass apply=true to also write ~/.claude/settings.json. For ANY provider use provider_setup.",
        "args": {
            "key": {"type": "string", "description": "DeepSeek API key (sk-...)"},
            "apply": {"type": "boolean", "description": "also write Claude settings.json (default false)"},
        },
        "fn": lambda a: deepseek_setup(key=a.get("key", ""), apply=bool(a.get("apply", False))),
    },
    "provider_setup": {
        "description": "Generate per-agent config snippets for ANY provider (deepseek|openai|anthropic|google|moonshot|zhipu|qwen|openrouter|ollama|custom). Pass provider + api_key (optional base_url/model overrides). apply=true also writes ~/.claude/settings.json.",
        "args": {
            "provider": {"type": "string", "description": "provider id: deepseek, openai, anthropic, google, moonshot, zhipu, qwen, openrouter, ollama, or custom"},
            "api_key": {"type": "string", "description": "API key (empty only for ollama/local)"},
            "base_url": {"type": "string", "description": "override base URL (optional; defaults from provider table)"},
            "model": {"type": "string", "description": "override model name (optional)"},
            "apply": {"type": "boolean", "description": "also write Claude settings.json (default false)"},
        },
        "fn": lambda a: provider_setup(
            provider=a.get("provider", "deepseek"),
            api_key=a.get("api_key", ""),
            base_url=a.get("base_url", ""),
            model=a.get("model", ""),
            apply=bool(a.get("apply", False)),
        ),
    },
}
