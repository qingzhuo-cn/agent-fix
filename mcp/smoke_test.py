#!/usr/bin/env python3
"""agent-fix MCP regression harness.

Spawns mcp/server.py over stdio, runs the MCP handshake, and exercises EVERY
registered tool through the wire protocol — asserting each returns a well-formed
result. Zero dependencies, no MCP client needed.

Usage:
    python mcp/smoke_test.py            # full pass (incl. slow network tools)
    python mcp/smoke_test.py --quick    # skip slow tools (doctor/net/versions/self_heal)

Exit code 0 = all passed, 1 = any failure. CI-friendly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = Path(__file__).resolve().parent / "server.py"

# tool -> (args, expect_isError)
# Safe invocations only: mutating tools are called in dry-run/list/error mode.
FAST_CALLS: dict = {
    "agents": ({}, False),
    "check": ({"issue_id": "node-version-too-old"}, False),
    "apply": ({"issue_id": "no-such-issue"}, False),  # unknown id -> text, no mutation
    "info": ({"issue_id": "provider-config"}, False),
    "logs": ({"lines": 5}, False),
    "audit": ({"depth": 1}, False),
    "backup": ({}, False),
    "restore": ({}, False),  # list mode, no confirm
    "provider": ({"provider": "ollama"}, False),  # local provider, no key needed
    "hooks": ({"action": "status"}, False),
}
SLOW_CALLS: dict = {
    "doctor": ({}, False),
    "net": ({"timeout": 3}, False),
    "versions": ({}, False),
    "self_heal": ({"apply": False}, False),  # diagnose-only, no mutation
}


def run_mcp(messages: list, timeout: int = 120) -> list:
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"server exited {proc.returncode}: {proc.stderr[:500]}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def exercise(calls: dict, timeout: int = 120) -> int:
    """Run one batch of tools through the gate; returns number of failures."""
    failed = 0
    msgs = []
    for i, (name, (args, _)) in enumerate(calls.items(), start=10):
        msgs.append(
            {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )
    results = {r["id"]: r for r in run_mcp(msgs, timeout=timeout)}

    for i, (name, (args, expect_err)) in enumerate(calls.items(), start=10):
        r = results.get(i)
        if r is None or "result" not in r:
            print(f"FAIL: {name}: no result ({r})")
            failed += 1
            continue
        res = r["result"]
        is_err = bool(res.get("isError"))
        text = (res.get("content") or [{}])[0].get("text", "")
        if is_err != expect_err:
            print(f"FAIL: {name}: isError={is_err} (expected {expect_err}) :: {text[:200]}")
            failed += 1
        elif not text.strip():
            print(f"FAIL: {name}: empty output")
            failed += 1
        else:
            print(f"  ok  {name}")
    return failed


def main() -> int:
    quick = "--quick" in sys.argv

    # 1) handshake + tool list; a notification must NOT be answered
    resp = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    assert resp[0]["result"]["serverInfo"]["name"] == "agent-fix", resp[0]
    assert len(resp) == 2, f"notification got a response (must stay silent): {resp}"
    tools = {t["name"] for t in resp[1]["result"]["tools"]}
    expected = set(FAST_CALLS) | set(SLOW_CALLS)
    missing = expected - tools
    if missing:
        print(f"FAIL: tools/list missing {sorted(missing)}")
        return 1
    print(f"handshake OK — {len(tools)} tools registered (expected {len(expected)})")

    # 2) every tool through the gate (fast batch first)
    failed = exercise(FAST_CALLS, timeout=120)

    # 3) slow / network tools (skipped with --quick)
    if not quick:
        print("-- slow tools --")
        failed += exercise(SLOW_CALLS, timeout=420)
    else:
        print("-- skipped slow tools (--quick) --")

    # 4) the gate must veto garbage + unknown tools + bad arg types
    bad = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 90, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 91, "method": "tools/call", "params": {"name": "audit", "arguments": {"depth": "abc"}}},
            {"jsonrpc": "2.0", "id": 92, "method": "tools/call", "params": {"name": "self_heal", "arguments": {"apply": "maybe"}}},
        ]
    )
    for r in bad:
        assert r["result"]["isError"] is True, f"gate did not veto: {r}"
    print("  ok  gate vetoes unknown tool + bad arg types")

    if failed:
        print(f"\n{failed} tool(s) FAILED")
        return 1
    print("\nALL TOOLS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
