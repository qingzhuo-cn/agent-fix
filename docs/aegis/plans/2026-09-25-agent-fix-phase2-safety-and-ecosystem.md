# Agent-fix Phase 2 Safety and Ecosystem Convergence Plan

> Execute phases in order. Do not run destructive operations against the real user profile, stage/reset/clean the dirty worktree, or add a second compatibility owner.

**Date:** 2026-09-25  
**Status:** User-approved continuation after the Phase 1 compatibility convergence  
**Implementation checkpoint:** Phase 2A–D code/docs changes, including explicit adapter status side-channels replacing human-text status inference, are implemented and locally verified; the declared cross-platform CI matrix remains an external verification gate. See `docs/aegis/work/2026-09-25-agent-fix-phase2-safety/20-checkpoint.md` for evidence and residual risk.  
**Goal:** Correct result truth and adapter status first, harden persistent-state and installation boundaries second, converge cross-platform/catalog ownership third, then retire dead paths and split modules only where tests prove a stable owner.

## Baseline and constraints

- Working tree: intentionally dirty from Phase 1; no reset, clean, broad staging, or commit.
- Python: 3.8+ standard library only; no new runtime dependency.
- Public compatibility to preserve:
  - all existing issue IDs and the 12 MCP tool names;
  - all launcher paths and both supported CLI `--json` placements;
  - explicit one-issue/one-agent targeting;
  - legacy hook status/uninstall cleanup;
  - no startup repair hooks and no bulk/automatic repair.
- Do not execute real install/uninstall/provider/hook/backup/restore operations against the user profile. Use mocks, synthetic catalogs, subprocess stubs, and temporary directories.
- `catalog.json` remains the public agent/issue source of truth. Code must not grow a second registry.

## TDD route

- Mode: `auto`
- Decision: `strict`
- Authority: current Aegis routing plus the explicit user request to optimize in order.
- Posture: write characterization/regression tests before each behavior or persistence change; keep tests hermetic and stdlib-only.

## Canonical owners

| Responsibility | Canonical owner | Planned change |
|---|---|---|
| Agent and issue data | `catalog.json` | Add/update evidence-backed registry entries and semantic invariants. |
| Target resolution and repair planning | `agentfix/engine.py` | Make applicability, result status, verification, and fix planning explicit. |
| CLI contract | `agentfix/cli.py` | Map typed results to exit codes and machine JSON. |
| MCP contract | `agentfix/mcp.py` | Map domain failures to `isError`; validate required/enum/range inputs. |
| Persistent config state | a single new `agentfix/state.py` owner, with existing callers as facades | Safe atomic writes, backup/restore transaction, permissions, containment. |
| Integration lifecycle | `agentfix/hooks.py` facade, then `installer`/`mcp_registration` owners if split | Remove stale copy behavior and duplicate mutation logic. |
| Documentation | existing `README*`, `SKILL.md`, `fixes/*.md`, `mcp/README.md` | Generate/check mirrors from catalog and update official tool contracts. |

A facade is allowed only to preserve a proven import boundary; it must delegate to one implementation and must not contain a second copy of the behavior.

## Current-tool intake research

The following facts were verified against current official documentation/package metadata before planning the registry update.

### Kimi Code CLI (existing `kimi-code` entry must be refreshed)

- Official package: `@moonshot-ai/kimi-code`.
- Binary: `kimi`.
- Node requirement: `>=22.19.0`.
- Data root: `~/.kimi-code`, overridable with `KIMI_CODE_HOME`.
- Config: `~/.kimi-code/config.toml`.
- Skills: `~/.kimi-code/skills`.
- MCP: `~/.kimi-code/mcp.json` with `mcpServers` entries using `command`, `args`, optional `env`, and `enabled`; project-level `.kimi-code/mcp.json` also exists.
- Login is interactive/OAuth or configured provider credentials; ordinary `KIMI_API_KEY` shell variables are not automatically consumed. Provider checks must not claim an env key proves readiness.
- Update command: `kimi upgrade`; verification includes `kimi --version` and real `kimi -p`/`kimi --help` behavior where applicable.
- Official references:
  - https://moonshotai.github.io/kimi-code/zh/guides/getting-started.html
  - https://moonshotai.github.io/kimi-code/zh/configuration/config-files.html
  - https://moonshotai.github.io/kimi-code/zh/configuration/data-locations.html
  - https://moonshotai.github.io/kimi-code/zh/customization/mcp.html
  - https://moonshotai.github.io/kimi-code/zh/configuration/env-vars.html
  - https://www.npmjs.com/package/@moonshot-ai/kimi-code

### MiniMax Code CLI (new `minimax-code` entry)

- Official package: `@minimax-ai/code`.
- Binary: `mcode`.
- Node requirement: `>=22.19 <23 || >=24 <27` (current package metadata: `0.5.4`).
- Data root: `~/.minimax`, overridable with `MINIMAX_DATA_DIR`; legacy `~/.mavis` is a compatibility location owned by MiniMax, not by agent-fix.
- Config: `~/.minimax/config.yaml`.
- Skills are data-dir scoped; the integration must verify the exact user-level root before copying.
- MCP primary file: `~/.minimax/mcp.json`; current source also reads legacy `~/.minimax/mcp/mcp.json`. New registrations use the primary path and preserve unknown entries.
- MCP entries use `mcpServers` with stdio `command`/`args` or HTTP/SSE `url`, optional `env`/`enabled`.
- CLI verification: `mcode --version`, `mcode --help`; headless verification can use `mcode exec --output-format json` only in an explicitly isolated test.
- Official references:
  - https://agent.minimax.cn/docs/cli/quick-start
  - https://agent.minimax.cn/docs/cli/configuration
  - https://agent.minimax.cn/docs/cli/features
  - https://agent.minimax.cn/docs/cli/reference
  - https://www.npmjs.com/package/@minimax-ai/code
  - https://github.com/MiniMax-AI/minimax-code

No legacy startup hook installation is added for either tool. MCP support is data-driven and opt-in; unsupported capabilities remain absent/null rather than guessed.

## Phase 2A — Result truth, adapter status, and output safety

### A1. Characterization tests first

Add isolated tests for:

- `_passes` with success, non-zero exit, denylist-only, empty output, timeout, and `|| true` cases;
- `no_version`, all-platform-skip, and no-verify outcomes;
- manual-fix outcome not being reported as verified before a user action;
- shared dry-run/apply planning for platform, native/npm, missing cwd, and declined steps;
- CLI non-zero exit and `isError` mapping for mcp/install/uninstall/provider errors;
- secret-bearing stdout/stderr/JSON/MCP exception paths;
- Node semantic-version and DSH web-bundle verification with fake command results.

### A2. Result contract

- Keep existing public keys for compatibility and add an explicit status/verification field.
- Use `PASS`, `FAIL`, `INCONCLUSIVE`, and `SKIPPED` internally.
- `broken=true` only for conclusive failure; `verified=true` only after at least one authoritative verification passes.
- A missing/unsupported platform is not healthy.
- Manual work returns `manual_required`/inconclusive until the user separately verifies.
- `_passes` defaults to the real process result; negative string checks are additional evidence only.
- Verification uses the same matcher semantics as checks.

### A3. Adapter result mapping

- Add a small typed/domain result at the engine/integration boundary.
- CLI maps domain failure to non-zero exit; MCP maps it to `isError`.
- Do not infer status by parsing human strings.
- Preserve existing human-readable messages and the one-JSON-document contract.

### A4. Unified secret sanitization

- Sanitize subprocess output before storing/printing it.
- Keep one pattern owner in `report.py`; remove duplicate engine patterns.
- Cover known key/token forms, Bearer values, URL credentials, and common auth headers without echoing raw values.
- Remove or explicitly deprecate `provider(show_key=true)` if the final adapter always masks it; do not add an unmasking bypass.

**Phase 2A exit:** synthetic regressions prove old Node, missing DSH web bundle, unsupported native target, and un-authenticated provider cannot be reported as verified; CLI/MCP status and JSON are safe.

## Phase 2B — Persistent state, restore, and installed artifact safety

### B1. Safe state owner

Create one internal owner (`agentfix/state.py`) for:

- secure temporary files and atomic replacement;
- symlink/reparse-point rejection and parent containment;
- mode preservation/0600-0700 defaults;
- fsync where supported;
- unique backup names and manifests;
- bounded archive validation and preflight.

Route provider settings, hook config writes, backup/restore, and installer staging through this owner. Do not make a second backup implementation in each module.

### B2. Backup/restore transaction

- Write archives to a unique temp file and rename only after successful close/validation.
- Restore validates every member, manifest identity, count, total size, and path before the first write.
- Stage each file beside its target, fsync, then atomically replace.
- Create a pre-restore snapshot and roll back on failure.
- Restore permissions and reject symlink members.
- Test CRC failure, disk/write failure, duplicate names, traversal, zip bomb budget, and same-second backups.

### B3. Integration write safety

- Fix unpaired hook markers fail-closed.
- Match legacy hook entries by exact provenance; preserve unrelated commands in mixed rules.
- Make destructive hook cleanup require explicit confirmation in MCP while preserving the public action name.
- Make Claude MCP registration update/rollback-safe instead of remove-then-add with no recovery.

### B4. Self-contained installer

- Replace `SOURCE_ITEMS` with an explicit runtime manifest including `mcp/server.py` and required docs/fixes.
- Exclude `__pycache__`, `.pyc`, test/development-only files, and stale generated artifacts.
- Stage a complete target, verify imports and MCP registration paths, then switch atomically or roll back.
- Point installed CLI/hooks/MCP paths at the installed target, not the source checkout.
- Verify uninstall does not report success when deletion fails.

**Phase 2B exit:** temp-HOME tests prove no half-written config, no permission widening, no stale installed files, no source-checkout dependency, and no destructive operation without confirmation.

## Phase 2C — Cross-platform execution and catalog convergence

### C1. Shell and path safety

- Resolve Windows Git Bash/`cmd.exe` through fixed allowlisted absolute paths; never execute an arbitrary PATH `bash`/`cmd` found by name.
- Add a dialect/availability declaration to catalog steps. Unsupported POSIX-only probes become `INCONCLUSIVE`, not Agent failure.
- Move env/config/auth probes to Python-native kinds where practical; retain shell only for the target CLI itself.
- Canonicalize `npm_root`, skills, config, and data paths; reject traversal/symlink escapes.

### C2. Registry and applicability invariants

- Add semantic catalog tests: unique IDs, valid issue references, supported npm scopes, Node requirements, doc paths, capability fields, and no stale executable paths.
- Replace `npm-postinstall-skipped` `all` scope with explicit npm-capable registry entries.
- Add declarative `node_requirement` evaluation for Kimi/MiniMax and existing npm agents.
- Make DSH ownership explicit: register it with verified metadata or model it as an intentional `command_only` target; do not silently delete the documented path.
- Split provider key roles from base URLs and avoid global key unions that make unrelated credentials look valid.
- Keep auth and provider issue IDs separate; fix their semantics rather than merging public IDs.

### C3. MCP schema and protocol

- Add `required`, enum, additional-property, finite/range, and length validation.
- Unknown hooks action is an error, not implicit status.
- Invalid JSON-RPC input receives a standards-shaped error rather than silent discard.
- Accept only explicitly supported protocol versions; preserve notification behavior.

### C4. New/current agent entries

- Refresh `kimi-code` to the Node/Minimax-era contract without breaking its existing public ID.
- Add `minimax-code` with only verified fields and explicit capabilities.
- Add/update user docs, agent matrix, provider guidance, and catalog-derived tests.
- Do not claim MiniMax legacy `.mavis` migration or any hook capability not implemented by agent-fix.

**Phase 2C exit:** synthetic catalog and isolated MCP tests cover Kimi/MiniMax metadata, scopes, Node ranges, config/MCP paths, and Windows/POSIX command behavior without touching a real user profile.

## Phase 2D — Delete-first cleanup, documentation, and optional module split

### D1. Retire invalid internal responsibilities

After characterization tests pass:

- remove or deprecate `ENDPOINTS` if no public consumer is found;
- remove the unused `deadline` plumbing and stale self-heal wording;
- remove internal `hooks_install` and unreachable install branches, while keeping the public disabled `hooks(action=install)` response;
- remove unused `dynamic`/`dynamic_agent` metadata after catalog-consumer review;
- eliminate the duplicate DSH probe without removing the supported target.

### D2. Documentation and CI cleanup

- Fix stale `scripts/mcp_register.py` references and self-heal claims.
- Synchronize agent matrix/provider tables from catalog or add semantic drift tests; do not delete public human documents.
- Replace hard-coded catalog counts with invariant tests.
- Make MCP smoke hermetic: temp HOME, no real DNS/npm, mocked process/socket boundaries.
- Add Python 3.8/current and Ubuntu/Windows/macOS CI axes; use current Node24 action majors (`actions/checkout@v5`, `actions/setup-python@v6`) and a supported Intel macOS label (`macos-15-intel`) so the matrix can schedule after hosted runner lifecycle changes; supplement with hermetic local Windows/Python 3.8/3.11/3.14 and WSL Ubuntu runs when hosted current-tree evidence is unavailable.
- Assert CLI adapter seams are actually invoked so a future facade refactor cannot produce a false-green error-path test.
- Characterize real Windows junction refusal in a temporary directory, with a platform skip when PowerShell/junction creation is unavailable.
- Characterize the macOS Hermes data-root branch and POSIX CLI symlink/shim fallback without touching a real home directory.
- Keep the single-owner persistence/status invariant in the local regression suite as well as the hosted CI gate.
- Require restore manifests, archive members, destinations, and directory-swap markers to pass owner-level fail-closed checks; use immutable archive snapshots and bounded source reads for the local-tampering boundary.

### D3. Conditional module split

Only after the above tests are green, extract:

- `state.py` for persistence;
- `installer.py` for runtime-manifest lifecycle;
- `mcp_registration.py` for MCP formats;
- `legacy_hooks.py` for status/uninstall only.

Keep `engine.py` as the repair/planning facade. Do not split solely for line count, and do not keep duplicate implementations behind a compatibility facade.

**Phase 2D exit:** dead paths are retired with evidence, public compatibility remains, docs are synchronized, CI covers declared platforms, and the module split has no behavior duplication.

## Verification matrix

Every phase runs:

```text
python -m unittest discover -s tests -v
python -c "from pathlib import Path; import py_compile; files=[Path('scripts/fix.py'),Path('mcp/server.py'),Path('mcp/smoke_test.py')]+list(Path('agentfix').glob('*.py'))+list(Path('tests').glob('*.py')); [py_compile.compile(str(p),doraise=True) for p in files]"
```

Phase-specific checks use temporary directories and mocks. No command in this plan authorizes real install/uninstall/check/apply, provider writes, hook cleanup, backup/restore, or user configuration changes.

Before handoff:

- read back the final diff and changed-file list;
- verify no unexpected files were added to the dirty worktree;
- run a final catalog/reference/documentation consistency scan;
- report any verification that could not be performed rather than treating it as passed.

## Anti-entropy record

- **Retirement decision:** internal dead responsibilities are delete-first only after characterization coverage.
- **Compatibility exceptions:** existing public issue IDs, 12 MCP tools, launcher paths, legacy hook cleanup, CLI `--json`, DSH documented path, and restore `latest` remain unless separately approved.
- **Confirmation-first:** `config_win`, persistent-state format changes, destructive cleanup behavior, and any unknown external consumer boundary.
- **Gap closure:** no new alias table, dispatcher, duplicate planner, duplicate writer, or automatic bulk repair is allowed.
