#!/usr/bin/env python3
"""agent-fix MCP regression harness for explicit-target tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = Path(__file__).resolve().parent / "server.py"

CALLS: dict = {
    "agents": ({}, False),
    "check": ({"issue_id": "node-version-too-old", "agent_id": "__not_installed__"}, True),
    "apply": ({"issue_id": "node-version-too-old", "agent_id": "__not_installed__"}, True),
    "info": ({"issue_id": "provider-config"}, False),
    "versions": ({"agent_id": "__not_installed__"}, True),
    "net": ({"host": "bad host", "timeout": 0.1}, True),
    "logs": ({"agent_id": "__not_installed__", "lines": 5}, True),
    "audit": ({"agent_id": "__not_installed__", "depth": 1}, True),
    "backup": ({"agent_id": "__not_installed__"}, True),
    "restore": ({}, False),
    "provider": ({"provider": "ollama", "agent_id": "__not_installed__"}, True),
    "hooks": ({"action": "status", "agent_id": "zcode"}, False),
}


def run_mcp(messages: list, timeout: int = 120, env: dict = None) -> list:
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"server exited {proc.returncode}: {proc.stderr[:500]}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def handshake() -> list:
    return [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-fix-mcp-smoke-") as td:
        home = Path(td)
        bin_dir = home / "bin"
        bin_dir.mkdir()
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "KIMI_CODE_HOME": "",
            "MINIMAX_DATA_DIR": "",
            "MAVIS_DATA_DIR": "",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "PATH": str(bin_dir),
        })
        resp = run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ], env=env)
        assert resp[0]["result"]["serverInfo"]["name"] == "agent-fix", resp[0]
        assert len(resp) == 2, f"notification got a response: {resp}"
        tools = {t["name"]: t for t in resp[1]["result"]["tools"]}
        assert set(tools) == set(CALLS), f"tool registry drift: {set(tools) ^ set(CALLS)}"
        assert "agent_id" in tools["check"]["inputSchema"]["properties"]
        assert "agent_id" in tools["apply"]["inputSchema"]["properties"]
        print(f"handshake OK - {len(tools)} explicit-target tools registered")

        messages = [
            {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
            for i, (name, (args, _)) in enumerate(CALLS.items(), start=10)
        ]
        results = {r["id"]: r for r in run_mcp(handshake() + messages, env=env)}
        failed = 0
        for i, (name, (_, expected_error)) in enumerate(CALLS.items(), start=10):
            response = results.get(i, {})
            result = response.get("result", {})
            actual_error = bool(result.get("isError"))
            text = (result.get("content") or [{}])[0].get("text", "")
            if "result" not in response or actual_error != expected_error or not text.strip():
                print(f"FAIL: {name}: isError={actual_error} expected={expected_error} :: {text[:200]}")
                failed += 1
            else:
                print(f"  ok  {name}")

        rejected_responses = run_mcp(handshake() + [
            {"jsonrpc": "2.0", "id": 90, "method": "tools/call", "params": {"name": "doctor", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 91, "method": "tools/call", "params": {"name": "self_heal", "arguments": {}}},
        ], env=env)
        rejected = [r for r in rejected_responses if r.get("id") in {90, 91}]
        assert all(r.get("error", {}).get("code") == -32602 for r in rejected), rejected
        print("  ok  removed bulk tools are protocol-rejected")
        if failed:
            print(f"\n{failed} tool(s) FAILED")
            return 1
        print("\nALL TARGETED TOOLS PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
