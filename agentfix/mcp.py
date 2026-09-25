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
  - Mutating tools default to dry-run. Bulk doctor/automatic-repair tools are not
    exposed; every operational tool requires one explicit agent_id or host.

Run:  python mcp/server.py            (stdio MCP server)
Test: python mcp/smoke_test.py        (regression harness, no client needed)
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Dict, List, Optional

from agentfix import __version__, engine, hooks
from agentfix import report as rep
from agentfix import result as operation_result

PROTOCOL = "2024-11-05"


def _hooks_tool(action: str, agent_id: Optional[str], confirm: bool = False) -> str:
    action = (action or "status").lower()
    if action == "uninstall" and not confirm:
        return f"dry run: would remove legacy startup hooks for {agent_id}; pass confirm=true to execute"
    text = hooks.dispatch(action, agent_id)
    if operation_result.status_of(text, "ok") == "error":
        raise RuntimeError(str(text))
    return str(text)


def _audit_tool(agent_id: str, depth: int) -> str:
    result = engine.audit_result(agent_id, depth=depth)
    text = engine.audit_text_from_result(result)
    if result.get("status") != "PASS":
        raise RuntimeError(text)
    return text


def _check_tool(issue_id: str, agent_id: str) -> str:
    result = engine.check_result(issue_id, agent_id)
    text = engine.format_check_result(result)
    state = result.get("state") or {}
    if state.get("broken") or state.get("status") in {"FAIL", "INCONCLUSIVE"}:
        raise RuntimeError(text)
    return text


def _apply_tool(issue_id: str, agent_id: str, confirm: bool) -> str:
    result = engine.apply_result(issue_id, agent_id, confirm=confirm)
    text = engine.format_apply_result(result)
    if confirm and not (result.get("outcome") or {}).get("verified", False):
        raise RuntimeError(text)
    return text


def _info_tool(issue_id: str) -> str:
    try:
        return engine.info_text(issue_id)
    except Exception as exc:
        raise RuntimeError(rep.mask_secrets(str(exc))) from exc


def _backup_tool(agent_id: str) -> str:
    text = engine.backup_text(agent_id=agent_id)
    if operation_result.status_of(text, "error") == "error":
        raise RuntimeError(rep.mask_secrets(str(text)))
    return str(text)


def _restore_tool(backup: Optional[str], agent_id: Optional[str], confirm: bool) -> str:
    text = engine.restore_text(backup=backup, agent_id=agent_id, confirm=confirm)
    if operation_result.status_of(text, "error") == "error":
        raise RuntimeError(rep.mask_secrets(str(text)))
    return str(text)


def _provider_tool(**kwargs: Any) -> str:
    text = engine.provider_text(**kwargs)
    if operation_result.status_of(text, "error") == "error":
        raise RuntimeError(rep.mask_secrets(str(text)))
    return str(text)


def _versions_tool(agent_id: str) -> str:
    result = engine.versions_result(agent_id)
    text = engine.versions_text_from_result(result)
    if result.get("status") != "PASS":
        raise RuntimeError(text)
    return text


def _net_tool(host: str, timeout: float) -> str:
    result = engine.net_result(timeout=timeout, host=host)
    text = engine.net_text_from_result(result, timeout=timeout)
    if not result.get("ok"):
        raise RuntimeError(text)
    return text


TOOLS: Dict[str, Dict[str, Any]] = {
    "check": {
        "description": "Run diagnostics for one issue id and one explicit target agent.",
        "args": {
            "issue_id": {"type": "string", "description": "issue id to check"},
            "agent_id": {"type": "string", "description": "target agent id; required"},
        },
        "fn": lambda a: _check_tool(a.get("issue_id", ""), a.get("agent_id", "")),
    },
    "apply": {
        "description": "Apply and verify one issue for one explicit target agent. Default is a DRY RUN; pass confirm=true to execute.",
        "args": {
            "issue_id": {"type": "string", "description": "issue id to fix"},
            "agent_id": {"type": "string", "description": "target agent id; required"},
            "confirm": {"type": "boolean", "description": "actually run the fixes (default false = dry-run)"},
        },
        "fn": lambda a: _apply_tool(
            a.get("issue_id", ""), a.get("agent_id", ""), bool(a.get("confirm", False))
        ),
    },
    "info": {
        "description": "Print the knowledge-base doc for an issue id.",
        "args": {"issue_id": {"type": "string", "description": "issue id"}},
        "fn": lambda a: _info_tool(a.get("issue_id", "")),
    },
    "agents": {
        "description": "List agents detected on this machine. This inventory is not a repair operation.",
        "args": {},
        "fn": lambda a: engine.agents_text(),
    },
    "versions": {
        "description": "Check the version of one explicit target agent.",
        "args": {"agent_id": {"type": "string", "description": "target agent id; required"}},
        "fn": lambda a: _versions_tool(a.get("agent_id", "")),
    },
    "net": {
        "description": "Check the explicitly requested API endpoint; pass host, or use the provider host.",
        "args": {
            "host": {"type": "string", "description": "hostname to check", "minLength": 1},
            "timeout": {"type": "number", "description": "connect timeout seconds (default 5)", "minimum": 0.1, "maximum": 120},
        },
        "fn": lambda a: _net_tool(a.get("host", ""), float(a.get("timeout", 5))),
    },
    "logs": {
        "description": "Scan one explicit agent's log locations for recent errors.",
        "args": {
            "agent_id": {"type": "string", "description": "target agent id; required", "minLength": 1},
            "lines": {"type": "number", "description": "max matching lines (default 30)", "minimum": 1, "maximum": 500},
        },
        "fn": lambda a: engine.logs_text(agent_id=a.get("agent_id", ""), lines=int(a.get("lines", 30))),
    },
    "audit": {
        "description": "Scan one explicit agent's config files for parse errors and leaked API keys.",
        "args": {
            "agent_id": {"type": "string", "description": "target agent id; required", "minLength": 1},
            "depth": {"type": "number", "description": "scan depth (default 3)", "minimum": 1, "maximum": 10},
        },
        "fn": lambda a: _audit_tool(a.get("agent_id", ""), int(a.get("depth", 3))),
    },
    "backup": {
        "description": "Snapshot one explicit agent's config directory.",
        "args": {"agent_id": {"type": "string", "description": "target agent id; required"}},
        "fn": lambda a: _backup_tool(a.get("agent_id", "")),
    },
    "restore": {
        "description": "List or restore a backup; restoring requires confirm=true.",
        "args": {
            "backup": {"type": "string", "description": "backup filename or latest; omit to list"},
            "agent_id": {"type": "string", "description": "target agent id; required when restoring"},
            "confirm": {"type": "boolean", "description": "must be true to restore"},
        },
        "fn": lambda a: _restore_tool(a.get("backup"), a.get("agent_id"), bool(a.get("confirm", False))),
    },
    "provider": {
        "description": "Generate a provider config snippet for one explicit target agent; apply writes Claude Code settings only.",
        "args": {
            "provider": {"type": "string", "description": "provider id"},
            "agent_id": {"type": "string", "description": "target agent id; required"},
            "api_key": {"type": "string", "description": "API key"},
            "base_url": {"type": "string", "description": "override base URL"},
            "model": {"type": "string", "description": "override model name"},
            "apply": {"type": "boolean", "description": "write Claude Code settings; other targets return manual steps (default false)"},
            "show_key": {"type": "boolean", "description": "deprecated compatibility flag; output is always masked"},
        },
        "fn": lambda a: _provider_tool(
            provider=a.get("provider", "deepseek"),
            agent_id=a.get("agent_id", ""),
            api_key=a.get("api_key", ""),
            base_url=a.get("base_url", ""),
            model=a.get("model", ""),
            apply=bool(a.get("apply", False)),
            show_key=bool(a.get("show_key", False)),
        ),
    },
    "hooks": {
        "description": "Inspect or remove legacy startup-hook files for one explicit agent; installation is disabled.",
        "args": {
            "action": {"type": "string", "description": "status, install, or uninstall", "enum": ["status", "install", "uninstall"]},
            "agent_id": {"type": "string", "description": "one agent id; required for install/uninstall"},
            "confirm": {"type": "boolean", "description": "must be true to execute uninstall (default false = dry-run)"},
        },
        "fn": lambda a: _hooks_tool(
            a.get("action", "status"), a.get("agent_id"), bool(a.get("confirm", False))
        ),
    },
}

_REQUIRED_ARGS = {
    "check": ("issue_id", "agent_id"),
    "apply": ("issue_id", "agent_id"),
    "info": ("issue_id",),
    "agents": (),
    "versions": ("agent_id",),
    "net": ("host",),
    "logs": ("agent_id",),
    "audit": ("agent_id",),
    "backup": ("agent_id",),
    "restore": (),
    "provider": ("provider", "agent_id"),
    "hooks": ("action", "agent_id"),
}


# ---------------------------------------------------------------- gate


class ReviewError(Exception):
    """A request was rejected at the gate (bad tool name or arguments)."""


class ProtocolState:
    """Per-process MCP initialization state."""

    def __init__(self) -> None:
        self.initialized = False
        self.ready = False


class ProtocolError(Exception):
    """A JSON-RPC/MCP request is structurally invalid."""

    def __init__(self, message: str, code: int = -32602):
        super().__init__(message)
        self.code = code


def _validate_tool_call(name: Any, arguments: Any) -> Dict[str, Any]:
    if not isinstance(name, str) or name not in TOOLS:
        raise ProtocolError("unknown tool")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ProtocolError("arguments must be an object")
    spec = TOOLS[name]
    try:
        clean = review(arguments, dict(spec, required=_REQUIRED_ARGS.get(name, ())))
    except ReviewError as exc:
        raise ProtocolError(str(exc)) from exc
    if name == "restore" and clean.get("confirm") and not clean.get("agent_id"):
        raise ProtocolError("agent_id is required when restore.confirm is true")
    if name == "hooks" and clean.get("action", "status") in {"install", "uninstall"}:
        if not clean.get("agent_id"):
            raise ProtocolError("agent_id is required for hooks install/uninstall")
        if clean.get("action") == "uninstall" and not clean.get("confirm"):
            raise ProtocolError("confirm=true is required for hooks uninstall")
    return clean


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
            if not math.isfinite(float(value)):
                raise ReviewError(f"argument '{arg_name}' must be finite, got {value!r}")
            return value
        try:
            converted = float(value)
            if not math.isfinite(converted):
                raise ValueError
            return converted
        except (TypeError, ValueError):
            raise ReviewError(f"argument '{arg_name}' must be a number, got {value!r}")
    # String values are not arbitrary objects: silently turning null/containers
    # into text can cause writes such as the literal token "None".
    if type_name == "string":
        if isinstance(value, str):
            return value
        raise ReviewError(f"argument '{arg_name}' must be a string, got {value!r}")
    raise ReviewError(f"argument '{arg_name}' has unsupported type: {type_name}")


def review(arguments: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize arguments against the tool's declared schema."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ReviewError("arguments must be an object")
    schema = spec.get("args") or {}
    unknown = sorted(set(arguments) - set(schema))
    if unknown:
        raise ReviewError(f"unknown argument(s): {', '.join(unknown)}")
    required = tuple(spec.get("required") or ())
    missing = [name for name in required if name not in arguments or arguments[name] is None or arguments[name] == ""]
    if missing:
        raise ReviewError(f"missing required argument(s): {', '.join(missing)}")

    clean: Dict[str, Any] = {}
    for arg_name, arg_spec in schema.items():
        if arg_name not in arguments:
            continue
        if arguments[arg_name] is None:
            raise ReviewError(f"argument '{arg_name}' must not be null")
        value = _coerce(arguments[arg_name], arg_spec.get("type", "string"), arg_name)
        if "enum" in arg_spec and value not in arg_spec["enum"]:
            raise ReviewError(f"argument '{arg_name}' must be one of {arg_spec['enum']!r}")
        if isinstance(value, str):
            if len(value) < int(arg_spec.get("minLength", 0)):
                raise ReviewError(f"argument '{arg_name}' is too short")
            if "maxLength" in arg_spec and len(value) > int(arg_spec["maxLength"]):
                raise ReviewError(f"argument '{arg_name}' is too long")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in arg_spec and value < arg_spec["minimum"]:
                raise ReviewError(f"argument '{arg_name}' is below the minimum")
            if "maximum" in arg_spec and value > arg_spec["maximum"]:
                raise ReviewError(f"argument '{arg_name}' is above the maximum")
        clean[arg_name] = value
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
        clean = _validate_tool_call(name, arguments)
        spec = TOOLS[name]
        text = str(spec["fn"](clean))
        return {"content": [{"type": "text", "text": rep.mask_secrets(text)}]}
    except (ReviewError, ProtocolError) as e:
        return {"content": [{"type": "text", "text": f"rejected: {rep.mask_secrets(str(e))}"}], "isError": True}
    except Exception as e:  # noqa: BLE001 — report any failure to the client
        return {"content": [{"type": "text", "text": f"tool error: {rep.mask_secrets(str(e))}"}], "isError": True}


def tools_list() -> List[Dict[str, Any]]:
    """MCP `tools/list` schema, generated from the registry."""
    return [
        {
            "name": name,
            "description": t["description"],
            "inputSchema": {
                "type": "object",
                "properties": t["args"],
                "required": list(_REQUIRED_ARGS.get(name, ())),
                "additionalProperties": False,
            },
        }
        for name, t in TOOLS.items()
    ]


# ---------------------------------------------------------------- protocol


def _rpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": rep.mask_secrets(str(message))},
    }


def _handle(msg: Dict[str, Any], state: Optional[ProtocolState] = None) -> Optional[Dict[str, Any]]:
    state = state or ProtocolState()
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "request must be an object")
    notification = "id" not in msg
    if notification:
        # Notifications never receive a response; only the lifecycle
        # notification has a state transition.
        if msg.get("jsonrpc") == "2.0" and msg.get("method") == "notifications/initialized" and state.initialized:
            state.ready = True
        return None
    request_id = msg.get("id")
    if "id" in msg and (isinstance(request_id, bool) or (request_id is not None and not isinstance(request_id, (str, int, float)))):
        return _rpc_error(None, -32600, "request id must be a string or number")
    if msg.get("jsonrpc") != "2.0":
        return _rpc_error(request_id, -32600, "jsonrpc must be '2.0'")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(request_id, -32600, "method must be a non-empty string")

    params = msg.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "params must be an object")

    if method == "initialize":
        if state.initialized:
            return _rpc_error(request_id, -32600, "initialize may only be sent once")
        if "protocolVersion" not in params or not isinstance(params["protocolVersion"], str):
            return _rpc_error(request_id, -32602, "initialize.protocolVersion is required")
        requested = params["protocolVersion"]
        if requested != PROTOCOL:
            return _rpc_error(request_id, -32602, f"unsupported protocol version: {requested}")
        state.initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agent-fix", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if not state.ready:
        return _rpc_error(request_id, -32002, "server is not initialized")
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools_list()}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            return _rpc_error(request_id, -32602, "tools/call.name is required")
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "tools/call.arguments must be an object")
        try:
            clean = _validate_tool_call(name, arguments)
        except ProtocolError as exc:
            return _rpc_error(request_id, exc.code, str(exc))
        try:
            text = str(TOOLS[name]["fn"](clean))
            result = {"content": [{"type": "text", "text": rep.mask_secrets(text)}]}
        except Exception as exc:  # domain/tool failure stays an MCP tool error
            result = {
                "content": [{"type": "text", "text": f"tool error: {rep.mask_secrets(str(exc))}"}],
                "isError": True,
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return _rpc_error(request_id, -32601, f"method not found: {method}")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _write_json(value: Any) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def _dispatch_one(message: Any, state: ProtocolState) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "request must be an object")
    try:
        return _handle(message, state)
    except Exception as exc:  # never let one malformed message kill the server
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32603, "message": f"internal error: {rep.mask_secrets(str(exc))}"},
        }


def main() -> int:
    state = ProtocolState()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError):
            _write_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}})
            continue
        if isinstance(message, list):
            if not message:
                _write_json(_rpc_error(None, -32600, "batch must not be empty"))
                continue
            responses = []
            for item in message:
                response = _dispatch_one(item, state)
                if response is not None:
                    responses.append(response)
            if responses:
                _write_json(responses)
            continue
        response = _dispatch_one(message, state)
        if response is not None:
            _write_json(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
