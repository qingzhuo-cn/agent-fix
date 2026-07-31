#!/usr/bin/env python3
"""
agent-fix: universal diagnostic & repair CLI for AI coding agents.

One catalog (catalog.json), one engine (this file). Works on Windows / macOS /
Linux, from a terminal OR imported by other programs:

    # CLI
    fix list                        # list all known issues
    fix check [id ...]              # run diagnostics (all issues by default)
    fix doctor                      # alias for `fix check`
    fix apply <id> [--yes]          # apply fixes for one issue, then verify
    fix auto                        # check all -> auto-apply fixes for broken ones
    fix selfheal                    # like auto, but silent when healthy + hard deadline
    fix info <id>                   # print the matching doc from fixes/

    # Program (importable API)
    from fix import load_catalog, check_issue, apply_issue, auto_fix
    result = check_issue(catalog["issues"][0], quiet=True)

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
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog.json"
DOCS_DIR = ROOT / "fixes"

# ---------------------------------------------------------------- utilities


def _is_windows() -> bool:
    return os.name == "nt"


def _shell_prefix() -> List[str]:
    """Pick a shell for catalog commands (written in POSIX syntax).

    - POSIX: /bin/sh
    - Windows: git-bash `bash` when available (standard for AI-agent users);
      otherwise fall back to cmd.exe (POSIX-only checks will fail with a clear
      message, but npm/node/reg commands still work).

    IMPORTANT: return the RESOLVED absolute path. On Windows, CreateProcess
    searches System32 BEFORE PATH, so a bare "bash" would resolve to the WSL
    launcher (C:\\Windows\\System32\\bash.exe) when WSL is installed, instead of
    Git Bash — and WSL bash cannot run Windows tools like node/npm.
    """
    if not _is_windows():
        return ["/bin/sh", "-c"]
    for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", "bash"):
        exe = cand if (cand.startswith("C:\\") or cand.startswith("/")) else (shutil.which(cand) or "")
        if exe and os.path.exists(exe):
            return [exe, "-c"]
    return ["cmd", "/c"]


_SHELL_PREFIX = _shell_prefix()


def run(cmd: str, cwd: Optional[Path] = None, timeout: int = 60) -> Dict[str, Any]:
    """Run a shell command, return {ok, exit, stdout, stderr, duration}."""
    t0 = time.time()
    try:
        proc = subprocess.run(
            _SHELL_PREFIX + [cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "exit": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "duration": round(time.time() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit": -1,
            "stdout": "",
            "stderr": f"timed out after {timeout}s",
            "duration": round(time.time() - t0, 2),
        }
    except FileNotFoundError:
        return {"ok": False, "exit": 127, "stdout": "", "stderr": "command not found", "duration": 0}


def resolve_cwd(template: str) -> Optional[Path]:
    """Resolve a cwd template like 'npm_root/opencode-ai' to an absolute path."""
    if not template:
        return None
    parts = template.split("/", 1)
    if parts[0] == "npm_root":
        base = run("npm root -g", timeout=30).get("stdout", "").strip()
        if not base:
            return None
        base = base.replace("\\", "/")
        p = Path(base) / (parts[1] if len(parts) > 1 else "")
        return p
    p = Path(template).expanduser()
    return p


def _passes(pass_spec: Optional[Dict[str, Any]], result: Dict[str, Any]) -> bool:
    """Evaluate a check's pass spec against a run result."""
    if pass_spec is None:
        return result["ok"]
    if "exit" in pass_spec:
        if result["exit"] != pass_spec["exit"]:
            return False
    if "stdout_equals" in pass_spec:
        if result["stdout"] != pass_spec["stdout_equals"]:
            return False
    if "stdout_contains" in pass_spec:
        if not all(s in result["stdout"] for s in pass_spec["stdout_contains"]):
            return False
    if "stdout_contains_any" in pass_spec:
        if not any(s in result["stdout"] for s in pass_spec["stdout_contains_any"]):
            return False
    if "stdout_not_contains" in pass_spec:
        low = result["stdout"].lower()
        if any(s.lower() in low for s in pass_spec["stdout_not_contains"]):
            return False
    return True


# ---------------------------------------------------------------- catalog


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else CATALOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"catalog not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def find_issue(catalog: Dict[str, Any], issue_id: str) -> Optional[Dict[str, Any]]:
    for issue in catalog["issues"]:
        if issue["id"] == issue_id:
            return issue
    return None


# ---------------------------------------------------------------- agents


# Common provider API-key env vars any agent may use (any provider, global devs)
COMMON_PROVIDER_KEYS = [
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "ZHIPU_API_KEY", "DASHSCOPE_API_KEY",
    "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "AZURE_OPENAI_API_KEY",
    "GROQ_API_KEY", "XAI_API_KEY",
]


def _config_path(agent: Dict[str, Any]) -> str:
    """Resolve an agent's config home for the current platform.

    Uses the `config_win` registry field on Windows (e.g. $LOCALAPPDATA/hermes);
    expands ~ and $VARs, normalizes to forward slashes for shell use.
    """
    cfg = agent.get("config_win") if (_is_windows() and agent.get("config_win")) else agent.get("config")
    if not cfg:
        return ""
    return os.path.expandvars(os.path.expanduser(cfg)).replace("\\", "/")


def _expand(template: Optional[str], agent: Dict[str, Any]) -> Optional[str]:
    """Replace {name}/{bin}/{npm_pkg}/{config}/{keys} placeholders in a catalog string."""
    if not template:
        return template
    keys = list(agent.get("provider_env") or []) + COMMON_PROVIDER_KEYS
    seen: set = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]
    return (
        template.replace("{name}", agent.get("name", agent.get("id", "agent")))
        .replace("{bin}", (agent.get("bin") or ["agent"])[0])
        .replace("{npm_pkg}", agent.get("npm_pkg") or "")
        .replace("{config}", _config_path(agent))
        .replace("{keys}", "|".join(keys))
    )


def detect_agents(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return registry entries actually installed on this machine.

    Detection: any candidate bin found on PATH, OR the agent's config dir exists.
    """
    detected = []
    for aid, info in catalog.get("agents", {}).items():
        exe = None
        for b in info.get("bin", []):
            found = shutil.which(b)
            if found:
                exe = found
                break
        cfg_exists = bool(_config_path(info)) and Path(_config_path(info).replace("/", os.sep)).exists()
        if exe or cfg_exists:
            detected.append({"id": aid, **info, "exe": exe})
    return detected


def cmd_agents(catalog: Dict[str, Any], args: argparse.Namespace) -> int:
    print(f"agent registry: {len(catalog.get('agents', {}))} known agents\n")
    print(f"{'AGENT':<28} {'BIN':<22} {'NPM PKG':<30} STATUS")
    print("-" * 108)
    for aid, info in catalog.get("agents", {}).items():
        bins = ", ".join(info.get("bin", []))
        npm = info.get("npm_pkg") or "-"
        exe = None
        for b in info.get("bin", []):
            exe = shutil.which(b)
            if exe:
                break
        if exe:
            status = f"INSTALLED ({exe})"
        elif info.get("config") and Path(info["config"]).expanduser().exists():
            status = "config-dir present"
        else:
            status = "not detected"
        print(f"{info.get('name','?'):<28} {bins:<22} {npm:<30} {status}")
    return 0


# ---------------------------------------------------------------- checks


def check_issue(
    issue: Dict[str, Any],
    quiet: bool = False,
    agents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run all checks for one issue. Dynamic issues expand per detected agent.

    Returns {id, title, results: [...], broken: bool}.
    """
    results = []
    if issue.get("dynamic"):
        detected = agents or []
        if not detected:
            if not quiet:
                print("    [SKIP] no agents detected on this machine")
            return {"id": issue["id"], "title": issue["title"], "results": [], "broken": False}
        for agent in detected:
            for check_t in issue.get("checks", []):
                check = {k: v for k, v in check_t.items()}
                check["name"] = _expand(check.get("name", ""), agent)
                check["cmd"] = _expand(check.get("cmd", ""), agent)
                results.append(_run_one_check(check, quiet, agent.get("id")))
    else:
        for check in issue.get("checks", []):
            results.append(_run_one_check(check, quiet))
    broken = any(r["status"] == "FAIL" for r in results)
    return {"id": issue["id"], "title": issue["title"], "results": results, "broken": broken}


def _run_one_check(
    check: Dict[str, Any],
    quiet: bool = False,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    platform = check.get("platform")
    if platform == "windows" and not _is_windows():
        return {"name": check["name"], "status": "SKIP", "detail": "windows-only", "exit": None, "agent": agent_id}
    if platform == "posix" and _is_windows():
        return {"name": check["name"], "status": "SKIP", "detail": "posix-only", "exit": None, "agent": agent_id}
    if check.get("kind") == "net":
        # native network check — no shell involved (see scripts/netcheck.py)
        import netcheck

        r = netcheck.check_endpoint(
            check.get("host", ""),
            port=int(check.get("port", 443)),
            timeout=float(check.get("timeout", 5)),
        )
        passed = r["ok"]
        detail = f"ok ({r['ms']}ms)" if r["ok"] else (r["error"] or "unreachable")
    else:
        result = run(check["cmd"], timeout=check.get("timeout", 30))
        passed = _passes(check.get("pass"), result)
        detail = result["stdout"] or result["stderr"]
    prefix = f"[{agent_id}] " if agent_id else ""
    if not quiet:
        mark = "PASS" if passed else "FAIL"
        print(f"    [{mark}] {prefix}{check['name']}")
        if detail and not passed:
            print(f"          {detail[:400]}")
    return {
        "name": check["name"],
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
        "exit": 0 if passed else 1,
        "agent": agent_id,
    }


# ---------------------------------------------------------------- fixes


def apply_issue(
    issue: Dict[str, Any],
    yes: bool = False,
    quiet: bool = False,
    agents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Apply the fixes for one issue, then run verify commands.

    Dynamic issues expand per detected agent ({name}/{bin}/{npm_pkg} templates).

    Returns {id, fixed: [...], skipped: [...], verified: bool}.
    """
    fixed, skipped = [], []
    fix_specs = []
    if issue.get("dynamic"):
        detected = agents or []
        if not detected:
            print("    [SKIP] no agents detected on this machine")
        for agent in detected:
            for fix_t in issue.get("fixes", []):
                fix = {k: v for k, v in fix_t.items()}
                fix["name"] = _expand(fix.get("name", ""), agent)
                fix["cmd"] = _expand(fix.get("cmd", ""), agent)
                if fix.get("cwd"):
                    fix["cwd"] = _expand(fix.get("cwd", ""), agent)
                fix["_agent"] = agent
                fix_specs.append(fix)
    else:
        fix_specs = list(issue.get("fixes", []))

    for fix in fix_specs:
        platform = fix.get("platform")
        if platform == "windows" and not _is_windows():
            skipped.append({"name": fix["name"], "reason": "windows-only"})
            continue
        if platform == "posix" and _is_windows():
            skipped.append({"name": fix["name"], "reason": "posix-only"})
            continue
        if fix.get("manual"):
            skipped.append({"name": fix["name"], "reason": "manual (see doc)"})
            if not quiet:
                print(f"    [SKIP] {fix['name']} (manual)")
                print(f"          {fix['cmd'].replace(chr(10), ' ')[:200]}")
            continue
        # npm-scoped fix but the agent is not npm-installed -> skip with a clear reason
        cwd_tpl = fix.get("cwd") or ""
        agent = fix.get("_agent")
        if "{npm_pkg}" in cwd_tpl and (not agent or not agent.get("npm_pkg")):
            skipped.append({"name": fix["name"], "reason": "agent is not npm-installed (native/desktop)"})
            if not quiet:
                print(f"    [SKIP] {fix['name']} (not npm-installed)")
            continue
        cwd = resolve_cwd(fix["cwd"]) if fix.get("cwd") else None
        if fix.get("cwd") and cwd is not None and not cwd.exists():
            skipped.append({"name": fix["name"], "reason": f"dir not found: {cwd}"})
            if not quiet:
                print(f"    [SKIP] {fix['name']} (dir not found: {cwd})")
            continue
        print(f"    [FIX ] {fix['name']}")
        if not yes:
            try:
                answer = input("          run this fix? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer not in ("y", "yes"):
                skipped.append({"name": fix["name"], "reason": "declined"})
                continue
        result = run(fix["cmd"], cwd=cwd, timeout=fix.get("timeout", 300))
        if result["ok"]:
            fixed.append({"name": fix["name"]})
            if not quiet:
                print(f"          ok ({result['duration']}s)")
                if result["stdout"]:
                    print(f"          {result['stdout'][:300]}")
        else:
            skipped.append({"name": fix["name"], "reason": f"failed: {result['stderr'][:200] or result['stdout'][:200]}"})
            if not quiet:
                print(f"          FAILED: {result['stderr'][:200] or result['stdout'][:200]}")

    # verify
    verified = True
    if issue.get("dynamic") and agents:
        for agent in agents:
            for v_t in issue.get("verify", []):
                v = {k: vv for k, vv in v_t.items()}
                v["name"] = _expand(v.get("name", ""), agent)
                v["cmd"] = _expand(v.get("cmd", ""), agent)
                verified = _run_one_verify(v, verified)
    else:
        for v in issue.get("verify", []):
            verified = _run_one_verify(v, verified)
    return {"id": issue["id"], "fixed": fixed, "skipped": skipped, "verified": verified}


def _run_one_verify(v: Dict[str, Any], verified: bool, quiet: bool = False) -> bool:
    platform = v.get("platform")
    if platform == "windows" and not _is_windows():
        return verified
    if platform == "posix" and _is_windows():
        return verified
    if v.get("kind") == "net":
        # native network verify — no shell involved (same engine as the check)
        import netcheck

        r = netcheck.check_endpoint(
            v.get("host", ""),
            port=int(v.get("port", 443)),
            timeout=float(v.get("timeout", 5)),
        )
        ok = r["ok"]
        if not ok:
            verified = False
        if not quiet:
            print(f"    [VERIFY{' OK' if ok else ' FAIL'}] {v['name']}")
            if r.get("error"):
                print(f"          {r['error'][:300]}")
        return verified
    result = run(v["cmd"], timeout=v.get("timeout", 60))
    ok = result["ok"] and ("STILL-MISSING" not in result["stdout"])
    if not ok:
        verified = False
    if not quiet:
        print(f"    [VERIFY{' OK' if ok else ' FAIL'}] {v['name']}")
        if result["stdout"]:
            print(f"          {result['stdout'][:300]}")
    return verified


def auto_fix(catalog: Dict[str, Any], quiet: bool = False) -> Dict[str, Any]:
    """Check every issue; auto-apply fixes for broken ones. Watchdog/cron mode."""
    agents = detect_agents(catalog)
    report = {"checked": [], "fixed": [], "unfixed": []}
    for issue in catalog["issues"]:
        print(f"\n== {issue['id']}: {issue['title']}")
        state = check_issue(issue, quiet=quiet, agents=agents)
        report["checked"].append({"id": issue["id"], "broken": state["broken"]})
        if state["broken"]:
            print("   -> broken, applying fixes...")
            outcome = apply_issue(issue, yes=True, quiet=quiet, agents=agents)
            if outcome["verified"]:
                report["fixed"].append(issue["id"])
                print("   -> verified OK")
            else:
                report["unfixed"].append(issue["id"])
                print("   -> still broken after fixes (see doc for manual steps)")
        else:
            print("   -> healthy")
    return report


def run_selfheal(catalog: Dict[str, Any], deadline: float = 75.0) -> Dict[str, Any]:
    """Check all + auto-fix, silent when healthy. Returns {fixed, unfixed, timed_out}.

    Shared by the CLI (`fix selfheal`) and the MCP server (`self_heal` tool) so
    both use the exact same pipeline. No printing — callers format the report.
    """
    agents = detect_agents(catalog)
    fixed: List[str] = []
    unfixed: List[str] = []
    start = time.monotonic()
    for issue in catalog["issues"]:
        if time.monotonic() - start > deadline:
            return {"fixed": fixed, "unfixed": unfixed, "timed_out": True}
        state = check_issue(issue, quiet=True, agents=agents)
        if not state["broken"]:
            continue
        fixes = issue.get("fixes") or []
        if not any(not f.get("manual") for f in fixes):
            # diagnostic-only issue (e.g. net-connectivity): nothing to
            # auto-repair, so don't nag the user about it on every start
            continue
        outcome = apply_issue(issue, yes=True, quiet=True, agents=agents)
        if outcome["verified"]:
            fixed.append(issue["id"])
        else:
            unfixed.append(issue["id"])
    return {"fixed": fixed, "unfixed": unfixed, "timed_out": False}


def cmd_selfheal(catalog: Dict[str, Any], args: argparse.Namespace, deadline: float = 75.0) -> int:
    """CLI wrapper around run_selfheal — prints nothing on success (hook mode)."""
    r = run_selfheal(catalog, deadline=deadline)
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


# ---------------------------------------------------------------- CLI


def cmd_list(catalog: Dict[str, Any], args: argparse.Namespace) -> int:
    print(f"agent-fix catalog v{catalog.get('version', '?')} — {len(catalog['issues'])} issues\n")
    print(f"{'ID':<26} {'AGENTS':<42} TITLE")
    print("-" * 110)
    for issue in catalog["issues"]:
        agents = ", ".join(issue.get("agents", []))[:40]
        print(f"{issue['id']:<26} {agents:<42} {issue['title']}")
    print("\nCommands: fix check [id...] | fix doctor | fix apply <id> [--yes] | fix auto | fix info <id>")
    return 0


def cmd_check(catalog: Dict[str, Any], args: argparse.Namespace) -> int:
    ids = args.ids or [i["id"] for i in catalog["issues"]]
    agents = detect_agents(catalog)
    any_broken = False
    for issue_id in ids:
        issue = find_issue(catalog, issue_id)
        if not issue:
            print(f"unknown issue: {issue_id}", file=sys.stderr)
            return 2
        print(f"== {issue['id']}: {issue['title']}")
        state = check_issue(issue, agents=agents)
        if state["broken"]:
            any_broken = True
            print(f"   -> BROKEN. Fix with: fix apply {issue['id']} --yes")
        else:
            print("   -> healthy")
    if args.json:
        print(json.dumps({"broken": any_broken, "checked": ids}, indent=2))
    return 1 if any_broken else 0


def cmd_apply(catalog: Dict[str, Any], args: argparse.Namespace) -> int:
    issue = find_issue(catalog, args.id)
    if not issue:
        print(f"unknown issue: {args.id}", file=sys.stderr)
        return 2
    agents = detect_agents(catalog)
    print(f"== {issue['id']}: {issue['title']}")
    print(f"   doc: {issue.get('doc', 'n/a')}")
    outcome = apply_issue(issue, yes=args.yes, agents=agents)
    if args.json:
        print(json.dumps(outcome, indent=2))
    if outcome["verified"]:
        print("\n=> verified OK")
        return 0
    print("\n=> not fully verified; check the doc for manual steps")
    return 1


def cmd_info(catalog: Dict[str, Any], args: argparse.Namespace) -> int:
    issue = find_issue(catalog, args.id)
    if not issue:
        print(f"unknown issue: {args.id}", file=sys.stderr)
        return 2
    doc = DOCS_DIR / Path(issue.get("doc", "")).name
    if doc.exists():
        print(doc.read_text(encoding="utf-8"))
    else:
        print(f"doc missing: {doc}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="fix", description="Universal agent repair CLI (agent-fix skill)")
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

    args = parser.parse_args(argv)
    try:
        catalog = load_catalog()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.command == "list":
        return cmd_list(catalog, args)
    if args.command == "agents":
        return cmd_agents(catalog, args)
    if args.command == "check":
        return cmd_check(catalog, args)
    if args.command == "doctor":
        args.ids = []
        return cmd_check(catalog, args)
    if args.command == "apply":
        return cmd_apply(catalog, args)
    if args.command == "auto":
        report = auto_fix(catalog)
        print(f"\n== summary: {len(report['fixed'])} fixed, {len(report['unfixed'])} still broken")
        return 1 if report["unfixed"] else 0
    if args.command == "selfheal":
        return cmd_selfheal(catalog, args)
    if args.command == "info":
        return cmd_info(catalog, args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
