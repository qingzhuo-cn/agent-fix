#!/usr/bin/env python3
"""尚书省 shared utilities — helpers used by more than one ministry.

mcp/court/shangshu/common.py  ->  repo root = parents[3]
Pure Python 3.8+ stdlib. Imported by the six ministry modules; never imports
them back (no cycles).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# repo root (scripts/fix.py, scripts/netcheck.py, scripts/heal_hooks.py live there)
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

BACKUP_DIR = Path.home() / ".agent-fix-backups"

_CONFIG_EXTS = {".json", ".toml", ".yaml", ".yml", ".env", ".ini", ".cfg"}

_NOISE_DIRS = {
    "node_modules", ".git", "sessions", "logs", "cache", ".cache",
    "__pycache__", "plugins", "projects", "shell-snapshots", "todos",
    "statsig", "file-history", "lsp", "tmp", "history", "rollouts",
    ".venv", "venv", "telemetry", "statsig",
}


# ---------------------------------------------------------------- catalog


def _load_catalog() -> Dict[str, Any]:
    import fix  # local import: keeps module import cheap (server is long-lived)

    return fix.load_catalog()


def _detect_agents() -> List[Dict[str, Any]]:
    import fix

    return fix.detect_agents(_load_catalog())


# ---------------------------------------------------------------- escaping


def _shellq(s: str) -> str:
    """Single-quote a value for POSIX shell snippet output (blocks $()/quote injection)."""
    return "'" + s.replace("'", "'\\''") + "'"


def _tomlq(s: str) -> str:
    """Escape a value for double-quoted TOML string output."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _sanitize_id(s: str) -> str:
    """Keep only safe chars for TOML section names / config ids."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "")


def _mask(secret: str) -> str:
    return secret[:5] + "***" + secret[-4:] if len(secret) > 10 else "***"


# Common secret patterns (API keys / tokens). config_audit labels them; these
# are used by _mask_secrets() to redact keys from arbitrary output (log_triage,
# diagnosis detail) so the MCP never echoes a user's key.
_SECRET_REGEXES = [
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b(AIza[0-9A-Za-z_-]{20,})\b"),
]


def _mask_secrets(text: str) -> str:
    """Redact known API-key/token patterns anywhere in a string (defense in depth)."""
    for pat in _SECRET_REGEXES:
        text = pat.sub(lambda m: _mask(m.group(0)), text)
    return text


# ---------------------------------------------------------------- path safety (zip-slip)


def _safe_rel(rel: str) -> bool:
    """True only if a zip member path can't escape its base dir (zip-slip guard)."""
    p = Path(rel)
    # p.root catches "/"-anchored paths that is_absolute() misses on Windows
    # (drive-relative), p.drive catches "C:evil" style paths.
    return bool(rel) and not (p.is_absolute() or p.drive or p.root or ".." in p.parts)


def _inside(base: Path, child: Path) -> bool:
    """True if child resolves inside base (containment check, belt-and-braces)."""
    try:
        return os.path.commonpath([str(base.resolve()), str(child.resolve())]) == str(base.resolve())
    except ValueError:
        return False


def _noisy(path: Path) -> bool:
    return any(part in _NOISE_DIRS for part in path.parts)


# ---------------------------------------------------------------- config walking


def _walk_configs(base: Path, max_depth: int = 3) -> List[Path]:
    """Config files under base, depth-bounded and noise-dir pruned (stdlib os.walk)."""
    out: List[Path] = []
    base_parts = len(base.parts)
    for root, dirs, names in os.walk(str(base)):
        depth = len(Path(root).parts) - base_parts
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in _NOISE_DIRS]
        names[:] = [n for n in names if n not in _NOISE_DIRS]
        for name in names:
            if Path(name).suffix in _CONFIG_EXTS:
                out.append(Path(root) / name)
    return out


# ---------------------------------------------------------------- snapshot targets


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
