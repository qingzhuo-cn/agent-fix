# Agent-fix Phase 1 Compatibility Convergence - Intent

## TaskIntentDraft

- Requested outcome: Resolve approved Phase 1 redundancy without breaking public compatibility or touching persistent user state
- Goal: Repair launcher and documentation contracts, collapse duplicate npm lifecycle ownership, clarify provider behavior, and add regression evidence
- Success evidence:
- Approved spec acceptance criteria pass through unit, CLI, launcher, catalog, documentation, and isolated MCP checks
- Stop condition: done only when all approved criteria pass; stop for scope change, persistent-state need, or public-contract retirement
- Non-goals:
- No persistent user-state mutation, new compatibility owner, MCP profile, or Phase 2 retirement
- Scope: Launcher argument forwarding; no-op JSON removal; npm lifecycle owner clarification; provider behavior text; executable documentation; focused tests
- Change kinds:
- refactor
- Risk hints:
- Pre-existing dirty workspace overlaps task paths; public commands and docs must remain synchronized

## BaselineReadSetHint

- AGENTS.md
- docs/aegis/specs/2026-09-25-agent-fix-phase1-convergence-brief.md
- docs/aegis/plans/2026-09-25-agent-fix-phase1-convergence.md

## BaselineUsageDraft

- Required baseline refs:
- AGENTS.md
- docs/aegis/specs/2026-09-25-agent-fix-phase1-convergence-brief.md
- docs/aegis/plans/2026-09-25-agent-fix-phase1-convergence.md
- Acknowledged before plan:
- none
- Cited in plan:
- none
- Missing refs:
- AGENTS.md
- docs/aegis/specs/2026-09-25-agent-fix-phase1-convergence-brief.md
- docs/aegis/plans/2026-09-25-agent-fix-phase1-convergence.md
- Advisory decision: needs-baseline-readback

## ImpactStatementDraft

- Compatibility boundary: Preserve all issue ids, 12 MCP tools, launcher paths, hook cleanup, and persistent user state
- Affected layers:
- CLI and launcher adapters
- catalog and provider behavior
- user documentation and tests
- Owners:
- agentfix existing owners
- Invariants:
- All repair and verification remain scoped to one explicit target
- Non-goals:
- No persistent user-state mutation, new compatibility owner, MCP profile, or Phase 2 retirement

These records are Method Pack drafts / hints, not authoritative runtime decisions.
