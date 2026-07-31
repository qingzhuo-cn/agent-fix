#!/usr/bin/env python3
"""
agent-fix MCP server — expose the whole agent-fix toolbox to ANY MCP-capable
agent (Claude Code, OpenCode, Cursor, ZCode, Codex, ...).

Implements the Model Context Protocol stdio transport (newline-delimited
JSON-RPC 2.0) with ZERO dependencies — pure Python 3.8+ stdlib.

Tools:
  Core (inspect/fix, from catalog.json):
    fix_agents   list detected agents
    fix_doctor   run all checks (incl. per-agent binary checks)
    fix_check    run checks for one issue
    fix_apply    apply fixes for one issue, then verify
    fix_info     print the doc for an issue
  Branch skills (see mcp/branches.py):
    net_diagnose    endpoint connectivity + latency + proxy
    version_check   installed vs latest versions
    config_audit    config parse errors + leaked keys
    log_triage      recent ERROR/WARN lines from agent logs
    backup_configs  snapshot agent configs
    restore_configs list/restore config backups
    deepseek_setup  per-agent DeepSeek config snippets

Run:  python mcp/server.py            (stdio MCP server)
Test: printf '{"jsonrpc":"2.0","id":1,"method":"initialize",...}\n...' | python mcp/server.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "1.2.0"
PROTOCOL = "2024-11-05"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fix  # noqa: E402  (the catalog engine)
import branches  # noqa: E402  (the branch skills)


# ---------------------------------------------------------------- core tools


def _fmt_agents() -> str:
    cat = fix.load_catalog()
    agents = fix.detect_agents(cat)
    lines = [f"agent registry: {len(cat.get('agents', {}))} known | detected: {len(agents)}", ""]
    for a in agents:
        lines.append(f"  {a.get('name','?'):<26} bin={a.get('bin',['?'])[0]:<12} exe={a.get('exe') or '(config-dir only)'}")
    return "\n".join(lines)


def _fmt_doctor() -> str:
    cat = fix.load_catalog()
    agents = fix.detect_agents(cat)
    lines = []
    broken = []
    for issue in cat["issues"]:
        state = fix.check_issue(issue, quiet=True, agents=agents)
        lines.append(f"== {issue['id']}: {issue['title']}")
        if state["broken"]:
            for r in state["results"]:
                if r["status"] == "FAIL":
                    lines.append(f"    [FAIL] {r.get('agent','') and '['+r['agent']+'] ' or ''}{r['name']}: {r['detail'][:200]}")
            broken.append(issue["id"])
        else:
            lines.append("   -> healthy")
    lines.append("")
    lines.append("=> " + ("BROKEN: " + ", ".join(broken) if broken else "all healthy"))
    return "\n".join(lines)


def _fmt_check(issue_id: str) -> str:
    cat = fix.load_catalog()
    issue = fix.find_issue(cat, issue_id)
    if not issue:
        return f"unknown issue: {issue_id} (try fix_info / docs)"
    agents = fix.detect_agents(cat)
    state = fix.check_issue(issue, quiet=True, agents=agents)
    lines = [f"== {issue['id']}: {issue['title']}", ""]
    for r in state["results"]:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r["status"]]
        prefix = f"[{r['agent']}] " if r.get("agent") else ""
        lines.append(f"  {mark} {prefix}{r['name']}")
        if r["status"] == "FAIL" and r.get("detail"):
            lines.append(f"        {r['detail'][:300]}")
    lines.append("")
    lines.append("=> broken" if state["broken"] else "=> healthy")
    return "\n".join(lines)


def _fmt_apply(issue_id: str, yes: bool = True) -> str:
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


def _fmt_info(issue_id: str) -> str:
    cat = fix.load_catalog()
    issue = fix.find_issue(cat, issue_id)
    if not issue:
        return f"unknown issue: {issue_id}"
    doc = Path(__file__).resolve().parent.parent / "fixes" / Path(issue.get("doc", "")).name
    if doc.exists():
        return doc.read_text(encoding="utf-8")
    return f"doc missing for {issue_id}"


CORE_TOOLS: Dict[str, Dict[str, Any]] = {
    "fix_agents": {
        "description": "List the agent registry and which agents are installed on this machine. Use first when diagnosing any agent problem.",
        "args": {},
        "fn": lambda a: _fmt_agents(),
    },
    "fix_doctor": {
        "description": "Run every catalog check, including per-agent binary checks for all detected agents. Returns a health report. Use to answer 'is anything broken?'.",
        "args": {},
        "fn": lambda a: _fmt_doctor(),
    },
    "fix_check": {
        "description": "Run diagnostics for one issue id. ids: agent-broken-generic, npm-postinstall-skipped, gui-path-blind, node-version-too-old, npm-registry-mirror, agent-auth-broken, deepseek-provider.",
        "args": {"issue_id": {"type": "string", "description": "issue id to check"}},
        "fn": lambda a: _fmt_check(a.get("issue_id", "")),
    },
    "fix_apply": {
        "description": "Apply the fixes for one issue (auto-yes), then verify. Use after fix_check shows a FAIL, or directly when the user reports a known symptom.",
        "args": {"issue_id": {"type": "string", "description": "issue id to fix"}},
        "fn": lambda a: _fmt_apply(a.get("issue_id", "")),
    },
    "fix_info": {
        "description": "Print the knowledge-base doc for an issue id (symptoms, root cause, manual fix, verification).",
        "args": {"issue_id": {"type": "string", "description": "issue id"}},
        "fn": lambda a: _fmt_info(a.get("issue_id", "")),
    },
}


# ---------------------------------------------------------------- MCP core


def _tool_defs() -> List[Dict[str, Any]]:
    defs = []
    for name, t in {**CORE_TOOLS, **branches.BRANCH_TOOLS}.items():
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


def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    all_tools = {**CORE_TOOLS, **branches.BRANCH_TOOLS}
    tool = all_tools.get(name)
    if not tool:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        text = tool["fn"](arguments or {})
        return {"content": [{"type": "text", "text": str(text)}]}
    except Exception as e:  # noqa: BLE001 — report any failure to the client
        return {"content": [{"type": "text", "text": f"tool error: {e}"}], "isError": True}


def _handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion", PROTOCOL)
        return {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "protocolVersion": requested if requested.startswith(("2024", "2025")) else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agent-fix", "version": VERSION},
            },
        }
    if method == "notifications/initialized":
        return None  # no response to notifications
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": _tool_defs()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {})
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
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
