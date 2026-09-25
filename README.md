# agent-fix

> **Repair the AI coding agent the user named, and nothing else.**

A zero-dependency repair engine for AI coding agents. One pure-stdlib kernel, exposed
through both a CLI and an MCP server, covering 10 failure classes across 15 agents.

| 10 failure classes | 15 agents | 12 MCP tools | 5 platforms | 0 dependencies | 232 tests |
|:------------------:|:---------:|:------------:|:-----------:|:---------------:|----------:|
| `catalog.json` | `catalog.json` | stdio protocol | Win / mac / Linux | pure stdlib | all green |

English / [简体中文](README_cn.md)

---

## Capability tree

```
agent-fix
│
├── Entry 1: CLI  (scripts/fix.py)
│   ├── list / agents / info          read-only, touches no user state
│   ├── check <issue> --agent <id>    diagnose one explicit target
│   ├── apply <issue> --agent <id>    repair + verify that same target
│   ├── install / uninstall --agent   deploy skill / clear legacy hooks
│   └── mcp register <agent>          register one agent's MCP, explicitly
│
├── Entry 2: MCP Server  (mcp/server.py, 12 tools)
│   ├── check   apply   info          diagnose / repair / documentation
│   ├── agents  versions  logs audit  inventory / versions / logs / audit
│   ├── net                              connectivity for an explicit host
│   ├── backup  restore                  transactional backup and restore
│   ├── provider                         config snippet; writes only on apply
│   └── hooks                            legacy hook cleanup only
│
├── Kernel  (agentfix/, pure stdlib)
│   ├── catalog.py    registry + path resolution   ← sole agent/issue source
│   ├── state.py      atomic write / ZIP / rollback ← sole persistence owner
│   ├── engine.py     checks, fixes, diagnostics
│   ├── result.py     typed results + status side channel
│   ├── report.py     credential masking
│   ├── hooks.py      installer / MCP registration / legacy cleanup
│   ├── mcp.py        MCP protocol and lifecycle
│   └── cli.py        command line and exit codes
│
└── Data  (catalog.json)
    ├── issues  ×10   each failure class → applicable agent list
    └── agents  ×15   bin / config home / skills dir / npm package
```

## Failure catalog

| issue id | symptom | applies to |
|---|---|---|
| `npm-postinstall-skipped` | `postinstall script was not run`, native binary missing | claude, codex, opencode, pi, gemini, qwen, kimi, minimax |
| `node-version-too-old` | `EBADENGINE`, crashes on startup | npm-distributed agents |
| `npm-registry-mirror` | install hangs, `ETIMEDOUT` | npm-distributed agents |
| `gui-path-blind` | GUI says installed but cannot run, terminal is fine | claude, codex, opencode, hermes |
| `agent-auth-broken` | not logged in, 401, missing API key | all |
| `provider-config` | no provider configured (key / base URL / model) | all |
| `agent-broken-generic` | the binary fails to run at all | all |
| `net-connectivity` | the explicitly named endpoint is unreachable | claude, codex, opencode, pi, kimi, zcode |
| `opencode-mcp-schema` | opencode.json MCP entry is invalid | opencode |
| `deepseek-harness-broken` | dsh will not boot | dsh |

## Supported agents

| id | name | config home |
|---|---|---|
| `claude-code` | Claude Code | `~/.claude` |
| `codex` | Codex CLI | `~/.codex` |
| `opencode` | OpenCode | `~/.config/opencode` |
| `hermes` | Hermes Agent | `~/.local/share/hermes` · `%LOCALAPPDATA%\hermes` |
| `kimi-code` | Kimi Code | `~/.kimi-code` |
| `minimax-code` | MiniMax Code | `~/.minimax` |
| `pi` | Pi (pi-coding-agent) | `~/.pi` |
| `qwen-code` | Qwen Code | `~/.qwen-code` |
| `gemini` | Gemini CLI | `~/.gemini` |
| `cursor` | Cursor | `~/.cursor` |
| `aider` | Aider | `~/.config/aider` |
| `zcode` | ZCode | `~/.zcode` |
| `amp` | Amp | `~/.config/amp` |
| `droid` | Droid | `~/.factory` |
| `dsh` | DeepSeek Harness | — |

> Adding an agent is **data work**: add one record to `agents` in `catalog.json`
> (bin, config home, skills dir, npm package). The CLI needs no code change.

## Core rule

Every repair command requires one issue id and one explicit agent id:

```bash
fix check npm-postinstall-skipped --agent opencode
fix apply npm-postinstall-skipped --agent opencode --yes
```

The engine resolves only `opencode`, runs only the checks and fixes that apply to
it, and verifies only `opencode`. It does not probe, repair, or validate any other
installed agent or model.

## Quick start

```bash
git clone https://github.com/qingzhuo-cn/agent-fix.git
cd agent-fix

./scripts/fix list                              # every failure class
./scripts/fix agents                            # inventory only
./scripts/fix check node-version-too-old --agent opencode
./scripts/fix apply node-version-too-old --agent opencode --yes

python scripts/fix.py install --agent opencode  # deploy skill, this target only
```

Install registers no startup self-healing, scans no other agent, and bulk-registers
no MCP server.

## Engineering highlights

The easiest mistake for a repair tool is not failing to fix something. It is
announcing success. So this kernel picks "rather not know" at several points that
matter.

| Where | What it does |
|---|---|
| **Result state** | Four states — `PASS` / `FAIL` / `INCONCLUSIVE` / `SKIPPED` — with a rollup requiring every check to be `PASS`; the exit code follows the real state, not the wording |
| **Exit codes** | 0 healthy, 1 fail or inconclusive, 2 target error — *inconclusive is nonzero too* |
| **Atomic write** | Target identity is re-verified after the commit; a mismatch rolls back to the pre-write bytes. `os.replace` not being kernel-atomic is a fact, not a detail |
| **Directory identity** | `(st_dev, st_ino, entry-name signature)` — `dev`/`ino` alone is fooled by inode reuse |
| **Parent directory** | Remembered when the operation starts, checked on both sides of the commit; a swapped parent gets neither the rollback nor the only good backup |
| **ZIP preflight** | EOCD checked before `ZipFile` is built, central directory streamed through a fixed 46-byte buffer; a forged 1/10/50 MB payload still peaks at 0.19 MB |
| **Credential masking** | The boundary reaches the subprocess; `token=...`, `{"token": ...}`, and `token: ...` no longer leak verbatim |
| **Ownership** | `state.py` is the sole persistence and archive owner; `hooks.py` converged to one table-driven path rather than gaining a second owner |

Two real defects worth naming:

- `uninstall` used to collect only `error` statuses, so a hook cleanup returning
  `inconclusive` still printed `ok` and exited 0 — **a removal that could not
  confirm completion was recorded as done**. That case is an `error` now.
- On Windows `os.path.abspath("CON")` returns the device namespace `\\.\CON`,
  which let a path skip the component check and reach `mkdir`, raising an
  unwrapped `OSError`. The whole `\\.\` and `\\?\` namespaces are refused now.

## Evidence

| Scope | Result |
|---|---|
| Local Windows 3.14 / Windows 3.8 / Ubuntu | 232 tests pass |
| GitHub Actions six-cell matrix (ubuntu-22.04, windows-2022, macos-15-intel × Python 3.8 / 3.11) | all green |
| MCP protocol smoke | 12 tools handshake, removed bulk tools protocol-rejected |
| Refactor equivalence | 30-case differential (3 agents × register/remove × 5 filesystem states), byte-identical after normalizing the temp path |
| Size | 5276 lines engine / 4445 lines tests / 0 third-party dependencies |

## Development

```bash
python -m unittest discover -s tests -v
python mcp/smoke_test.py
```

## License

[MIT](LICENSE)
