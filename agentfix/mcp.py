#!/usr/bin/env python3
"""agentfix.mcp — zero-dependency MCP stdio server.

Exposes the agent-fix engine to ANY MCP-capable agent (Claude Code, OpenCode,
Cursor, ZCode, Codex, ...) as native tools. Implements the Model Context
Protocol stdio transport (newline-delimited JSON-RPC 2.0) in pure Python 3.8+
stdlib — a repair tool must run when the environment is broken, so it carries
no dependencies.

Design:
  - Generic VERB tools; the catalog's issue ids are the arguments. Adding an
    issue to catalog.json automatically extends check/apply/info — no new tool
    code (this replaces the v1 court/ ministry tree).
  - Every call passes a small review gate: unknown tools are refused, argument
    types are coerced to the declared schema, any failure becomes a clean
    isError result so the client never sees a crash.
  - Notifications (messages without an "id") are never answered.
  - Mutating tools default to dry-run: `apply` and `self_heal` change nothing
    until confirm/apply is true; `restore` has always required confirm.

Run:  python mcp/server.py            (stdio MCP server)
Test: python mcp/smoke_test.py        (regression harness, no client needed)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from agentfix import __version__, engine, hooks
from agentfix import report as rep

PROTOCOL = "2024-11-05"

TOOLS: Dict[str, Dict[str, Any]] = {
    "doctor": {
        "description": "Run every catalog check, including per-agent binary checks for all detected agents. Returns a health report. Use to answer 'is anything broken?'.",
        "args": {},
        "fn": lambda a: engine.doctor_text(),
    },
    "check": {
        "description": "Run diagnostics for one issue id. Issue ids (see also `info`): agent-broken-generic, npm-postinstall-skipped, gui-path-blind, node-version-too-old, npm-registry-mirror, agent-auth-broken, provider-config, net-connectivity, opencode-mcp-schema, deepseek-harness-broken.",
        "args": {"issue_id": {"type": "string", "description": "issue id to check"}},
        "fn": lambda a: engine.check_text(a.get("issue_id", "")),
    },
    "apply": {
        "description": "Apply the fixes for one issue id, then verify. Default is a DRY RUN listing the fix commands; pass confirm=true to actually execute. Use after `check` shows a FAIL, or directly when the user reports a known symptom.",
        "args": {
            "issue_id": {"type": "string", "description": "issue id to fix"},
            "confirm": {"type": "boolean", "description": "actually run the fixes (default false = dry-run)"},
        },
        "fn": lambda a: engine.apply_text(a.get("issue_id", ""), confirm=bool(a.get("confirm", False))),
    },
    "info": {
        "description": "Print the knowledge-base doc for an issue id (symptoms, root cause, manual fix, verification).",
        "args": {"issue_id": {"type": "string", "description": "issue id"}},
        "fn": lambda a: engine.info_text(a.get("issue_id", "")),
    },
    "agents": {
        "description": "List the agent registry and which agents are installed on this machine. Use first when diagnosing any agent problem.",
        "args": {},
        "fn": lambda a: engine.agents_text(),
    },
    "versions": {
        "description": "Compare installed vs latest version for every detected agent (npm agents query the registry; GUI desktop apps are never probed and get update hints). Use before/after upgrades.",
        "args": {},
        "fn": lambda a: engine.versions_text(),
    },
    "net": {
        "description": "Check TCP connectivity + latency to every agent's API endpoint (anthropic/openai/deepseek/moonshot/google/zhipu/alibaba/github/npm) and show proxy env. Use when an agent 'suddenly stopped working' or for network diagnosis.",
        "args": {"timeout": {"type": "number", "description": "connect timeout seconds (default 5)"}},
        "fn": lambda a: engine.net_text(timeout=float(a.get("timeout", 5))),
    },
    "logs": {
        "description": "Scan agent log locations for recent ERROR/WARN/Traceback lines. Use when an agent fails without a clear message.",
        "args": {
            "agent_id": {"type": "string", "description": "restrict to one agent id (e.g. kimi-code); omit for all"},
            "lines": {"type": "number", "description": "max matching lines per agent (default 30)"},
        },
        "fn": lambda a: engine.logs_text(agent_id=a.get("agent_id"), lines=int(a.get("lines", 30))),
    },
    "audit": {
        "description": "Scan agent config files for JSON parse errors and leaked API keys (masked). Use before committing configs or when an agent ignores its config.",
        "args": {"depth": {"type": "number", "description": "scan depth (default 3)"}},
        "fn": lambda a: engine.audit_text(depth=int(a.get("depth", 3))),
    },
    "backup": {
        "description": "Snapshot every detected agent's config dir into ~/.agent-fix-backups/<timestamp>.zip (excludes node_modules/sessions/logs). Use before any repair or upgrade.",
        "args": {},
        "fn": lambda a: engine.backup_text(),
    },
    "restore": {
        "description": "List config backups, or restore one. Pass backup='latest' or a filename AND confirm=true to actually restore (zip-slip guarded; restores only into currently-known agent config dirs).",
        "args": {
            "backup": {"type": "string", "description": "backup filename or 'latest'; omit to list"},
            "confirm": {"type": "boolean", "description": "must be true to actually restore"},
        },
        "fn": lambda a: engine.restore_text(backup=a.get("backup"), confirm=bool(a.get("confirm", False))),
    },
    "provider": {
        "description": "Generate per-agent config snippets for ANY provider (deepseek|openai|anthropic|google|moonshot|zhipu|qwen|openrouter|ollama|custom). Pass provider + api_key (optional base_url/model overrides). Key is MASKED in output by default; pass show_key=true to reveal it, or apply=true to write ~/.claude/settings.json.",
        "args": {
            "provider": {"type": "string", "description": "provider id: deepseek, openai, anthropic, google, moonshot, zhipu, qwen, openrouter, ollama, or custom"},
            "api_key": {"type": "string", "description": "API key (empty only for ollama/local)"},
            "base_url": {"type": "string", "description": "override base URL (optional; defaults from the provider table)"},
            "model": {"type": "string", "description": "override model name (optional)"},
            "apply": {"type": "boolean", "description": "also write Claude settings.json (default false)"},
            "show_key": {"type": "boolean", "description": "print the full key in snippets (default false — masked)"},
        },
        "fn": lambda a: engine.provider_text(
            provider=a.get("provider", "deepseek"),
            api_key=a.get("api_key", ""),
            base_url=a.get("base_url", ""),
            model=a.get("model", ""),
            apply=bool(a.get("apply", False)),
            show_key=bool(a.get("show_key", False)),
        ),
    },
    "hooks": {
        "description": "Manage agent-fix self-heal startup hooks. action: status (default) | install | uninstall. agent_id optional (omit = all installed hook-capable agents). install registers the startup hook so the agent auto-checks+repairs on every launch; uninstall removes it; status shows what is registered.",
        "args": {
            "action": {"type": "string", "description": "status (default), install, or uninstall"},
            "agent_id": {"type": "string", "description": "one agent id (e.g. claude-code); omit for all installed"},
        },
        "fn": lambda a: hooks.dispatch(a.get("action", "status"), a.get("agent_id")),
    },
    "self_heal": {
        "description": "Run the full check pipeline once (same engine as the startup hooks). Default apply=false = diagnose-only (reports broken issues, changes nothing); pass apply=true to auto-fix anything broken. Use when the user reports any agent symptom, or as a periodic health pass.",
        "args": {
            "apply": {"type": "boolean", "description": "auto-fix what is broken (default false = diagnose only)"},
        },
        "fn": lambda a: engine.selfheal_text(apply=bool(a.get("apply", False))),
    },
}


# ---------------------------------------------------------------- gate


class ReviewError(Exception):
    """A request was rejected at the gate (bad tool name or arguments)."""


def _coerce(value: Any, type_name: str, arg_name: str) -> Any:
    """Coerce one argument to its declared JSON-schema type; refuse if impossible."""
    if type_name == "boolean":
        if isinstance(value, bool):
            return value
        low = value.strip().lower() if isinstance(value, str) else None
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ReviewError(f"argument '{arg_name}' must be a boolean, got {value!r}")
    if type_name == "number":
        if isinstance(value, bool):  # bool is an int subclass — reject it
            raise ReviewError(f"argument '{arg_name}' must be a number, got {value!r}")
        if isinstance(value, (int, float)):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ReviewError(f"argument '{arg_name}' must be a number, got {value!r}")
    # string (default) — accept anything, stringify
    return value if isinstance(value, str) else str(value)


def review(arguments: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize arguments against the tool's declared schema."""
    schema = spec.get("args") or {}
    clean: Dict[str, Any] = {}
    for arg_name, arg_spec in schema.items():
        if arg_name not in arguments:
            continue
        clean[arg_name] = _coerce(arguments[arg_name], arg_spec.get("type", "string"), arg_name)
    return clean


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Gate pipeline: review -> execute -> wrap as an MCP tools/call result."""
    spec = TOOLS.get(name)
    if not spec:
        return {
            "content": [{"type": "text", "text": f"unknown tool: {name} (registered: {', '.join(sorted(TOOLS))})"}],
            "isError": True,
        }
    try:
        clean = review(arguments or {}, spec)
        text = str(spec["fn"](clean))
        return {"content": [{"type": "text", "text": rep.mask_secrets(text)}]}
    except ReviewError as e:
        return {"content": [{"type": "text", "text": f"rejected: {e}"}], "isError": True}
    except Exception as e:  # noqa: BLE001 — report any failure to the client
        return {"content": [{"type": "text", "text": f"tool error: {e}"}], "isError": True}


def tools_list() -> List[Dict[str, Any]]:
    """MCP `tools/list` schema, generated from the registry."""
    return [
        {
            "name": name,
            "description": t["description"],
            "inputSchema": {"type": "object", "properties": t["args"]},
        }
        for name, t in TOOLS.items()
    ]


# ---------------------------------------------------------------- protocol


def _handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Notifications (no "id") are never answered — responding to one makes
    # well-behaved clients log spurious errors.
    if "id" not in msg:
        return None
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
                "serverInfo": {"name": "agent-fix", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": tools_list()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        return {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": call_tool(params.get("name", ""), args),
        }
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
