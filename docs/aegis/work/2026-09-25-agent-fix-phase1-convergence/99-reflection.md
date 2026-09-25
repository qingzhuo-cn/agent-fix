# Agent-fix Phase 1 Compatibility Convergence - Reflection

## Outcome

- Phase 1 compatibility convergence is implemented and verified within the approved scope.
- Public issue ids, all 12 MCP tools, all launcher paths, legacy hook cleanup, and persistent user state remain preserved.
- The only approved user-visible contract change is that no-op `list/info --json` invocations now fail; `check/apply --json` works before or after the subcommand and emits one JSON document.

## Repair and retirement

- **Repair track:** launchers forward one explicit target; the CLI JSON contract is executable and parseable; npm lifecycle repair has one catalog owner; non-Claude provider apply is manual/no-write; documentation matches catalog applicability.
- **Retirement track:** duplicate npm lifecycle execution under `agent-broken-generic`, hidden no-op JSON registrations, retired bulk command examples, wrong MCP names, and ZCode `gui-path-blind` routing were removed.
- **Retained paths:** `agent-broken-generic` remains a public diagnostic/manual-routing issue; launchers, hook cleanup, and the 12-tool MCP surface remain compatibility surfaces. Phase 2 retirement candidates remain explicitly deferred.

## Review closure

- Independent review initially found three Important and two Minor issues.
- All findings were repaired: root-level JSON compatibility, real-engine JSON stdout/prompt separation, ZCode documentation routing, multi-launcher documentation scanning, and spec/plan consistency.
- Final advisory re-review reported no open findings and `Ready: Yes`.

## Evidence and limits

- Fresh post-repair evidence: `py_compile` passed; 21/21 unit tests passed; four unsupported JSON placements failed as expected; root/check/apply help passed; PowerShell and POSIX launcher help checks passed; isolated-home MCP smoke passed with exactly 12 tools; temporary state was removed; `git diff --check` passed.
- Real install/uninstall/check/apply operations were intentionally not executed because they can mutate user or Agent state. Parser, handler, real-engine-with-mocked-command, launcher-help, and isolated MCP seams cover the approved Phase 1 risk.
- Confidence: B - strong bounded evidence, with destructive and cross-OS installation paths intentionally unexecuted.

## Complexity and ADR closure

- `agentfix/engine.py` is 974 lines and is a pre-existing soft-pressure artifact; this slice made only local wiring/output-stream changes, added no owner, adapter, or fallback, and retired duplicate behavior. Test and CLI artifacts remain below 800 lines. Net entropy decreased; no unresolved complexity blocker.
- ADR backfill was skipped: the ownership clarification is reversible and already durably recorded in the approved spec, implementation plan, `AGENTS.md`, and catalog contract; no new dependency direction or durable platform trade-off requires a separate ADR.

## Repository handoff

- The repository was already heavily dirty before this task. Task-owned changes overlap those paths, so no files were staged, reset, cleaned, or committed.
