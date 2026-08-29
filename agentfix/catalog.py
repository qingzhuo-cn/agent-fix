#!/usr/bin/env python3
"""agentfix.catalog — catalog.json access, the single source of truth.

Every question about agents and issues is answered here exactly once so the
CLI, the MCP server and the installer can never drift apart:

  - load / find issues
  - per-platform paths: a path field may be a plain string or an explicit
    ``{"posix": ..., "win": ...}`` dict; the legacy ``config_win`` per-agent
    override is still honored
  - template expansion ({name}/{bin}/{npm_pkg}/{config}/{keys})
  - agent detection (a candidate bin on PATH, or the config dir present)

Pure stdlib, Python 3.8+.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog.json"
DOCS_DIR = ROOT / "fixes"

# Common provider API-key env vars any agent may use (any provider, global devs)
COMMON_PROVIDER_KEYS = [
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "ZHIPU_API_KEY", "DASHSCOPE_API_KEY",
    "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "AZURE_OPENAI_API_KEY",
    "GROQ_API_KEY", "XAI_API_KEY",
]


def is_windows() -> bool:
    return os.name == "nt"


def platform_value(value: Any) -> str:
    """Accept a plain string or an explicit {"posix": ..., "win": ...} dict."""
    if isinstance(value, dict):
        return value.get("win" if is_windows() else "posix", "") or ""
    return value or ""


def config_path(agent: Dict[str, Any]) -> str:
    """Resolve an agent's config home for this platform (forward slashes)."""
    cfg = platform_value(agent.get("config"))
    if is_windows() and agent.get("config_win"):
        cfg = agent["config_win"]  # legacy per-agent override, still honored
    if not cfg:
        return ""
    return os.path.expandvars(os.path.expanduser(cfg)).replace("\\", "/")


def skills_dir(agent: Dict[str, Any]) -> str:
    """Resolve the agent's skill install dir for this platform ('' = none)."""
    return platform_value(agent.get("skills"))


def provider_keys(agent: Dict[str, Any]) -> List[str]:
    """Provider env var names for one agent: its own first, then the common set."""
    keys = list(agent.get("provider_env") or []) + COMMON_PROVIDER_KEYS
    seen: set = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def expand(template: Optional[str], agent: Dict[str, Any]) -> Optional[str]:
    """Replace {name}/{bin}/{npm_pkg}/{config}/{keys} placeholders in a string."""
    if not template:
        return template
    return (
        template.replace("{name}", agent.get("name", agent.get("id", "agent")))
        .replace("{bin}", (agent.get("bin") or ["agent"])[0])
        .replace("{npm_pkg}", agent.get("npm_pkg") or "")
        .replace("{config}", config_path(agent))
        .replace("{keys}", "|".join(provider_keys(agent)))
    )


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else CATALOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"catalog not found: {p}")
    import json

    return json.loads(p.read_text(encoding="utf-8"))


def find_issue(catalog: Dict[str, Any], issue_id: str) -> Optional[Dict[str, Any]]:
    for issue in catalog["issues"]:
        if issue["id"] == issue_id:
            return issue
    return None


def doc_path(issue: Dict[str, Any]) -> Path:
    """The knowledge-base doc for an issue (name-only join: no path escape)."""
    return DOCS_DIR / Path(issue.get("doc", "")).name


def detect_agents(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return registry entries actually installed on this machine.

    Detection: any candidate bin found on PATH, OR the agent's config dir
    exists (resolved through config_path, so per-platform paths count).
    """
    detected = []
    for aid, info in catalog.get("agents", {}).items():
        exe = None
        for b in info.get("bin", []):
            found = shutil.which(b)
            if found:
                exe = found
                break
        cfg = config_path(info)
        cfg_exists = bool(cfg) and Path(cfg.replace("/", os.sep)).exists()
        if exe or cfg_exists:
            detected.append({"id": aid, **info, "exe": exe})
    return detected
