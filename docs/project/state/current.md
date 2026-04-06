# Current State

## Objective
- Implement Phase 1 of the staged reliability architecture for AtlasClaw so the runtime can decide when answers must be grounded through tools or external systems.
- Define the next-stage provider-driven web search and grounding runtime that can support enterprise-grade retrieval beyond the current HTML-scrape search implementation.
- Keep Full Context Engine work as a separate future track.
- Align AtlasClaw context management architecture with OpenClaw's extension-oriented context runtime, including context-window guard, pruning, compaction safeguards, bootstrap injection, and memory citation behavior.

## Completed
- Reviewed canonical architecture, module, and development docs.
- Split the design into two specs:
  - Phase 1: Tool Necessity Gate runtime policy
  - Phase 2: Full Context Engine
- Wrote and aligned both specs.
- Wrote the Phase 1 implementation plan.
- Implemented the Phase 1 Tool Necessity Gate runtime skeleton, matcher, runner enforcement path, prompt/runtime event integration, and tests.
- Removed hard-coded keyword-table classification from the core gate.
- Removed the default hidden model-backed pre-classification pass from normal chat turns.
- Model-backed gate classification is now an explicit runtime capability, not a silent default.
- Updated canonical docs to describe the Tool Necessity Gate runtime.
- Completed post-implementation code/task/spec alignment review.
- Completed final standalone code review with no blocking findings.
- Completed verification:
  - targeted Tool Gate tests
  - full backend non-e2e suite
  - full backend e2e suite
  - compileall
  - diff hygiene
- Expanded the Phase 1 spec to include:
  - reasoning-only response handling,
  - bounded retry rules,
  - controlled-path escalation,
  - frontend-visible runtime states and retained thinking display.
- Re-aligned state/task/spec after the expanded Phase 1 scope.
- Implemented the expanded Phase 1 runtime behavior:
  - reasoning-only response detection,
  - bounded retry,
  - controlled-path escalation,
  - retained/toggleable thinking display,
  - frontend runtime visibility for `reasoning`, `retrying`, `waiting_for_tool`, `tool_running`, `controlled_path`, `answered`, and `failed`.
- Completed post-extension code/task/spec alignment review.
- Completed final standalone code review for the expanded Phase 1 implementation.
- Re-verified:
  - full backend non-e2e suite,
  - full frontend suite,
  - full backend e2e suite.
- Reviewed the current `web_search` implementation and confirmed it is still HTML-scrape-first with Bing default and public-search fallback logic.
- Wrote a new spec for a provider-driven web search and grounding runtime.
- Wrote a design-tracking task file for the provider-driven search track.
- Completed a document-alignment review for the provider-driven search `state`, `task`, and `spec`.
- Wrote the provider-driven web search implementation plan.
- Completed AtlasClaw vs OpenClaw context-management baseline comparison and identified guard/pruning/compaction/prompt/memory/session governance gaps.
- Confirmed parity direction as pragmatic alignment (behavior parity first, not 1:1 internal clone).
- Wrote full context-management alignment spec at `docs/superpowers/specs/2026-04-04-context-management-alignment-design.md`.
- Completed state/task/spec alignment review for the context-management track.
- Executed the context-management alignment implementation plan:
  - Task 1: context-window guard integrated into runner (`warn` + hard `block`).
  - Task 2: prompt context resolver integrated with per-file and total bootstrap budgets.
  - Task 3: runtime context pruning integrated into runner and compaction safeguard integrated into compaction summary generation.
  - Task 4: memory search/get tools upgraded with structured citation output (`path/start_line/end_line/citation`).
  - Task 5: session governance upgraded with transcript cache, transient read retry, and archive budget cleanup.
- Verified targeted context-alignment tests:
  - `tests/atlasclaw/test_context_window_guard.py`
  - `tests/atlasclaw/test_prompt_context_resolver.py`
  - `tests/atlasclaw/test_context_pruning.py`
  - `tests/atlasclaw/test_memory_tool_citations.py`
  - `tests/atlasclaw/session/test_session_manager_governance.py`
  - Result: all pass.

## In Progress
- Task 6 closeout for context-management alignment:
  - final documentation reconciliation,
  - full-suite regression note capture,
  - commit packaging.

## Risks / Decisions
- Phase 1 must stay narrow and avoid expanding into a full Context Engine rewrite.
- Full Context Engine remains required, but is intentionally deferred to the separate spec and later implementation track.
- The core gate must not depend on static business keyword tables; classification must be driven by explicit classifier decisions with a neutral fallback.
- The runtime must not silently reuse the primary answer model for an extra blocking gate round-trip on every request.
- Reasoning-only model behavior must be handled as a first-class runtime policy problem, not as a frontend-only loading problem.
- Frontend users must be able to observe runtime state progression instead of waiting through silent retries.
- The current HTML-based `web_search` implementation is not sufficient as the long-term enterprise search architecture.
- AtlasClaw search should align with OpenClaw's provider-driven model, but strengthen it with explicit source governance, query recovery, and runtime-policy integration.
- Context alignment should prioritize runtime stability and observability first (guard/pruning/compaction), then move to optional capability parity.
- Avoid coupling context management with hard-coded business heuristics; context policy should remain model- and runtime-driven.
- Current full backend suite still contains pre-existing failures outside this task scope (e2e connectivity assumptions and existing search-provider/default assertions). Keep them tracked separately from context-management implementation correctness.

## Next Step
- Complete Task 6 closeout and package implementation changes for user confirmation before push.
