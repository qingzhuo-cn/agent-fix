#!/usr/bin/env python3
"""agentfix.cli — the explicit-target `fix` command-line interface.

Repair commands always require one issue and one target agent. They never scan,
repair, or verify another agent on the machine.

Pure stdlib, no dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from agentfix import __version__
from agentfix import catalog as cat
from agentfix import engine, hooks
from agentfix import report as rep
from agentfix import result as operation_result


def _json_text(value, indent: Optional[int] = None) -> str:
    """Serialize one JSON document and apply the final secret-mask boundary."""
    return rep.mask_secrets(json.dumps(value, indent=indent, ensure_ascii=False))


def _emit_error(exc: Exception, as_json: bool) -> None:
    message = rep.mask_secrets(str(exc))
    if as_json:
        print(_json_text({"status": "error", "error": message}))
    else:
        print(message, file=sys.stderr)


def cmd_list(catalog, args) -> int:
    print(f"agent-fix v{__version__} catalog — {len(catalog['issues'])} issues\n")
    print(f"{'ID':<26} {'AGENTS':<42} TITLE")
    print("-" * 110)
    for issue in catalog["issues"]:
        agents = ", ".join(issue.get("agents", []))[:40]
        print(f"{issue['id']:<26} {agents:<42} {issue['title']}")
    print("\nCommands: fix check <id> --agent <agent-id> | fix apply <id> --agent <agent-id> [--yes]")
    return 0


def cmd_check(catalog, args) -> int:
    issue = cat.find_issue(catalog, args.id)
    if not issue:
        _emit_error(engine.TargetError(f"unknown issue: {args.id}"), args.json)
        return 2
    try:
        agent = engine.resolve_target(catalog, issue, args.agent)
        if not args.json:
            print(f"== {issue['id']}: {issue['title']} [{args.agent}]")
        state = engine.check_issue(issue, agent=agent, quiet=args.json)
    except engine.TargetError as exc:
        _emit_error(engine.TargetError(f"target error: {exc}"), args.json)
        return 2
    except Exception as exc:
        _emit_error(exc, args.json)
        return 1
    if args.json:
        print(_json_text(state, indent=2))
    elif state.get("broken") or state.get("status") == "FAIL":
        print(f"   -> BROKEN. Fix with: fix apply {issue['id']} --agent {args.agent} --yes")
    elif state.get("status") == "INCONCLUSIVE":
        print("   -> inconclusive; no authoritative check passed")
    else:
        print("   -> healthy")
    return 1 if state.get("broken") or state.get("status") in {"FAIL", "INCONCLUSIVE"} else 0


def cmd_apply(catalog, args) -> int:
    issue = cat.find_issue(catalog, args.id)
    if not issue:
        _emit_error(engine.TargetError(f"unknown issue: {args.id}"), args.json)
        return 2
    try:
        agent = engine.resolve_target(catalog, issue, args.agent)
        if not args.json:
            print(f"== {issue['id']}: {issue['title']} [{args.agent}]")
            print(f"   doc: {issue.get('doc', 'n/a')}")
        outcome = engine.apply_issue(
            issue, agent=agent, yes=args.yes, quiet=args.json
        )
    except engine.TargetError as exc:
        _emit_error(engine.TargetError(f"target error: {exc}"), args.json)
        return 2
    except Exception as exc:
        _emit_error(exc, args.json)
        return 1
    if args.json:
        print(_json_text(outcome, indent=2))
        return 0 if outcome["verified"] else 1
    if outcome["verified"]:
        print("\n=> verified OK")
        return 0
    print("\n=> not fully verified; check the doc for manual steps")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fix", description="Explicit-target agent repair CLI (agent-fix skill)")
    parser.add_argument("--version", action="version", version=f"agent-fix {__version__}")
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable check/apply output (place before command)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list known issues")
    sub.add_parser("agents", help="list known agents and which are installed")
    p_check = sub.add_parser("check", help="check one issue for one explicit agent")
    p_check.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable output",
    )
    p_check.add_argument("id", help="issue id, e.g. npm-postinstall-skipped")
    p_check.add_argument("--agent", required=True, help="target agent id, e.g. opencode")
    p_apply = sub.add_parser("apply", help="apply and verify one issue for one explicit agent")
    p_apply.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable output",
    )
    p_apply.add_argument("id", help="issue id")
    p_apply.add_argument("--agent", required=True, help="target agent id")
    p_apply.add_argument("--yes", "-y", action="store_true", help="run fixes without prompting")
    p_info = sub.add_parser("info", help="print the doc for an issue")
    p_info.add_argument("id", help="issue id")
    p_net = sub.add_parser("net", help="network diagnostics for one explicit endpoint")
    p_net.add_argument("host", help="hostname to check")
    p_net.add_argument("--timeout", type=float, default=5.0, help="connect timeout seconds (default 5)")
    p_mcp = sub.add_parser("mcp", help="register/unregister the MCP server with one agent")
    m_sub = p_mcp.add_subparsers(dest="mcp_command", required=True)
    for verb, help_text in (("register", "register with one detected MCP-capable agent"), ("remove", "unregister from one agent")):
        m = m_sub.add_parser(verb, help=help_text)
        m.add_argument("agent", help="one agent id")
    p_install = sub.add_parser("install", help="deploy skill files for one explicit agent")
    p_install.add_argument("--agent", required=True, help="target agent id")
    p_uninstall = sub.add_parser("uninstall", help="remove skill files and registrations for one explicit agent")
    p_uninstall.add_argument("--agent", required=True, help="target agent id")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and args.command not in {"check", "apply"}:
        parser.error("--json is supported only by check and apply")
    try:
        catalog = cat.load_catalog()
    except Exception as exc:
        _emit_error(exc, bool(getattr(args, "json", False)))
        return 2
    if args.command == "list":
        return cmd_list(catalog, args)
    if args.command == "agents":
        print(engine.agents_text())
        return 0
    if args.command == "check":
        return cmd_check(catalog, args)
    if args.command == "apply":
        return cmd_apply(catalog, args)
    if args.command == "info":
        try:
            print(engine.info_text(args.id))
        except engine.TargetError as exc:
            _emit_error(exc, False)
            return 2
        return 0
    if args.command == "net":
        if not getattr(args, "host", ""):
            print("target host required: fix net <host>", file=sys.stderr)
            return 2
        try:
            result = engine.net_result(timeout=args.timeout, host=args.host)
        except engine.TargetError as exc:
            print(f"target error: {exc}", file=sys.stderr)
            return 2
        print(engine.net_text_from_result(result, timeout=args.timeout))
        return 0 if result.get("ok") else 1
    if args.command == "mcp":
        operation = "mcp_register" if args.mcp_command == "register" else "mcp_remove"
        lines = hooks.mcp_register(args.agent) if args.mcp_command == "register" else hooks.mcp_remove(args.agent)
        result = operation_result.from_lines(
            operation, lines, status=operation_result.aggregate_status(lines, "error")
        )
        for line in result.lines:
            print(line)
        return 0 if result.ok else 1
    if args.command == "install":
        lines = hooks.install_target(args.agent)
        result = operation_result.from_lines(
            "install_target", lines, status=operation_result.status_of(lines, "error")
        )
        for line in result.lines:
            print(line)
        return 0 if result.ok else 1
    if args.command == "uninstall":
        lines = hooks.uninstall_target(args.agent)
        result = operation_result.from_lines(
            "uninstall_target", lines, status=operation_result.status_of(lines, "error")
        )
        for line in result.lines:
            print(line)
        return 0 if result.ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
