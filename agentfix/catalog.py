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
    config_env = agent.get("config_env")
    if config_env:
        names = config_env if isinstance(config_env, (list, tuple)) else [config_env]
        for name in names:
            if os.environ.get(str(name)):
                cfg = os.environ[str(name)]
                break
    if is_windows() and agent.get("config_win"):
        cfg = agent["config_win"]  # legacy per-agent override, still honored
    if not cfg:
        return ""
    return os.path.expandvars(os.path.expanduser(cfg)).replace("\\", "/")


def skills_dir(agent: Dict[str, Any]) -> str:
    """Resolve the agent's skill install dir for this platform ('' = none)."""
    value = platform_value(agent.get("skills"))
    skills_env = agent.get("skills_env")
    if skills_env:
        names = skills_env if isinstance(skills_env, (list, tuple)) else [skills_env]
        for name in names:
            if os.environ.get(str(name)):
                value = str(Path(os.environ[str(name)]) / "skills")
                break
    if not value:
        return ""
    return os.path.expandvars(os.path.expanduser(value)).replace("\\", "/")


def provider_keys(agent: Dict[str, Any]) -> List[str]:
    """Return only credential variables owned by this registry entry.

    A global key union makes an unrelated provider look authenticated. Agents
    that intentionally accept multiple providers must declare that ownership in
    their own catalog entry.
    """
    keys = list(agent.get("provider_env") or [])
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


def find_agent(catalog: Dict[str, Any], agent_id: str) -> Optional[Dict[str, Any]]:
    """Return one registry entry by id without probing any other agent."""
    info = catalog.get("agents", {}).get(agent_id)
    return {"id": agent_id, **info} if info else None


def _probe_agent(agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Probe one already-resolved agent entry."""
    exe = None
    for candidate in agent.get("bin", []):
        found = shutil.which(candidate)
        if found:
            exe = found
            break
    cfg = config_path(agent)
    cfg_exists = bool(cfg) and Path(cfg.replace("/", os.sep)).exists()
    return {**agent, "exe": exe} if exe or cfg_exists else None


def detect_agent(catalog: Dict[str, Any], agent_id: str) -> Optional[Dict[str, Any]]:
    """Probe one registry agent only; return its entry when detected."""
    agent = find_agent(catalog, agent_id)
    return _probe_agent(agent) if agent else None


def detect_agents(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return registry entries actually installed on this machine.

    This bulk inventory helper is used only by explicit inventory/installation
    commands. Repair commands use :func:`detect_agent` so they never probe
    unrelated agents.
    """
    detected = []
    for agent_id in catalog.get("agents", {}):
        agent = detect_agent(catalog, agent_id)
        if agent:
            detected.append(agent)
    return detected
