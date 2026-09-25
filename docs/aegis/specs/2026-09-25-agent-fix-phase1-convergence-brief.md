# Agent-fix Phase 1 Compatibility Convergence Brief

- Date: 2026-09-25
- Status: User-approved design, pending written-spec review
- Scope: Phase 1 only

## Intent

Reduce confirmed feature and implementation redundancy without breaking the
existing public issue ids, MCP tool registry, or integration entry points.
Phase 1 corrects broken entry behavior, clarifies canonical owners, removes
no-op CLI surface, and synchronizes executable documentation.

## Baseline and authority

- Project rules: `AGENTS.md`
- Public issue source of truth: `catalog.json`
- Issue resolution/execution owner: `agentfix/engine.py`
- CLI owner: `agentfix/cli.py`
- MCP registry/transport owner: `agentfix/mcp.py`
- Agent integration owner: `agentfix/hooks.py`
- User documentation owners: `README*.md`, `SKILL.md`, `fixes/*.md`,
  `scripts/README.md`, and `mcp/README.md`
- Compatibility rule: repair and verification stay scoped to the one target
  selected by the user.

## Goals

1. Make all three install/uninstall launchers honor the current explicit
   `--agent <id>` contract.
2. Give npm lifecycle repair one canonical owner while retaining every public
   issue id.
3. Give provider configuration one documented implementation entry without
   claiming unsupported write behavior.
4. Remove CLI options that are accepted but have no effect.
5. Remove executable-documentation drift involving missing targets, removed
   commands, hostless network calls, and nonexistent MCP tool names.
6. Add focused regression checks for each changed contract.

## Canonical ownership

| Responsibility | Canonical owner | Phase 1 behavior |
|---|---|---|
| Public issue ids and applicability | `catalog.json` | All ids remain available. |
| Generic binary diagnosis | `agent-broken-generic` | Diagnoses and provides native/manual guidance; no longer owns the npm lifecycle repair. |
| npm lifecycle repair | `npm-postinstall-skipped` | Retains the ignore-scripts check, lifecycle execution, and version verification. |
| Authentication recovery | `agent-auth-broken` | Remains a separate diagnosis/manual-recovery path. |
| Provider readiness diagnosis | `provider-config` | Remains a separate provider key/base-url/model diagnosis. |
| Provider snippet generation | MCP `provider` tool | Referenced by docs as the existing implementation entry. |
| Install/uninstall execution | `fix install/uninstall --agent <id>` | Launchers only forward arguments to this owner. |
| Legacy hook cleanup | `hooks.status` / `hooks.uninstall` | Unchanged in Phase 1. |

## In scope

### 1. Installer argument forwarding

- `install/install.sh` forwards its arguments to
  `scripts/fix.py install`.
- `install/install.ps1` forwards its arguments to
  `scripts/fix.py install`.
- `install/uninstall.sh` forwards its arguments to
  `scripts/fix.py uninstall`.
- Documentation invokes each launcher with an explicit `--agent <id>`.

### 2. Issue-owner clarification

- Remove the npm lifecycle execution step from `agent-broken-generic`.
- Keep its binary check and manual native/desktop guidance.
- Update generic/native-agent documentation to route npm packages to
  `npm-postinstall-skipped`.
- Do not add an alias table or compatibility dispatcher.

### 3. Provider ownership and capability text

- Replace references to nonexistent `provider_setup` with `provider`.
- Replace nonexistent `config_audit` with `audit`.
- State that MCP `provider(apply=true)` writes settings only for Claude Code;
  other supported agents receive manual/config-file instructions.
- Do not merge `agent-auth-broken` and `provider-config` in Phase 1.

### 4. No-op CLI surface

- `--json` remains available on `check` and `apply` in both the legacy
  root-level placement (`fix --json check ...`) and the subcommand placement
  (`fix check ... --json`).
- `list` and `info` no longer accept `--json`.
- In JSON mode, stdout is exactly one parseable JSON document; engine
  progress and confirmation prompts do not contaminate it.
- An invocation that previously supplied an ignored option now fails through
  normal argument parsing instead of silently returning text.

### 5. Documentation synchronization

Documentation under the project must no longer contain:

- `fix doctor`, `auto`, or `selfheal` as available commands;
- `fix check <issue>` or `fix apply <issue>` examples without `--agent`;
- hostless `fix net` examples;
- `provider_setup` or `config_audit` MCP names;
- ZCode instructions that invoke `gui-path-blind` when that issue does not
  list Zcode as supported.

Per-agent and DeepSeek-provider reference documents remain valid; only stale
commands and unsupported claims are corrected.

## Compatibility boundary

Preserved:

- every current `catalog.json` issue id;
- the 12-tool MCP registry;
- CLI and MCP as separate adapters over the shared engine;
- all three launcher paths;
- legacy hook status/removal behavior;
- explicit-target safety and dry-run/confirmation behavior;
- user configuration, hooks, backups, and other persistent state.

Intentional behavior change:

- `list --json` and `info --json` become invalid because their previous
  acceptance was a no-op.

Deferred retirement candidates:

- public issue-id aliases/removal;
- deletion of launcher paths;
- MCP tool-profile changes;
- `config_win`, DSH registry integration, `deadline`, `hooks_install`, and
  `ENDPOINTS` cleanup;
- deeper auth/provider implementation consolidation.

## Non-goals

- No live diagnosis, repair, install, uninstall, MCP registration, hook
  cleanup, backup, or restore against a user's agents.
- No schema, persistence, security-permission, or distribution redesign.
- No MCP tool additions, removals, or renames.
- No new fallback, adapter, alias owner, or compatibility dispatcher.
- No restoration of removed `doctor`, `auto`, or `selfheal` behavior.

## Acceptance criteria

1. `install/install.ps1 --help` reaches CLI `install --help` and documents the
   required `--agent` option without changing persistent state.
2. Static/runtime-safe checks show all launcher call sites forward `"$@"` or
   PowerShell `@args` to a CLI subcommand that accepts `--agent`.
3. `agent-broken-generic` contains no `npm_root` lifecycle command, while
   `npm-postinstall-skipped` retains its lifecycle command and verification.
4. `list --json` and `info <issue> --json` fail argument parsing; `check
   --json` and `apply --json` remain registered.
5. Documentation checks find no removed command, missing-target repair
   example, hostless network invocation, or nonexistent MCP tool name.
6. Existing unit tests and the MCP smoke regression pass in an isolated test
   home.
7. No tracked user configuration or persistent state is changed.

## Verification commands

```powershell
python -m unittest discover -s tests -v
python scripts/fix.py list --json
python scripts/fix.py info provider-config --json
python scripts/fix.py check --help
python scripts/fix.py apply --help
& .\install\install.ps1 --help
```

The two intentionally invalid JSON invocations must return a non-zero argparse
exit. The MCP smoke test must run with an isolated temporary home so its backup
probe cannot touch the real user profile.

## TDD route

- Mode: `off`
- Decision: `skipped`
- Authority: current session Aegis configuration
- Posture: add focused regression coverage alongside the implementation; do not
  introduce a new test framework or a separate RED/GREEN compatibility layer.

## Change necessity and complexity

Documentation-only changes cannot repair broken launcher contracts or remove
duplicate issue ownership. The minimum code boundary is existing owner files:
`catalog.json`, `agentfix/cli.py`, MCP/engine provider descriptions, launcher
scripts, and focused tests. No new source module is justified.

Recommendation: edit in place. Do not add a registry, alias layer, adapter, or
fallback.

## Retirement record

- Old path retired in Phase 1: npm lifecycle execution under
  `agent-broken-generic`.
- Old path retained: both public issue ids, all launcher paths, MCP registry,
  and legacy hook cleanup.
- Retention reason: explicit staged compatibility boundary.
- Retirement trigger for deferred paths: verified external-consumer evidence,
  migration/deprecation notice, and a separately approved Phase 2 scope.

## ADR signal

The change clarifies existing owners but does not establish a new durable
architecture. Re-evaluate an ADR only if Phase 2 retires a public contract or
changes the source-of-truth/dependency direction.
