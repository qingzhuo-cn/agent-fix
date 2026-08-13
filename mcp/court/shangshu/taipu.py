#!/usr/bin/env python3
"""太仆寺 · Imperial Harness Office — 治辕: keep the DeepSeek Harness (dsh) healthy.

The DeepSeek Harness (`dsh`, npm `@deepseek-ai/dsh`) is the product launcher that
boots DeepSeek Harness profiles (the web GUI, the headless runner, plugins). This
office diagnoses a broken install (binary missing, engine too old, incomplete
package tree) and repairs it (reinstall / rebuild), reusing the catalog issue
`deepseek-harness-broken` so `fix_doctor` / `fix_check` / `fix_apply` and these
two tools share one engine.

Pure Python 3.8+ stdlib. Part of the 三省六部 MCP court (see mcp/README.md).
"""

from __future__ import annotations

import shutil
from typing import Any, Dict

MINISTRY = {
    "id": "taipu",
    "name": "太仆寺",
    "en": "Imperial Harness Office",
    "motto": "治辕 · keep the DeepSeek Harness (dsh) healthy",
}

ISSUE_ID = "deepseek-harness-broken"


def _fmt_diagnose() -> str:
    import fix

    cat = fix.load_catalog()
    issue = fix.find_issue(cat, ISSUE_ID)
    if not issue:
        return f"catalog missing issue '{ISSUE_ID}'"
    npm_root = fix.run("npm root -g", timeout=30).get("stdout", "").strip()
    lines = [
        f"== {issue['id']}: {issue['title']}",
        "",
        "  install facts:",
        f"    dsh on PATH: {shutil.which('dsh') or 'NOT FOUND'}",
        f"    npm root -g: {npm_root or '?'}",
        f"    package dir: {(npm_root + '/@deepseek-ai/dsh') if npm_root else '?'}",
        "",
        "  checks:",
    ]
    state = fix.check_issue(issue, quiet=True)
    for r in state["results"]:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r["status"]]
        lines.append(f"    {mark} {r['name']}")
        if r["status"] == "FAIL" and r.get("detail"):
            lines.append(f"          {r['detail'][:300]}")
    lines.append("")
    lines.append("=> " + ("BROKEN — run dsh_fix with apply=true to repair" if state["broken"] else "healthy"))
    return "\n".join(lines)


def dsh_fix(apply: bool = False) -> str:
    import fix

    cat = fix.load_catalog()
    issue = fix.find_issue(cat, ISSUE_ID)
    if not issue:
        return f"catalog missing issue '{ISSUE_ID}'"
    if not apply:
        lines = ["DRY RUN — dsh_fix would run these repairs (pass apply=true to execute):", ""]
        for f in issue.get("fixes", []):
            kind = "[MANUAL]" if f.get("manual") else "[AUTO]  "
            lines.append(f"  {kind} {f['name']}")
            lines.append(f"        $ {f.get('cmd', '')}")
        lines.append("")
        lines.append("Tip: run dsh_diagnose first to see which checks are failing.")
        return "\n".join(lines)
    lines = [f"== {issue['id']}: {issue['title']}", ""]
    out = fix.apply_issue(issue, yes=True, quiet=True)
    for f in out["fixed"]:
        lines.append(f"  [FIXED] {f['name']}")
    for s in out["skipped"]:
        lines.append(f"  [SKIP]  {s['name']} ({s['reason']})")
    lines.append("")
    lines.append("=> verified OK" if out["verified"] else f"=> not fully verified (see {issue.get('doc', 'fixes/deepseek-harness.md')})")
    return "\n".join(lines)


TOOLS: Dict[str, Dict[str, Any]] = {
    "dsh_diagnose": {
        "description": "Diagnose the DeepSeek Harness (dsh / @deepseek-ai/dsh) install: binary on PATH, node engine, package integrity, and a boot smoke test. Returns a health report plus the exact repair command when broken. Makes no changes.",
        "args": {},
        "fn": lambda a: _fmt_diagnose(),
    },
    "dsh_fix": {
        "description": "Repair a broken DeepSeek Harness (dsh) install. Default apply=false shows a dry-run of the repair commands; apply=true re-runs the install (npm install -g @deepseek-ai/dsh) then verifies dsh --version / --help. Use after dsh_diagnose reports BROKEN.",
        "args": {
            "apply": {"type": "boolean", "description": "actually run the repair (default false = dry-run only)"},
        },
        "fn": lambda a: dsh_fix(apply=bool(a.get("apply", False))),
    },
}
