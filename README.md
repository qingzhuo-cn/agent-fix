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

## Engineering highlights

What is unusual about this tool is not the set of failures it repairs but that it
**does not trust anything it receives**: not ZIP archives, not the filesystem
between the check and the commit, not the status text an adapter returns, not even
the output of the commands it runs itself. A few places that show the approach.

### Atomic writes: treating "checked" to "committed" as an attack window

`os.replace` in the standard library **cannot** be kernel-atomic against a
same-user concurrent writer. `agentfix/state.py` does not pretend otherwise; it
defends the whole window:

- The target's content and identity are captured before the write and
  **re-verified after the commit**; a mismatch rolls back to the pre-write bytes.
- Directory identity is a `(st_dev, st_ino, entry-name signature)` triple.
  `dev`/`ino` alone is not enough — inode reuse lets a brand-new file present the
  same numbers as the file it replaced.
- Parent-directory identity is captured when the operation starts and re-checked
  both before and after the commit. If the parent was swapped, the rollback is
  **not** written into the new directory and the only good backup is **not** moved.
- Directory-swap recovery markers record the stage, backup, and old-target
  identities. A tampered backup is refused rather than installed.
- A restore whose snapshot or parent directory was replaced is refused outright
  instead of guessed at.

### ZIP: preflight before allocation

The EOCD is preflighted, then the central directory is walked in a stream with a
fixed 46-byte buffer; `ZipFile` is constructed only **after** the archive is
confirmed self-consistent. A forged entry count could previously skip the central
directory walk and make the trailing payload get read into memory. Such input is
now refused before allocation happens: peak usage stays at ~0.19 MB whether the
forged payload is 1 MB, 10 MB, or 50 MB.

### Reporting the indeterminate as indeterminate

Four-state results (`PASS` / `FAIL` / `INCONCLUSIVE` / `SKIPPED`), with a
conservative rollup that requires every check to be `PASS`; the CLI returns a
nonzero exit code for real user status (0 healthy, 1 fail or inconclusive, 2
target error).

This replaced a textbook false success: `uninstall` only collected `error`, so a
hook cleanup that returned `inconclusive` still reported `ok` and exited 0 — a
removal that could not confirm completion was counted as done. That is now an
`error`.

Status is no longer inferred from human-readable text. `StatusText`/`StatusLines`
carry an explicit `.status` side channel, so the exit code follows the real state
rather than the wording.

### The masking boundary reaches the subprocess

API keys, access tokens, secrets, query strings, and Bearer headers are all
redacted. But `token=...`, `{"token": ...}`, and `token: ...` — the forms real
config files actually use — **leaked verbatim**; they are covered now. Subprocess
stdout/stderr crosses the masking layer on both the success and failure paths; the
original implementation masked only the failing one.

### Convergence, not a second owner

Persistence, archiving, and atomic writes have exactly one owner: `state.py`.
`hooks.py` had held three near-identical copies of the MCP registration path;
they are now one table-driven path, with the per-agent differences held as data in
`_MCP_JSON_SPECS`. Splitting `hooks.py` was assessed and **rejected**: a new agent
is registry data in `catalog.json` and needs no code change, and all four of its
responsibilities share one `_write`/`_load_json` substrate and target the same user
config files — splitting would add a shared utility layer, which is precisely the
"second owner" the rules forbid.

### Evidence

232 tests across three local interpreters (Windows 3.14, Windows 3.8, Ubuntu), plus
a green six-cell GitHub Actions matrix (ubuntu-22.04, windows-2022, macos-15-intel ×
Python 3.8 / 3.11).

Rewriting the MCP registration path used a 30-case differential harness (3 agents ×
register/remove × 5 filesystem states) comparing the exact status text and on-disk
JSON before and after. Normalizing the random temp directory name, the two runs are
byte-identical — behavioral equivalence was proven, not assumed.

5276 lines of engine and 4445 lines of tests, with **zero third-party
dependencies**.

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
