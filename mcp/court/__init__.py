#!/usr/bin/env python3
"""三省六部 — the agent-fix MCP court.

    server.py (朝廷)  ->  mensheng (门下省 · review gate)  ->  zhongshu (中书省 · registry)
                                                             ->  shangshu (尚书省 · six ministries)

Public facade for the rest of the server:
    tool_defs()   -> MCP tools/list schema
    call_tool()   -> run one tool through the review gate (MCP tools/call result)
    registry()    -> the official tool registry (name -> spec, tagged with ministry)
    court_status()-> the organizational chart (also exposed as an MCP tool)
"""

from __future__ import annotations

from .mensheng import call_tool
from .zhongshu import court_status, registry, tool_defs

__all__ = ["call_tool", "court_status", "registry", "tool_defs"]
