# Current State

## Objective
- Design a staged reliability architecture for AtlasClaw so the runtime can decide when answers must be grounded through tools or external systems, and so a later full Context Engine can explicitly govern how evidence, memory, session history, provider state, and hook outputs are assembled for the model.
- Split the design into two specs:
  - Phase 1: Tool Necessity Gate runtime policy
  - Phase 2: Full Context Engine

## Completed
- Reviewed canonical architecture, module, and development docs before proposing the design.
- Reviewed the current prompt builder, runner, tool registration, and web search tool path.
- Confirmed AtlasClaw already exposes `web_search`, `web_fetch`, browser, provider tools, Hook Runtime, and session/memory context sources.
- Confirmed the current runtime does not enforce tool usage for time-sensitive or externally-grounded questions; the model is free to answer directly even when tools are needed.
- Confirmed AtlasClaw already has basic context assembly, but not an explicit Context Engine that governs source selection, ranking, transformation, budgeting, and provenance.
- Finalized the staged design direction:
  - Phase 1: Tool Necessity Gate, Capability Matcher, Mandatory Tool Enforcement, and minimal context integration
  - Phase 2: Full Context Engine
- Wrote the Phase 1 Tool Necessity Gate design spec.
- Wrote the separate Full Context Engine design spec.
- Completed a document alignment review across `state`, `task`, and both `spec` files so the scope, terminology, phase boundaries, and next step match.

## In Progress
- Waiting for user review of the written specs before moving to implementation planning.

## Risks / Decisions
- This feature must solve a general reliability problem, not a narrow "weather query" special case.
- Phase 1 should decide whether tools are mandatory and prevent unsupported direct answers.
- Phase 1 must include only the minimal context integration needed to ensure tool results become privileged evidence.
- The full Context Engine is required, but it should remain a separate design and implementation track rather than being implicitly hidden inside the Tool Necessity Gate workstream.
- AtlasClaw should take inspiration from OpenClaw's richer runtime stack (grounding/search/context/hook patterns) while making runtime policy and context orchestration more explicit.
- The design should remain compatible with the existing Hook Runtime, memory, session, and tool systems instead of creating a separate orchestration stack.

## Next Step
- Have the user review:
  - `docs/superpowers/specs/2026-03-31-tool-necessity-gate-design.md`
  - `docs/superpowers/specs/2026-03-31-full-context-engine-design.md`
- If approved, write the implementation plan before touching code.
