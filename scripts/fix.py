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
    fix info <id>                   # print the matching doc from fixes/

    # Program (importable API)
    from fix import load_catalog, check_issue, apply_issue, auto_fix
    result = check_issue(catalog["issues"][0], quiet=True)

Exit codes (useful for cron / CI / watchdog wrappers):
    0  all checks passed
    1  at least one check failed
    2  usage error / catalog missing

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


# ---------------------------------------------------------------- checks


def check_issue(issue: Dict[str, Any], quiet: bool = False) -> Dict[str, Any]:
    """Run all checks for one issue. Returns {id, results: [...], broken: bool}."""
    results = []
    for check in issue.get("checks", []):
        platform = check.get("platform")
        if platform == "windows" and not _is_windows():
            results.append({"name": check["name"], "status": "SKIP", "detail": "windows-only", "exit": None})
            continue
        if platform == "posix" and _is_windows():
            results.append({"name": check["name"], "status": "SKIP", "detail": "posix-only", "exit": None})
            continue
        result = run(check["cmd"], timeout=check.get("timeout", 30))
        passed = _passes(check.get("pass"), result)
        detail = result["stdout"] or result["stderr"]
        if not quiet:
            mark = "PASS" if passed else "FAIL"
            print(f"    [{mark}] {check['name']}")
            if detail and not passed:
                print(f"          {detail}")
        results.append(
            {
                "name": check["name"],
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
                "exit": result["exit"],
            }
        )
    broken = any(r["status"] == "FAIL" for r in results)
    return {"id": issue["id"], "title": issue["title"], "results": results, "broken": broken}


# ---------------------------------------------------------------- fixes


def apply_issue(
    issue: Dict[str, Any],
    yes: bool = False,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Apply the fixes for one issue, then run verify commands.

    Returns {id, fixed: [...], skipped: [...], verified: bool}.
    """
    fixed, skipped = [], []
    for fix in issue.get("fixes", []):
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
        cwd = resolve_cwd(fix.get("cwd", "")) if fix.get("cwd") else None
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
    for v in issue.get("verify", []):
        platform = v.get("platform")
        if platform == "windows" and not _is_windows():
            continue
        if platform == "posix" and _is_windows():
            continue
        result = run(v["cmd"], timeout=v.get("timeout", 60))
        ok = result["ok"] and ("STILL-MISSING" not in result["stdout"])
        if not ok:
            verified = False
        if not quiet:
            print(f"    [VERIFY{' OK' if ok else ' FAIL'}] {v['name']}")
            if result["stdout"]:
                print(f"          {result['stdout'][:300]}")
    return {"id": issue["id"], "fixed": fixed, "skipped": skipped, "verified": verified}


def auto_fix(catalog: Dict[str, Any], quiet: bool = False) -> Dict[str, Any]:
    """Check every issue; auto-apply fixes for broken ones. Watchdog/cron mode."""
    report = {"checked": [], "fixed": [], "unfixed": []}
    for issue in catalog["issues"]:
        print(f"\n== {issue['id']}: {issue['title']}")
        state = check_issue(issue, quiet=quiet)
        report["checked"].append({"id": issue["id"], "broken": state["broken"]})
        if state["broken"]:
            print("   -> broken, applying fixes...")
            outcome = apply_issue(issue, yes=True, quiet=quiet)
            if outcome["verified"]:
                report["fixed"].append(issue["id"])
                print("   -> verified OK")
            else:
                report["unfixed"].append(issue["id"])
                print("   -> still broken after fixes (see doc for manual steps)")
        else:
            print("   -> healthy")
    return report


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
    any_broken = False
    for issue_id in ids:
        issue = find_issue(catalog, issue_id)
        if not issue:
            print(f"unknown issue: {issue_id}", file=sys.stderr)
            return 2
        print(f"== {issue['id']}: {issue['title']}")
        state = check_issue(issue)
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
    print(f"== {issue['id']}: {issue['title']}")
    print(f"   doc: {issue.get('doc', 'n/a')}")
    outcome = apply_issue(issue, yes=args.yes)
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
    if args.command == "info":
        return cmd_info(catalog, args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
