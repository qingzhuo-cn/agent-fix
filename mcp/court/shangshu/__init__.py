#!/usr/bin/env python3
"""尚书省 · Department of State Affairs — 执行: the six ministries.

Assembles the six ministries (吏户礼兵刑工) into one ordered list. Each ministry
module exposes:
    MINISTRY = {"id", "name", "en", "motto"}
    TOOLS    = {tool_name: {"description", "args", "fn"}}

Adding a ministry = add one module here and append it to MINISTRIES.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import bingbu, gongbu, hubu, libu_personnel, libu_rites, xingbu

# 吏 户 礼 兵 刑 工 — the traditional order of the six ministries
MINISTRIES: List[Any] = [
    libu_personnel,  # 吏部 — the officials (agents)
    hubu,            # 户部 — the registers (configs)
    libu_rites,      # 礼部 — the protocol (providers)
    bingbu,          # 兵部 — the defense (network)
    xingbu,          # 刑部 — the investigation (diagnosis)
    gongbu,          # 工部 — the works (repair)
]


def merge_tools() -> Dict[str, Dict[str, Any]]:
    """Merge every ministry's TOOLS into one registry, tagging each tool with its ministry."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in MINISTRIES:
        for name, spec in m.TOOLS.items():
            out[name] = {**spec, "ministry": m.MINISTRY}
    return out
