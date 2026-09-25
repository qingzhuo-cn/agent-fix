#!/usr/bin/env python3
"""agentfix.hooks — every write into an agent's home directory lives here.

One module owns all agent integration, driven by the catalog (the single
source of truth):

  legacy hooks   inspect/remove old agent-fix startup hooks for one named agent;
                 new hook installation is disabled
  MCP registration register/remove the toolbox for one explicit agent
  skill copies    deploy/remove one explicit agent's skill files plus the shared
                 fallback; installation never scans or modifies other agents

Idempotent: safe to re-run after `git pull`; file writes use the single
state-owned atomic writer.  Configuration backups are explicit `fix backup`
archives, not an implicit `*.agent-fix-bak` copy.  Integration updates are
scoped to the named catalog agent. Adding an integration = one catalog field +
one flavor function here — never another hand-maintained copy of the target list.

Pure stdlib, Python 3.8+. Runs on Windows / macOS / Linux.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentfix import catalog as cat
from agentfix import report as rep
from agentfix import state
from agentfix.result import StatusLines, StatusText, status_of

ROOT = cat.ROOT
FIX_PY = ROOT / "scripts" / "fix.py"
MCP_SERVER = ROOT / "mcp" / "server.py"

SKILL_NAME = "agent-fix"
# what gets copied into every skill dir (the whole runnable skill)
SOURCE_ITEMS = ("SKILL.md", "fixes", "catalog.json", "scripts", "agentfix", "mcp/server.py")

MARK_BEGIN = "# --- agent-fix self-heal (installed"
MARK_END = "# --- /agent-fix ---"
AGENTS_MD_BEGIN = "# --- agent-fix (installed"
AGENTS_MD_END = MARK_END
PLUGIN_NAME = "agent-fix-selfheal.js"
CRON_NAME = "agent-fix-watchdog"


# ---------------------------------------------------------------- write helpers


def _write(path: Path, text: str) -> None:
    """Write integration state through the single secure state owner."""
    state.atomic_write_text(path, text, private=True)


def _load_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse a JSON config; returns (data, error). Never raises."""
    if not path.exists():
        return {}, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:
        return None, str(e)


def _fix_cmd(runtime_root: Optional[Path] = None) -> str:
    """The exact command hooks should run (absolute interpreter + fix.py)."""
    fix_py = (runtime_root or ROOT) / "scripts" / "fix.py"
    return f'"{sys.executable}" "{fix_py}" selfheal'


# ---------------------------------------------------------------- startup hook flavors


def _claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _hook_claude(action: str) -> str:
    path = _claude_settings()
    if action == "status":
        if not path.exists():
            return StatusText("not registered (no settings.json)", "ok")
        data, err = _load_json(path)
        if data is None:
            return StatusText("unknown (settings.json unparsable)", "inconclusive")
        hooks_cfg = data.get("hooks")
        rules = hooks_cfg.get("SessionStart") if isinstance(hooks_cfg, dict) else []
        if not isinstance(rules, list):
            rules = []
        cmd = _fix_cmd()
        for r in rules:
            if isinstance(r, dict) and any(
                h.get("command") == cmd for h in r.get("hooks", []) if isinstance(h, dict)
            ):
                return StatusText("registered (SessionStart)", "ok")
        return StatusText("not registered", "ok")
    data, err = _load_json(path)
    if data is None:
        return StatusText(f"claude-code: SKIP — cannot parse {path}: {err}", "inconclusive")
    if action == "uninstall":
        if not path.exists():
            return StatusText("claude-code: nothing to remove (no settings.json)", "ok")
        hooks_cfg = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        rules = hooks_cfg.get("SessionStart")
        if not isinstance(rules, list):
            return StatusText("claude-code: no SessionStart hooks", "ok")
        cmd = _fix_cmd()
        kept = [
            r
            for r in rules
            if not (
                isinstance(r, dict)
                and any(h.get("command") == cmd for h in r.get("hooks", []) if isinstance(h, dict))
            )
        ]
        if len(kept) == len(rules):
            return StatusText("claude-code: no agent-fix hook found", "ok")
        if kept:
            hooks_cfg["SessionStart"] = kept
        else:
            hooks_cfg.pop("SessionStart", None)
        data["hooks"] = hooks_cfg
        _write(path, json.dumps(data, indent=2) + "\n")
        return StatusText("claude-code: SessionStart hook removed", "ok")
    # install is intentionally unavailable: repairs require an explicit target.
    return StatusText("claude-code: startup self-heal installation disabled", "inconclusive")


def _codex_config() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _codex_hook_present(text: str) -> bool:
    """Functional detection: marker comment OR a [hooks] session_start that runs us."""
    return "agent-fix self-heal" in text or (
        "[hooks]" in text and "session_start" in text and "selfheal" in text and "fix.py" in text
    )


def _hook_codex(action: str) -> str:
    path = _codex_config()
    if action == "status":
        if not path.exists():
            return StatusText("not registered (no config.toml)", "ok")
        return StatusText("registered ([hooks] session_start)", "ok") if _codex_hook_present(path.read_text(encoding="utf-8")) else StatusText("not registered", "ok")
    if action == "uninstall":
        if not path.exists():
            return StatusText("codex: nothing to remove (no config.toml)", "ok")
        lines = path.read_text(encoding="utf-8").splitlines()
        # pass 1: drop marker blocks (the format agent-fix installs)
        out: List[str] = []
        skipping = False
        removed_marker = False
        unpaired_marker = False
        for line in lines:
            s = line.strip()
            if s.startswith(MARK_BEGIN) and s.endswith(")"):
                if skipping:
                    unpaired_marker = True
                    break
                skipping = True
                removed_marker = True
                continue
            if s == MARK_END:
                if not skipping:
                    unpaired_marker = True
                    break
                skipping = False
                continue
            if not skipping:
                out.append(line)
        if unpaired_marker or skipping:
            return StatusText("codex: refusing to edit config — unpaired agent-fix marker", "error")
        # pass 2: drop the agent-fix session_start line; drop a now-empty [hooks] header
        res: List[str] = []
        removed_entry = False
        i = 0
        while i < len(out):
            line = out[i]
            if line.strip() == "[hooks]":
                j = i + 1
                section = []
                while j < len(out) and not out[j].strip().startswith("["):
                    section.append(out[j])
                    j += 1
                # Match the shape of the entry we install, not the individual
                # words: a user comment that merely mentions these terms must
                # not be deleted. Our entry always invokes scripts/fix.py.
                kept = [
                    ln for ln in section
                    if not ("selfheal" in ln and "fix.py" in ln)
                ]
                if len(kept) != len(section):
                    removed_entry = True
                if any(ln.strip() and not ln.strip().startswith("#") for ln in kept):
                    res.append(line)
                    res.extend(kept)
                i = j
                continue
            res.append(line)
            i += 1
        if not removed_marker and not removed_entry:
            return StatusText("codex: no agent-fix hook found", "ok")
        _write(path, "\n".join(res).rstrip() + "\n")
        return StatusText("codex: [hooks] block removed", "ok")
    # install is intentionally unavailable: repairs require an explicit target.
    return StatusText("codex: startup self-heal installation disabled", "inconclusive")


def _opencode_plugin_dir() -> Path:
    # OpenCode global plugin dir: ~/.config/opencode/plugins/ on every OS
    return Path.home() / ".config" / "opencode" / "plugins"


def _hook_opencode(action: str) -> str:
    f = _opencode_plugin_dir() / PLUGIN_NAME
    if action == "status":
        return StatusText(f"registered ({f.name})", "ok") if f.exists() else StatusText("not registered", "ok")
    if action == "uninstall":
        if f.exists():
            try:
                state.ensure_safe_path(f)
                f.unlink()
            except Exception as exc:
                return StatusText(f"opencode: error: plugin removal failed: {rep.mask_secrets(str(exc))}", "error")
            return StatusText(f"opencode: plugin removed ({f})", "ok")
        return StatusText("opencode: no agent-fix plugin found", "ok")
    return StatusText("opencode: startup self-heal installation disabled", "inconclusive")


def _hermes_scripts_dir() -> Path:
    # Hermes stores scripts under its data dir; CLI resolves relative names there
    local = Path.home() / "AppData" / "Local" / "hermes" / "scripts"
    return local if local.exists() or sys.platform == "win32" else Path.home() / ".hermes" / "scripts"


def _hermes_cron(*args: str, timeout: int = 60) -> Tuple[bool, str]:
    r = subprocess.run(["hermes", "cron", *args], capture_output=True, text=True, errors="replace", timeout=timeout)
    output = (r.stdout or r.stderr or "").strip()
    if r.returncode != 0:
        return False, f"error: hermes cron exited {r.returncode}: {output}"
    return True, output


def _hook_hermes(action: str) -> str:
    if action == "status":
        try:
            ok, listed = _hermes_cron("list", timeout=30)
            if not ok:
                return StatusText(f"unknown (hermes cron status failed: {listed})", "inconclusive")
            return StatusText(f"registered ({CRON_NAME})", "ok") if CRON_NAME in listed else StatusText("not registered", "ok")
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return StatusText("not registered (hermes CLI unavailable)", "inconclusive")
    if action == "uninstall":
        msgs = []
        errors = []
        wd = _hermes_scripts_dir() / "agent-fix-watchdog.py"
        if wd.exists():
            try:
                state.ensure_safe_path(wd)
                wd.unlink()
                msgs.append("watchdog script removed")
            except Exception as exc:
                errors.append(f"watchdog removal failed: {rep.mask_secrets(str(exc))}")
        try:
            ok, listed = _hermes_cron("list", timeout=30)
            if not ok:
                errors.append(listed)
            elif CRON_NAME in listed:
                ok, output = _hermes_cron("remove", CRON_NAME)
                if ok:
                    msgs.append("cron job removed")
                else:
                    errors.append(output)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            errors.append(f"hermes CLI unavailable: {rep.mask_secrets(str(exc))}")
        if errors:
            return StatusText(f"hermes: error: {'; '.join(errors)}", "error")
        return StatusText(f"hermes: {'; '.join(msgs) if msgs else 'nothing to remove'}", "ok")
    return StatusText("hermes: startup self-heal installation disabled", "inconclusive")


HOOK_FLAVORS: Dict[str, Any] = {
    "claude": _hook_claude,
    "codex": _hook_codex,
    "opencode": _hook_opencode,
    "hermes": _hook_hermes,
}


# ---------------------------------------------------------------- hooks API


def hooks_uninstall(agent_id: Optional[str] = None) -> List[str]:
    """Remove a legacy startup hook for one explicitly named agent."""
    if not agent_id:
        return [StatusText("error: agent_id is required for legacy hook cleanup", "error")]
    try:
        info = cat.load_catalog().get("agents", {}).get(agent_id)
        flavor = info.get("hook") if info else None
        if flavor not in HOOK_FLAVORS:
            return [StatusText(f"no legacy startup hook for '{agent_id}'", "ok")]
        return [HOOK_FLAVORS[flavor]("uninstall")]
    except Exception as exc:
        return [StatusText(f"error: hook cleanup failed: {rep.mask_secrets(str(exc))}", "error")]


def hooks_status(agent_id: Optional[str] = None) -> str:
    """Report legacy startup-hook status for one explicit agent."""
    if not agent_id:
        raise ValueError("agent_id is required for legacy hook status")
    info = cat.load_catalog().get("agents", {}).get(agent_id)
    if not info:
        raise ValueError(f"unknown agent: {agent_id}")
    flavor = info.get("hook")
    if flavor not in HOOK_FLAVORS:
        return StatusText(f"{agent_id}: no legacy startup-hook integration", "ok")
    inner = HOOK_FLAVORS[flavor]("status")
    return StatusText(f"{agent_id}: {inner}", status_of(inner, "inconclusive"))


def dispatch(action: str, agent_id: Optional[str] = None) -> str:
    """MCP hook cleanup entry: status or uninstall only."""
    action = (action or "status").lower()
    if action == "uninstall":
        if not agent_id:
            return StatusText("error: agent_id is required for hook cleanup", "error")
        values = hooks_uninstall(agent_id)
        status = "error" if any(status_of(value, "error") == "error" for value in values) else "ok"
        return StatusText("\n".join(str(value) for value in values), status)
    if action == "install":
        return StatusText("error: startup self-heal installation is disabled", "error")
    return hooks_status(agent_id)


# ---------------------------------------------------------------- MCP registration flavors


def _run_cmd(args: List[str]) -> Tuple[bool, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60)
        # Subprocess output reaches MCP callers verbatim, so it crosses the
        # secret-masking boundary here just like any other user-visible text.
        output = rep.mask_secrets((r.stdout or r.stderr or "").strip())
        if r.returncode != 0:
            return False, f"error: command exited {r.returncode}: {output}"
        return True, output
    except Exception as exc:
        return False, f"error: {rep.mask_secrets(str(exc))}"


def _mcp_claude(register: bool) -> str:
    claude = shutil.which("claude")
    if not claude:
        return StatusText("SKIP claude: not installed", "inconclusive")
    if not register:
        ok, output = _run_cmd([claude, "mcp", "remove", SKILL_NAME])
        if not ok:
            return StatusText(f"claude: {output}", "error")
        return StatusText(f"claude: {output or 'removed'}", "ok")
    # Never remove an existing registration speculatively: a failed remove can
    # destroy the only known-good MCP path. Require a successful inventory and
    # ask for an explicit removal/retry when the name already exists.
    ok, listing = _run_cmd([claude, "mcp", "list"])
    if not ok:
        return StatusText(f"claude: {listing} (cannot safely inspect existing registration)", "error")
    if SKILL_NAME in listing:
        return StatusText(f"claude: SKIP existing {SKILL_NAME} registration; remove it explicitly before re-registering", "inconclusive")
    ok, output = _run_cmd(
        [claude, "mcp", "add", "--scope", "user", SKILL_NAME, "--", sys.executable, MCP_SERVER.as_posix()]
    )
    if not ok:
        return StatusText(f"claude: {output}", "error")
    return StatusText(f"claude: {output or 'registered (claude mcp add --scope user)'}", "ok")


def _mcp_opencode_entry() -> Dict[str, Any]:
    # OpenCode schema (v1.18+): type must be "local" (command is an ARRAY) or
    # "remote"; "enabled" is required. A "stdio" entry is INVALID and fails the
    # whole config at startup. See fixes/opencode-mcp-schema.md.
    return {"type": "local", "command": [sys.executable, MCP_SERVER.as_posix()], "enabled": True}


def _json_bucket_merge(path: Path, root_key: str, entry: Dict[str, Any], label: str) -> str:
    data, err = _load_json(path)
    if data is None:
        return StatusText(f"SKIP {label}: {path} unparsable ({err}) — merge manually", "inconclusive")
    bucket = data.get(root_key)
    if bucket is None:
        bucket = {}
        data[root_key] = bucket
    if not isinstance(bucket, dict):
        return StatusText(f"SKIP {label}: {path} has a non-object {root_key} block", "inconclusive")
    existing = bucket.get(SKILL_NAME)
    if existing is not None:
        if existing == entry:
            return StatusText(f"{label}: already registered in {path}", "ok")
        return StatusText(f"SKIP {label}: existing {SKILL_NAME} entry is not owned by agent-fix; remove it explicitly", "error")
    bucket[SKILL_NAME] = entry
    _write(path, json.dumps(data, indent=2) + "\n")
    return StatusText(f"registered in {path}", "ok")


def _json_bucket_unmerge(path: Path, root_key: str, label: str, entry: Dict[str, Any]) -> str:
    if not path.exists():
        return StatusText(f"SKIP {label}: {path} not found", "inconclusive")
    data, err = _load_json(path)
    if data is None:
        return StatusText(f"SKIP {label}: {path} unparsable ({err}) — remove manually", "inconclusive")
    bucket = data.get(root_key)
    if isinstance(bucket, dict) and SKILL_NAME in bucket:
        if bucket[SKILL_NAME] != entry:
            return StatusText(f"error: {label}: {SKILL_NAME} entry is not owned by agent-fix; refusing removal", "error")
        del bucket[SKILL_NAME]
        _write(path, json.dumps(data, indent=2) + "\n")
        return StatusText(f"removed from {path}", "ok")
    return StatusText(f"{label}: no {SKILL_NAME} entry", "ok")


def _mcp_opencode(register: bool) -> str:
    path = Path.home() / ".config" / "opencode" / "opencode.json"
    if not path.parent.exists():
        return StatusText("SKIP opencode: not installed", "inconclusive")
    if register:
        return _json_bucket_merge(path, "mcp", _mcp_opencode_entry(), "opencode")
    return _json_bucket_unmerge(path, "mcp", "opencode", _mcp_opencode_entry())


def _mcp_entry(typed: bool) -> Dict[str, Any]:
    """The MCP entry agent-fix owns for these agents.

    Cursor's schema takes ``{command,args}``; Kimi Code and MiniMax Code take
    the stdio shape with ``enabled``. The divergence is deliberate per agent,
    and the contract tests assert each shape so a silent change cannot pass.
    """
    if typed:
        return {
            "type": "stdio",
            "command": sys.executable,
            "args": [MCP_SERVER.as_posix()],
            "enabled": True,
        }
    return {"command": sys.executable, "args": [MCP_SERVER.as_posix()]}


def _mcp_json_agent(agent: str, register: bool) -> str:
    """Shared single-file mcp.json integration.

    These agents all keep one ``mcp.json`` in a config home and one entry under
    ``mcpServers``; only the config home, the entry shape, and whether removal
    must also sweep a historical path differ. Those differences live in
    ``_MCP_JSON_SPECS`` instead of being copied into one function per agent.
    """
    spec = _MCP_JSON_SPECS[agent]
    home = Path.home() / spec["fallback"]
    base = home
    if spec["catalog"]:
        info = cat.load_catalog().get("agents", {}).get(spec["catalog"], {})
        base = Path(cat.config_path(info) or home.as_posix())
    primary = base / "mcp.json"
    entry = _mcp_entry(bool(spec["typed"]))
    if register:
        if not primary.parent.exists():
            return StatusText(f"SKIP {agent}: not installed", "inconclusive")
        return _json_bucket_merge(primary, "mcpServers", entry, agent)
    legacy = base / spec["legacy"] if spec["legacy"] else None
    if legacy is None:
        if not primary.parent.exists():
            return StatusText(f"SKIP {agent}: not installed", "inconclusive")
        return _json_bucket_unmerge(primary, "mcpServers", agent, entry)
    # Removal must also clear a historical layout, so sweep both locations and
    # report the worst outcome rather than only the first one touched.
    results = [
        _json_bucket_unmerge(path, "mcpServers", agent, entry)
        for path in (primary, legacy)
        if path.exists()
    ]
    if not results:
        return StatusText(f"SKIP {agent}: no MCP config found", "inconclusive")
    status = "error" if any(status_of(value, "ok") == "error" for value in results) else (
        "inconclusive" if any(status_of(value, "ok") == "inconclusive" for value in results) else "ok"
    )
    return StatusText("; ".join(str(value) for value in results), status)


def _mcp_cursor(register: bool) -> str:
    return _mcp_json_agent("cursor", register)


def _mcp_kimi(register: bool) -> str:
    return _mcp_json_agent("kimi-code", register)


def _mcp_minimax(register: bool) -> str:
    return _mcp_json_agent("minimax-code", register)


def _codex_mcp_block() -> str:
    return (
        f"[mcp_servers.{SKILL_NAME}]\n"
        # json.dumps -> \\, \" escapes are valid TOML basic-string escapes too
        f"command = {json.dumps(sys.executable)}\n"
        f"args = [{json.dumps(MCP_SERVER.as_posix())}]\n"
    )


def _codex_marker_owned(text: str, marker: str) -> bool:
    """Report whether every agent-fix block in this config is ours.

    The section name is the ownership marker: a block under
    `mcp_servers.agent-fix` is agent-fix's namespace, and a well-formed
    command/args pair identifies it as installed by us. Requiring the body to
    embed the runtime path as well would strand genuinely installed legacy
    entries and leave them behind on uninstall, which is a worse failure than
    the narrow case of a user hand-writing into our own namespace.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == marker]
    if not starts:
        return False
    for start in starts:
        block = []
        for line in lines[start + 1 :]:
            if line.strip().startswith("["):
                break
            block.append(line)
        if not any(line.strip().startswith("command") for line in block) or not any(
            line.strip().startswith("args") for line in block
        ):
            return False
    return True


def _strip_toml_block(text: str, marker: str) -> str:
    """Remove every validated agent-fix section, including internal blank/comment lines."""
    lines = text.splitlines()
    out = []
    in_block = False
    for line in lines:
        if line.strip() == marker:
            in_block = True
            continue
        if in_block:
            if line.strip().startswith("["):
                in_block = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _mcp_codex(register: bool) -> str:
    path = _codex_config()
    if register and not path.parent.exists():
        return StatusText("SKIP codex: not installed", "inconclusive")
    if not register and not path.exists():
        return StatusText("SKIP codex: not found", "inconclusive")
    marker = f"[mcp_servers.{SKILL_NAME}]"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    had_block = marker in text
    if had_block and not _codex_marker_owned(text, marker):
        return StatusText(f"codex: refusing to edit unowned {marker} block", "error")
    if had_block:
        text = _strip_toml_block(text, marker)
    if not register:
        if not had_block:
            return StatusText(f"codex: no {SKILL_NAME} entry", "ok")
        _write(path, text)
        return StatusText(f"codex: removed from {path}", "ok")
    if text and not text.endswith("\n"):
        text += "\n"
    _write(path, text + _codex_mcp_block())
    return StatusText(f"codex: registered in {path}", "ok")


# MCP targets that share one shape: a single mcp.json in a config home, holding
# one agent-fix entry under "mcpServers". The per-agent differences are data so
# the merge/unmerge body is written once.
#   catalog  — registry id consulted for the config home (None = fixed path)
#   fallback — config home used when the registry has no path for that agent
#   typed    — whether that agent's schema wants the stdio entry with `enabled`
#   legacy   — extra historical mcp.json that removal must also clear
_MCP_JSON_SPECS: Dict[str, Dict[str, Any]] = {
    "cursor": {"catalog": None, "fallback": ".cursor", "typed": False, "legacy": None},
    "kimi-code": {"catalog": "kimi-code", "fallback": ".kimi-code", "typed": True, "legacy": None},
    "minimax-code": {
        "catalog": "minimax-code",
        "fallback": ".minimax",
        "typed": True,
        "legacy": "mcp/mcp.json",
    },
}


MCP_FLAVORS: Dict[str, Any] = {
    "claude": _mcp_claude,
    "opencode": _mcp_opencode,
    "cursor": _mcp_cursor,
    "codex": _mcp_codex,
    "kimi-code": _mcp_kimi,
    "minimax-code": _mcp_minimax,
}


def _mcp_agents(agent: Optional[str]) -> List[Tuple[str, Any]]:
    """Return exactly one explicitly selected MCP integration."""
    if not agent or agent == "all":
        return []
    flavor = cat.load_catalog().get("agents", {}).get(agent, {}).get("mcp")
    return [(agent, flavor)] if flavor in MCP_FLAVORS else []


def mcp_register(agent: Optional[str] = None) -> List[str]:
    if not agent or agent == "all":
        return [StatusText("error: one explicit MCP target agent is required", "error")]
    try:
        if not MCP_SERVER.exists():
            return [StatusText(f"SKIP MCP: server entry not found at {MCP_SERVER} (run from the repo checkout)", "inconclusive")]
        targets = _mcp_agents(agent)
        if not targets:
            return [StatusText(f"agent '{agent}' is not MCP-capable", "error")]
        return [MCP_FLAVORS[flavor](True) for _, flavor in targets]
    except Exception as exc:
        return [StatusText(f"error: MCP registration failed: {rep.mask_secrets(str(exc))}", "error")]


def mcp_remove(agent: Optional[str] = None) -> List[str]:
    if not agent or agent == "all":
        return [StatusText("error: one explicit MCP target agent is required", "error")]
    try:
        targets = _mcp_agents(agent)
        if not targets:
            return [StatusText(f"agent '{agent}' is not MCP-capable", "error")]
        return [MCP_FLAVORS[flavor](False) for _, flavor in targets]
    except Exception as exc:
        return [StatusText(f"error: MCP removal failed: {rep.mask_secrets(str(exc))}", "error")]


# ---------------------------------------------------------------- install / uninstall


def _skill_targets(agent_id: str) -> List[Path]:
    """Return the skill directory for exactly one registry agent."""
    info = cat.load_catalog().get("agents", {}).get(agent_id)
    if not info:
        return []
    skill_dir = cat.skills_dir(info)
    if not skill_dir:
        return []
    return [Path(os.path.expandvars(os.path.expanduser(skill_dir)).replace("\\", "/")) / SKILL_NAME]


def _install_ignore(_directory: str, names: List[str]) -> List[str]:
    ignored = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}
    return [name for name in names if name in ignored or name.endswith((".pyc", ".pyo"))]


def _install_skill(target: Path) -> str:
    target = Path(target).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    state.ensure_safe_path(target.parent)
    stage = state.unique_backup_path(target.parent, f".{target.name}.stage", "")
    stage.mkdir(parents=True, exist_ok=False)
    try:
        for item in SOURCE_ITEMS:
            src = ROOT / item
            dst = stage / item
            if not src.exists():
                raise state.StateError(f"runtime source missing: {src}")
            if src.is_dir():
                shutil.copytree(src, dst, ignore=_install_ignore)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        required = (stage / "scripts" / "fix.py", stage / "mcp" / "server.py", stage / "agentfix" / "__init__.py")
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise state.StateError(f"staged runtime incomplete: {', '.join(missing)}")
        state.atomic_replace_directory(stage, target)
        return f"skill installed -> {target}"
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _agents_md_hook(file: Path, runtime_root: Optional[Path] = None) -> str:
    runtime_root = Path(runtime_root or ROOT)
    fix_py = runtime_root / "scripts" / "fix.py"
    marker = AGENTS_MD_END
    if file.exists() and marker in file.read_text(encoding="utf-8"):
        return f"hook already present in {file}"
    block = (
        f"\n{AGENTS_MD_BEGIN} {time.strftime('%Y-%m-%d')} ---\n"
        f"When asked to fix a broken AI coding agent, use the agent-fix skill at {runtime_root}\n"
        f"  - Read {(runtime_root / 'fixes').as_posix()}/*.md (knowledge base) and {(runtime_root / 'SKILL.md').as_posix()}\n"
        f"  - Run: python \"{fix_py.as_posix()}\" check <issue-id> --agent <agent-id>\n"
        f"  - Apply only the requested target: python \"{fix_py.as_posix()}\" apply <issue-id> --agent <agent-id> --yes\n"
        f"{marker}\n"
    )
    existing = file.read_text(encoding="utf-8") if file.exists() else ""
    _write(file, existing + block)
    return f"AGENTS.md hook appended -> {file}"


def _agents_md_unhook(file: Path) -> str:
    if not file.exists():
        return StatusText(f"nothing to remove in {file}", "ok")
    text = file.read_text(encoding="utf-8")
    if AGENTS_MD_END not in text and AGENTS_MD_BEGIN not in text:
        return StatusText(f"no agent-fix hook in {file}", "ok")
    out, skipping = [], False
    unpaired = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(AGENTS_MD_BEGIN) and s.endswith("---"):
            if skipping:
                unpaired = True
                break
            skipping = True
            continue
        if s == AGENTS_MD_END:
            if not skipping:
                unpaired = True
                break
            skipping = False
            continue
        if not skipping:
            out.append(line)
    if unpaired or skipping:
        return StatusText(f"refusing to edit {file} — unpaired agent-fix hook marker", "error")
    _write(file, "\n".join(out).rstrip() + "\n")
    return StatusText(f"agent-fix hook removed from {file}", "ok")


def _launcher_is_owned(path: Path) -> bool:
    """Report whether an existing launcher was written by agent-fix.

    Without this, installing would silently destroy a same-named file the user
    or another tool owns. Both launcher forms embed the agent-fix script path,
    so that is the ownership marker.
    """
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "agent-fix" in body or str(Path("scripts") / "fix.py") in body.replace("\\", "/")


def _install_cli(runtime_root: Optional[Path] = None) -> str:
    runtime_root = Path(runtime_root or ROOT)
    fix_py = runtime_root / "scripts" / "fix.py"
    if not fix_py.is_file():
        raise state.StateError(f"runtime launcher missing: {fix_py}")
    bindir = Path.home() / "bin"
    state.ensure_safe_path(bindir)
    if cat.is_windows():
        cmd_path = bindir / "fix.cmd"
        body = f'@echo off\r\n"{sys.executable}" "{fix_py}" %*\r\n'
        bindir.mkdir(parents=True, exist_ok=True)
        state.ensure_safe_path(bindir)
        if cmd_path.is_symlink() or (cmd_path.exists() and not _launcher_is_owned(cmd_path)):
            raise state.StateError(
                f"refusing to overwrite an unowned launcher: {cmd_path}"
            )
        _write(cmd_path, body)
        return f"CLI installed -> {cmd_path} (add {bindir} to PATH if needed)"
    target = bindir / "fix"
    bindir.mkdir(parents=True, exist_ok=True)
    state.ensure_safe_path(bindir)
    if target.is_symlink():
        try:
            if target.resolve() != fix_py.resolve():
                raise state.StateError(f"CLI target is an unowned symlink: {target}")
        except OSError as exc:
            raise state.StateError(f"cannot inspect CLI target: {target}") from exc
        target.unlink()
    elif target.exists():
        state.ensure_safe_path(target)
        if target.is_dir():
            raise state.StateError(f"CLI target is a directory: {target}")
        if not _launcher_is_owned(target):
            raise state.StateError(f"refusing to overwrite an unowned launcher: {target}")
        target.unlink()
    else:
        state.ensure_safe_path(target)
    try:
        os.symlink(fix_py, target)
    except OSError:
        # git-bash / MSYS / non-privileged installs without symlink support:
        # write an exec shim embedding the absolute installed runtime path.
        _write(target, f'#!/usr/bin/env bash\nexec "{fix_py}" "$@"\n')
        os.chmod(target, 0o755)
    return f"CLI installed -> {target}"


def _agents_md_target(agent_id: str) -> Optional[Path]:
    return {
        "codex": Path.home() / ".codex" / "AGENTS.md",
        "opencode": Path.home() / ".config" / "opencode" / "AGENTS.md",
    }.get(agent_id)


def install_target(agent_id: str) -> List[str]:
    """Deploy skill files for one explicit agent; never install repair hooks."""
    try:
        targets = _skill_targets(agent_id)
    except Exception as exc:
        return StatusLines([f"error: install setup failed: {rep.mask_secrets(str(exc))}"], "error")
    if not targets:
        return StatusLines([f"error: unknown agent '{agent_id}'"], "error")
    lines = [f"installing agent-fix for {agent_id} from {ROOT}"]
    errors: List[str] = []
    for target in targets:
        try:
            lines.append("  " + _install_skill(target))
        except Exception as exc:
            errors.append(f"  error: skill install failed: {rep.mask_secrets(str(exc))}")
    if errors:
        return StatusLines(lines + errors, "error")
    instruction_file = _agents_md_target(agent_id)
    if instruction_file:
        try:
            lines.append("  " + _agents_md_hook(instruction_file, targets[0]))
        except Exception as exc:
            errors.append(f"  error: AGENTS.md hook failed: {rep.mask_secrets(str(exc))}")
    if not errors:
        try:
            lines.append("  " + _install_cli(targets[0]))
        except Exception as exc:
            errors.append(f"  error: CLI install failed: {rep.mask_secrets(str(exc))}")
    if errors:
        return StatusLines(lines + errors, "error")
    lines.append("  startup repair: disabled")
    lines.append("  MCP registration: skipped (use fix mcp register <agent-id>)")
    lines.append(f"done. Use: fix check <issue-id> --agent {agent_id}")
    return StatusLines(lines, "ok")


def uninstall_target(agent_id: str) -> List[str]:
    """Remove agent-fix integrations for one explicit agent only."""
    try:
        targets = _skill_targets(agent_id)
    except Exception as exc:
        return StatusLines([f"error: uninstall setup failed: {rep.mask_secrets(str(exc))}"], "error")
    if not targets:
        return StatusLines([f"error: unknown agent '{agent_id}'"], "error")
    lines = [f"uninstalling agent-fix for {agent_id}"]
    errors: List[str] = []
    # A cleanup that could not determine whether it removed something must not
    # be reported as a completed uninstall; the caller turns this status into a
    # nonzero exit.
    inconclusive: List[str] = []

    def collect(values) -> None:
        for line in values:
            lines.append("  " + line)
            state_value = status_of(line, "ok")
            if state_value == "error":
                errors.append(line)
            elif state_value == "inconclusive":
                inconclusive.append(line)

    try:
        hook_lines = hooks_uninstall(agent_id)
    except Exception as exc:
        hook_lines = [StatusText(f"error: hook cleanup failed: {rep.mask_secrets(str(exc))}", "error")]
    collect(hook_lines)
    try:
        mcp_targets = _mcp_agents(agent_id)
    except Exception as exc:
        mcp_targets = []
        errors.append(f"MCP target lookup failed: {rep.mask_secrets(str(exc))}")
    if mcp_targets:
        try:
            mcp_lines = mcp_remove(agent_id)
        except Exception as exc:
            mcp_lines = [StatusText(f"error: MCP removal failed: {rep.mask_secrets(str(exc))}", "error")]
        collect(mcp_lines)
    instruction_file = _agents_md_target(agent_id)
    if instruction_file:
        try:
            unhook = _agents_md_unhook(instruction_file)
            lines.append("  " + unhook)
            if status_of(unhook, "ok") == "error":
                errors.append(unhook)
            elif status_of(unhook, "ok") == "inconclusive":
                inconclusive.append(unhook)
        except Exception as exc:
            errors.append(f"  error: AGENTS.md cleanup failed: {rep.mask_secrets(str(exc))}")
    for target in targets:
        if not target.exists():
            continue
        try:
            state.ensure_safe_path(target)
            shutil.rmtree(target)
            lines.append(f"  skill removed -> {target}")
        except Exception as exc:
            errors.append(f"  error: skill removal failed: {rep.mask_secrets(str(exc))}")
    if errors:
        return StatusLines(lines + ["error: uninstall incomplete; inspect the failures above"], "error")
    if inconclusive:
        return StatusLines(
            lines
            + [
                "error: uninstall could not confirm every removal; "
                "inspect the skipped steps above"
            ],
            "error",
        )
    lines.append("done. Shared CLI/backups are kept because other explicit targets may use them.")
    return StatusLines(lines, "ok")
