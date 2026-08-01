#!/usr/bin/env python3
"""中书省 · Central Secretariat — 出令: the official registry (政令).

This department drafts and maintains the policy table: every tool the court
offers, its schema, and which ministry executes it. Adding a new tool means
writing its function in a ministry module (尚书省) and it is automatically
promulgated here — one policy entry per tool. It also answers
`court_status`, the organizational chart of the 三省六部.

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .shangshu import MINISTRIES, merge_tools

# 中书省's own tools (the registry itself is its domain)
COURT_TOOLS: Dict[str, Dict[str, Any]] = {
    "court_status": {
        "description": "Show the agent-fix MCP organizational chart (三省六部 / Three Departments & Six Ministries): which department registers, reviews and executes tools, and which ministry owns which tool. Use to discover the toolbox structure.",
        "args": {},
        "fn": lambda a: court_status(),
    },
}


def registry() -> Dict[str, Dict[str, Any]]:
    """The official registry: every tool in the court, tagged with its ministry."""
    return {**COURT_TOOLS, **merge_tools()}


def tool_defs() -> List[Dict[str, Any]]:
    """MCP `tools/list` schema, generated from the registry."""
    defs = []
    for name, t in registry().items():
        defs.append(
            {
                "name": name,
                "description": t["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": t["args"],
                },
            }
        )
    return defs


def court_status() -> str:
    """The organizational chart: departments, ministries, and their tools."""
    reg = registry()
    by_ministry: Dict[str, List[str]] = {}
    for name, spec in reg.items():
        key = spec.get("ministry", {}).get("name", "?")
        by_ministry.setdefault(key, []).append(name)

    lines = [
        "AGENT-FIX MCP — 三省六部 (Three Departments & Six Ministries)",
        "",
        "三省 (Three Departments):",
        "  中书省 Zhongshu  (registry & edicts) — registers every tool, answers court_status",
        "  门下省 Mensheng  (review gate)        — validates every call, vetoes bad ones",
        "  尚书省 Shangshu  (execution)          — the six ministries below",
        "",
        f"六部 (Six Ministries) — {len(reg)} tools in service:",
    ]
    for m in MINISTRIES:
        meta = m.MINISTRY
        tools = ", ".join(sorted(by_ministry.get(meta["name"], [])))
        lines.append(f"  {meta['name']} {meta['en']:<24} {meta['motto']}")
        lines.append(f"      {tools}")
    lines.append("")
    lines.append("Adding a tool: write it in a ministry module under court/shangshu/ and it is")
    lines.append("automatically registered here. See mcp/README.md for the full protocol.")
    return "\n".join(lines)
