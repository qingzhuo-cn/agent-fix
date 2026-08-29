#!/usr/bin/env python3
"""agentfix.hooks — every write into an agent's home directory lives here.

One module owns all agent integration, driven by the catalog (the single
source of truth):

  startup hooks   agents with a ``hook`` flavor (claude|codex|opencode|hermes)
                  run `fix selfheal` on session start — install/uninstall/status
  MCP registration agents with an ``mcp`` flavor (claude|codex|opencode|cursor)
                  get the toolbox as native tools — register/remove
  skill copies    agents with a ``skills`` dir get SKILL.md + fixes + catalog +
                  scripts + agentfix — `fix install` / `fix uninstall` = all of
                  the above plus AGENTS.md hooks and the `fix` CLI shim

Idempotent: safe to re-run after `git pull`; every config write is marker-
tagged, atomic (tmp + os.replace) and backed up (*.agent-fix-bak) on first
modification. Adding an integration = one catalog field + one flavor function
here — never another hand-maintained copy of the target list.

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

ROOT = cat.ROOT
FIX_PY = ROOT / "scripts" / "fix.py"
MCP_SERVER = ROOT / "mcp" / "server.py"

SKILL_NAME = "agent-fix"
# what gets copied into every skill dir (the whole runnable skill)
SOURCE_ITEMS = ("SKILL.md", "fixes", "catalog.json", "scripts", "agentfix")

MARK_BEGIN = "# --- agent-fix self-heal (installed"
MARK_END = "# --- /agent-fix ---"
AGENTS_MD_BEGIN = "# --- agent-fix (installed"
AGENTS_MD_END = MARK_END
PLUGIN_NAME = "agent-fix-selfheal.js"
CRON_NAME = "agent-fix-watchdog"
BACKUP_SUFFIX = ".agent-fix-bak"


# ---------------------------------------------------------------- write helpers


def _write(path: Path, text: str) -> None:
    """Atomic config write: tmp file + os.replace, parents created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".agent-fix-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> None:
    if path.exists() and not Path(str(path) + BACKUP_SUFFIX).exists():
        shutil.copy2(path, str(path) + BACKUP_SUFFIX)


def _load_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse a JSON config; returns (data, error). Never raises."""
    if not path.exists():
        return {}, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:
        return None, str(e)


def _fix_cmd() -> str:
    """The exact command hooks should run (absolute interpreter + fix.py)."""
    return f'"{sys.executable}" "{FIX_PY}" selfheal'


# ---------------------------------------------------------------- startup hook flavors


def _claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _hook_claude(action: str) -> str:
    path = _claude_settings()
    if action == "status":
        if not path.exists():
            return "not registered (no settings.json)"
        data, err = _load_json(path)
        if data is None:
            return "unknown (settings.json unparsable)"
        rules = (data.get("hooks") or {}).get("SessionStart") or []
        cmd = _fix_cmd()
        for r in rules:
            if isinstance(r, dict) and any(
                h.get("command") == cmd for h in r.get("hooks", []) if isinstance(h, dict)
            ):
                return "registered (SessionStart)"
        return "not registered"
    data, err = _load_json(path)
    if data is None:
        return f"claude-code: SKIP — cannot parse {path}: {err}"
    if action == "uninstall":
        if not path.exists():
            return "claude-code: nothing to remove (no settings.json)"
        hooks_cfg = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        rules = hooks_cfg.get("SessionStart")
        if not isinstance(rules, list):
            return "claude-code: no SessionStart hooks"
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
            return "claude-code: no agent-fix hook found"
        if kept:
            hooks_cfg["SessionStart"] = kept
        else:
            hooks_cfg.pop("SessionStart", None)
        data["hooks"] = hooks_cfg
        _write(path, json.dumps(data, indent=2) + "\n")
        return "claude-code: SessionStart hook removed"
    # install
    _backup(path)
    hooks_cfg = data.get("hooks")
    if not isinstance(hooks_cfg, dict):
        hooks_cfg = {}
    rules = hooks_cfg.get("SessionStart")
    if not isinstance(rules, list):
        rules = []
    cmd = _fix_cmd()
    if any(
        isinstance(r, dict)
        and any(h.get("command") == cmd for h in r.get("hooks", []) if isinstance(h, dict))
        for r in rules
    ):
        return "claude-code: already registered (SessionStart hook present)"
    rules.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": cmd,
                    "timeout": 90,
                    "description": "agent-fix self-heal: check + auto-repair agents at session start",
                }
            ],
        }
    )
    hooks_cfg["SessionStart"] = rules
    data["hooks"] = hooks_cfg
    _write(path, json.dumps(data, indent=2) + "\n")
    return f"claude-code: SessionStart hook registered -> {path}"


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
            return "not registered (no config.toml)"
        return "registered ([hooks] session_start)" if _codex_hook_present(path.read_text(encoding="utf-8")) else "not registered"
    if action == "uninstall":
        if not path.exists():
            return "codex: nothing to remove (no config.toml)"
        lines = path.read_text(encoding="utf-8").splitlines()
        # pass 1: drop marker blocks (the format agent-fix installs)
        out: List[str] = []
        skipping = False
        removed_marker = False
        for line in lines:
            s = line.strip()
            if s.startswith(MARK_BEGIN):
                skipping = True
                removed_marker = True
                continue
            if s == MARK_END:
                skipping = False
                continue
            if not skipping:
                out.append(line)
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
                kept = [
                    ln for ln in section
                    if not ("session_start" in ln and "selfheal" in ln and "agent-fix" in ln)
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
            return "codex: no agent-fix hook found"
        _write(path, "\n".join(res).rstrip() + "\n")
        return "codex: [hooks] block removed"
    # install
    _backup(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if _codex_hook_present(text):
        return "codex: already registered ([hooks] session_start present)"
    if "[hooks]" in text:
        return (
            "codex: SKIP — ~/.codex/config.toml already has a [hooks] section; "
            "merge manually: add session_start = [{ command = <fix selfheal cmd> }]"
        )
    block = (
        f"\n{MARK_BEGIN} {time.strftime('%Y-%m-%d')} ---\n"
        "[hooks]\n"
        # json.dumps -> \\, \" escapes are valid TOML basic-string escapes too
        f"session_start = [{{ command = {json.dumps(f'{sys.executable} {FIX_PY} selfheal')}, timeout = 90 }}]\n"
        f"{MARK_END}\n"
    )
    _write(path, text + block)
    return f"codex: [hooks] session_start registered -> {path}"


def _opencode_plugin_dir() -> Path:
    # OpenCode global plugin dir: ~/.config/opencode/plugins/ on every OS
    return Path.home() / ".config" / "opencode" / "plugins"


def _opencode_plugin_source() -> str:
    cmd = json.dumps(_fix_cmd())
    return (
        "// agent-fix self-heal (installed by agent-fix installer) — runs `fix selfheal`\n"
        "// when a session is created, silently. Remove this file to disable.\n"
        'import { execSync } from "node:child_process";\n'
        "\n"
        "export const AgentFixSelfHeal = async () => {\n"
        "  return {\n"
        '    "session.created": async () => {\n'
        "      try {\n"
        f"        execSync({cmd}, {{ stdio: \"ignore\", timeout: 90000, shell: true }});\n"
        "      } catch { /* broken is fine — fix selfheal reports nothing when healthy */ }\n"
        "    },\n"
        "  };\n"
        "};\n"
    )


def _hook_opencode(action: str) -> str:
    f = _opencode_plugin_dir() / PLUGIN_NAME
    if action == "status":
        return f"registered ({f.name})" if f.exists() else "not registered"
    if action == "uninstall":
        if f.exists():
            f.unlink()
            return f"opencode: plugin removed ({f})"
        return "opencode: no agent-fix plugin found"
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists() and f.read_text(encoding="utf-8") == _opencode_plugin_source():
        return "opencode: already registered (plugin present)"
    _backup(f)
    _write(f, _opencode_plugin_source())
    return f"opencode: session.created plugin registered -> {f}"


def _hermes_scripts_dir() -> Path:
    # Hermes stores scripts under its data dir; CLI resolves relative names there
    local = Path.home() / "AppData" / "Local" / "hermes" / "scripts"
    return local if local.exists() or sys.platform == "win32" else Path.home() / ".hermes" / "scripts"


def _hermes_watchdog_source() -> str:
    return (
        "# agent-fix self-heal watchdog (installed by agent-fix installer).\n"
        "# For a no-agent Hermes cron job: empty stdout = silent; any output is the alert.\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        f"FIX_PY = {str(FIX_PY)!r}\n"
        "r = subprocess.run([sys.executable, FIX_PY, 'selfheal'], capture_output=True, text=True, timeout=120)\n"
        "out = (r.stdout or '').strip()\n"
        "if out:\n"
        "    print(f'agent-fix watchdog: {out}')\n"
    )


def _hermes_cron(*args: str, timeout: int = 60) -> str:
    r = subprocess.run(["hermes", "cron", *args], capture_output=True, text=True, errors="replace", timeout=timeout)
    return (r.stdout or r.stderr or "").strip()


def _hook_hermes(action: str) -> str:
    if action == "status":
        try:
            listed = _hermes_cron("list", timeout=30)
            return f"registered ({CRON_NAME})" if CRON_NAME in listed else "not registered"
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return "not registered (hermes CLI unavailable)"
    if action == "uninstall":
        msgs = []
        wd = _hermes_scripts_dir() / "agent-fix-watchdog.py"
        if wd.exists():
            wd.unlink()
            msgs.append("watchdog script removed")
        try:
            listed = _hermes_cron("list", timeout=30)
            if CRON_NAME in listed:
                _hermes_cron("remove", CRON_NAME)
                msgs.append("cron job removed")
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass
        return f"hermes: {'; '.join(msgs) if msgs else 'nothing to remove'}"
    # install
    scripts = _hermes_scripts_dir()
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "agent-fix-watchdog.py").write_text(_hermes_watchdog_source(), encoding="utf-8")
    try:
        listed = _hermes_cron("list", timeout=30)
        if CRON_NAME in listed:
            return f"hermes: watchdog cron already registered ({CRON_NAME})"
        _hermes_cron(
            "create", "0 9 * * *",
            "--name", CRON_NAME,
            "--script", "agent-fix-watchdog.py",
            "--no-agent",
            "--deliver", "local",
        )
        return f"hermes: watchdog cron registered (daily '0 9 * * *', deliver=local; change with: hermes cron edit {CRON_NAME} --deliver telegram)"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return f"hermes: SKIP — could not register cron ({e})"


HOOK_FLAVORS: Dict[str, Any] = {
    "claude": _hook_claude,
    "codex": _hook_codex,
    "opencode": _hook_opencode,
    "hermes": _hook_hermes,
}


# ---------------------------------------------------------------- hooks API


def _hooked_agents() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(agents with a hook flavor, detected agents without hook support)."""
    detected = cat.detect_agents(cat.load_catalog())
    hooked = [a for a in detected if a.get("hook") in HOOK_FLAVORS]
    instruction_only = [a for a in detected if a.get("hook") not in HOOK_FLAVORS]
    return hooked, instruction_only


def hooks_install(agent_id: Optional[str] = None) -> List[str]:
    hooked, _ = _hooked_agents()
    if agent_id:
        hooked = [a for a in hooked if a["id"] == agent_id]
        if not hooked:
            _, instruction_only = _hooked_agents()
            names = ", ".join(a["id"] for a in instruction_only) or "none"
            return [f"error: no startup-hook support for '{agent_id}' (instruction-only here: {names})"]
    return [HOOK_FLAVORS[a["hook"]]("install") for a in hooked]


def hooks_uninstall(agent_id: Optional[str] = None) -> List[str]:
    hooked, _ = _hooked_agents()
    if agent_id:
        hooked = [a for a in hooked if a["id"] == agent_id]
        if not hooked:
            return [f"error: no startup-hook support for '{agent_id}'"]
    return [HOOK_FLAVORS[a["hook"]]("uninstall") for a in hooked]


def hooks_status() -> str:
    """Watchdog status: every agent's self-heal registration (hook or instruction-only)."""
    hooked, instruction_only = _hooked_agents()
    lines = [rep.data_tag("local hook registration"), ""]
    for a in hooked:
        lines.append(f"  {a.get('name', a['id']):<16} {HOOK_FLAVORS[a['hook']]('status')}")
    for a in instruction_only:
        lines.append(f"  {a.get('name', a['id']):<16} no startup hook — AGENTS.md instruction covers on-demand repair")
    lines.append("")
    lines.append("  Run 'fix selfheal' directly to test the check+fix pipeline.")
    return "\n".join(lines)


def dispatch(action: str, agent_id: Optional[str] = None) -> str:
    """MCP `hooks` tool entry: status (default) | install | uninstall."""
    action = (action or "status").lower()
    if action == "install":
        return "\n".join(hooks_install(agent_id))
    if action == "uninstall":
        return "\n".join(hooks_uninstall(agent_id))
    return hooks_status()


# ---------------------------------------------------------------- MCP registration flavors


def _run_cmd(args: List[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"error: {e}"


def _mcp_claude(register: bool) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "SKIP claude: not installed"
    if not register:
        return f"claude: {_run_cmd([claude, 'mcp', 'remove', SKILL_NAME]) or 'removed'}"
    # `claude mcp add` with an existing name does NOT update the command path,
    # so remove any stale registration first (harmless if absent).
    _run_cmd([claude, "mcp", "remove", SKILL_NAME])
    out = _run_cmd([claude, "mcp", "add", "--scope", "user", SKILL_NAME, "--", sys.executable, MCP_SERVER.as_posix()])
    return f"claude: {out or 'registered (claude mcp add --scope user)'}"


def _mcp_opencode_entry() -> Dict[str, Any]:
    # OpenCode schema (v1.18+): type must be "local" (command is an ARRAY) or
    # "remote"; "enabled" is required. A "stdio" entry is INVALID and fails the
    # whole config at startup. See fixes/opencode-mcp-schema.md.
    return {"type": "local", "command": [sys.executable, MCP_SERVER.as_posix()], "enabled": True}


def _json_bucket_merge(path: Path, root_key: str, entry: Dict[str, Any], label: str) -> str:
    data, err = _load_json(path)
    if data is None:
        return f"SKIP {label}: {path} unparsable ({err}) — merge manually"
    bucket = data.get(root_key)
    if bucket is None:
        bucket = {}
        data[root_key] = bucket
    bucket[SKILL_NAME] = entry
    _write(path, json.dumps(data, indent=2) + "\n")
    return f"registered in {path}"


def _json_bucket_unmerge(path: Path, root_key: str, label: str) -> str:
    if not path.exists():
        return f"SKIP {label}: {path} not found"
    data, err = _load_json(path)
    if data is None:
        return f"SKIP {label}: {path} unparsable ({err}) — remove manually"
    bucket = data.get(root_key)
    if isinstance(bucket, dict) and SKILL_NAME in bucket:
        del bucket[SKILL_NAME]
        _write(path, json.dumps(data, indent=2) + "\n")
        return f"removed from {path}"
    return f"{label}: no {SKILL_NAME} entry"


def _mcp_opencode(register: bool) -> str:
    path = Path.home() / ".config" / "opencode" / "opencode.json"
    if not path.parent.exists():
        return "SKIP opencode: not installed"
    if register:
        return _json_bucket_merge(path, "mcp", _mcp_opencode_entry(), "opencode")
    return _json_bucket_unmerge(path, "mcp", "opencode")


def _mcp_cursor(register: bool) -> str:
    path = Path.home() / ".cursor" / "mcp.json"
    if not path.parent.exists():
        return "SKIP cursor: not installed"
    if register:
        entry = {"command": sys.executable, "args": [MCP_SERVER.as_posix()]}
        return _json_bucket_merge(path, "mcpServers", entry, "cursor")
    return _json_bucket_unmerge(path, "mcpServers", "cursor")


def _codex_mcp_block() -> str:
    return (
        f"[mcp_servers.{SKILL_NAME}]\n"
        # json.dumps -> \\, \" escapes are valid TOML basic-string escapes too
        f"command = {json.dumps(sys.executable)}\n"
        f"args = [{json.dumps(MCP_SERVER.as_posix())}]\n"
    )


def _strip_toml_block(text: str, marker: str) -> str:
    """Remove a [marker] block up to the next [section/comment/blank line]."""
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
    return "\n".join(out).rstrip() + "\n"


def _mcp_codex(register: bool) -> str:
    path = _codex_config()
    if register and not path.parent.exists():
        return "SKIP codex: not installed"
    if not register and not path.exists():
        return "SKIP codex: not found"
    marker = f"[mcp_servers.{SKILL_NAME}]"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    had_block = marker in text
    if had_block:
        text = _strip_toml_block(text, marker)
    if not register:
        if not had_block:
            return f"codex: no {SKILL_NAME} entry"
        _write(path, text)
        return f"codex: removed from {path}"
    if text and not text.endswith("\n"):
        text += "\n"
    _write(path, text + _codex_mcp_block())
    return f"codex: registered in {path}"


MCP_FLAVORS: Dict[str, Any] = {
    "claude": _mcp_claude,
    "opencode": _mcp_opencode,
    "cursor": _mcp_cursor,
    "codex": _mcp_codex,
}


def _mcp_agents(agent: Optional[str]) -> List[Tuple[str, Any]]:
    """(agent id, mcp flavor) to touch: one agent, or every detected one."""
    if agent and agent != "all":
        flavor = cat.load_catalog().get("agents", {}).get(agent, {}).get("mcp")
        return [(agent, flavor)] if flavor in MCP_FLAVORS else []
    detected = cat.detect_agents(cat.load_catalog())
    return [(a["id"], a["mcp"]) for a in detected if a.get("mcp") in MCP_FLAVORS]


def mcp_register(agent: Optional[str] = None) -> List[str]:
    if not MCP_SERVER.exists():
        return [f"SKIP MCP: server entry not found at {MCP_SERVER} (run from the repo checkout)"]
    targets = _mcp_agents(agent)
    if not targets:
        return ["no MCP-capable agents installed (supports: " + ", ".join(MCP_FLAVORS) + ")"]
    return [MCP_FLAVORS[flavor](True) for _, flavor in targets]


def mcp_remove(agent: Optional[str] = None) -> List[str]:
    if agent and agent != "all":
        targets = _mcp_agents(agent)
    else:
        # uninstall covers every catalog agent, detected or not — a stale
        # registration in an uninstalled agent's config still deserves cleanup
        targets = [
            (aid, info["mcp"])
            for aid, info in cat.load_catalog().get("agents", {}).items()
            if info.get("mcp") in MCP_FLAVORS
        ]
    if not targets:
        return ["nothing to remove"]
    return [MCP_FLAVORS[flavor](False) for _, flavor in targets]


# ---------------------------------------------------------------- install / uninstall


def _skill_targets(uninstall: bool = False) -> List[Path]:
    """Skill dirs to write: every catalog agent's skills dir + the shared one.

    install targets only DETECTED agents (plus the always-shared ~/.agents);
    uninstall covers every catalog agent so nothing is left behind.
    """
    catalog = cat.load_catalog()
    targets = []
    for aid, info in catalog.get("agents", {}).items():
        if not uninstall and aid not in {a["id"] for a in cat.detect_agents(catalog)}:
            continue
        sd = cat.skills_dir(info)
        if sd:
            targets.append(Path(os.path.expandvars(os.path.expanduser(sd)).replace("\\", "/")) / SKILL_NAME)
    shared = Path.home() / ".agents" / "skills" / SKILL_NAME
    if shared not in targets:
        targets.append(shared)
    seen, out = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _install_skill(target: Path) -> str:
    target.mkdir(parents=True, exist_ok=True)
    for item in SOURCE_ITEMS:
        src = ROOT / item
        dst = target / item
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.exists():
            shutil.copy2(src, dst)
    return f"skill installed -> {target}"


def _agents_md_hook(file: Path) -> str:
    marker = AGENTS_MD_END
    if file.exists() and marker in file.read_text(encoding="utf-8"):
        return f"hook already present in {file}"
    block = (
        f"\n{AGENTS_MD_BEGIN} {time.strftime('%Y-%m-%d')} ---\n"
        f"When asked to fix a broken AI coding agent, use the agent-fix skill at {ROOT}\n"
        f"  - Read {(ROOT / 'fixes').as_posix()}/*.md (knowledge base) and {(ROOT / 'SKILL.md').as_posix()}\n"
        f"  - Run: python \"{FIX_PY.as_posix()}\" doctor   (then: fix apply <id> --yes)\n"
        f"{marker}\n"
    )
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return f"AGENTS.md hook appended -> {file}"


def _agents_md_unhook(file: Path) -> str:
    if not file.exists():
        return f"nothing to remove in {file}"
    text = file.read_text(encoding="utf-8")
    if AGENTS_MD_END not in text and AGENTS_MD_BEGIN not in text:
        return f"no agent-fix hook in {file}"
    out, skipping = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(AGENTS_MD_BEGIN):
            skipping = True
            continue
        if s == AGENTS_MD_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    _write(file, "\n".join(out).rstrip() + "\n")
    return f"agent-fix hook removed from {file}"


def _install_cli() -> str:
    bindir = Path.home() / "bin"
    if cat.is_windows():
        cmd_path = bindir / "fix.cmd"
        body = f'@echo off\r\npython "{FIX_PY}" %*\r\n'
        bindir.mkdir(parents=True, exist_ok=True)
        _write(cmd_path, body)
        return f"CLI installed -> {cmd_path} (add {bindir} to PATH if needed)"
    target = bindir / "fix"
    bindir.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        os.symlink(FIX_PY, target)
    except OSError:
        # git-bash / MSYS / non-privileged installs without symlink support:
        # write an exec shim embedding the absolute repo path (a plain copy
        # would resolve its own location and look for fix.py in the wrong place).
        _write(target, f'#!/usr/bin/env bash\nexec "{FIX_PY}" "$@"\n')
        os.chmod(target, 0o755)
    return f"CLI installed -> {target}"


def _uninstall_cli() -> str:
    msgs = []
    for target in (Path.home() / "bin" / "fix", Path.home() / ".local" / "bin" / "fix"):
        if target.is_symlink() or target.exists():
            target.unlink(missing_ok=True)
            msgs.append(f"removed {target}")
    if cat.is_windows():
        cmd_path = Path.home() / "bin" / "fix.cmd"
        if cmd_path.exists():
            cmd_path.unlink()
            msgs.append(f"removed {cmd_path}")
    return "; ".join(msgs) or "no CLI shim found"


def install_all() -> List[str]:
    """`fix install` — deploy the skill everywhere the catalog says it belongs."""
    lines = [f"installing agent-fix from {ROOT}"]
    for target in _skill_targets():
        lines.append("  " + _install_skill(target))
    for file in (Path.home() / ".codex" / "AGENTS.md", Path.home() / ".config" / "opencode" / "AGENTS.md"):
        lines.append("  " + _agents_md_hook(file))
    lines.append("  startup hooks:")
    lines += ["    " + l for l in hooks_install()]
    lines.append("  MCP registration:")
    lines += ["    " + l for l in mcp_register()]
    lines.append("  " + _install_cli())
    lines.append("done. Try: fix doctor")
    return lines


def uninstall_all() -> List[str]:
    """`fix uninstall` — the exact inverse of install_all."""
    lines = ["uninstalling agent-fix"]
    lines.append("  startup hooks:")
    lines += ["    " + l for l in hooks_uninstall()]
    lines.append("  MCP registration:")
    lines += ["    " + l for l in mcp_remove()]
    for file in (Path.home() / ".codex" / "AGENTS.md", Path.home() / ".config" / "opencode" / "AGENTS.md"):
        lines.append("  " + _agents_md_unhook(file))
    for target in _skill_targets(uninstall=True):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            lines.append(f"  skill removed -> {target}")
    lines.append("  " + _uninstall_cli())
    lines.append("done. (~/.agent-fix-backups kept — delete it yourself if unwanted)")
    return lines
