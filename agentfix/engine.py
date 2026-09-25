#!/usr/bin/env python3
"""agentfix.engine — the one engine: checks, fixes, diagnostics.

Everything the CLI and the MCP server expose funnels through here:

  shell      run catalog commands (POSIX syntax) with per-platform shell
             selection; on Windows bare "bash" is never trusted (CreateProcess
             resolves System32's WSL launcher first — it cannot run node/npm)
  checks     catalog checks / fixes / verify with per-agent dynamic expansion;
             dynamic repairs require one explicit target agent
  network    TCP connectivity + latency via non-blocking connect + select (on
             Windows a blocking connect ignores socket timeouts for hosts that
             silently drop SYNs), plus a proxy-env report with masked creds
  config     config audit (parse errors + leaked keys), zip backup/restore
             (zip-slip guarded, restores only into currently-known dirs)
  reports    log triage, version check (GUI agents never probed — their
             ``--version`` launches the app), provider config snippets

Pure stdlib, Python 3.8+. All text reports pass through report.mask_secrets.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional, Tuple

from agentfix import catalog as cat
from agentfix import report as rep
from agentfix import state
from agentfix.result import StatusText

BACKUP_DIR = Path.home() / ".agent-fix-backups"

# ---------------------------------------------------------------- shell


def _shell_prefix() -> List[str]:
    """Pick a shell for catalog commands (written in POSIX syntax).

    - POSIX: /bin/sh
    - Windows: git-bash when available, else cmd.exe (POSIX-only checks then
      fail with a clear message, but npm/node/powershell still work).

    Git-bash is located WITHOUT relying on a bare "bash" resolving through
    PATH: CreateProcess (and shutil.which on a default PATH) can hit
    C:\\Windows\\System32\\bash.exe — the WSL launcher — which cannot run
    Windows tools like node/npm. A PATH hit is only accepted when it is not
    under System32.
    """
    if not cat.is_windows():
        return ["/bin/sh", "-c"]

    def _usable(path: str) -> bool:
        return bool(path) and os.path.exists(path) and "system32" not in path.lower()

    for cand in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if _usable(cand):
            return [cand, "-c"]
    which_bash = shutil.which("bash") or ""
    if _usable(which_bash):
        return [which_bash, "-c"]
    return ["cmd", "/c"]


_SHELL_PREFIX = _shell_prefix()


def run(
    cmd: str,
    cwd: Optional[Path] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
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
            "stdout": rep.mask_secrets((proc.stdout or "").strip()),
            "stderr": rep.mask_secrets((proc.stderr or "").strip()),
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
        return Path(base) / (parts[1] if len(parts) > 1 else "")
    return Path(template).expanduser()


def _passes(pass_spec: Optional[Dict[str, Any]], result: Dict[str, Any]) -> bool:
    """Evaluate a check's pass spec against a real process result.

    A negative string matcher is additional evidence, not permission to ignore a
    failed process.  Callers that intentionally accept a non-zero exit must
    state that explicitly with ``pass.exit``.
    """
    pass_spec = pass_spec or {}
    exit_code = result.get("exit")
    if "exit" in pass_spec:
        if exit_code != pass_spec["exit"]:
            return False
    elif not result.get("ok", False):
        return False

    stdout = str(result.get("stdout") or "")
    if "stdout_equals" in pass_spec and stdout != pass_spec["stdout_equals"]:
        return False
    if "stdout_contains" in pass_spec and not all(s in stdout for s in pass_spec["stdout_contains"]):
        return False
    if "stdout_contains_any" in pass_spec and not any(s in stdout for s in pass_spec["stdout_contains_any"]):
        return False
    if "stdout_not_contains" in pass_spec:
        low = stdout.lower()
        if any(s.lower() in low for s in pass_spec["stdout_not_contains"]):
            return False
    return True


# ---------------------------------------------------------------- network


def check_endpoint(host: str, port: int = 443, timeout: float = 5.0) -> Dict[str, Any]:
    """TCP connect test with a HARD timeout (non-blocking connect + select).

    Returns {ok, ms, error}.
    """
    t0 = datetime.datetime.now()
    # Windows defines these as WSAEINPROGRESS/WSAEWOULDBLOCK (10036/10035);
    # the POSIX names don't exist there.
    einprogress = getattr(socket, "EINPROGRESS", 10036)
    ewouldblock = getattr(socket, "EWOULDBLOCK", 10035)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        err = sock.connect_ex((host, port))
        if err not in (0, einprogress, ewouldblock):
            return {"ok": False, "ms": None, "error": f"CONNECT-REFUSED (errno {err})"}
        _, ready_w, _ = select.select([], [sock], [], timeout)
        if not ready_w:
            return {"ok": False, "ms": None, "error": f"TIMEOUT (>{timeout}s)"}
        so_err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if so_err != 0:
            return {"ok": False, "ms": None, "error": f"ERROR (errno {so_err})"}
        ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
        return {"ok": True, "ms": ms, "error": None}
    except socket.gaierror as e:
        return {"ok": False, "ms": None, "error": f"DNS-FAIL ({e})"}
    except OSError as e:
        return {"ok": False, "ms": None, "error": f"ERROR ({e})"}
    finally:
        sock.close()


def _proxy_env_report() -> List[str]:
    lines = ["PROXY ENV:"]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        v = os.environ.get(k) or os.environ.get(k.lower())
        lines.append(f"  {k:<12} = {rep.mask_url_creds(v)}")
    npm_proxy = None
    try:
        npm_proxy = subprocess.run(
            ["npm", "config", "get", "proxy"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        ).stdout
        npm_proxy = (npm_proxy or "").strip()
    except Exception:
        pass
    lines.append(f"  npm proxy  = {rep.mask_url_creds(npm_proxy)}")
    return lines


def net_result(timeout: float = 5.0, host: str = "") -> Dict[str, Any]:
    """Return a structured connectivity result for one explicit host."""
    if not host:
        raise TargetError("target host required")
    r = check_endpoint(host, 443, timeout)
    return {"host": host, "ok": bool(r["ok"]), "ms": r.get("ms"), "error": r.get("error")}


def net_text_from_result(result: Dict[str, Any], timeout: float = 5.0) -> str:
    """Format a previously computed connectivity result without re-probing."""
    host = result.get("host", "")
    lines = [f"NETWORK DIAGNOSTIC (TCP:443, timeout={timeout}s)", ""]
    if result.get("ok"):
        lines.append(f"  OK      {host}  ({result.get('ms')}ms)")
    else:
        lines.append(f"  {result.get('error') or 'UNREACHABLE':<9} {host}")
    lines.append("")
    lines += _proxy_env_report()
    return rep.mask_secrets("\n".join(lines))


def net_text(timeout: float = 5.0, host: str = "") -> str:
    """Connectivity report for one explicitly requested endpoint."""
    return net_text_from_result(net_result(timeout=timeout, host=host), timeout=timeout)


# ---------------------------------------------------------------- checks


class TargetError(ValueError):
    """The requested issue/agent target is missing or invalid."""


def resolve_agent(catalog: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    """Resolve one detected registry agent without probing the rest."""
    if not agent_id:
        raise TargetError("target agent required (pass agent_id=<agent-id>)")
    if not cat.find_agent(catalog, agent_id):
        raise TargetError(f"unknown target agent: {agent_id}")
    agent = cat.detect_agent(catalog, agent_id)
    if not agent:
        raise TargetError(f"target agent '{agent_id}' is not detected on this machine")
    return agent


def resolve_target(catalog: Dict[str, Any], issue: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    """Resolve one explicit repair target without probing other agents."""
    if not agent_id:
        raise TargetError("target agent required (pass --agent <agent-id>)")
    supported = issue.get("agents") or []
    if "all" not in supported and agent_id not in supported:
        allowed = ", ".join(supported) or "none"
        raise TargetError(f"issue '{issue['id']}' does not apply to '{agent_id}' (supported: {allowed})")
    registered = cat.find_agent(catalog, agent_id)
    if registered:
        if registered.get("command_only"):
            # The explicit registry target remains diagnosable when its binary is
            # absent; issue checks will report the real command failure.
            return {**registered, "exe": registered.get("exe")}
        return resolve_agent(catalog, agent_id)
    if agent_id in supported:
        command_agent = {"id": agent_id, "name": agent_id, "bin": [agent_id]}
        detected = cat._probe_agent(command_agent)
        if not detected:
            raise TargetError(f"target agent '{agent_id}' is not detected on this machine")
        return detected
    raise TargetError(f"unknown target agent: {agent_id}")


def _step_applies(step: Dict[str, Any], agent_id: str) -> bool:
    supported = step.get("agents")
    return not supported or "all" in supported or agent_id in supported


def _safe_detail(value: Any) -> str:
    """Return bounded, masked command output for structured and human output."""
    return rep.mask_secrets(str(value or "")).strip()


def _version_tuple(value: str) -> Tuple[int, ...]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value))
    if not match:
        raise ValueError(f"invalid version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())


def _compare_versions(left: Tuple[int, ...], right: Tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    left = tuple(left) + (0,) * (width - len(left))
    right = tuple(right) + (0,) * (width - len(right))
    return (left > right) - (left < right)


def _node_satisfies(version: str, requirement: str) -> bool:
    actual = _version_tuple(version)
    for clause in str(requirement or ">=20").split("||"):
        matched = True
        for term in clause.split():
            match = re.match(r"(>=|<=|>|<|==)?\s*(\d+(?:\.\d+){0,2})$", term)
            if not match:
                matched = False
                break
            op = match.group(1) or "=="
            expected = _version_tuple(match.group(2))
            comparison = _compare_versions(actual, expected)
            if op == ">=" and comparison < 0:
                matched = False
            elif op == "<=" and comparison > 0:
                matched = False
            elif op == ">" and comparison <= 0:
                matched = False
            elif op == "<" and comparison >= 0:
                matched = False
            elif op == "==" and comparison != 0:
                matched = False
        if matched:
            return True
    return False


def _node_requirement_result(step: Dict[str, Any], agent_id: Optional[str] = None) -> Dict[str, Any]:
    agent = step.get("_agent") or {}
    requirement = step.get("requirement") or agent.get("node_requirement") or ">=20"
    probe = run("node -p process.versions.node 2>&1", timeout=15)
    if not probe.get("ok"):
        return {"name": step.get("name", "node version"), "status": "INCONCLUSIVE", "detail": "node version unavailable", "exit": None, "agent": agent_id}
    match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", probe.get("stdout", ""))
    if not match:
        return {"name": step.get("name", "node version"), "status": "INCONCLUSIVE", "detail": "node version output was not parseable", "exit": None, "agent": agent_id}
    version = match.group(1)
    passed = _node_satisfies(version, requirement)
    return {
        "name": step.get("name", "node version"),
        "status": "PASS" if passed else "FAIL",
        "detail": f"node {version}; required {requirement}",
        "exit": 0 if passed else 1,
        "agent": agent_id,
    }


def _platform_applies(platform: Optional[str]) -> bool:
    if not platform or platform in {"any", "all"}:
        return True
    if platform == "windows":
        return cat.is_windows()
    if platform == "posix":
        return not cat.is_windows()
    return False


def _rollup_status(results: List[Dict[str, Any]]) -> str:
    """Aggregate conservatively: only an all-PASS set is healthy."""
    if any(r.get("status") == "FAIL" for r in results):
        return "FAIL"
    if any(r.get("status") == "INCONCLUSIVE" for r in results):
        return "INCONCLUSIVE"
    if results and all(r.get("status") == "PASS" for r in results):
        return "PASS"
    return "INCONCLUSIVE"


def _expand_steps(issue: Dict[str, Any], field: str, agent: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = []
    for template in issue.get(field, []):
        if not _step_applies(template, agent["id"]):
            continue
        step = dict(template)
        for key in ("name", "cmd", "cwd"):
            if key in step:
                step[key] = cat.expand(step.get(key, ""), agent)
        step["_agent"] = agent
        steps.append(step)
    return steps


def check_issue(
    issue: Dict[str, Any],
    agent: Dict[str, Any],
    quiet: bool = False,
) -> Dict[str, Any]:
    """Run one issue's checks for exactly one explicit target agent."""
    agent_id = agent.get("id", "")
    if not agent_id:
        raise TargetError("target agent required")
    checks = _expand_steps(issue, "checks", agent)
    if not checks:
        return {
            "status": "INCONCLUSIVE",
            "broken": False,
            "results": [],
            "detail": f"issue '{issue['id']}' has no applicable checks for target '{agent_id}' on this platform",
        }
    results = []
    for check in checks:
        if agent.get("no_version") and "--version" in check.get("cmd", ""):
            detail = "GUI app — binary presence is not authoritative; functional status inconclusive"
            status = "INCONCLUSIVE"
            if not quiet:
                print(f"    [{status}] [{agent_id}] {check.get('name', '')}")
                print(f"          {detail}")
            results.append(
                {"name": check.get("name", ""), "status": status, "detail": detail, "exit": None, "agent": agent_id}
            )
            continue
        results.append(_run_one_check(check, quiet, agent_id))
    status = _rollup_status(results)
    return {
        "id": issue["id"],
        "title": issue["title"],
        "target": agent_id,
        "results": results,
        "broken": status == "FAIL",
        "status": status,
    }


def _credential_result(step: Dict[str, Any], agent_id: Optional[str] = None) -> Dict[str, Any]:
    agent = step.get("_agent") or {}
    keys = cat.provider_keys(agent)
    if not keys:
        return {
            "name": step.get("name", "credential"),
            "status": "INCONCLUSIVE",
            "detail": "no agent-owned credential variables declared",
            "exit": None,
            "agent": agent_id,
        }
    present = [key for key in keys if (os.environ.get(key) or os.environ.get(key.lower()) or "").strip()]
    detail = (
        f"KEY-SET ({len(present)} non-empty variable(s)); credential validity is unverified"
        if present
        else "declared credential variables are unset or empty"
    )
    return {
        "name": step.get("name", "credential"),
        "status": "INCONCLUSIVE",
        "detail": detail,
        "exit": None,
        "agent": agent_id,
    }


def _run_one_check(
    check: Dict[str, Any],
    quiet: bool = False,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    if check.get("kind") == "env_keys":
        return _credential_result(check, agent_id)
    if check.get("kind") == "node_requirement":
        return _node_requirement_result(check, agent_id)
    platform = check.get("platform")
    if not _platform_applies(platform):
        detail = f"unsupported platform: {platform}" if platform else "platform unavailable"
        return {"name": check["name"], "status": "SKIPPED", "detail": detail, "exit": None, "agent": agent_id}
    if check.get("kind") == "net":
        r = check_endpoint(
            check.get("host", ""),
            port=int(check.get("port", 443)),
            timeout=float(check.get("timeout", 5)),
        )
        passed = r["ok"]
        exit_code = 0 if passed else 1
        detail = f"ok ({r['ms']}ms)" if r["ok"] else (r["error"] or "unreachable")
    else:
        result = run(check["cmd"], timeout=check.get("timeout", 30))
        passed = _passes(check.get("pass"), result)
        exit_code = result.get("exit", 0 if passed else 1)
        detail = _safe_detail(result.get("stdout") or result.get("stderr"))
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
        "exit": exit_code,
        "agent": agent_id,
    }


# ---------------------------------------------------------------- fixes


def _plan_fixes(
    issue: Dict[str, Any], agent: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return the same applicability plan for dry-run and real application."""
    plans: List[Dict[str, Any]] = []
    for fix in _expand_steps(issue, "fixes", agent):
        plan: Dict[str, Any] = {"fix": fix, "status": "AUTO", "reason": "", "cwd": None}
        platform = fix.get("platform")
        if not _platform_applies(platform):
            plan["status"] = "SKIP"
            plan["reason"] = f"unsupported platform: {platform}" if platform else "platform unavailable"
            plans.append(plan)
            continue
        if fix.get("manual"):
            plan["status"] = "MANUAL"
            plan["reason"] = "manual (see doc)"
            plans.append(plan)
            continue
        cwd_tpl = fix.get("cwd") or ""
        if cwd_tpl.startswith("npm_root/") and not agent.get("npm_pkg"):
            plan["status"] = "SKIP"
            plan["reason"] = "agent is not npm-installed (native/desktop)"
            plans.append(plan)
            continue
        cwd = resolve_cwd(cwd_tpl) if cwd_tpl else None
        if cwd_tpl and (cwd is None or not cwd.exists()):
            plan["status"] = "SKIP"
            plan["reason"] = f"dir not found: {cwd or cwd_tpl}"
            plans.append(plan)
            continue
        plan["cwd"] = cwd
        plans.append(plan)
    return plans


def apply_issue(
    issue: Dict[str, Any],
    agent: Dict[str, Any],
    yes: bool = False,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Apply and verify one issue for exactly one explicit target agent."""
    agent_id = agent.get("id", "")
    if not agent_id:
        raise TargetError("target agent required")
    plans = _plan_fixes(issue, agent)
    if not plans:
        raise TargetError(f"issue '{issue['id']}' has no fixes for target '{agent_id}'")
    fixed, skipped = [], []
    fix_failed = False
    fix_executed = False
    fix_incomplete = False

    for plan in plans:
        fix = plan["fix"]
        if plan["status"] != "AUTO":
            fix_incomplete = True
            reason = plan["reason"]
            skipped.append({"name": fix["name"], "reason": reason})
            if not quiet:
                print(f"    [SKIP] {fix['name']} ({reason})")
                if plan["status"] == "MANUAL":
                    print(f"          {_safe_detail(fix.get('cmd', '').replace(chr(10), ' '))[:200]}")
            continue
        cwd = plan.get("cwd")
        if not quiet:
            print(f"    [FIX ] {fix['name']}")
        if not yes:
            try:
                print("          run this fix? [y/N] ", end="", file=sys.stderr, flush=True)
                answer = sys.stdin.readline().strip().lower()
            except EOFError:
                answer = "n"
            if answer not in ("y", "yes"):
                fix_incomplete = True
                skipped.append({"name": fix["name"], "reason": "declined"})
                continue
        fix_executed = True
        result = run(fix["cmd"], cwd=cwd, timeout=fix.get("timeout", 300))
        if result["ok"]:
            fixed.append({"name": fix["name"]})
            if not quiet:
                print(f"          ok ({result['duration']}s)")
                detail = _safe_detail(result.get("stdout"))
                if detail:
                    print(f"          {detail[:300]}")
        else:
            fix_failed = True
            reason = f"failed: {_safe_detail(result.get('stderr') or result.get('stdout'))[:200]}"
            skipped.append({"name": fix["name"], "reason": reason})
            if not quiet:
                print(f"          FAILED: {reason[len('failed: '):]}")

    verifies = _expand_steps(issue, "verify", agent)
    if not verifies:
        raise TargetError(f"issue '{issue['id']}' has no verification for target '{agent_id}'")

    manual_required = any(plan.get("status") == "MANUAL" for plan in plans)
    verification_results: List[Dict[str, Any]] = []
    if manual_required or not fix_executed or fix_failed or fix_incomplete:
        verification_status = "inconclusive"
    else:
        for verify in verifies:
            verification_results.append(_run_one_verify_result(verify, quiet=quiet))
        if any(r.get("status") == "FAIL" for r in verification_results):
            verification_status = "failed"
        elif verification_results and all(r.get("status") == "PASS" for r in verification_results):
            verification_status = "verified"
        else:
            verification_status = "inconclusive"

    return {
        "id": issue["id"],
        "target": agent_id,
        "fixed": fixed,
        "skipped": skipped,
        "verified": verification_status == "verified",
        "verification_status": verification_status,
        "manual_required": manual_required,
        "verification_results": verification_results,
    }


def _run_one_verify_result(
    v: Dict[str, Any],
    quiet: bool = False,
) -> Dict[str, Any]:
    """Run one verification step and preserve pass/fail/inconclusive."""
    if v.get("kind") == "env_keys":
        return _credential_result(v)
    if v.get("kind") == "node_requirement":
        return _node_requirement_result(v)
    agent = v.get("_agent") or {}
    if agent.get("no_version") and "--version" in v.get("cmd", ""):
        return {
            "name": v.get("name", ""),
            "status": "INCONCLUSIVE",
            "detail": "presence-only check; --version intentionally not launched",
            "exit": None,
        }
    platform = v.get("platform")
    if not _platform_applies(platform):
        detail = f"unsupported platform: {platform}" if platform else "platform unavailable"
        return {"name": v["name"], "status": "SKIPPED", "detail": detail, "exit": None}
    if v.get("kind") == "net":
        result = check_endpoint(
            v.get("host", ""),
            port=int(v.get("port", 443)),
            timeout=float(v.get("timeout", 5)),
        )
        passed = bool(result.get("ok"))
        exit_code = 0 if passed else 1
        detail = f"ok ({result['ms']}ms)" if passed else (result.get("error") or "unreachable")
    else:
        run_result = run(v["cmd"], timeout=v.get("timeout", 30))
        passed = _passes(v.get("pass"), run_result)
        if "STILL-MISSING" in str(run_result.get("stdout") or ""):
            passed = False
        exit_code = run_result.get("exit", 0 if passed else 1)
        detail = _safe_detail(run_result.get("stdout") or run_result.get("stderr"))

    status = "PASS" if passed else "FAIL"
    if not quiet:
        print(f"    [{status}] {v.get('name', '')}")
        if detail and not passed:
            print(f"          {detail[:400]}")
    return {"name": v.get("name", ""), "status": status, "detail": detail, "exit": exit_code}


def _run_one_verify(
    v: Dict[str, Any],
    verified: bool,
    quiet: bool = False,
) -> bool:
    """Compatibility wrapper for callers that only need a boolean."""
    return bool(verified and _run_one_verify_result(v, quiet=quiet)["status"] == "PASS")


def check_result(issue_id: str, agent_id: str) -> Dict[str, Any]:
    """Resolve and diagnose one explicit issue/agent pair."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        raise TargetError(f"unknown issue: {issue_id} (try `fix list` for ids)")
    agent = resolve_target(catalog, issue, agent_id)
    return {
        "issue": issue,
        "agent": agent,
        "state": check_issue(issue, agent=agent, quiet=True),
    }


def format_check_result(result: Dict[str, Any]) -> str:
    """Format a structured check result for human/MCP output."""
    issue = result["issue"]
    agent = result["agent"]
    state = result["state"]
    lines = [f"== {issue['id']}: {issue['title']} [{agent.get('id', '')}]", ""]
    for r in state["results"]:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIPPED": "[SKIPPED]", "INCONCLUSIVE": "[INCONCLUSIVE]"}[r["status"]]
        prefix = f"[{r['agent']}] " if r.get("agent") else ""
        lines.append(f"  {mark} {prefix}{r['name']}")
        if r["status"] in {"FAIL", "INCONCLUSIVE"} and r.get("detail"):
            lines.append(f"        {r['detail'][:300]}")
    if state.get("detail"):
        lines.append(f"        {rep.mask_secrets(str(state['detail']))[:300]}")
    lines.append("")
    if state.get("status") == "FAIL" or state.get("broken"):
        lines.append("=> broken")
    elif state.get("status") == "INCONCLUSIVE":
        lines.append("=> inconclusive")
    else:
        lines.append("=> healthy")
    return rep.mask_secrets("\n".join(lines))


def check_text(issue_id: str, agent_id: str) -> str:
    """Diagnostics for one issue and one explicitly named agent."""
    return format_check_result(check_result(issue_id, agent_id))


def apply_result(issue_id: str, agent_id: str, confirm: bool = False) -> Dict[str, Any]:
    """Resolve and either plan or execute one explicit issue/agent repair."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        raise TargetError(f"unknown issue: {issue_id} (try `fix list` for ids)")
    agent = resolve_target(catalog, issue, agent_id)
    if not confirm:
        return {"issue": issue, "agent": agent, "dry_run": True}
    return {
        "issue": issue,
        "agent": agent,
        "dry_run": False,
        "outcome": apply_issue(issue, agent=agent, yes=True, quiet=True),
    }


def format_apply_result(result: Dict[str, Any]) -> str:
    """Format a structured apply result without reparsing human text."""
    issue = result["issue"]
    agent = result["agent"]
    if result.get("dry_run"):
        lines = [
            f"== {issue['id']}: {issue['title']} [{agent.get('id', '')}]",
            "",
            "DRY RUN — these targeted fixes would run (pass confirm=true to execute):",
            "",
        ]
        for plan in _plan_fixes(issue, agent):
            fix = plan["fix"]
            if plan["status"] == "AUTO":
                kind = "[AUTO]  "
            elif plan["status"] == "MANUAL":
                kind = "[MANUAL]"
            else:
                kind = "[SKIP]  "
            lines.append(f"  {kind} {fix.get('name', '')}")
            if plan.get("reason"):
                lines.append(f"        {plan['reason']}")
            if plan["status"] == "AUTO":
                lines.append(f"        $ {_safe_detail(fix.get('cmd', '').replace(chr(10), ' '))[:200]}")
        lines.append("")
        lines.append("Tip: run `check` for this same issue and agent first.")
        return rep.mask_secrets("\n".join(lines))

    out = result["outcome"]
    lines = [f"== {issue['id']}: {issue['title']} [{agent.get('id', '')}]", ""]
    for f in out["fixed"]:
        lines.append(f"  [FIXED] {f['name']}")
    for s in out["skipped"]:
        lines.append(f"  [SKIP]  {s['name']} ({s['reason']})")
    lines.append("")
    if out["verified"]:
        lines.append("=> verified OK")
    elif out.get("verification_status") == "failed":
        lines.append("=> verification failed")
    else:
        lines.append(f"=> not fully verified (see {issue.get('doc', 'the doc')})")
    return rep.mask_secrets("\n".join(lines))


def apply_text(issue_id: str, agent_id: str, confirm: bool = False) -> str:
    """Apply one issue for one explicit agent, or show its targeted fixes."""
    return format_apply_result(apply_result(issue_id, agent_id, confirm=confirm))


def info_text(issue_id: str) -> str:
    """The knowledge-base doc for an issue id."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        raise TargetError(f"unknown issue: {issue_id}")
    doc = cat.doc_path(issue)
    if doc.exists():
        return rep.data_tag("fix doc") + "\n" + rep.mask_secrets(doc.read_text(encoding="utf-8"))
    raise TargetError(f"doc missing for {issue_id}: {doc}")


def agents_text() -> str:
    """Which agents are INSTALLED on this machine.

    Only installed agents are ever listed — an agent that is not present on
    the machine is deliberately never named, so the tool cannot guide anyone
    toward (or expose the package names of) agents they do not already have.
    """
    catalog = cat.load_catalog()
    detected = cat.detect_agents(catalog)
    if not detected:
        return "no agents detected on this machine"
    lines = [f"installed agents: {len(detected)}", ""]
    for a in detected:
        bins = ", ".join(a.get("bin", []))
        npm = a.get("npm_pkg") or "-"
        exe = a.get("exe") or ""
        lines.append(f"  {a.get('name', a['id']):<26} {bins:<22} {npm:<34} {exe}")
    return "\n".join(lines)


# version hints for native/desktop agents (no npm registry to query)
_UPDATE_HINTS = {
    "kimi-code": "update: npm install -g @moonshot-ai/kimi-code@latest",
    "minimax-code": "update: npm install -g @minimax-ai/code@latest",
    "hermes": "run: hermes update",
    "zcode": "update via ZCode Desktop",
    "cursor": "update via Cursor app",
    "amp": "run: amp upgrade",
    "droid": "update via Droid app",
    "dsh": "update: npm install -g @deepseek-ai/dsh@latest",
}


def _resolve_bin(name: str) -> str:
    """Resolve a bare command name via PATH (CreateProcess can't on Windows)."""
    return shutil.which(name) or name


def _version_probe(args: List[str], timeout: int) -> Dict[str, Any]:
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=timeout
        )
        output = (r.stdout or r.stderr or "").strip().splitlines()
        value = output[0][:120] if output else ""
        if r.returncode != 0:
            return {"ok": False, "value": f"ERROR exit {r.returncode}: {value or 'no output'}"}
        if not value:
            return {"ok": False, "value": "ERROR: command returned no version"}
        return {"ok": True, "value": value}
    except FileNotFoundError:
        return {"ok": False, "value": "ERROR: command not found"}
    except Exception as exc:
        return {"ok": False, "value": f"ERROR: {rep.mask_secrets(str(exc))}"}


def versions_result(agent_id: str) -> Dict[str, Any]:
    """Return installed/latest version evidence without treating errors as versions."""
    agent = resolve_agent(cat.load_catalog(), agent_id)
    name = agent.get("name", agent.get("id"))
    npm_pkg = agent.get("npm_pkg")
    bin_name = (agent.get("bin") or ["?"])[0]
    if agent.get("no_version"):
        hint = _UPDATE_HINTS.get(agent.get("id"), "update via the app")
        return {
            "agent": agent.get("id"),
            "name": name,
            "status": "INCONCLUSIVE",
            "installed": "GUI app, not probed",
            "latest": None,
            "hint": hint,
        }
    installed = _version_probe([_resolve_bin(bin_name), "--version"], 20)
    latest = None
    if npm_pkg:
        latest = _version_probe([_resolve_bin("npm"), "view", npm_pkg, "version"], 30)
    status = "PASS" if installed["ok"] and (latest is None or latest["ok"]) else "INCONCLUSIVE"
    return {
        "agent": agent.get("id"),
        "name": name,
        "status": status,
        "installed": installed["value"],
        "latest": latest["value"] if latest else None,
        "installed_ok": installed["ok"],
        "latest_ok": latest["ok"] if latest else None,
    }


def versions_text_from_result(result: Dict[str, Any]) -> str:
    """Format structured version evidence without re-running probes."""
    name = result.get("name", result.get("agent", "agent"))
    if result.get("status") == "INCONCLUSIVE" and result.get("installed") == "GUI app, not probed":
        return rep.mask_secrets(
            "\n".join(
                ["VERSION CHECK", "", f"  {name:<26} installed=(GUI app, not probed) update: {result.get('hint', 'update via the app')}", "=> inconclusive"]
            )
        )
    if result.get("latest") is not None:
        lines = [f"  {name:<26} installed={result.get('installed', ''):<40} latest={result.get('latest', '')}"]
    else:
        hint = _UPDATE_HINTS.get(result.get("agent"), "reinstall per official docs")
        lines = [f"  {name:<26} installed={result.get('installed', ''):<40} update: {hint}"]
    lines.append(f"=> {str(result.get('status', 'INCONCLUSIVE')).lower()}")
    return rep.mask_secrets("\n".join(["VERSION CHECK", ""] + lines))


def versions_text(agent_id: str) -> str:
    """Installed vs latest version for one explicitly selected agent."""
    return versions_text_from_result(versions_result(agent_id))


# ---------------------------------------------------------------- config data

_CONFIG_EXTS = {".json", ".toml", ".yaml", ".yml", ".env", ".ini", ".cfg"}

_NOISE_DIRS = {
    "node_modules", ".git", "sessions", "logs", "cache", ".cache",
    "__pycache__", "plugins", "projects", "shell-snapshots", "todos",
    "statsig", "file-history", "lsp", "tmp", "history", "rollouts",
    ".venv", "venv", "telemetry",
}

# (label, regex) — same patterns as report.SECRET_REGEXES, with labels for the audit
_KEY_PATTERNS = [
    ("anthropic/openai/deepseek key", re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b")),
    ("github PAT", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b")),
    ("github fine-grained", re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws access key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("google api key", re.compile(r"\b(AIza[0-9A-Za-z_-]{20,})\b")),
]


def _walk_files(
    base: Path,
    max_depth: int = 3,
    suffixes: Optional[set] = None,
    skipped: Optional[List[Path]] = None,
    walk_errors: Optional[List[OSError]] = None,
) -> List[Path]:
    """Files under base, depth-bounded and noise-dir pruned (stdlib os.walk).

    Pruning happens during traversal (not on the results), so huge noise trees
    like node_modules inside agent config dirs cost nothing to walk.
    """
    state.ensure_safe_path(base)
    out: List[Path] = []
    base_parts = len(base.parts)
    for root, dirs, names in os.walk(str(base), onerror=(walk_errors.append if walk_errors is not None else None)):
        depth = len(Path(root).parts) - base_parts
        kept_dirs = []
        for d in dirs:
            child = Path(root) / d
            try:
                state.ensure_safe_path(child)
            except state.StateError:
                if skipped is not None:
                    skipped.append(child)
                continue
            if d in _NOISE_DIRS or depth >= max_depth:
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs
        for name in names:
            file_path = Path(root) / name
            try:
                state.ensure_safe_path(file_path)
                state.ensure_regular_file(file_path)
            except state.StateError:
                if skipped is not None:
                    skipped.append(file_path)
                continue
            if name in _NOISE_DIRS:
                continue
            if suffixes is not None and file_path.suffix not in suffixes:
                continue
            out.append(file_path)
    return out


def _iter_backup_files(base: Path, max_depth: int = 4, max_files: int = 2000, max_bytes: int = 20_000_000):
    """Return bounded regular files plus explicit truncation/oversize evidence."""
    out: List[Path] = []
    skipped: List[Path] = []
    total = 0
    truncated = False
    state.ensure_safe_path(base)
    base_parts = len(base.parts)
    def raise_walk_error(error: OSError) -> None:
        raise error

    for root, dirs, names in os.walk(str(base), onerror=raise_walk_error):
        depth = len(Path(root).parts) - base_parts
        kept_dirs = []
        for directory in dirs:
            child = Path(root) / directory
            state.ensure_safe_path(child)
            if directory in _NOISE_DIRS or depth >= max_depth:
                continue
            kept_dirs.append(directory)
        dirs[:] = kept_dirs
        for name in names:
            p = Path(root) / name
            state.ensure_safe_path(p)
            state.ensure_regular_file(p)
            if name in _NOISE_DIRS:
                continue
            try:
                size = p.stat().st_size
            except OSError as exc:
                raise state.StateError(f"cannot stat backup source: {p}") from exc
            if size >= max_bytes:
                skipped.append(p)
                continue
            if len(out) >= max_files or total + size > max_bytes:
                truncated = True
                dirs[:] = []
                break
            out.append(p)
            total += size
        if truncated:
            break
    return out, truncated, skipped


def _snapshot_targets(agent_id: str) -> List[Path]:
    """Return the config dir for one explicitly selected agent."""
    agent = resolve_agent(cat.load_catalog(), agent_id)
    cfg = cat.config_path(agent)
    if not cfg:
        return []
    p = Path(cfg.replace("/", os.sep))
    state.ensure_safe_path(p)
    if not p.exists():
        return []
    if not p.is_dir():
        raise state.StateError(f"agent config path is not a regular directory: {p}")
    return [p]


def _safe_rel(rel: str) -> bool:
    """True only if a zip member path can't escape its base dir (zip-slip guard)."""
    p = Path(rel)
    # p.root catches "/"-anchored paths that is_absolute() misses on Windows
    # (drive-relative), p.drive catches "C:evil" style paths.
    return bool(rel) and not (p.is_absolute() or p.drive or p.root or ".." in p.parts)


def _inside(base: Path, child: Path) -> bool:
    """True if child resolves inside base (containment check, belt-and-braces)."""
    try:
        return os.path.commonpath([str(base.resolve()), str(child.resolve())]) == str(base.resolve())
    except (ValueError, RuntimeError):
        return False


def _same_path(left: Path, right: Path) -> bool:
    """Compare normalized absolute paths for backup ownership checks."""
    left_text = os.path.normcase(os.path.abspath(os.path.expanduser(str(left))))
    right_text = os.path.normcase(os.path.abspath(os.path.expanduser(str(right))))
    return left_text == right_text


def audit_result(agent_id: str, depth: int = 3) -> Dict[str, Any]:
    """Scan one explicit config tree and report incomplete evidence honestly."""
    agent = resolve_agent(cat.load_catalog(), agent_id)
    cfg = cat.config_path(agent)
    if not cfg:
        return {"agent": agent.get("id"), "status": "INCONCLUSIVE", "reason": "no config dir for target agent", "files": [], "parse_errors": [], "leaks": []}
    base = Path(cfg.replace("/", os.sep))
    try:
        state.ensure_safe_path(base)
    except state.StateError as exc:
        return {"agent": agent.get("id"), "status": "INCONCLUSIVE", "reason": rep.mask_secrets(str(exc)), "files": [], "parse_errors": [], "leaks": []}
    if not base.exists():
        return {"agent": agent.get("id"), "status": "INCONCLUSIVE", "reason": "no agent config dir found", "files": [], "parse_errors": [], "leaks": []}
    if not base.is_dir():
        return {"agent": agent.get("id"), "status": "INCONCLUSIVE", "reason": f"agent config path is not a regular directory: {base}", "files": [], "parse_errors": [], "leaks": []}

    skipped_symlinks: List[Path] = []
    walk_errors: List[OSError] = []
    try:
        candidates = _walk_files(
            base,
            max_depth=max(1, int(depth)),
            suffixes=_CONFIG_EXTS,
            skipped=skipped_symlinks,
            walk_errors=walk_errors,
        )
    except state.StateError as exc:
        return {
            "agent": agent.get("id"),
            "name": agent.get("name", agent.get("id")),
            "base": str(base),
            "status": "INCONCLUSIVE",
            "reason": rep.mask_secrets(str(exc)),
            "files": [],
            "parse_errors": [],
            "leaks": [],
        }
    except OSError as exc:
        walk_errors.append(exc)
        candidates = []
    candidates_seen = len(candidates) + len(skipped_symlinks)
    files: List[Path] = []
    oversize: List[Path] = []
    stat_errors: List[Path] = []
    for f in candidates:
        try:
            state.ensure_regular_file(f)
            if f.stat().st_size < 2_000_000:
                files.append(f)
            else:
                oversize.append(f)
        except (OSError, state.StateError):
            stat_errors.append(f)
    selected = files[:200]
    truncated = len(files) > 200
    parse_errors, leaks = [], []
    read_errors: List[Path] = []
    for f in selected:
        try:
            state.ensure_safe_path(f)
            state.ensure_regular_file(f)
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, state.StateError):
            read_errors.append(f)
            continue
        if f.suffix == ".json":
            try:
                json.loads(text)
            except Exception as exc:
                parse_errors.append(f"{f.name}: {exc}")
        for label, pat in _KEY_PATTERNS:
            for match in pat.findall(text):
                leaks.append(f"{label}: {rep.mask(match)} (in {f.name})")
    incomplete = bool(
        truncated
        or oversize
        or stat_errors
        or read_errors
        or walk_errors
        or skipped_symlinks
    )
    status = "FAIL" if parse_errors or leaks else ("INCONCLUSIVE" if incomplete else "PASS")
    return {
        "agent": agent.get("id"),
        "name": agent.get("name", agent.get("id")),
        "base": str(base),
        "status": status,
        "reason": "scan incomplete" if incomplete else "complete",
        "candidate_count": candidates_seen,
        "scanned_count": len(selected) - len(read_errors),
        "selected_count": len(selected),
        "truncated": truncated,
        "oversize_count": len(oversize),
        "stat_error_count": len(stat_errors),
        "read_error_count": len(read_errors),
        "walk_error_count": len(walk_errors),
        "symlink_count": len(skipped_symlinks),
        "files": [str(f) for f in selected],
        "parse_errors": parse_errors,
        "leaks": leaks,
    }


def audit_text_from_result(result: Dict[str, Any]) -> str:
    """Format audit evidence without implying unscanned files are safe."""
    if not result.get("base") and result.get("reason"):
        return f"AUDIT STATUS: {result.get('status', 'INCONCLUSIVE')}\nreason: {result['reason']}"
    lines = [
        rep.data_tag("local config scan"),
        "",
        f"== {result.get('name', result.get('agent'))} ({result.get('base')})",
        f"   AUDIT STATUS: {result.get('status', 'INCONCLUSIVE')}",
        f"   config files discovered: {result.get('candidate_count', 0)}",
        f"   config files scanned: {result.get('scanned_count', 0)}",
    ]
    if result.get("truncated"):
        lines.append("   warning: file-count limit reached; scan is incomplete")
    skipped = sum(
        int(result.get(key, 0))
        for key in ("oversize_count", "stat_error_count", "read_error_count", "walk_error_count", "symlink_count")
    )
    if skipped:
        lines.append(f"   warning: skipped/unreadable entries: {skipped}; scan is incomplete")
    errors = result.get("parse_errors") or []
    lines.append(f"   PARSE ERRORS ({len(errors)}):")
    lines.extend(f"     - {e}" for e in errors[:10]) if errors else lines.append("   no parse errors in scanned files")
    leaks = result.get("leaks") or []
    lines.append(f"   LEAKED KEYS ({len(leaks)}):")
    if leaks:
        lines.extend(f"     - {leak}" for leak in leaks[:20])
    elif result.get("status") == "PASS":
        lines.append("   no leaked keys detected in scanned files")
    else:
        lines.append("   no leaks observed in scanned files; overall scan is incomplete")
    return rep.mask_secrets("\n".join(lines))


def audit_text(agent_id: str, depth: int = 3) -> str:
    """Scan one explicit agent config dir: parse errors + leaked API keys."""
    return audit_text_from_result(audit_result(agent_id, depth=depth))


def backup_text(agent_id: str) -> str:
    """Snapshot one explicit agent config dir into a private atomic archive."""
    try:
        targets = _snapshot_targets(agent_id)
    except state.StateError as exc:
        return StatusText(rep.mask_secrets(f"backup refused: {exc}"), "error")
    except (OSError, ValueError) as exc:
        return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
    if not targets:
        return "no target agent config dir found to back up"
    try:
        state.ensure_safe_path(BACKUP_DIR)
        dest = state.unique_backup_path(BACKUP_DIR, f"agent-config-{agent_id}")
    except state.StateError as exc:
        return StatusText(rep.mask_secrets(f"backup refused: {exc}"), "error")
    except (OSError, ValueError) as exc:
        return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
    manifest: Dict[str, str] = {}
    notes: List[str] = []
    members: List[Tuple[str, Path]] = []
    for i, base in enumerate(targets):
        try:
            state.ensure_safe_path(base)
            # Normalize without resolving symlinks; resolution here could turn a
            # post-check root replacement into an apparently safe external path.
            base = Path(os.path.abspath(os.path.expanduser(str(base))))
            state.ensure_safe_path(base)
        except state.StateError as exc:
            return StatusText(rep.mask_secrets(f"backup refused: {exc}"), "error")
        except (OSError, ValueError) as exc:
            return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
        prefix = f"{i:02d}-{base.name}"
        manifest[prefix] = str(base)
        try:
            files, truncated, skipped = _iter_backup_files(base)
        except state.StateError as exc:
            return StatusText(rep.mask_secrets(f"backup refused: {exc}"), "error")
        except (OSError, ValueError) as exc:
            return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
        for file_path in files:
            members.append((f"{prefix}/{file_path.relative_to(base).as_posix()}", file_path))
        if truncated:
            notes.append(
                f"  note: {base} exceeded the snapshot budget (2000 files / 20 MB) — deep bulk skipped, top-level configs kept"
            )
        if skipped:
            notes.append(
                f"  note: {base} skipped {len(skipped)} oversized file(s) at/above 20 MB"
            )
    try:
        state.create_zip_atomic(dest, members, manifest)
    except (OSError, state.StateError, ValueError) as exc:
        return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
    try:
        size = dest.stat().st_size
    except OSError as exc:
        return StatusText(rep.mask_secrets(f"backup failed: {exc}"), "error")
    lines = [
        f"backup created: {dest}",
        f"size: {size/1024:.1f} KB | files: {len(members)}",
    ]
    lines += notes
    lines.append("note: contains plaintext configs (may include API keys) — kept local & private")
    return "\n".join(lines)


def _restore_from_snapshot(
    snapshot: Path, archive_data: bytes, target: Path, agent_id: str
):
    """Validate one immutable archive byte buffer and restore its owned members."""
    manifest, members = state.read_backup_layout_bytes(archive_data)
    known: Dict[str, Path] = {}
    for path in _snapshot_targets(agent_id):
        state.ensure_safe_path(path)
        known[path.name] = path
    prefixes = {
        member.split("/", 1)[0]
        for member in members
        if member != "_manifest.json" and "/" in member
    }
    if not manifest:
        raise state.StateError(f"backup {target.name} manifest is empty — refusing to restore")
    if prefixes and set(manifest) != prefixes:
        raise state.StateError(
            f"backup {target.name} manifest does not match its members — refusing to restore"
        )
    for prefix, original in manifest.items():
        pname = prefix.split("-", 1)[1] if "-" in prefix else prefix
        base = known.get(pname)
        if not pname or not base or not _same_path(Path(original), base):
            raise state.StateError(
                f"backup {target.name} has an invalid target mapping — refusing to restore"
            )
    destinations: Dict[str, Path] = {}
    for member in members:
        if member == "_manifest.json":
            continue
        if "/" not in member:
            raise state.StateError(
                f"backup {target.name} contains an unowned member {member!r} — refusing to restore"
            )
        prefix, rel = member.split("/", 1)
        if prefix not in manifest:
            raise state.StateError(
                f"backup {target.name} contains an unknown member prefix {prefix!r} — refusing to restore"
            )
        pname = prefix.split("-", 1)[1] if "-" in prefix else prefix
        base = known.get(pname)
        if not base:
            raise state.StateError(
                f"backup {target.name} member prefix is not owned by the target — refusing to restore"
            )
        if not _safe_rel(rel) or not _inside(base, base / rel):
            raise state.StateError(
                f"refusing to restore {target.name}: unsafe member path {member!r} (zip-slip guard)"
            )
        destinations[member] = base / rel
    return state.restore_zip_members_bytes(
        archive_data,
        destinations,
        allowed_roots=tuple(known.values()),
        archive_path=snapshot,
    )


def restore_text(backup: Optional[str] = None, agent_id: Optional[str] = None, confirm: bool = False) -> str:
    """List backups, or restore one explicit target with process-level rollback."""
    try:
        state.ensure_safe_path(BACKUP_DIR)
    except state.StateError as exc:
        return StatusText(rep.mask_secrets(f"backup refused: {exc}"), "error")
    if not BACKUP_DIR.exists():
        return StatusText("no backups found (~/.agent-fix-backups missing)", "error" if backup else "ok")
    backups = sorted(BACKUP_DIR.glob("agent-config-*.zip"), key=state.backup_sort_key, reverse=True)
    if not backups:
        return StatusText("no backups found", "error" if backup else "ok")
    if not backup:
        lines = ["AVAILABLE BACKUPS:", ""]
        for b in backups:
            try:
                state.ensure_safe_path(b)
                size = b.stat().st_size
            except (OSError, state.StateError) as exc:
                return StatusText(rep.mask_secrets(f"backup listing refused: {exc}"), "error")
            lines.append(f"  {b.name}  ({size/1024:.1f} KB)")
        lines.append("")
        lines.append("restore with: restore(backup='<name>', agent_id='<id>', confirm=true)")
        return StatusText("\n".join(lines), "ok")
    if not agent_id:
        raise TargetError("agent_id is required when restoring a backup")
    target_agent = resolve_agent(cat.load_catalog(), agent_id)
    backup_prefix = f"agent-config-{agent_id}-"
    if backup == "latest":
        target = next((b for b in backups if b.name.startswith(backup_prefix)), None)
    else:
        target = next((b for b in backups if b.name == backup), None)
        if target is not None and not target.name.startswith(backup_prefix):
            return StatusText(rep.mask_secrets(f"backup {backup} does not belong to agent {agent_id}"), "error")
    if not target:
        return StatusText(rep.mask_secrets(f"backup not found: {backup}"), "error")
    if not confirm:
        return StatusText(f"dry run: would restore {target.name} into {target_agent['id']} — pass confirm=true to actually restore", "ok")
    snapshot = None
    result: Optional[StatusText] = None
    restored = []
    try:
        snapshot, snapshot_identity = state.snapshot_zip(target)
        archive_data = state.read_snapshotted_bytes(snapshot, snapshot_identity)
        restored = _restore_from_snapshot(snapshot, archive_data, target, agent_id)
    except (OSError, ValueError, RuntimeError, state.StateError) as exc:
        result = StatusText(rep.mask_secrets(f"restore refused: {exc}"), "error")

    cleanup_error = None
    if snapshot is not None:
        try:
            snapshot.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        cleanup_status = StatusText(
            rep.mask_secrets(
                f"restore refused: temporary snapshot cleanup failed: {cleanup_error}"
            ),
            "error",
        )
        if result is None:
            result = cleanup_status
        else:
            result = StatusText(
                rep.mask_secrets(f"{result}; temporary snapshot cleanup failed: {cleanup_error}"),
                "error",
            )
    if result is not None:
        return result
    return StatusText(
        rep.mask_secrets(
            f"restored {len(restored)} files from {target.name} into {agent_id}\n"
            + "\n".join(restored[:20])
            + ("\n..." if len(restored) > 20 else "")
        ),
        "ok",
    )


def logs_text(agent_id: str, lines: int = 30) -> str:
    """Scan one explicitly selected agent's log locations."""
    agent = resolve_agent(cat.load_catalog(), agent_id)
    pat = re.compile(r"(ERROR|WARN|Traceback|postinstall|Fatal|panic|exit code)", re.I)
    out = [rep.data_tag("local log lines"), ""]
    cfg = cat.config_path(agent)
    if not cfg:
        return "no config dir for target agent"
    base = Path(cfg.replace("/", os.sep))
    try:
        state.ensure_safe_path(base)
    except state.StateError as exc:
        return f"logs refused: {rep.mask_secrets(str(exc))}"
    logs: List[Path] = []
    skipped: List[Path] = []
    walk_errors: List[OSError] = []
    incomplete_reasons = 0
    if base.exists() and base.is_dir():
        try:
            logs = _walk_files(
                base,
                max_depth=4,
                suffixes={".log"},
                skipped=skipped,
                walk_errors=walk_errors,
            )[-5:]
        except state.StateError as exc:
            return f"logs refused: {rep.mask_secrets(str(exc))}"
        incomplete_reasons += len(skipped) + len(walk_errors)
        logdir = base / "logs"
        if logdir.exists():
            try:
                state.ensure_safe_path(logdir)
            except state.StateError as exc:
                return f"logs refused: {rep.mask_secrets(str(exc))}"
            if logdir.is_dir():
                try:
                    for p in logdir.iterdir():
                        try:
                            state.ensure_safe_path(p)
                            state.ensure_regular_file(p)
                        except state.StateError:
                            incomplete_reasons += 1
                            continue
                        logs.append(p)
                    logs = sorted(logs)[-5:]
                except OSError as exc:
                    return f"logs refused: {rep.mask_secrets(str(exc))}"
    if not logs:
        detail = "scan incomplete" if incomplete_reasons else "no log files found"
        return "\n".join(out + [f"== {agent.get('name')}", f"   {detail}"])
    hits: List[str] = []
    read_failures = 0
    for f in logs:
        try:
            state.ensure_safe_path(f)
            state.ensure_regular_file(f)
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, state.StateError):
            read_failures += 1
            continue
        for line in text.splitlines()[-500:]:
            if pat.search(line):
                hits.append(f"{f.name}: {line.strip()[:160]}")
    out.append(f"== {agent.get('name')}")
    if incomplete_reasons or read_failures:
        total = incomplete_reasons + read_failures
        out.append(f"   scan incomplete: {total} entries could not be read")
    out.extend([f"   {h}" for h in hits[-int(lines):]] or ["   no recent error/warn lines"])
    return rep.mask_secrets("\n".join(out))


# ---------------------------------------------------------------- provider

_KNOWN_PROVIDERS = {
    "deepseek": {"base": "https://api.deepseek.com", "model": "deepseek-chat", "anthropic_path": "/anthropic"},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o", "anthropic_path": None},
    "anthropic": {"base": "https://api.anthropic.com", "model": "claude-sonnet-4-5", "anthropic_path": None},
    "google": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-pro", "anthropic_path": None},
    "moonshot": {"base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "anthropic_path": None},
    "zhipu": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6", "anthropic_path": None},
    "qwen": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "anthropic_path": None},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o", "anthropic_path": None},
    "ollama": {"base": "http://localhost:11434/v1", "model": "qwen2.5-coder:latest", "anthropic_path": None},
}


def _valid_provider_base(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and not any(ch.isspace() for ch in url)
    )


def provider_text(
    provider: str = "deepseek",
    agent_id: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    apply: bool = False,
    show_key: bool = False,
) -> str:
    """Generate a provider config snippet for one explicit target agent."""
    status = "ok"
    agent = resolve_agent(cat.load_catalog(), agent_id)
    info = _KNOWN_PROVIDERS.get((provider or "").lower()) or {"base": "", "model": "", "anthropic_path": None}
    base = base_url or info.get("base", "")
    if agent.get("id") == "kimi-code" and provider.lower() in {"kimi", "moonshot"} and not base_url:
        base = "https://api.moonshot.ai/v1"
    model = model or info.get("model", "deepseek-chat")
    if not _valid_provider_base(base):
        return StatusText("error: base_url must be an HTTP(S) URL without embedded credentials", "error")
    anthropic_base = base.rstrip("/") + (info.get("anthropic_path") or "") if info.get("anthropic_path") else base
    if not base:
        return StatusText("error: unknown provider — pass base_url explicitly (see fixes/provider-config.md)", "error")
    if not api_key and provider.lower() != "ollama":
        return StatusText("error: api_key is required (leave empty only for ollama)", "error")
    key_env = rep.sanitize_id(f"AGENTFIX_{provider}_API_KEY") or "AGENTFIX_API_KEY"
    key_hint = "(local; no key required)" if provider.lower() == "ollama" else "<set your API key in the environment>"
    prov_id = rep.sanitize_id(provider) or "custom"
    model_id = rep.sanitize_id(model) or "custom"
    aid = agent.get("id")
    name = agent.get("name", aid)
    lines = [f"PROVIDER SETUP: {provider} for {name} (base={base}, model={model}, key={key_hint})", ""]
    if aid == "claude-code":
        lines += [f"  export ANTHROPIC_BASE_URL={rep.shellq(anthropic_base)}", "  # set ANTHROPIC_AUTH_TOKEN in your shell before launching Claude Code", f"  export ANTHROPIC_MODEL={rep.shellq(model)}"]
    elif aid in ("codex", "opencode", "pi", "qwen-code"):
        lines += [f"  export OPENAI_BASE_URL={rep.shellq(base)}", "  # set OPENAI_API_KEY in your shell before launching the agent"]
    elif aid == "kimi-code":
        config_home = cat.config_path(agent) or "~/.kimi-code"
        provider_type = "kimi" if provider.lower() in {"kimi", "moonshot"} else "openai"
        lines += [
            f"  # {config_home}/config.toml (merge manually; never paste the key into chat):",
            f"  # set {key_env} in your shell before launching Kimi Code",
            f'  [providers."{prov_id}"]',
            f'  type = "{provider_type}"',
            f'  base_url = "{rep.tomlq(base)}"',
            f'  api_key_env = "{key_env}"',
            f'  [models."{prov_id}/{model_id}"]',
            f'  provider = "{prov_id}"',
            f'  model = "{rep.tomlq(model)}"',
            "  # required: max_context_size = <positive integer from the provider docs>",
        ]
    elif aid == "hermes":
        lines += [f"  hermes config set provider {prov_id}", f"  hermes config set model {model_id}"]
    elif aid == "zcode":
        lines += ["  # ZCode app provider settings:", f"  Base URL: {base}", "  API key:  (set in the app's secure provider field)", f"  Model:    {model}"]
    elif aid == "gemini":
        lines.append("  # set GEMINI_API_KEY in your shell before launching Gemini")
    elif aid == "aider":
        lines += ["  # set OPENAI_API_KEY in your shell before launching aider", f"  aider --openai-api-base {rep.shellq(base)} --model {rep.shellq(model)}"]
    else:
        lines.append("  set provider env for this agent (see fixes/provider-config.md)")
    if apply:
        if aid != "claude-code":
            lines.append("NO WRITE: apply the manual/config-file steps above for this target")
        else:
            write_result = _apply_provider_settings(anthropic_base, api_key, model)
            if write_result.startswith("wrote "):
                lines.append(f"APPLIED: {write_result}")
            else:
                status = "error"
                lines.append(f"ERROR: {write_result}")
    if api_key:
        lines.append("NOTE: the supplied key is never echoed; keep it in the referenced environment variable and use the manual step above.")
    return StatusText(rep.mask_secrets("\n".join(lines)), status)


def _apply_provider_settings(anthropic_base: str, api_key: str, model: str) -> str:
    target = Path.home() / ".claude" / "settings.json"
    if target.is_symlink():
        return f"refusing symlink target: {target} — apply manually"
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return f"could not parse existing {target} — apply manually"
    if not isinstance(data, dict):
        return f"could not parse existing {target} — apply manually"
    env = dict(data.get("env") or {})
    env.update(
        {
            "ANTHROPIC_BASE_URL": anthropic_base,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_MODEL": model,
        }
    )
    data["env"] = env
    try:
        state.atomic_write_json(target, data, private=True)
    except state.StateError as exc:
        return f"could not write {target}: {exc} — apply manually"
    return f"wrote {target} (model {model})"
