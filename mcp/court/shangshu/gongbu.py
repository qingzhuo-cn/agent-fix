#!/usr/bin/env python3
"""工部 · Ministry of Works — 治工: carry out the repairs (execution).

Where 刑部 investigates, this ministry rebuilds: it applies the documented
fixes (fix_apply), maintains the self-healing infrastructure (self_heal), and
installs the watchdog hooks that keep agents repairing themselves (heal_hooks).

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .common import _detect_agents

MINISTRY = {
    "id": "gongbu",
    "name": "工部",
    "en": "Ministry of Works",
    "motto": "治工 · carry out the repairs (execution)",
}


def _fmt_apply(issue_id: str, yes: bool = True) -> str:
    import fix

    cat = fix.load_catalog()
    issue = fix.find_issue(cat, issue_id)
    if not issue:
        return f"unknown issue: {issue_id}"
    agents = fix.detect_agents(cat)
    lines = [f"== {issue['id']}: {issue['title']}", ""]
    out = fix.apply_issue(issue, yes=yes, quiet=True, agents=agents)
    for f in out["fixed"]:
        lines.append(f"  [FIXED] {f['name']}")
    for s in out["skipped"]:
        lines.append(f"  [SKIP]  {s['name']} ({s['reason']})")
    lines.append("")
    lines.append("=> verified OK" if out["verified"] else "=> not fully verified (see doc)")
    return "\n".join(lines)


def self_heal() -> str:
    """Run the full check + auto-fix pipeline once (same engine as startup hooks)."""
    import fix

    r = fix.run_selfheal(fix.load_catalog())
    lines = ["SELF-HEAL"]
    if r["timed_out"]:
        lines.append("  timed out — run fix_doctor manually")
    elif r["fixed"] or r["unfixed"]:
        if r["fixed"]:
            lines.append("  fixed: " + ", ".join(r["fixed"]))
        if r["unfixed"]:
            lines.append("  still broken: " + ", ".join(r["unfixed"]))
    else:
        lines.append("  all healthy")
    return "\n".join(lines)


def heal_hooks(action: str = "status", agent_id: Optional[str] = None) -> str:
    """Manage agent-fix startup hooks (install/uninstall/status) for one or all agents."""
    import heal_hooks as hh

    if agent_id and agent_id not in hh.HOOKS:
        return f"error: no startup-hook support for '{agent_id}' (instruction-only: {', '.join(hh.INSTRUCTION_ONLY)})"
    if agent_id:
        ids = [agent_id]
    else:
        installed = {a.get("id") for a in _detect_agents()}
        ids = [aid for aid in hh.HOOKS if aid in installed]
    action = (action or "status").lower()
    if not ids:
        return f"no hook-capable agents installed (supports: {', '.join(hh.HOOKS)})"
    if action == "install":
        return "\n".join(f"  {hh.HOOKS[a]['install']()}" for a in ids)
    if action == "uninstall":
        return "\n".join(f"  {hh.HOOKS[a]['uninstall']()}" for a in ids)
    return "\n".join(f"  {a}: {hh.HOOKS[a]['status']()}" for a in ids)


TOOLS: Dict[str, Dict[str, Any]] = {
    "fix_apply": {
        "description": "Apply the fixes for one issue (auto-yes), then verify. Use after fix_check shows a FAIL, or directly when the user reports a known symptom.",
        "args": {"issue_id": {"type": "string", "description": "issue id to fix"}},
        "fn": lambda a: _fmt_apply(a.get("issue_id", "")),
    },
    "self_heal": {
        "description": "Run the full check + auto-fix pipeline once (same engine as the startup hooks): every catalog check, auto-apply fixes for anything broken, report concise results. Use when the user reports any agent symptom, or as a periodic health pass.",
        "args": {},
        "fn": lambda a: self_heal(),
    },
    "heal_hooks": {
        "description": "Manage agent-fix self-heal startup hooks. action: status (default) | install | uninstall. agent_id optional (claude-code|codex|opencode|hermes; omit = all installed). install registers the startup hook so the agent auto-checks+repairs on every launch; uninstall removes it; status shows what is registered.",
        "args": {
            "action": {"type": "string", "description": "status (default), install, or uninstall"},
            "agent_id": {"type": "string", "description": "claude-code|codex|opencode|hermes; omit for all installed agents"},
        },
        "fn": lambda a: heal_hooks(action=a.get("action", "status"), agent_id=a.get("agent_id")),
    },
}
