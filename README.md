# agent-fix

**Repair the AI coding agent the user named, and nothing else.**

`agent-fix` is a zero-dependency knowledge base and CLI for diagnosing, repairing,
and verifying Claude Code, Codex, OpenCode, Hermes, Kimi Code, Pi, ZCode, Cursor,
Gemini CLI, Aider, Qwen Code, Amp, Droid, and npm-distributed coding agents on
Windows, macOS, and Linux.

English / [简体中文](README_cn.md)

## Core Rule

Every repair command requires one issue id and one explicit agent id:

```bash
fix check npm-postinstall-skipped --agent opencode
fix apply npm-postinstall-skipped --agent opencode --yes
```

The engine resolves only `opencode`, runs only the checks and fixes that apply to
it, and verifies only `opencode`. It does not probe, repair, or validate any other
installed agent or model.

## The hard part isn't fixing it. It's not claiming it worked.

The easiest mistake for a repair tool is not failing to fix something. It's
announcing success.

So a result here is not a success/failure pair. It is four states — `PASS`,
`FAIL`, `INCONCLUSIVE`, `SKIPPED` — and the rollup is conservative to the point
of stubbornness: everything has to be `PASS` for the whole to count. The CLI
exits nonzero when the machine is unhealthy, and it also exits nonzero when the
answer is *inconclusive*.

That is not fastidiousness; it is a specific false success that shipped. `uninstall`
used to collect only `error` statuses, so when a hook cleanup quietly returned
`inconclusive` it still printed `ok` and still exited 0. A removal that could not
confirm it had finished was recorded as done. That case is an `error` now.

Status is no longer guessed from human-readable text either. `StatusText` and
`StatusLines` carry an explicit `.status` side channel, so the exit code follows
the real state rather than the wording.

The same stubbornness shows up where files get written. `os.replace` cannot be
kernel-atomic against a same-user concurrent writer — that is a fact about the
standard library, not a detail to work around. So the engine treats everything
between "checked" and "committed" as an attack surface: it re-verifies the target
identity after the commit and rolls back to the pre-write bytes when they disagree.

The details cost something. Directory identity is a `(st_dev, st_ino, entry-name
signature)` triple, because `dev`/`ino` alone can be fooled by inode reuse — a
brand-new file can carry the same numbers as the file it replaced. The parent
directory is remembered when the operation starts and checked again on both sides
of the commit; if the parent was swapped, the rollback does not go into the new
directory and the only good backup does not get moved.

Which leaves a write with exactly three outcomes: fully applied, fully rolled
back, or refused with a stated reason. **There is no "it looked like it worked."**

A ZIP archive is the same idea in a different shape. The EOCD is preflighted and
the archive is confirmed self-consistent before `ZipFile` is ever constructed,
with the central directory walked in a stream through a fixed 46-byte buffer. A
forged entry count used to skip that step and let the whole trailing payload be
read into memory. Now the forged payload can be 1 MB, 10 MB, or 50 MB and peak
usage stays at 0.19 MB.

The masking boundary reaches all the way to the subprocess. `api_key`,
`access_token`, and Bearer headers were covered long ago, but `token=...`,
`{"token": ...}`, and `token: ...` — the forms real config files actually use —
leaked verbatim, because they don't look enough like secrets. Subprocess output
now crosses the masking layer on both the success and the failure path; only the
failing one used to be masked.

## Convergence, not another owner

`hooks.py` used to hold three near-identical copies of the MCP registration
path. They are now one table-driven path, with the differences between agents
held as data in `_MCP_JSON_SPECS`.

Whether to split that file was assessed, and the answer was no. A new agent is
registry data in `catalog.json` and needs no code change; and all four of the
file's responsibilities share one `_write`/`_load_json` substrate and target the
same user config files, so splitting would only add a shared utility layer —
precisely the "second owner" the rules forbid.

Some refactors are about line count, some are about ownership. This one was the
second kind, so what moved was the duplication, not the directories.

## Evidence

232 tests across three local interpreters (Windows 3.14, Windows 3.8, Ubuntu),
plus a green six-cell GitHub Actions matrix — ubuntu-22.04, windows-2022,
macos-15-intel, each on Python 3.8 and 3.11.

Rewriting the MCP registration path used a 30-case differential harness (3 agents
× register/remove × 5 filesystem states) comparing the exact status text and
on-disk JSON before and after. Normalizing the random temp directory name, the two
runs are byte-identical.

Behavioral equivalence was proven, not assumed.

5276 lines of engine, 4445 lines of tests, zero third-party dependencies.

## Why agent-fix

Agent failures often share root causes:

- `postinstall script was not run` or `native binary not installed`
- GUI launchers report `installed but cannot run` while a terminal works
- `EBADENGINE`, old Node, registry timeouts, or invalid MCP config
- expired login, missing API key, or provider/model configuration errors

The repository keeps those fixes in `catalog.json` and `fixes/*.md`, with one
stdlib-only engine behind the CLI and MCP server.

## Quick Start

```bash
git clone https://github.com/qingzhuo-cn/agent-fix.git
cd agent-fix

./scripts/fix list
./scripts/fix check npm-postinstall-skipped --agent opencode
./scripts/fix apply npm-postinstall-skipped --agent opencode --yes
```

Install the skill for one named agent:

```bash
python scripts/fix.py install --agent opencode
python scripts/fix.py mcp register opencode   # optional, explicit registration
```

Installation does not register startup repair hooks, scan other agents, or bulk
register MCP servers.

## CLI

| Command | Purpose |
|---|---|
| `fix list` | List known issue ids |
| `fix agents` | Explicit inventory of detected agents; does not diagnose or repair |
| `fix check <issue> --agent <id>` | Diagnose one issue for one agent |
| `fix apply <issue> --agent <id> [--yes]` | Repair and verify that same agent |
| `fix info <issue>` | Print the matching knowledge-base document |
| `fix net <host> [--timeout N]` | Check one explicitly named endpoint |
| `fix mcp register\|remove <agent-id>` | Change MCP registration for one agent |
| `fix install\|uninstall --agent <id>` | Deploy or remove files for one agent |

There is no `doctor`, `auto`, or `selfheal` command. Automatic bulk diagnosis and
startup repair were removed intentionally.

## Issue Catalog

| ID | Problem |
|---|---|
| `agent-broken-generic` | The selected agent binary fails |
| `npm-postinstall-skipped` | npm lifecycle script was skipped |
| `gui-path-blind` | A GUI process cannot find the selected agent binary |
| `node-version-too-old` | Node is too old for the selected agent |
| `npm-registry-mirror` | npm registry is slow or unreachable |
| `agent-auth-broken` | Login or API credential is missing/expired |
| `provider-config` | Provider key, base URL, or model is not configured |
| `net-connectivity` | The selected agent's API endpoint is unreachable |
| `opencode-mcp-schema` | OpenCode MCP configuration has an invalid schema |
| `deepseek-harness-broken` | `dsh` is missing or cannot boot |

See [fixes/README.md](fixes/README.md) for the complete documentation index.

## Python API

```python
from agentfix import catalog, engine

cat = catalog.load_catalog()
issue = catalog.find_issue(cat, "npm-postinstall-skipped")
target = engine.resolve_target(cat, issue, "opencode")

state = engine.check_issue(issue, agent=target, quiet=True)
outcome = engine.apply_issue(issue, agent=target, yes=True, quiet=True)
```

`resolve_target` probes only the requested registry entry. An unknown, unsupported,
or undetected target raises `TargetError`; an empty target is never reported as
healthy or verified.

## MCP Server

The stdio MCP server exposes 12 tools:

`check`, `apply`, `info`, `agents`, `versions`, `net`, `logs`, `audit`, `backup`,
`restore`, `provider`, and `hooks`.

Except for the explicit `agents` inventory, each operational tool requires one
`agent_id` or `host`. `check` and `apply` require both `issue_id` and `agent_id`.
`apply` and `restore` remain dry-run by default. The `provider` tool is also
read-only by default; `apply=true` writes Claude Code settings only, while other
targets receive manual configuration steps. `hooks` can only inspect or remove
a legacy hook for one named agent; new hook installation is disabled.

```bash
python scripts/fix.py mcp register claude-code
python mcp/smoke_test.py
```

See [mcp/README.md](mcp/README.md).

## Supported Agents

The registry currently includes Claude Code, Codex, OpenCode, Hermes, Kimi Code,
Pi, ZCode, Cursor, Gemini CLI, Aider, Qwen Code, Amp, and Droid. The registry is
data, not an instruction to inspect everything on the machine. It resolves only
the explicit target supplied by the user.

## Safety Properties

- Repair and verification remain scoped to the same target.
- Network checks use one requested host or the selected agent's catalog step.
- Version, logs, audit, backup, restore, and provider tools require a target.
- Startup self-heal and periodic watchdog installation are disabled.
- Outputs mask API keys, tokens, and proxy credentials.
- Restores reject unsafe archive paths and only write into the selected agent's
  recognized config directory.
- Windows command execution avoids bare `bash` so WSL does not intercept Git Bash
  repair commands.

## Development

```bash
python -m py_compile agentfix/*.py scripts/fix.py mcp/server.py mcp/smoke_test.py tests/*.py
python -m unittest discover -s tests -v
python mcp/smoke_test.py
```

The regression suite constructs multiple fake agents and asserts that targeted
check, fix, and verify operations never execute commands for another agent.

## License

[MIT](LICENSE)
