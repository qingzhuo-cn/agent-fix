# Phase 2 Safety and Ecosystem Convergence — Intent

- Workstream: `2026-09-25-agent-fix-phase2-safety`
- Parent plan: `docs/aegis/plans/2026-09-25-agent-fix-phase2-safety-and-ecosystem.md`
- Goal: correct result truth and adapter status, harden persistent state and installed artifacts, converge catalog/platform ownership, add current Kimi Code/MiniMax Code metadata, then retire dead paths.
- Scope: repository code, catalog, tests, CI, and user-facing docs.
- Non-goals: real user-profile install/uninstall/check/apply/provider/hook/backup/restore; bulk repair; startup hooks; broad Git cleanup/staging/commit; new runtime dependencies.
- Compatibility: preserve public issue IDs, 12 MCP tools, launchers, explicit single-target safety, legacy hook cleanup, CLI JSON placement, and documented manual fallbacks.
- TDD: auto/strict; hermetic characterization tests precede behavior changes.
- Baseline: branch `main`, HEAD `541281801b998cb11dbef32521f48026348ae286`, dirty worktree inherited from Phase 1; no staged paths; no active worktree operation.
- Authority refs: `AGENTS.md`, Phase 1 plan/spec, current code/tests/catalog, official Kimi/MiniMax docs recorded in the parent plan.
- Stop states: done when phase acceptance and full verification pass; blocked only for a concrete unresolved contract/safety blocker; needs-verification when a required environment cannot be exercised.
