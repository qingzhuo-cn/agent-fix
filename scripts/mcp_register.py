#!/usr/bin/env python3
"""
mcp_register.py — register (or unregister) the agent-fix MCP server with an agent.

Usage:
    python scripts/mcp_register.py <agent|all> [--remove]

Supported agents:
    claude     -> `claude mcp add agent-fix -- python <server.py>`
    opencode   -> merge "mcp" block into ~/.config/opencode/opencode.json
    cursor     -> merge "mcpServers" into ~/.cursor/mcp.json
    codex      -> append [mcp_servers.agent-fix] to ~/.codex/config.toml

Pure stdlib. Idempotent: re-running updates the existing registration.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "server.py"
SERVER_ARG = SERVER.as_posix()  # forward slashes: works on Windows + POSIX
NAME = "agent-fix"

HOME = Path.home()


def _python() -> str:
    return sys.executable or "python"


def _cmd(args: List[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"error: {e}"


# ---------------------------------------------------------------- registrars


def register_claude() -> str:
    claude = shutil.which("claude")
    if not claude:
        return "SKIP claude: not installed"
    # `claude mcp add` with an existing name does NOT update the command path,
    # so remove any stale registration first (harmless if absent).
    _cmd([claude, "mcp", "remove", NAME])
    out = _cmd([claude, "mcp", "add", "--scope", "user", NAME, "--", _python(), SERVER_ARG])
    return f"claude: {out or 'registered (claude mcp add --scope user)'}"


def _merge_json(path: Path, root_key: str, entry: Dict[str, Any]) -> str:
    """Merge entry into path[root_key][NAME], preserving everything else."""
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return f"SKIP {path.name}: unparsable ({e}) — merge manually"
    bucket = data.get(root_key)
    if bucket is None:
        bucket = {}
        data[root_key] = bucket
    bucket[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"registered in {path}"


def register_opencode() -> str:
    cfg = HOME / ".config" / "opencode" / "opencode.json"
    if not cfg.parent.exists():
        return "SKIP opencode: not installed"
    # OpenCode schema (v1.18+): type must be "local" (command is an ARRAY) or
    # "remote"; "enabled" is required. A "stdio" entry is INVALID and fails the
    # whole config at startup -> ConfigInvalidError on EVERY opencode run
    # (including session list / -c history). See fixes/opencode-mcp-schema.md.
    entry = {"type": "local", "command": [_python(), SERVER_ARG], "enabled": True}
    return f"opencode: {_merge_json(cfg, 'mcp', entry)}"


def register_cursor() -> str:
    cfg = HOME / ".cursor" / "mcp.json"
    if not cfg.parent.exists():
        return "SKIP cursor: not installed"
    entry = {"command": _python(), "args": [SERVER_ARG]}
    return f"cursor: {_merge_json(cfg, 'mcpServers', entry)}"


def register_codex() -> str:
    cfg = HOME / ".codex" / "config.toml"
    if not cfg.parent.exists():
        return "SKIP codex: not installed"
    block = (
        f"[mcp_servers.{NAME}]\n"
        # json.dumps -> \\\\, \\" escapes are valid TOML basic-string escapes too
        f"command = {json.dumps(_python())}\n"
        f"args = [{json.dumps(SERVER_ARG)}]\n"
    )
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    marker = f"[mcp_servers.{NAME}]"
    if marker in text:
        # replace the existing block (until the next [section)
        lines = text.splitlines()
        out, in_block = [], False
        for line in lines:
            if line.strip() == marker:
                in_block = True
                continue
            if in_block:
                s = line.strip()
                # stop the block at the next section, a comment/marker, or a
                # blank line — never delete content that isn't ours
                if s.startswith("[") or s.startswith("#") or not s:
                    in_block = False
            if not in_block:
                out.append(line)
        text = "\n".join(out).rstrip() + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text + block, encoding="utf-8")
    return f"codex: registered in {cfg}"


_REGISTRARS: Dict[str, Any] = {
    "claude": register_claude,
    "opencode": register_opencode,
    "cursor": register_cursor,
    "codex": register_codex,
}


# ---------------------------------------------------------------- unregister


def unregister_claude() -> str:
    claude = shutil.which("claude")
    if not claude:
        return "SKIP claude: not installed"
    return f"claude: {_cmd([claude, 'mcp', 'remove', NAME]) or 'removed'}"


def _unmerge_json(path: Path, root_key: str) -> str:
    if not path.exists():
        return f"SKIP {path.name}: not found"
    data = json.loads(path.read_text(encoding="utf-8"))
    bucket = data.get(root_key)
    if bucket and NAME in bucket:
        del bucket[NAME]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return f"removed from {path}"
    return f"{path.name}: no {NAME} entry"


def unregister_codex() -> str:
    cfg = HOME / ".codex" / "config.toml"
    if not cfg.exists():
        return "SKIP codex: not found"
    marker = f"[mcp_servers.{NAME}]"
    lines = cfg.read_text(encoding="utf-8").splitlines()
    out, in_block = [], False
    for line in lines:
        if line.strip() == marker:
            in_block = True
            continue
        if in_block and line.strip().startswith("["):
            in_block = False
        if not in_block:
            out.append(line)
    cfg.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return f"codex: removed from {cfg}"


_UNREGISTRARS = {
    "claude": unregister_claude,
    "opencode": lambda: _unmerge_json(HOME / ".config/opencode/opencode.json", "mcp"),
    "cursor": lambda: _unmerge_json(HOME / ".cursor/mcp.json", "mcpServers"),
    "codex": unregister_codex,
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="mcp_register.py")
    ap.add_argument("agent", choices=list(_REGISTRARS) + ["all"], help="agent to register, or 'all'")
    ap.add_argument("--remove", action="store_true", help="unregister instead")
    args = ap.parse_args()

    targets = list(_REGISTRARS) if args.agent == "all" else [args.agent]
    table = _UNREGISTRARS if args.remove else _REGISTRARS
    for t in targets:
        print(table[t]())
    return 0


if __name__ == "__main__":
    sys.exit(main())
