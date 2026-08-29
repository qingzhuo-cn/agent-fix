#!/usr/bin/env python3
"""agentfix.cli — the `fix` command-line interface.

    fix list                        # list all known issues
    fix agents                      # which agents are installed
    fix check [id ...]              # run diagnostics (all issues by default)
    fix doctor                      # alias for `fix check`
    fix apply <id> [--yes]          # apply fixes for one issue, then verify
    fix auto                        # check all -> auto-apply fixes for broken ones
    fix selfheal                    # like auto, but silent when healthy + hard deadline
    fix info <id>                   # print the matching doc from fixes/
    fix net [--timeout N]           # network diagnostics (TCP + proxy env)
    fix hooks install|uninstall|status [--agent id]   # self-heal startup hooks
    fix mcp register|remove [agent] # MCP server registration
    fix install | uninstall         # deploy/remove the whole skill

    # Program (importable API)
    from agentfix import catalog, engine
    result = engine.check_issue(catalog.load_catalog()["issues"][0], quiet=True)

Exit codes (useful for cron / CI / watchdog wrappers):
    0  all checks passed
    1  at least one check failed
    2  usage error / catalog missing

`fix selfheal` is the agent-startup hook command: prints NOTHING when everything
is healthy (so hooks stay quiet), prints one concise line when it fixed or could
not fix something, and self-aborts after a hard deadline so it never blocks an
agent from starting.

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


def cmd_list(catalog, args) -> int:
    print(f"agent-fix v{__version__} catalog — {len(catalog['issues'])} issues\n")
    print(f"{'ID':<26} {'AGENTS':<42} TITLE")
    print("-" * 110)
    for issue in catalog["issues"]:
        agents = ", ".join(issue.get("agents", []))[:40]
        print(f"{issue['id']:<26} {agents:<42} {issue['title']}")
    print(
        "\nCommands: fix check [id...] | fix doctor | fix apply <id> [--yes] | fix auto"
        " | fix info <id> | fix net | fix hooks | fix mcp | fix install"
    )
    return 0


def cmd_check(catalog, args) -> int:
    ids = args.ids or [i["id"] for i in catalog["issues"]]
    agents = cat.detect_agents(catalog)
    any_broken = False
    for issue_id in ids:
        issue = cat.find_issue(catalog, issue_id)
        if not issue:
            print(f"unknown issue: {issue_id}", file=sys.stderr)
            return 2
        print(f"== {issue['id']}: {issue['title']}")
        state = engine.check_issue(issue, agents=agents)
        if state["broken"]:
            any_broken = True
            print(f"   -> BROKEN. Fix with: fix apply {issue['id']} --yes")
        else:
            print("   -> healthy")
    if args.json:
        print(json.dumps({"broken": any_broken, "checked": ids}, indent=2))
    return 1 if any_broken else 0


def cmd_apply(catalog, args) -> int:
    issue = cat.find_issue(catalog, args.id)
    if not issue:
        print(f"unknown issue: {args.id}", file=sys.stderr)
        return 2
    agents = cat.detect_agents(catalog)
    print(f"== {issue['id']}: {issue['title']}")
    print(f"   doc: {issue.get('doc', 'n/a')}")
    outcome = engine.apply_issue(issue, yes=args.yes, agents=agents)
    if args.json:
        print(json.dumps(outcome, indent=2))
    if outcome["verified"]:
        print("\n=> verified OK")
        return 0
    print("\n=> not fully verified; check the doc for manual steps")
    return 1


def cmd_selfheal(catalog, args, deadline: float = 75.0) -> int:
    """Hook mode: prints nothing on success."""
    r = engine.run_selfheal(catalog, deadline=deadline)
    if r["timed_out"]:
        print("agent-fix selfheal: timed out — run 'fix doctor' manually")
        return 2
    if not r["fixed"] and not r["unfixed"]:
        return 0  # healthy — stay silent for hooks
    parts = []
    if r["fixed"]:
        parts.append("fixed: " + ", ".join(r["fixed"]))
    if r["unfixed"]:
        parts.append("still broken: " + ", ".join(r["unfixed"]))
    print("agent-fix selfheal — " + "; ".join(parts))
    return 1 if r["unfixed"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fix", description="Universal agent repair CLI (agent-fix skill)")
    parser.add_argument("--version", action="version", version=f"agent-fix {__version__}")
    parser.add_argument("--json", action="store_true", help="machine-readable output where supported")
    sub = parser.add_subparsers(dest="command", required=True)

    def _json_opt(p: argparse.ArgumentParser) -> None:
        # allow `fix check --json` as well as `fix --json check`
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p_list = sub.add_parser("list", help="list known issues")
    _json_opt(p_list)
    p_agents = sub.add_parser("agents", help="list known agents and which are installed")
    _json_opt(p_agents)
    p_check = sub.add_parser("check", help="run diagnostics for issues (default: all)")
    _json_opt(p_check)
    p_check.add_argument("ids", nargs="*", help="issue ids, e.g. npm-postinstall-skipped")
    p_doc = sub.add_parser("doctor", help="alias for: fix check")
    _json_opt(p_doc)
    p_apply = sub.add_parser("apply", help="apply fixes for one issue")
    _json_opt(p_apply)
    p_apply.add_argument("id", help="issue id")
    p_apply.add_argument("--yes", "-y", action="store_true", help="run fixes without prompting")
    p_auto = sub.add_parser("auto", help="check all; auto-apply fixes for broken ones (watchdog mode)")
    _json_opt(p_auto)
    p_selfheal = sub.add_parser(
        "selfheal",
        help="check all + auto-fix; silent when healthy, hard deadline (agent startup hook mode)",
    )
    _json_opt(p_selfheal)
    p_info = sub.add_parser("info", help="print the doc for an issue")
    _json_opt(p_info)
    p_info.add_argument("id", help="issue id")
    p_net = sub.add_parser("net", help="network diagnostics (TCP connectivity + proxy env)")
    p_net.add_argument("--timeout", type=float, default=5.0, help="connect timeout seconds (default 5)")

    p_hooks = sub.add_parser("hooks", help="manage self-heal startup hooks (claude/codex/opencode/hermes)")
    h_sub = p_hooks.add_subparsers(dest="hooks_command", required=True)
    for verb, help_text in (("install", "register startup hooks"), ("uninstall", "remove startup hooks"), ("status", "show what is registered")):
        h = h_sub.add_parser(verb, help=help_text)
        h.add_argument("--agent", help="only this agent id (e.g. claude-code)")

    p_mcp = sub.add_parser("mcp", help="register/unregister the MCP server with agents")
    m_sub = p_mcp.add_subparsers(dest="mcp_command", required=True)
    m_add = m_sub.add_parser("register", help="register with every installed MCP-capable agent")
    m_add.add_argument("agent", nargs="?", help="one agent id (default: all detected)")
    m_rm = m_sub.add_parser("remove", help="unregister")
    m_rm.add_argument("agent", nargs="?", help="one agent id (default: all)")

    sub.add_parser("install", help="deploy the skill into every detected agent (+ hooks, MCP, CLI)")
    sub.add_parser("uninstall", help="remove everything `fix install` deployed")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = cat.load_catalog()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.command == "list":
        return cmd_list(catalog, args)
    if args.command == "agents":
        print(engine.agents_text())
        return 0
    if args.command == "check":
        return cmd_check(catalog, args)
    if args.command == "doctor":
        args.ids = []
        return cmd_check(catalog, args)
    if args.command == "apply":
        return cmd_apply(catalog, args)
    if args.command == "auto":
        report = engine.auto_fix(catalog)
        print(f"\n== summary: {len(report['fixed'])} fixed, {len(report['unfixed'])} still broken")
        return 1 if report["unfixed"] else 0
    if args.command == "selfheal":
        return cmd_selfheal(catalog, args)
    if args.command == "info":
        issue = cat.find_issue(catalog, args.id)
        if not issue:
            print(f"unknown issue: {args.id}", file=sys.stderr)
            return 2
        doc = cat.doc_path(issue)
        print(doc.read_text(encoding="utf-8") if doc.exists() else f"doc missing: {doc}")
        return 0
    if args.command == "net":
        print(engine.net_text(timeout=args.timeout))
        return 0
    if args.command == "hooks":
        if args.hooks_command == "install":
            lines = hooks.hooks_install(getattr(args, "agent", None))
        elif args.hooks_command == "uninstall":
            lines = hooks.hooks_uninstall(getattr(args, "agent", None))
        else:
            print("agent-fix self-heal: startup hook status\n")
            lines = [hooks.hooks_status()]
        for line in lines:
            print("  " + line)
        if args.hooks_command in ("install", "uninstall"):
            print("\nDone. Verify with: fix hooks status")
        return 0
    if args.command == "mcp":
        lines = hooks.mcp_register(getattr(args, "agent", None)) if args.mcp_command == "register" else hooks.mcp_remove(getattr(args, "agent", None))
        for line in lines:
            print(line)
        return 0
    if args.command == "install":
        for line in hooks.install_all():
            print(line)
        return 0
    if args.command == "uninstall":
        for line in hooks.uninstall_all():
            print(line)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
