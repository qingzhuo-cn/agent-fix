# Agent-fix Phase 1 Compatibility Convergence Implementation Plan

> **For agentic workers:** Execute each task in order. Do not broaden Phase 1,
> touch persistent user state, or retire public compatibility paths.

**Goal:** Remove confirmed Phase 1 redundancy and executable-documentation
drift while preserving all issue ids, all 12 MCP tools, all launcher paths, and
explicit-target safety.

**Architecture:** Keep `catalog.json` as the public issue source of truth,
`agentfix/engine.py` as the execution owner, `agentfix/cli.py` and
`agentfix/mcp.py` as separate adapters, and `agentfix/hooks.py` as the
integration-write owner. Move npm lifecycle execution out of the generic
binary issue without adding aliases or a new dispatcher. Launcher scripts remain
thin argument-forwarding adapters.

**Tech stack:** Python 3.8+ standard library, `argparse`, JSON catalog,
PowerShell, POSIX shell, `unittest`, existing MCP smoke harness.

## Baseline and authority refs

- `AGENTS.md`
- `docs/aegis/specs/2026-09-25-agent-fix-phase1-convergence-brief.md`
- `catalog.json`
- `agentfix/cli.py`
- `agentfix/engine.py`
- `agentfix/mcp.py`
- `agentfix/hooks.py`
- `install/install.sh`
- `install/install.ps1`
- `install/uninstall.sh`
- current user-facing documentation under `README*.md`, `SKILL.md`,
  `scripts/README.md`, `mcp/README.md`, and `fixes/*.md`

## Requirement readiness

- Requirement source: user-approved Phase 1 design and written spec.
- Scope: installer forwarding, issue/provider owner clarification, no-op JSON
  removal, documentation synchronization, focused regression coverage.
- Scenario: a caller uses an existing launcher or documented command to operate
  on one explicitly selected Agent.
- Acceptance: spec criteria 1-7.
- Open blocker questions: none.
- Decision: `ready`.

## Compatibility boundary

Preserve all public issue ids, MCP tool names/count, launcher paths, legacy hook
status/removal, and persistent user state. The only intentional user-visible
contract correction is that ignored `list --json` / `info --json` invocations
now fail normal argument parsing.

Do not remove `config_win`, DSH special resolution, `deadline`,
`hooks_install`, or `ENDPOINTS` in this phase. Do not add a new alias,
compatibility layer, fallback, adapter owner, or MCP profile.

## TDD route

- Mode: `off`
- Decision: `skipped`
- Authority: current session Aegis configuration
- Test posture: add focused regression tests with each contract change, then run
  the complete existing suite and smoke harness.

## Change necessity

- User-visible need: broken launchers and stale/no-op commands make supported
  features appear duplicated or unusable.
- No-change option: documentation edits alone cannot forward installer targets,
  remove ignored options, or change issue ownership.
- Minimum code boundary: existing CLI/parser, catalog data, provider behavior
  text, three launchers, and tests.
- Decision: `code-change`.

## Ripple signal triage

- Canonical owner affected: public issue behavior in `catalog.json`; CLI option
  registration in `agentfix/cli.py`; provider behavior text in
  `agentfix/mcp.py` and `agentfix/engine.py`.
- Consumers: CLI users, skill loaders, MCP clients, launcher scripts, README and
  knowledge-base readers.
- Retirement risk: the generic issue's npm lifecycle step is removed while its
  public id remains. The specialized issue must retain the only executable npm
  lifecycle path.
- Fallback/adapter policy: no new fallback or alias layer; retain existing
  launchers as thin adapters.
- Verification expansion: catalog ownership regression, parser contract,
  runtime-safe PowerShell forwarding, documentation scan, full unit suite,
  isolated MCP smoke.

## Complexity budget

- Artifact class: source, test, and documentation contract changes.
- Target files: `engine.py` is a large mixed-purpose owner, but Phase 1 changes
  only a narrow provider-result line; `cli.py`, catalog, launchers, and docs are
  smaller owner-local edits.
- Current pressure: high file count and pre-existing dirty working tree.
- Projected pressure: lower functional entropy; slightly higher regression
  coverage; no new runtime owner.
- Budget result: `at-risk`, within budget through owner-local edits and focused
  test files.
- Recommendation: edit in place; do not extract a new module.

## TaskStartSnapshot

The workspace already contains extensive user-owned changes in core source,
documentation, CI, and untracked `tests/` and `.zcode/` paths. Do not restore,
reset, stage, or commit them. Before each task, inspect the exact target text
and use targeted edits. Final evidence must distinguish pre-existing dirty
state from this plan's changes.

No commit is planned: the shared dirty workspace overlaps the task paths, and
the user did not request staging or commit lifecycle operations.

## Task 1 — Add focused surface-contract tests

**Files**

- Create `tests/test_surface_contracts.py`
- Read `agentfix/cli.py`, `catalog.json`, and launcher scripts for asserted
  contracts.

**Purpose**

Establish executable regression coverage before changing each Phase 1 contract.

**Implementation**

1. Test that `check --json` and `apply --json` remain parseable.
2. Test that `list --json` and `info provider-config --json` are rejected by
   `build_parser()`.
3. Load `catalog.json`; assert `agent-broken-generic` has no `npm_root` fix or
   lifecycle command and `npm-postinstall-skipped` retains both.
4. Assert the three launcher scripts contain the correct argument-forwarding
   expression and invoke an `install`/`uninstall` subcommand.

**Verification**

```powershell
python -m unittest discover -s tests -p "test_surface_contracts.py" -v
```

The new tests should initially expose only the intended contract gaps. Do not
assert private implementation details beyond the approved public/data contract.

## Task 2 — Repair launcher contracts and remove no-op JSON options

**Files**

- `agentfix/cli.py`
- `install/install.sh`
- `install/install.ps1`
- `install/uninstall.sh`

**Repair track**

- POSIX launchers forward `"$@"` after their `install` or `uninstall`
  subcommand.
- PowerShell launcher forwards `@args` after its `install` subcommand.
- Keep Python discovery and repository-root resolution unchanged.
- Update launcher comments so they no longer claim automatic startup hooks or
  MCP registration.
- Preserve the legacy root-level `--json` placement for `check` and `apply`
  (`fix --json check ...`) while also accepting the subcommand placement. Reject
  root-level `--json` for every unsupported command and retire the hidden
  `list/info` registrations.
- JSON mode suppresses shared-engine progress and keeps any confirmation prompt
  on stderr so stdout remains one parseable JSON document.

**Retirement track**

- Retire the silently ignored `list/info --json` surface.
- Do not remove or rename launcher paths.

**Verification**

```powershell
python -m unittest discover -s tests -p "test_surface_contracts.py" -v
python scripts/fix.py list --json
python scripts/fix.py info provider-config --json
python scripts/fix.py --json list
python scripts/fix.py --json info provider-config
python scripts/fix.py check --help
python scripts/fix.py apply --help
& .\install\install.ps1 --help
```

Expected: the four JSON misuse commands return non-zero; focused tests prove
both placements remain valid for `check/apply` and emit one JSON document; help
commands return zero and show `--agent`; PowerShell help delegates without
writing user state.

## Task 3 — Collapse npm lifecycle ownership and clarify provider behavior

**Files**

- `catalog.json`
- `agentfix/mcp.py`
- `agentfix/engine.py`
- `tests/test_surface_contracts.py`

**Repair track**

1. Remove the executable npm lifecycle step from `agent-broken-generic`.
2. Retain its binary check and manual native/desktop guidance, explicitly
   routing npm-distributed Agents to `npm-postinstall-skipped`.
3. Confirm `npm-postinstall-skipped` retains ignore-scripts diagnosis, lifecycle
   execution, and version verification.
4. Change the catalog manual provider message from nonexistent
   `provider_setup` to the existing `provider` MCP tool.
5. Change the MCP `provider` description/schema text to state that `apply=true`
   writes Claude Code settings only.
6. In `engine.provider_text`, replace the misleading `APPLIED` line for
   non-Claude Agents with an explicit manual/no-write result.
7. Revise the masked-key guidance so it no longer implies that `apply=true`
   writes configuration files for every Agent.

**Retirement track**

- Old behavior retired: duplicate npm lifecycle execution under
  `agent-broken-generic`.
- Preserved behavior: public issue ids, generic diagnosis, specialized npm
  repair, provider snippets, and dry-run/confirmation semantics.

**Verification**

```powershell
python -m unittest discover -s tests -p "test_surface_contracts.py" -v
python -m json.tool catalog.json > $null
python -m py_compile agentfix/mcp.py agentfix/engine.py
```

No command that resolves or repairs a real Agent is permitted.

## Task 4 — Synchronize executable documentation

**Files**

Update only stale statements in:

- `AGENTS.md`
- `README.md`
- `README_cn.md`
- `SKILL.md`
- `scripts/README.md`
- `mcp/README.md`
- `fixes/README.md`
- `fixes/agent-matrix.md`
- `fixes/deepseek-harness.md`
- `fixes/kimi-code.md`
- `fixes/net-connectivity.md`
- `fixes/npm-postinstall.md`
- `fixes/npm-registry.md`
- `fixes/pi.md`
- `fixes/provider-config.md`
- `fixes/zcode.md`

Add or extend documentation assertions in `tests/test_surface_contracts.py`.
The executable-command scan covers the user-facing files listed above, not
`docs/aegis/`; specs and plans intentionally quote legacy commands as migration
evidence. Within Markdown, scan fenced command examples rather than prose so a
valid sentence such as "there is no `fix doctor` command" is preserved.

**Repair track**

- Every executable `fix check/apply` example includes `--agent <id>`.
- Remove `fix doctor` as an available command.
- Replace `provider_setup` with `provider` and `config_audit` with `audit`.
- Describe `fix net` as requiring one explicit host.
- State provider apply support accurately.
- Route npm lifecycle docs to `npm-postinstall-skipped`.
- Replace ZCode's unsupported `gui-path-blind` command with its manual PATH
  guidance.
- Update installer examples to pass `--agent` through each retained launcher.
- Preserve explicit-target safety and the absence of bulk/automatic repair.

**Retirement track**

- Remove stale claims that describe retired commands as available.
- Keep negative statements such as "there is no doctor command" when they are
  not presented as runnable instructions.

**Verification**

```powershell
python -m unittest discover -s tests -p "test_surface_contracts.py" -v
```

The documentation test must fail on executable legacy patterns without rejecting
valid negative safety statements.

## Task 5 — Integrated verification and review

**Commands**

```powershell
python -m py_compile agentfix/*.py scripts/fix.py mcp/server.py mcp/smoke_test.py tests/*.py
python -m unittest discover -s tests -v
& .\install\install.ps1 --help
python scripts/fix.py list
python scripts/fix.py info provider-config
python scripts/fix.py check --help
python scripts/fix.py apply --help
python "C:\Users\qingz\.dsh\profiles\web\node_modules\aegis\scripts\aegis-workspace.py" check --root "C:\Users\qingz\Desktop\agent-fix"
```

Run `python mcp/smoke_test.py` with `HOME` and `USERPROFILE` redirected to an
isolated temporary directory. Remove only that derived test directory after the
run.

Run the same compile/unit/smoke commands in an isolated environment where the
POSIX launcher can be exercised without violating the Windows bare-bash rule.

**Review**

1. Inspect `git diff --` for every task-owned path; do not treat other dirty
   paths as task output.
2. Search for lingering `fix doctor`, missing-target examples,
   `provider_setup`, `config_audit`, hostless net calls, and npm lifecycle
   commands under `agent-broken-generic`.
3. Confirm no MCP tool names/count changed.
4. Confirm no persistent user path was written.
5. Record complexity closure and residual Phase 2 candidates.

## Verification obligations and stop conditions

Stop and return to design if any of the following becomes necessary:

- deleting or renaming a public issue id or MCP tool;
- changing persistent user configuration, hooks, or backups;
- adding an alias/dispatcher/compatibility owner;
- broadening Phase 1 into DSH, `config_win`, `deadline`, `hooks_install`, or
  `ENDPOINTS` retirement;
- an installer would execute a real install/uninstall during verification.

## Risks

- Scripts that relied on ignored `list/info --json` will now fail; this is the
  approved intentional contract correction.
- External users may depend on generic npm repair behavior. The public issue id
  remains available and the catalog/doc text routes them to the specialized
  owner before `apply`; migration messaging is deferred unless external usage
  evidence shows the route is insufficient.
- The pre-existing dirty workspace makes diff-based attribution important; no
  broad staging, reset, or commit is allowed.

## Retirement trigger

Phase 2 may retire public ids, launcher paths, MCP tools, or legacy fields only
after verified consumer evidence, a deprecation/migration notice, and a
separately approved destructive scope. This plan does not authorize that work.
