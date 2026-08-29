#!/usr/bin/env python3
"""agentfix.engine — the one engine: checks, fixes, self-heal, diagnostics.

Everything the CLI and the MCP server expose funnels through here:

  shell      run catalog commands (POSIX syntax) with per-platform shell
             selection; on Windows bare "bash" is never trusted (CreateProcess
             resolves System32's WSL launcher first — it cannot run node/npm)
  checks     catalog checks / fixes / verify with per-agent dynamic expansion;
             a self-heal *deadline* (absolute monotonic time) is honored inside
             every command timeout, not only between issues
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
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentfix import catalog as cat
from agentfix import report as rep

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
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a shell command, return {ok, exit, stdout, stderr, duration}.

    ``deadline`` (absolute time.monotonic()) caps the timeout so the self-heal
    budget holds even inside a single long check.
    """
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return {"ok": False, "exit": -1, "stdout": "", "stderr": "self-heal deadline exceeded", "duration": 0.0}
        timeout = max(5, min(timeout, int(remaining)))
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
        return Path(base) / (parts[1] if len(parts) > 1 else "")
    return Path(template).expanduser()


def _passes(pass_spec: Optional[Dict[str, Any]], result: Dict[str, Any]) -> bool:
    """Evaluate a check's pass spec against a run result."""
    if pass_spec is None:
        return result["ok"]
    if "exit" in pass_spec and result["exit"] != pass_spec["exit"]:
        return False
    if "stdout_equals" in pass_spec and result["stdout"] != pass_spec["stdout_equals"]:
        return False
    if "stdout_contains" in pass_spec and not all(s in result["stdout"] for s in pass_spec["stdout_contains"]):
        return False
    if "stdout_contains_any" in pass_spec and not any(s in result["stdout"] for s in pass_spec["stdout_contains_any"]):
        return False
    if "stdout_not_contains" in pass_spec:
        low = result["stdout"].lower()
        if any(s.lower() in low for s in pass_spec["stdout_not_contains"]):
            return False
    return True


# ---------------------------------------------------------------- network

# (label, host) — every AI-agent API endpoint we care about
ENDPOINTS: List[Tuple[str, str]] = [
    ("anthropic (claude-code)", "api.anthropic.com"),
    ("openai (codex)", "api.openai.com"),
    ("deepseek", "api.deepseek.com"),
    ("moonshot (kimi)", "api.moonshot.cn"),
    ("google (gemini)", "generativelanguage.googleapis.com"),
    ("zhipu (zcode/glm)", "open.bigmodel.cn"),
    ("alibaba (qwen)", "dashscope.aliyuncs.com"),
    ("github (gh/actions)", "api.github.com"),
    ("npm registry", "registry.npmjs.org"),
]


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


def net_text(timeout: float = 5.0) -> str:
    """Connectivity + latency report for every agent API endpoint."""
    lines = [f"NETWORK DIAGNOSTIC (TCP:443, timeout={timeout}s)", ""]
    for label, host in ENDPOINTS:
        r = check_endpoint(host, 443, timeout)
        if r["ok"]:
            lines.append(f"  OK      {label:<28} {host}  ({r['ms']}ms)")
        else:
            lines.append(f"  {r['error']:<9} {label:<28} {host}")
    lines.append("")
    lines += _proxy_env_report()
    return rep.mask_secrets("\n".join(lines))


# ---------------------------------------------------------------- checks


def check_issue(
    issue: Dict[str, Any],
    quiet: bool = False,
    agents: Optional[List[Dict[str, Any]]] = None,
    deadline: Optional[float] = None,
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
                check = dict(check_t)
                if agent.get("no_version") and "--version" in check.get("cmd", ""):
                    # GUI/desktop app (zcode/cursor): --version would launch the
                    # GUI and hang. Binary presence is already proven by
                    # detection (bin resolved on PATH), so report PASS.
                    name = cat.expand(check.get("name", ""), agent)
                    detail = "GUI app — binary presence verified via PATH (no --version probe)"
                    if not quiet:
                        print(f"    [PASS] [{agent.get('id')}] {name}")
                        print(f"          {detail}")
                    results.append(
                        {"name": name, "status": "PASS", "detail": detail, "exit": 0, "agent": agent.get("id")}
                    )
                    continue
                check["name"] = cat.expand(check.get("name", ""), agent)
                check["cmd"] = cat.expand(check.get("cmd", ""), agent)
                results.append(_run_one_check(check, quiet, agent.get("id"), deadline))
    else:
        for check in issue.get("checks", []):
            results.append(_run_one_check(check, quiet, None, deadline))
    broken = any(r["status"] == "FAIL" for r in results)
    return {"id": issue["id"], "title": issue["title"], "results": results, "broken": broken}


def _run_one_check(
    check: Dict[str, Any],
    quiet: bool = False,
    agent_id: Optional[str] = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    platform = check.get("platform")
    if platform == "windows" and not cat.is_windows():
        return {"name": check["name"], "status": "SKIP", "detail": "windows-only", "exit": None, "agent": agent_id}
    if platform == "posix" and cat.is_windows():
        return {"name": check["name"], "status": "SKIP", "detail": "posix-only", "exit": None, "agent": agent_id}
    if check.get("kind") == "net":
        r = check_endpoint(
            check.get("host", ""),
            port=int(check.get("port", 443)),
            timeout=float(check.get("timeout", 5)),
        )
        passed = r["ok"]
        detail = f"ok ({r['ms']}ms)" if r["ok"] else (r["error"] or "unreachable")
    else:
        result = run(check["cmd"], timeout=check.get("timeout", 30), deadline=deadline)
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
    deadline: Optional[float] = None,
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
                fix = dict(fix_t)
                fix["name"] = cat.expand(fix.get("name", ""), agent)
                fix["cmd"] = cat.expand(fix.get("cmd", ""), agent)
                if fix.get("cwd"):
                    fix["cwd"] = cat.expand(fix.get("cwd", ""), agent)
                fix["_agent"] = agent
                fix_specs.append(fix)
    else:
        fix_specs = list(issue.get("fixes", []))

    for fix in fix_specs:
        platform = fix.get("platform")
        if platform == "windows" and not cat.is_windows():
            skipped.append({"name": fix["name"], "reason": "windows-only"})
            continue
        if platform == "posix" and cat.is_windows():
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
        if not quiet:
            print(f"    [FIX ] {fix['name']}")
        if not yes:
            try:
                answer = input("          run this fix? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer not in ("y", "yes"):
                skipped.append({"name": fix["name"], "reason": "declined"})
                continue
        result = run(fix["cmd"], cwd=cwd, timeout=fix.get("timeout", 300), deadline=deadline)
        if result["ok"]:
            fixed.append({"name": fix["name"]})
            if not quiet:
                print(f"          ok ({result['duration']}s)")
                if result["stdout"]:
                    print(f"          {result['stdout'][:300]}")
        else:
            reason = f"failed: {result['stderr'][:200] or result['stdout'][:200]}"
            skipped.append({"name": fix["name"], "reason": reason})
            if not quiet:
                print(f"          FAILED: {reason[len('failed: '):]}")

    # verify
    verified = True
    if issue.get("dynamic") and agents:
        for agent in agents:
            for v_t in issue.get("verify", []):
                v = dict(v_t)
                if agent.get("no_version") and "--version" in v.get("cmd", ""):
                    continue  # GUI app: skip --version verify (presence proven at detection)
                v["name"] = cat.expand(v.get("name", ""), agent)
                v["cmd"] = cat.expand(v.get("cmd", ""), agent)
                verified = _run_one_verify(v, verified, quiet=quiet, deadline=deadline)
    else:
        for v in issue.get("verify", []):
            verified = _run_one_verify(v, verified, quiet=quiet, deadline=deadline)
    return {"id": issue["id"], "fixed": fixed, "skipped": skipped, "verified": verified}


def _run_one_verify(
    v: Dict[str, Any],
    verified: bool,
    quiet: bool = False,
    deadline: Optional[float] = None,
) -> bool:
    platform = v.get("platform")
    if platform == "windows" and not cat.is_windows():
        return verified
    if platform == "posix" and cat.is_windows():
        return verified
    if v.get("kind") == "net":
        r = check_endpoint(
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
    result = run(v["cmd"], timeout=v.get("timeout", 60), deadline=deadline)
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
    agents = cat.detect_agents(catalog)
    report: Dict[str, List[Any]] = {"checked": [], "fixed": [], "unfixed": []}
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


# ---------------------------------------------------------------- self-heal


def run_selfheal(catalog: Dict[str, Any], deadline: float = 75.0, apply: bool = True) -> Dict[str, Any]:
    """Check all (+ auto-fix when apply=True) under a hard time budget.

    Returns {fixed, unfixed, timed_out}. apply=False runs diagnose-only: broken
    issues are reported in `unfixed` but nothing is applied. Shared by the CLI
    (`fix selfheal`) and the MCP server (`self_heal` tool) so both use the exact
    same pipeline. No printing — callers format the report.
    """
    agents = cat.detect_agents(catalog)
    fixed: List[str] = []
    unfixed: List[str] = []
    end = time.monotonic() + deadline
    for issue in catalog["issues"]:
        if time.monotonic() > end:
            return {"fixed": fixed, "unfixed": unfixed, "timed_out": True}
        state = check_issue(issue, quiet=True, agents=agents, deadline=end)
        if not state["broken"]:
            continue
        if not apply:
            unfixed.append(issue["id"])
            continue
        fixes = issue.get("fixes") or []
        if not any(not f.get("manual") for f in fixes):
            # diagnostic-only issue (e.g. net-connectivity): nothing to
            # auto-repair, so don't nag the user about it on every start
            continue
        outcome = apply_issue(issue, yes=True, quiet=True, agents=agents, deadline=end)
        if outcome["verified"]:
            fixed.append(issue["id"])
        else:
            unfixed.append(issue["id"])
    return {"fixed": fixed, "unfixed": unfixed, "timed_out": False}


def selfheal_text(apply: bool = False) -> str:
    """Text report for the MCP self_heal tool. Default: diagnose-only."""
    r = run_selfheal(cat.load_catalog(), apply=apply)
    if r["timed_out"]:
        return "SELF-HEAL\n  timed out — run `fix doctor` manually"
    lines = ["SELF-HEAL" + ("" if apply else " (diagnose-only, no fixes applied)")]
    if r["fixed"]:
        lines.append("  fixed: " + ", ".join(r["fixed"]))
    if r["unfixed"]:
        label = "still broken" if apply else "broken (not fixed)"
        lines.append(f"  {label}: " + ", ".join(r["unfixed"]))
    if not r["fixed"] and not r["unfixed"]:
        lines.append("  all healthy")
    return rep.mask_secrets("\n".join(lines))


# ---------------------------------------------------------------- reports


def doctor_text() -> str:
    """Full health report: every issue, every detected agent."""
    catalog = cat.load_catalog()
    agents = cat.detect_agents(catalog)
    lines, broken = [], []
    for issue in catalog["issues"]:
        state = check_issue(issue, quiet=True, agents=agents)
        lines.append(f"== {issue['id']}: {issue['title']}")
        if state["broken"]:
            for r in state["results"]:
                if r["status"] == "FAIL":
                    prefix = f"[{r['agent']}] " if r.get("agent") else ""
                    lines.append(f"    [FAIL] {prefix}{r['name']}: {r['detail'][:200]}")
            broken.append(issue["id"])
        else:
            lines.append("   -> healthy")
    lines.append("")
    lines.append("=> " + ("BROKEN: " + ", ".join(broken) if broken else "all healthy"))
    return rep.mask_secrets("\n".join(lines))


def check_text(issue_id: str) -> str:
    """Diagnostics for one issue id."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        return f"unknown issue: {issue_id} (try `fix list` for ids)"
    agents = cat.detect_agents(catalog)
    state = check_issue(issue, quiet=True, agents=agents)
    lines = [f"== {issue['id']}: {issue['title']}", ""]
    for r in state["results"]:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r["status"]]
        prefix = f"[{r['agent']}] " if r.get("agent") else ""
        lines.append(f"  {mark} {prefix}{r['name']}")
        if r["status"] == "FAIL" and r.get("detail"):
            lines.append(f"        {r['detail'][:300]}")
    lines.append("")
    lines.append("=> broken" if state["broken"] else "=> healthy")
    return rep.mask_secrets("\n".join(lines))


def apply_text(issue_id: str, confirm: bool = False) -> str:
    """Apply one issue's fixes (confirm=true), or show what would run."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        return f"unknown issue: {issue_id} (try `fix list` for ids)"
    if not confirm:
        lines = [f"== {issue['id']}: {issue['title']}", "", "DRY RUN — these fixes would run (pass confirm=true to execute):", ""]
        for f in issue.get("fixes", []):
            kind = "[MANUAL]" if f.get("manual") else "[AUTO]  "
            lines.append(f"  {kind} {f.get('name', '')}")
            lines.append(f"        $ {f.get('cmd', '').replace(chr(10), ' ')[:200]}")
        lines.append("")
        lines.append("Tip: run `check` first to see which checks are failing.")
        return "\n".join(lines)
    agents = cat.detect_agents(catalog)
    lines = [f"== {issue['id']}: {issue['title']}", ""]
    out = apply_issue(issue, yes=True, quiet=True, agents=agents)
    for f in out["fixed"]:
        lines.append(f"  [FIXED] {f['name']}")
    for s in out["skipped"]:
        lines.append(f"  [SKIP]  {s['name']} ({s['reason']})")
    lines.append("")
    lines.append("=> verified OK" if out["verified"] else f"=> not fully verified (see {issue.get('doc', 'the doc')})")
    return rep.mask_secrets("\n".join(lines))


def info_text(issue_id: str) -> str:
    """The knowledge-base doc for an issue id."""
    catalog = cat.load_catalog()
    issue = cat.find_issue(catalog, issue_id)
    if not issue:
        return f"unknown issue: {issue_id}"
    doc = cat.doc_path(issue)
    if doc.exists():
        return rep.data_tag("fix doc") + "\n" + doc.read_text(encoding="utf-8")
    return f"doc missing for {issue_id}"


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
    "kimi-code": "run: kimi upgrade (or reinstall from kimi.com/code)",
    "hermes": "run: hermes update",
    "zcode": "update via ZCode Desktop",
    "cursor": "update via Cursor app",
    "amp": "run: amp upgrade",
    "droid": "update via Droid app",
}


def _resolve_bin(name: str) -> str:
    """Resolve a bare command name via PATH (CreateProcess can't on Windows)."""
    return shutil.which(name) or name


def versions_text() -> str:
    """Installed vs latest version for every detected agent.

    Agents flagged no_version (GUI desktop apps) are never probed — their
    ``--version`` would launch the GUI on the user's desktop.
    """
    lines = ["VERSION CHECK", ""]
    for agent in cat.detect_agents(cat.load_catalog()):
        name = agent.get("name", agent.get("id"))
        npm_pkg = agent.get("npm_pkg")
        bin_name = (agent.get("bin") or ["?"])[0]
        if agent.get("no_version"):
            hint = _UPDATE_HINTS.get(agent.get("id"), "update via the app")
            lines.append(f"  {name:<26} installed=(GUI app, not probed){' ' * 6} update: {hint}")
            continue
        installed = "?"
        try:
            r = subprocess.run(
                [_resolve_bin(bin_name), "--version"], capture_output=True, text=True, errors="replace", timeout=20
            )
            installed = (r.stdout or r.stderr or "").strip().splitlines()[0][:60]
        except Exception:
            pass
        if npm_pkg:
            latest = "?"
            try:
                r = subprocess.run(
                    [_resolve_bin("npm"), "view", npm_pkg, "version"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                latest = (r.stdout or r.stderr or "").strip()
            except Exception:
                pass
            lines.append(f"  {name:<26} installed={installed:<40} latest={latest}")
        else:
            hint = _UPDATE_HINTS.get(agent.get("id"), "reinstall per official docs")
            lines.append(f"  {name:<26} installed={installed:<40} update: {hint}")
    return rep.mask_secrets("\n".join(lines))


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


def _walk_files(base: Path, max_depth: int = 3, suffixes: Optional[set] = None) -> List[Path]:
    """Files under base, depth-bounded and noise-dir pruned (stdlib os.walk).

    Pruning happens during traversal (not on the results), so huge noise trees
    like node_modules inside agent config dirs cost nothing to walk.
    """
    out: List[Path] = []
    base_parts = len(base.parts)
    for root, dirs, names in os.walk(str(base)):
        depth = len(Path(root).parts) - base_parts
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in _NOISE_DIRS and not (Path(root) / d).is_symlink()]
        for name in names:
            if name in _NOISE_DIRS:
                continue
            if suffixes is not None and Path(name).suffix not in suffixes:
                continue
            out.append(Path(root) / name)
    return out


def _iter_backup_files(base: Path, max_depth: int = 4, max_files: int = 2000, max_bytes: int = 20_000_000):
    """Regular files under base for the snapshot: noise dirs pruned while
    walking (agent config trees can hold enormous plugin caches), depth-bounded,
    and capped per target — some "config dirs" (e.g. $LOCALAPPDATA/hermes) also
    contain a whole application install (venv, apps) that must not be zipped.

    Returns (files, truncated). Ordered breadth-ish by os.walk, so the cap
    keeps top-level config files and drops deep bulk first.
    """
    out: List[Path] = []
    total = 0
    truncated = False
    base_parts = len(base.parts)
    for root, dirs, names in os.walk(str(base)):
        if len(Path(root).parts) - base_parts >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in _NOISE_DIRS and not (Path(root) / d).is_symlink()]
        for name in names:
            if name in _NOISE_DIRS:
                continue
            p = Path(root) / name
            if p.is_symlink():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue  # vanished or unreadable — skip it
            if size >= 20_000_000:
                continue
            if len(out) >= max_files or total + size > max_bytes:
                truncated = True
                dirs[:] = []  # stop walking this target entirely
                break
            out.append(p)
            total += size
        if truncated:
            break
    return out, truncated


def _snapshot_targets() -> List[Path]:
    """Config dirs (resolved per platform) of every detected agent."""
    targets = []
    for agent in cat.detect_agents(cat.load_catalog()):
        cfg = cat.config_path(agent)
        if not cfg:
            continue
        p = Path(cfg.replace("/", os.sep))
        if p.exists():
            targets.append(p)
    return targets


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
    except ValueError:
        return False


def audit_text(depth: int = 3) -> str:
    """Scan agent config dirs: parse errors + leaked API keys (masked)."""
    lines = [rep.data_tag("local config scan"), ""]
    for agent in cat.detect_agents(cat.load_catalog()):
        cfg = cat.config_path(agent)
        if not cfg:
            continue
        base = Path(cfg.replace("/", os.sep))
        if not base.exists():
            continue
        files = []
        for f in _walk_files(base, max_depth=max(1, int(depth)), suffixes=_CONFIG_EXTS):
            try:
                if f.stat().st_size < 2_000_000:
                    files.append(f)
            except OSError:
                continue  # file vanished or is unreadable — skip it
        files = files[:200]
        parse_errors, leaks = [], []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if f.suffix == ".json":
                try:
                    json.loads(text)
                except Exception as e:
                    parse_errors.append(f"{f.name}: {e}")
            for label, pat in _KEY_PATTERNS:
                for m in pat.findall(text):
                    leaks.append(f"{label}: {rep.mask(m)} (in {f.name})")
        lines.append(f"== {agent.get('name')} ({base})")
        lines.append(f"   config files scanned: {len(files)}")
        if parse_errors:
            lines.append(f"   PARSE ERRORS ({len(parse_errors)}):")
            lines += [f"     - {e}" for e in parse_errors[:10]]
        else:
            lines.append("   parse: OK")
        if leaks:
            lines.append(f"   LEAKED KEYS ({len(leaks)}):")
            lines += [f"     - {l}" for l in leaks[:20]]
        else:
            lines.append("   no obvious leaked keys")
        lines.append("")
    return rep.mask_secrets("\n".join(lines)) or "no agent config dirs found"


def backup_text() -> str:
    """Snapshot every agent config dir into ~/.agent-fix-backups/<ts>.zip."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"agent-configs-{ts}.zip"
    targets = _snapshot_targets()
    if not targets:
        return "no agent config dirs found to back up"
    manifest: Dict[str, str] = {}
    notes: List[str] = []
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, base in enumerate(targets):
            base = base.resolve()
            prefix = f"{i:02d}-{base.name}"
            manifest[prefix] = str(base)
            files, truncated = _iter_backup_files(base)
            for f in files:
                zf.write(f, f"{prefix}/{f.relative_to(base).as_posix()}")
                count += 1
            if truncated:
                notes.append(f"  note: {base} exceeded the snapshot budget (2000 files / 20 MB) — deep bulk skipped, top-level configs kept")
        zf.writestr("_manifest.json", json.dumps(manifest, indent=2))
    size = dest.stat().st_size
    try:
        os.chmod(dest, 0o600)  # configs may contain API keys — restrict perms (POSIX)
    except OSError:
        pass
    lines = [
        f"backup created: {dest}",
        f"size: {size/1024:.1f} KB | files: {count}",
    ]
    lines += notes
    lines.append("note: contains plaintext configs (may include API keys) — kept local & private")
    return "\n".join(lines)


def restore_text(backup: Optional[str] = None, confirm: bool = False) -> str:
    """List backups, or restore one (backup filename or 'latest', confirm=True)."""
    if not BACKUP_DIR.exists():
        return "no backups found (~/.agent-fix-backups missing)"
    backups = sorted(BACKUP_DIR.glob("agent-configs-*.zip"), key=lambda p: p.name, reverse=True)
    if not backups:
        return "no backups found"
    if not backup:
        lines = ["AVAILABLE BACKUPS:", ""]
        for b in backups:
            lines.append(f"  {b.name}  ({b.stat().st_size/1024:.1f} KB)")
        lines.append("")
        lines.append("restore with: restore(backup='<name>' or 'latest', confirm=true)")
        return "\n".join(lines)
    target = backups[0] if backup == "latest" else next((b for b in backups if b.name == backup), None)
    if not target:
        return f"backup not found: {backup}"
    if not confirm:
        return f"dry run: would restore {target.name} from {target} — pass confirm=true to actually restore"
    # Restore ONLY into directories the CURRENT catalog recognizes as agent
    # config dirs (matched by dir name). The manifest's recorded absolute paths
    # are NOT trusted — a tampered zip could otherwise point anywhere, and a
    # crafted member path could escape its base (zip-slip).
    known = {p.resolve().name: p for p in _snapshot_targets()}
    restored = []
    with zipfile.ZipFile(target) as zf:
        try:
            manifest = json.loads(zf.read("_manifest.json"))
        except KeyError:
            return f"backup {target.name} has no manifest — refusing to restore"
        for member in zf.namelist():
            if member == "_manifest.json" or "/" not in member:
                continue
            prefix, rel = member.split("/", 1)
            pname = prefix.split("-", 1)[1] if "-" in prefix else prefix
            base = known.get(pname)
            if not base:
                continue  # not a currently-known agent config dir
            if not _safe_rel(rel) or not _inside(base, base / rel):
                return f"refusing to restore {target.name}: unsafe member path {member!r} (zip-slip guard)"
            out = base / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            restored.append(str(out))
    return rep.mask_secrets(
        f"restored {len(restored)} files from {target.name}\n"
        + "\n".join(restored[:20])
        + ("\n..." if len(restored) > 20 else "")
    )


def logs_text(agent_id: Optional[str] = None, lines: int = 30) -> str:
    """Scan known agent log locations for recent ERROR/WARN lines."""
    pat = re.compile(r"(ERROR|WARN|Traceback|postinstall|Fatal|panic|exit code)", re.I)
    out = [rep.data_tag("local log lines"), ""]
    for agent in cat.detect_agents(cat.load_catalog()):
        if agent_id and agent.get("id") != agent_id:
            continue
        cfg = cat.config_path(agent)
        if not cfg:
            continue  # never fall back to scanning the CWD (Path(''))
        base = Path(cfg.replace("/", os.sep))
        logs: List[Path] = []
        if base.exists():
            logs = _walk_files(base, max_depth=4, suffixes={".log"})[-5:]
            logdir = base / "logs"
            if logdir.exists():
                logs += sorted(p for p in logdir.iterdir() if p.is_file())[-5:]
        if not logs:
            continue
        hits: List[str] = []
        for f in logs:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines()[-500:]:
                if pat.search(line):
                    hits.append(f"{f.name}: {line.strip()[:160]}")
        out.append(f"== {agent.get('name')}")
        if hits:
            out += [f"   {h}" for h in hits[-int(lines):]]
        else:
            out.append("   no recent error/warn lines")
        out.append("")
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


def provider_text(
    provider: str = "deepseek",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    apply: bool = False,
    show_key: bool = False,
) -> str:
    """Generate per-agent config snippets for ANY provider.

    provider: deepseek|openai|anthropic|google|moonshot|zhipu|qwen|openrouter|
              ollama|custom. api_key required for cloud providers (empty for
              Ollama). base_url/model default from the provider table.
    apply=True also writes Claude's ~/.claude/settings.json.
    """
    info = _KNOWN_PROVIDERS.get((provider or "").lower(), {"base": "", "model": ""})
    base = base_url or info.get("base", "")
    model = model or info.get("model", "deepseek-chat")
    anthropic_base = base
    if info.get("anthropic_path"):
        anthropic_base = base.rstrip("/") + info["anthropic_path"]
    if not base:
        return "error: unknown provider — pass base_url explicitly (see fixes/provider-config.md)"
    if not api_key and provider.lower() != "ollama":
        return "error: api_key is required (leave empty only for ollama)"

    shown = api_key if show_key else (rep.mask(api_key) if api_key else "(local)")
    prov_id = rep.sanitize_id(provider) or "custom"
    model_id = rep.sanitize_id(model) or "custom"

    lines = [
        f"PROVIDER SETUP: {provider}  (base={base}, model={model}, key={rep.mask(api_key) if api_key else '(local)'})",
        "",
    ]
    for agent in cat.detect_agents(cat.load_catalog()):
        aid = agent.get("id")
        name = agent.get("name", aid)
        lines.append(f"== {name}")
        if aid == "claude-code":
            lines.append(f"  export ANTHROPIC_BASE_URL={rep.shellq(anthropic_base)}")
            lines.append(f"  export ANTHROPIC_AUTH_TOKEN={rep.shellq(shown)}")
            lines.append(f"  export ANTHROPIC_MODEL={rep.shellq(model)}")
            lines.append("  # or persist in ~/.claude/settings.json env block (apply=true does this)")
        elif aid in ("codex", "opencode", "pi", "qwen-code"):
            lines.append(f"  export OPENAI_BASE_URL={rep.shellq(base)}")
            lines.append(f"  export OPENAI_API_KEY={rep.shellq(shown)}")
            if aid == "qwen-code":
                lines.append(f"  # or DASHSCOPE_API_KEY + --dashscope-url {rep.shellq(base)}")
        elif aid == "kimi-code":
            lines.append("  # ~/.kimi-code/config.toml:")
            lines.append(f"  [provider.{prov_id}]")
            lines.append(f'  base_url = "{rep.tomlq(base)}"')
            lines.append(f'  api_key = "{rep.tomlq(shown)}"')
            lines.append(f"  [model.{model_id}]")
            lines.append(f'  provider = "{prov_id}"')
        elif aid == "hermes":
            lines.append(f"  hermes config set provider {prov_id}")
            lines.append(f"  hermes config set model {model_id}")
            lines.append(f"  # key via provider config / .env (e.g. {prov_id.upper()}_API_KEY)")
        elif aid == "zcode":
            lines.append("  # ZCode app provider settings:")
            lines.append(f"  Base URL: {base}")
            lines.append(f"  API key:  {shown}")
            lines.append(f"  Model:    {model}")
        elif aid == "gemini":
            lines.append(f"  export GEMINI_API_KEY={rep.shellq(shown)}")
        elif aid == "aider":
            lines.append(f"  export OPENAI_API_KEY={rep.shellq(shown)}")
            lines.append(f"  aider --openai-api-base {rep.shellq(base)} --model {rep.shellq(model)}")
        else:
            lines.append("  set provider env for this agent (see fixes/provider-config.md)")
        lines.append("")
    if apply:
        written = _apply_provider_settings(anthropic_base, api_key, model)
        lines.append(f"APPLIED: {written}")
    if api_key and not show_key:
        lines.append("NOTE: key masked in output — pass show_key=true to reveal, or apply=true to write config files.")
    lines.append("Note: verify with a real model prompt; run `audit` before pushing keys to git.")
    return rep.mask_secrets("\n".join(lines))


def _apply_provider_settings(anthropic_base: str, api_key: str, model: str) -> str:
    target = Path.home() / ".claude" / "settings.json"
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return f"could not parse existing {target} — apply manually"
    env = dict(data.get("env", {}))
    env.update(
        {
            "ANTHROPIC_BASE_URL": anthropic_base,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_MODEL": model,
        }
    )
    data["env"] = env
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)  # a key lives here — restrict perms (best-effort)
    except OSError:
        pass
    return f"wrote {target} (model {model})"
