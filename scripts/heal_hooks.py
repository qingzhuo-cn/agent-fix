#!/usr/bin/env python3
"""
agent-fix self-heal hooks — make every installed agent run `fix selfheal`
automatically when it starts, so repairs happen on their own.

Supported startup hooks (verified against installed binaries/schema):
  claude-code  ~/.claude/settings.json  -> hooks.SessionStart command
  codex        ~/.codex/config.toml     -> [hooks] session_start command
  opencode     ~/.config/opencode/plugins/agent-fix-selfheal.js (session.created)
  hermes       hermes cron create --no-agent daily watchdog script

Agents without a startup-hook mechanism (kimi-code, pi, zcode, cursor, gemini,
aider, qwen-code, ...) rely on the AGENTS.md instruction the installer already
writes — `heal_hooks.py status` reports them as "instruction-only".

Usage:
  python scripts/heal_hooks.py install [--agent claude-code]   # register hooks
  python scripts/heal_hooks.py uninstall [--agent claude-code] # remove hooks
  python scripts/heal_hooks.py status                          # what's active

Idempotent: safe to re-run after `git pull`; every write is marker-tagged and
backed up (configs get a .agent-fix-bak-<ts> sibling on first modification).

Pure stdlib, Python 3.8+. Runs on Windows / macOS / Linux.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
FIX_PY = ROOT / "scripts" / "fix.py"
MARK_BEGIN = "# --- agent-fix self-heal (installed"
MARK_END = "# --- /agent-fix ---"
PLUGIN_NAME = "agent-fix-selfheal.js"
CRON_NAME = "agent-fix-watchdog"
BACKUP_SUFFIX = ".agent-fix-bak"


# ---------------------------------------------------------------- helpers


def _home() -> Path:
    return Path.home()


def _fix_cmd() -> str:
    """The exact command hooks should run (absolute interpreter + fix.py)."""
    return f'"{sys.executable}" "{FIX_PY}" selfheal'


def _backup(path: Path) -> None:
    if path.exists() and not Path(str(path) + BACKUP_SUFFIX).exists():
        shutil.copy2(path, str(path) + BACKUP_SUFFIX)


def _detect_agents() -> List[Dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import fix

    return fix.detect_agents(fix.load_catalog())


def _agent_config(agent_id: str) -> Optional[Path]:
    for a in _detect_agents():
        if a.get("id") == agent_id and a.get("config"):
            return Path(a["config"]).expanduser()
    return None


def _claude_settings() -> Path:
    return _home() / ".claude" / "settings.json"


def _codex_config() -> Path:
    return _home() / ".codex" / "config.toml"


def _opencode_plugin_dir() -> Path:
    # OpenCode global plugin dir: ~/.config/opencode/plugins/ on every OS
    return _home() / ".config" / "opencode" / "plugins"


def _hermes_scripts_dir() -> Path:
    # Hermes stores scripts under its data dir; CLI resolves relative names there
    local = _home() / "AppData" / "Local" / "hermes" / "scripts"
    return local if local.exists() or sys.platform == "win32" else _home() / ".hermes" / "scripts"


# ---------------------------------------------------------------- claude


def _claude_install() -> str:
    path = _claude_settings()
    _backup(path)
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return f"claude-code: SKIP — cannot parse {path}: {e}"
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    rules = hooks.get("SessionStart")
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
    hooks["SessionStart"] = rules
    data["hooks"] = hooks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"claude-code: SessionStart hook registered -> {path}"


def _claude_uninstall() -> str:
    path = _claude_settings()
    if not path.exists():
        return "claude-code: nothing to remove (no settings.json)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"claude-code: SKIP — cannot parse {path}: {e}"
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    rules = hooks.get("SessionStart")
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
        hooks["SessionStart"] = kept
    else:
        hooks.pop("SessionStart", None)
    data["hooks"] = hooks
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "claude-code: SessionStart hook removed"


# ---------------------------------------------------------------- codex


def _codex_hook_present(text: str) -> bool:
    """Functional detection: marker comment OR a [hooks] session_start that runs us."""
    return "agent-fix self-heal" in text or (
        "[hooks]" in text and "session_start" in text and "selfheal" in text and "fix.py" in text
    )


def _codex_install() -> str:
    path = _codex_config()
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
        f"\n# --- agent-fix self-heal (installed {time.strftime('%Y-%m-%d')}) ---\n"
        "[hooks]\n"
        # json.dumps -> \\\\, \\" escapes are valid TOML basic-string escapes too
        f"session_start = [{{ command = {json.dumps(f'{sys.executable} {FIX_PY} selfheal')}, timeout = 90 }}]\n"
        "# --- /agent-fix ---\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return f"codex: [hooks] session_start registered -> {path}"


def _codex_uninstall() -> str:
    path = _codex_config()
    if not path.exists():
        return "codex: nothing to remove (no config.toml)"
    lines = path.read_text(encoding="utf-8").splitlines()
    # pass 1: drop marker blocks (the format heal_hooks installs)
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
    path.write_text("\n".join(res).rstrip() + "\n", encoding="utf-8")
    return "codex: [hooks] block removed"


# ---------------------------------------------------------------- opencode


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


def _opencode_install() -> str:
    d = _opencode_plugin_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / PLUGIN_NAME
    if f.exists() and f.read_text(encoding="utf-8") == _opencode_plugin_source():
        return f"opencode: already registered (plugin present)"
    _backup(f)
    f.write_text(_opencode_plugin_source(), encoding="utf-8")
    return f"opencode: session.created plugin registered -> {f}"


def _opencode_uninstall() -> str:
    f = _opencode_plugin_dir() / PLUGIN_NAME
    if f.exists():
        f.unlink()
        return f"opencode: plugin removed ({f})"
    return "opencode: no agent-fix plugin found"


# ---------------------------------------------------------------- hermes


def _hermes_watchdog_source() -> str:
    return (
        "# agent-fix self-heal watchdog (installed by agent-fix installer).\n"
        "# For a no-agent Hermes cron job: empty stdout = silent; any output is the alert.\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        f"FIX_PY = {str(FIX_PY)!r}\n"
        "r = subprocess.run([sys.executable, FIX_PY, 'selfheal'], capture_output=True, text=True, timeout=120)\n"
        "out = (r.stdout or '').strip()\n"
        "if out:\n"
        "    print(f'agent-fix watchdog: {out}')\n"
    )


def _hermes_install() -> str:
    scripts = _hermes_scripts_dir()
    scripts.mkdir(parents=True, exist_ok=True)
    wd = scripts / "agent-fix-watchdog.py"
    wd.write_text(_hermes_watchdog_source(), encoding="utf-8")
    # check existing cron job
    try:
        listed = subprocess.run(
            ["hermes", "cron", "list"], capture_output=True, text=True, timeout=30
        ).stdout
        if CRON_NAME in listed:
            return f"hermes: watchdog cron already registered ({CRON_NAME})"
        subprocess.run(
            [
                "hermes", "cron", "create", "0 9 * * *",
                "--name", CRON_NAME,
                "--script", "agent-fix-watchdog.py",
                "--no-agent",
                "--deliver", "local",
            ],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return f"hermes: watchdog cron registered (daily '0 9 * * *', deliver=local; change with: hermes cron edit {CRON_NAME} --deliver telegram)"
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return f"hermes: SKIP — could not register cron ({e})"


def _hermes_uninstall() -> str:
    msgs = []
    wd = _hermes_scripts_dir() / "agent-fix-watchdog.py"
    if wd.exists():
        wd.unlink()
        msgs.append("watchdog script removed")
    try:
        listed = subprocess.run(
            ["hermes", "cron", "list"], capture_output=True, text=True, timeout=30
        ).stdout
        if CRON_NAME in listed:
            subprocess.run(
                ["hermes", "cron", "remove", CRON_NAME],
                check=True, capture_output=True, text=True, timeout=60,
            )
            msgs.append("cron job removed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return f"hermes: {'; '.join(msgs) if msgs else 'nothing to remove'}"


# ---------------------------------------------------------------- status


def _claude_status() -> str:
    path = _claude_settings()
    if not path.exists():
        return "not registered (no settings.json)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown (settings.json unparsable)"
    rules = (data.get("hooks") or {}).get("SessionStart") or []
    cmd = _fix_cmd()
    for r in rules:
        if isinstance(r, dict) and any(
            h.get("command") == cmd for h in r.get("hooks", []) if isinstance(h, dict)
        ):
            return "registered (SessionStart)"
    return "not registered"


def _codex_status() -> str:
    path = _codex_config()
    if not path.exists():
        return "not registered (no config.toml)"
    return "registered ([hooks] session_start)" if _codex_hook_present(path.read_text(encoding="utf-8")) else "not registered"


def _opencode_status() -> str:
    f = _opencode_plugin_dir() / PLUGIN_NAME
    return f"registered ({f.name})" if f.exists() else "not registered"


def _hermes_status() -> str:
    try:
        listed = subprocess.run(
            ["hermes", "cron", "list"], capture_output=True, text=True, timeout=30
        ).stdout
        return f"registered ({CRON_NAME})" if CRON_NAME in listed else "not registered"
    except (FileNotFoundError, subprocess.SubprocessError):
        return "not registered (hermes CLI unavailable)"


# ---------------------------------------------------------------- CLI


HOOKS: Dict[str, Any] = {
    "claude-code": {"install": _claude_install, "uninstall": _claude_uninstall, "status": _claude_status},
    "codex": {"install": _codex_install, "uninstall": _codex_uninstall, "status": _codex_status},
    "opencode": {"install": _opencode_install, "uninstall": _opencode_uninstall, "status": _opencode_status},
    "hermes": {"install": _hermes_install, "uninstall": _hermes_uninstall, "status": _hermes_status},
}

# agents with no startup-hook mechanism (AGENTS.md instruction only)
INSTRUCTION_ONLY = {
    "kimi-code": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "pi": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "zcode": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "cursor": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "gemini": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "aider": "no startup hook — AGENTS.md instruction covers on-demand repair",
    "qwen-code": "no startup hook — AGENTS.md instruction covers on-demand repair",
}


def _which_agents(args: argparse.Namespace) -> List[str]:
    if args.agent:
        if args.agent not in HOOKS:
            print(f"error: no startup hook support for '{args.agent}' (instruction-only agents: {', '.join(INSTRUCTION_ONLY)})", file=sys.stderr)
            sys.exit(2)
        return [args.agent]
    installed = {a.get("id") for a in _detect_agents()}
    return [aid for aid in HOOKS if aid in installed]


def cmd_install(args: argparse.Namespace) -> int:
    print("agent-fix self-heal: registering startup hooks\n")
    for aid in _which_agents(args):
        print("  " + HOOKS[aid]["install"]())
    print("\nDone. Verify with: python scripts/heal_hooks.py status")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    print("agent-fix self-heal: removing startup hooks\n")
    for aid in _which_agents(args):
        print("  " + HOOKS[aid]["uninstall"]())
    print("\nDone.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    agents = _detect_agents()
    by_id = {a.get("id"): a for a in agents}
    print("agent-fix self-heal: startup hook status\n")
    for aid, impl in HOOKS.items():
        name = (by_id.get(aid) or {}).get("name", aid)
        print(f"  {name:<16} {impl['status']()}")
    print()
    for aid, note in INSTRUCTION_ONLY.items():
        if aid in by_id:
            name = by_id[aid].get("name", aid)
            print(f"  {name:<16} {note}")
    print()
    print("  Run 'fix selfheal' directly to test the check+fix pipeline.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="heal_hooks", description="Register/remove agent-fix self-heal startup hooks")
    sub = parser.add_subparsers(dest="command", required=True)
    p_install = sub.add_parser("install", help="register startup hooks for installed agents")
    p_install.add_argument("--agent", help="only this agent id (claude-code|codex|opencode|hermes)")
    p_uninstall = sub.add_parser("uninstall", help="remove agent-fix startup hooks")
    p_uninstall.add_argument("--agent", help="only this agent id")
    sub.add_parser("status", help="show what is registered")
    args = parser.parse_args(argv)
    if args.command == "install":
        return cmd_install(args)
    if args.command == "uninstall":
        return cmd_uninstall(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
