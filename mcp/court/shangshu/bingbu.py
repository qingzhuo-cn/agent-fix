#!/usr/bin/env python3
"""兵部 · Ministry of War — 治防: the frontier defense (network).

Before any battle (or any agent that talks to a remote API), the frontier must
be open. This ministry patrols the passes: TCP connectivity + latency to every
endpoint the agents rely on (net_diagnose).

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

from typing import Any, Dict

MINISTRY = {
    "id": "bingbu",
    "name": "兵部",
    "en": "Ministry of War",
    "motto": "治防 · the frontier defense (network)",
}


def net_diagnose(timeout: float = 5.0) -> str:
    """Check TCP connectivity + latency to every agent's API endpoint.

    Backed by the shared scripts/netcheck.py engine — the same checks the CLI
    runs for the `net-connectivity` catalog issue.
    """
    import netcheck  # scripts/ is on sys.path (court/shangshu/common.py inserts it)

    return netcheck.format_report(timeout=timeout)


TOOLS: Dict[str, Dict[str, Any]] = {
    "net_diagnose": {
        "description": "Check TCP connectivity + latency to every agent's API endpoint (anthropic/openai/deepseek/moonshot/google/zhipu/alibaba/npm) and show proxy env. Use when an agent 'suddenly stopped working' or for network diagnosis.",
        "args": {"timeout": {"type": "number", "description": "connect timeout seconds (default 5)"}},
        "fn": lambda a: net_diagnose(timeout=float(a.get("timeout", 5))),
    },
}
