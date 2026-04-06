# Context Management Alignment Plan (AtlasClaw vs OpenClaw)

## Scope
- Compare AtlasClaw and OpenClaw context-management implementations at architecture and runtime levels.
- Define the target alignment boundaries for AtlasClaw (must-align / optional / out-of-scope).
- Produce an implementation-ready design direction before any code refactor.

## Steps
1. [x] Baseline comparison (success criteria: concrete file-level diff map for guard, pruning, compaction, prompt/bootstrap, memory tools, session/transcript)
2. [x] Parity target selection (success criteria: user-approved target level: strict parity / pragmatic parity / selective parity)
3. [x] Design options and trade-offs (success criteria: 2-3 options with recommendation and risks)
4. [x] Final design alignment review (success criteria: state/task/spec terminology and scope aligned; no contradictory boundaries)
5. [x] Implementation plan handoff (success criteria: detailed executable plan with tests and rollout checkpoints)

## Verification
- command: compare `app/atlasclaw/agent|session|memory` with `openclaw-cn/src/agents|config/sessions|memory`; write spec; run self-review for placeholders/contradictions/scope drift; cross-check state/task/spec consistency
- expected: a complete, user-reviewable context-alignment spec with explicit acceptance criteria and phased rollout
- actual: completed. Spec written at `docs/superpowers/specs/2026-04-04-context-management-alignment-design.md`; parity mode fixed to pragmatic alignment; state/task/spec terminology aligned

## Handoff Notes
- No implementation changes are included in this plan file.
- This plan is the collaboration protocol entrypoint for the next context-management refactor cycle.
- Implementation plan written at `docs/superpowers/plans/2026-04-05-context-management-alignment-implementation-plan.md`.
- Execution mode selected: inline execution (user requested to continue without pausing).

## Implementation Execution Status (2026-04-05)
- [x] Task 1 complete: context window guard module integrated into runner warn/block path.
- [x] Task 2 complete: session-aware prompt context resolver integrated into prompt builder with per-file and total budgets.
- [x] Task 3 complete: runtime context pruning integrated into runner; compaction safeguard integrated into compaction summary pipeline.
- [x] Task 4 complete: memory search/get tools now return structured citation fields (`path`, `start_line`, `end_line`, `citation`) in `details`.
- [x] Task 5 complete: session manager now includes transcript cache (mtime/size invalidation), transient read retry, and archive budget cleanup.
- [ ] Task 6 in progress: final docs reconciliation + final regression summary + commit hygiene.

## Implementation Verification Snapshot
- command: `pytest tests/atlasclaw/test_context_window_guard.py tests/atlasclaw/test_prompt_context_resolver.py tests/atlasclaw/test_context_pruning.py tests/atlasclaw/test_memory_tool_citations.py tests/atlasclaw/session/test_session_manager_governance.py -q`
- expected: all context-alignment task tests pass
- actual: `16 passed`
- command: `pytest tests/atlasclaw -q`
- expected: full backend suite passes
- actual: `972 passed, 8 failed, 4 skipped`; current failures are outside this task scope (`e2e_api` connectivity assumptions, existing search-provider preference tests, existing `SkillsConfig` default assertion mismatch)
