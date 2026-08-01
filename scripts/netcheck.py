#!/usr/bin/env python3
"""
netcheck.py — shared network connectivity engine for agent-fix.

Used by:
  - catalog issue `net-connectivity`  (fix check / fix doctor, via fix.py)
  - MCP branch tool `net_diagnose`    (via mcp/court/shangshu/bingbu.py)

Pure stdlib. CLI: `python scripts/netcheck.py [--timeout N]`
Exit code: 0 if every endpoint is reachable, 1 otherwise.
"""

from __future__ import annotations

import datetime
import os
import select
import socket
import subprocess
import sys
from typing import Any, Dict, List, Tuple

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
    """TCP connect test with a HARD timeout.

    Uses a non-blocking connect + select() instead of socket.create_connection:
    on Windows, blocking connect() ignores the timeout for hosts that silently
    drop SYNs (it retries for ~30s+ regardless of the socket timeout). select()
    enforces the timeout deterministically on every platform.

    Returns {ok, ms, error}.
    """
    t0 = datetime.datetime.now()
    # Windows defines these as WSAEINPROGRESS/WSAEWOULDBLOCK (10036/10035);
    # the POSIX names don't exist there.
    EINPROGRESS = getattr(socket, "EINPROGRESS", 10036)
    EWOULDBLOCK = getattr(socket, "EWOULDBLOCK", 10035)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        err = sock.connect_ex((host, port))
        if err not in (0, EINPROGRESS, EWOULDBLOCK):
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


def proxy_env_report() -> List[str]:
    lines = ["PROXY ENV:"]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        v = os.environ.get(k) or os.environ.get(k.lower())
        lines.append(f"  {k:<12} = {v or '(unset)'}")
    npm_proxy = None
    try:
        npm_proxy = (
            subprocess.run(["npm", "config", "get", "proxy"], capture_output=True, text=True, timeout=10)
            .stdout.strip()
        )
    except Exception:
        pass
    lines.append(f"  npm proxy  = {npm_proxy or '(unset)'}")
    return lines


def run_all(timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Check every endpoint; returns list of {label, host, ok, ms, error}."""
    out = []
    for label, host in ENDPOINTS:
        r = check_endpoint(host, 443, timeout)
        out.append({"label": label, "host": host, **r})
    return out


def format_report(timeout: float = 5.0, results: Optional[List[Dict[str, Any]]] = None) -> str:
    lines = [f"NETWORK DIAGNOSTIC (TCP:443, timeout={timeout}s)", ""]
    results = results if results is not None else run_all(timeout)
    for r in results:
        if r["ok"]:
            lines.append(f"  OK      {r['label']:<28} {r['host']}  ({r['ms']}ms)")
        else:
            lines.append(f"  {r['error']:<9} {r['label']:<28} {r['host']}")
    lines.append("")
    lines += proxy_env_report()
    return "\n".join(lines)


def main() -> int:
    timeout = 5.0
    if "--timeout" in sys.argv:
        try:
            timeout = float(sys.argv[sys.argv.index("--timeout") + 1])
        except (IndexError, ValueError):
            pass
    results = run_all(timeout)  # run once; reuse for both report and exit code
    print(format_report(timeout, results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
