#!/usr/bin/env python3
"""DEPRECATED compatibility shim — the MCP branch tools have moved into the
三省六部 court (mcp/court/shangshu/<ministry>.py).

Kept so any external code that did `from mcp.branches import BRANCH_TOOLS` or
`from mcp.branches import version_check` keeps working. New code should import
from the court:  `from court import registry, call_tool, tool_defs`.
"""

from __future__ import annotations

from typing import Any, Dict

from court.shangshu import MINISTRIES

# Same merged table server.py used to consume (now with a "ministry" tag).
BRANCH_TOOLS: Dict[str, Dict[str, Any]] = {
    name: spec for m in MINISTRIES for name, spec in m.TOOLS.items()
}

# Re-export the branch functions for direct importers.
from court.shangshu.bingbu import net_diagnose  # noqa: E402
from court.shangshu.gongbu import heal_hooks, self_heal  # noqa: E402
from court.shangshu.hubu import backup_configs, config_audit, restore_configs  # noqa: E402
from court.shangshu.libu_personnel import version_check, watchdog_status  # noqa: E402
from court.shangshu.libu_rites import deepseek_setup, provider_setup  # noqa: E402
from court.shangshu.xingbu import log_triage  # noqa: E402
