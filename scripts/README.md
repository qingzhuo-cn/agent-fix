# scripts — the `fix` CLI

The `fix` CLI is the machine side of the `agent-fix` skill. It reads
[`../catalog.json`](../catalog.json) (same knowledge as the `fixes/*.md` docs) and
can diagnose + repair agent installs from a terminal, a cron job, a CI step, or any
program. The implementation lives in the [`../agentfix/`](../agentfix/) package;
`scripts/fix.py` is a thin launcher, so the same entry works from a repo checkout
and from a deployed skill copy.

## Requirements

- Python 3.8+ (stdlib only — no pip install needed)

## Quick start

```bash
# from this repo
./fix list

# install the CLI onto your PATH (symlink/copy to ~/bin or ~/.local/bin)
ln -s "$(pwd)/scripts/fix" ~/.local/bin/fix   # or add scripts/ to PATH
```

## Commands

| Command | What it does | Exit code |
|---------|--------------|-----------|
| `fix list` | list every known issue in the catalog | 0 |
| `fix agents` | list the agents installed on this machine | 0 |
| `fix check` | run all diagnostics (including per-agent binary checks) | 0 healthy / 1 broken |
| `fix check <id> [<id>...]` | run diagnostics for specific issues | 0 / 1 |
| `fix doctor` | alias for `fix check` | 0 / 1 |
| `fix apply <id> [--yes]` | apply fixes for one issue, then verify | 0 verified |
| `fix auto` | check all → auto-apply fixes for broken ones (watchdog mode) | 0 all fixed |
| `fix info <id>` | print the matching doc from `../fixes/` | 0 |
| `fix net [--timeout N]` | network diagnostics (TCP connectivity + proxy env) | 0 reachable |
| `fix hooks install\|uninstall\|status [--agent id]` | manage the self-heal startup hooks | 0 |
| `fix mcp register\|remove [agent]` | manage MCP server registration | 0 |
| `fix install` / `fix uninstall` | deploy/remove the whole skill (copies, hooks, MCP, CLI) | 0 |
| `fix --json ...` | machine-readable output on supported commands | — |

The catalog ships an **agent registry** (`catalog.json` → `agents`): add an agent
as one line of data (bin, config home, skills dir, npm package) and `fix doctor`
starts checking it automatically. `agent-broken-generic` is the dynamic issue that
verifies `--version` for every detected agent and repairs npm-installed agents by
re-running their postinstall/install script.

`net-connectivity` uses the built-in engine in `agentfix/engine.py` (hard-timeout
TCP checks via non-blocking connect + select, so unreachable hosts cost exactly
the timeout, not ~30s of Windows SYN retries). The MCP `net` tool calls the same
engine.

`fix check` and `fix auto` exit non-zero when something is broken, so they drop
straight into scripts:

```bash
# cron: every morning, auto-repair
0 9 * * *  cd /path/to/agent-fix-skill && ./scripts/fix auto >> /var/log/agent-fix.log 2>&1

# wrapper: repair opencode before it runs
opencode() { command opencode --version >/dev/null 2>&1 || fix apply npm-postinstall-skipped --yes; command opencode "$@"; }
```

## Using it from programs

The package is a normal Python package — import it:

```python
import sys
sys.path.insert(0, "/path/to/agent-fix-skill")
from agentfix import catalog, engine

cat = catalog.load_catalog()
issue = next(i for i in cat["issues"] if i["id"] == "npm-postinstall-skipped")
state = engine.check_issue(issue, quiet=True)
print("broken" if state["broken"] else "healthy")

outcome = engine.apply_issue(issue, yes=True, quiet=True)
```

Or call it as a subprocess with `--json`:

```python
import json, subprocess
out = subprocess.run(["fix", "check", "--json"], capture_output=True, text=True)
report = json.loads(out.stdout)
```

## Adding your own issue

1. Append a block to `catalog.json` (id, checks, fixes, verify, doc).
2. Add a matching `fixes/<id>.md` doc.
3. `fix check <your-id>` to validate the checks; `fix apply <your-id> --yes` to test
   the fix. No code changes needed — the catalog is data-driven.
