#!/usr/bin/env python3
"""门下省 · Chancellery — 审覆: the review gate (封驳).

Every tool call passes through this gate before execution. It checks the
memorial (arguments) against the official registry of 中书省: unknown tools are
vetoed, arguments are coerced to their declared types, and any failure is
returned as a clean `isError` result so the client never sees a crash.

The ministries keep their own safety guards (confirm/apply flags, key masking,
zip-slip protection); this department enforces the *protocol* around them.

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

from typing import Any, Dict

from .zhongshu import registry


class ReviewError(Exception):
    """封驳 — a request was rejected at the gate (bad tool name or arguments)."""


def _coerce(value: Any, type_name: str, arg_name: str) -> Any:
    """Coerce one argument to its declared JSON-schema type; veto if impossible."""
    if type_name == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
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


def review(name: str, arguments: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize arguments against the registry schema (封驳 on failure)."""
    schema = spec.get("args") or {}
    clean: Dict[str, Any] = {}
    for arg_name, arg_spec in schema.items():
        if arg_name not in arguments:
            continue
        clean[arg_name] = _coerce(arguments[arg_name], arg_spec.get("type", "string"), arg_name)
    return clean


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Full gate pipeline: review -> execute in the ministry -> wrap the result.

    Returns an MCP `tools/call` result dict: {content: [{type: text, text}], isError?}.
    """
    spec = registry().get(name)
    if not spec:
        return {
            "content": [{"type": "text", "text": f"unknown tool: {name} (see court_status for the registry)"}],
            "isError": True,
        }
    try:
        clean = review(name, arguments or {}, spec)
        text = spec["fn"](clean)
        return {"content": [{"type": "text", "text": str(text)}]}
    except ReviewError as e:
        return {"content": [{"type": "text", "text": f"vetoed by 门下省: {e}"}], "isError": True}
    except Exception as e:  # noqa: BLE001 — report any failure to the client
        return {"content": [{"type": "text", "text": f"tool error: {e}"}], "isError": True}
