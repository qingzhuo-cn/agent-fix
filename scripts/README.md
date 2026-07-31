# scripts — the `fix` CLI

The `fix` CLI is the machine side of the `agent-fix` skill. It reads
[`../catalog.json`](../catalog.json) (same knowledge as the `fixes/*.md` docs) and
can diagnose + repair agent installs from a terminal, a cron job, a CI step, or any
program.

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
| `fix agents` | list the agent registry and which agents are installed | 0 |
| `fix check` | run all diagnostics (including per-agent binary checks) | 0 healthy / 1 broken |
| `fix check <id> [<id>...]` | run diagnostics for specific issues | 0 / 1 |
| `fix doctor` | alias for `fix check` | 0 / 1 |
| `fix apply <id> [--yes]` | apply fixes for one issue, then verify | 0 verified |
| `fix auto` | check all → auto-apply fixes for broken ones (watchdog mode) | 0 all fixed |
| `fix info <id>` | print the matching doc from `../fixes/` | 0 |
| `fix --json ...` | machine-readable output on supported commands | — |

The catalog ships an **agent registry** (`catalog.json` → `agents`): add an agent
as one line of data (bin, config home, skills dir, npm package) and `fix doctor`
starts checking it automatically. `agent-broken-generic` is the dynamic issue that
verifies `--version` for every detected agent and repairs npm-installed agents by
re-running their postinstall/install script.

`net-connectivity` uses the shared `scripts/netcheck.py` engine (hard-timeout TCP
checks via non-blocking connect + select, so unreachable hosts cost exactly the
timeout, not ~30s of Windows SYN retries). The MCP `net_diagnose` tool calls the
same engine.

`fix check` and `fix auto` exit non-zero when something is broken, so they drop
straight into scripts:

```bash
# cron: every morning, auto-repair
0 9 * * *  cd /path/to/agent-fix-skill && ./scripts/fix auto >> /var/log/agent-fix.log 2>&1

# wrapper: repair opencode before it runs
opencode() { command opencode --version >/dev/null 2>&1 || fix apply npm-postinstall-skipped --yes; command opencode "$@"; }
```

## Using it from programs

`fix.py` is a normal module — import it:

```python
import sys
sys.path.insert(0, "/path/to/agent-fix-skill/scripts")
from fix import load_catalog, check_issue, apply_issue, auto_fix

catalog = load_catalog()
state = check_issue(next(i for i in catalog["issues"] if i["id"] == "npm-postinstall-skipped"), quiet=True)
print("broken" if state["broken"] else "healthy")

outcome = apply_issue(state_issue, yes=True, quiet=True)
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
