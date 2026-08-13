#!/usr/bin/env python3
"""agent-fix MCP server — 朝廷 (the Imperial Court).

Exposes the whole agent-fix toolbox to ANY MCP-capable agent (Claude Code,
OpenCode, Cursor, ZCode, Codex, ...) as native tools. Implements the Model
Context Protocol stdio transport (newline-delimited JSON-RPC 2.0) with ZERO
dependencies — pure Python 3.8+ stdlib.

Governance (三省六部 — see mcp/court/ and mcp/README.md):
    中书省 Zhongshu  — the registry: every tool, its schema, its ministry
    门下省 Mensheng  — the review gate: validates every call (封驳), wraps errors
    尚书省 Shangshu  — the six ministries that execute (吏户礼兵刑工)

This file is deliberately thin: it speaks JSON-RPC and delegates every
tools/call to the court. Tool logic lives in mcp/court/shangshu/<ministry>.py.

Run:  python mcp/server.py            (stdio MCP server)
Test: python mcp/smoke_test.py        (regression harness, no client needed)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from court import call_tool, tool_defs

VERSION = "1.7.0"
PROTOCOL = "2024-11-05"


def _handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    if method == "initialize":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        requested = params.get("protocolVersion", PROTOCOL)
        if not isinstance(requested, str) or not requested.startswith(("2024", "2025")):
            requested = PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agent-fix", "version": VERSION},
            },
        }
    if method == "notifications/initialized":
        return None  # no response to notifications
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": tool_defs()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        result = call_tool(params.get("name", ""), args)
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            resp = _handle(msg)
        except Exception as e:  # noqa: BLE001 — never let a malformed message kill the server
            resp = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"internal error: {e}"},
            }
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
