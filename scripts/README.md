# scripts — the `fix` CLI

The CLI reads `catalog.json` and performs explicit-target diagnosis and repair.
It uses Python 3.8+ standard library only.

## Commands

| Command | Purpose |
|---|---|
| `fix list` | List known issue ids |
| `fix agents` | Explicit inventory; no health checks |
| `fix check <issue> --agent <id>` | Diagnose one issue for one agent |
| `fix apply <issue> --agent <id> [--yes]` | Repair and verify that same agent |
| `fix info <issue>` | Print one fix document |
| `fix net <host> [--timeout N]` | Check one endpoint |
| `fix mcp register\|remove <agent-id>` | Change MCP registration for one agent |
| `fix install\|uninstall --agent <id>` | Deploy/remove one agent integration |

The CLI has no bulk `doctor`, `auto`, or `selfheal` mode. Startup repair hooks are
not installed.

## Example

```bash
./fix check npm-postinstall-skipped --agent opencode
./fix apply npm-postinstall-skipped --agent opencode --yes
```

## Python API

```python
from agentfix import catalog, engine

cat = catalog.load_catalog()
issue = catalog.find_issue(cat, "npm-postinstall-skipped")
agent = engine.resolve_target(cat, issue, "opencode")
state = engine.check_issue(issue, agent=agent, quiet=True)
outcome = engine.apply_issue(issue, agent=agent, yes=True, quiet=True)
```

The target resolver probes only the requested agent. Missing, unsupported, or
undetected targets fail explicitly.
