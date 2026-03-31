# Full Context Engine Design

## 1. Overview

AtlasClaw already assembles context from multiple sources, including session history, long-term memory, provider outputs, tool results, Hook Runtime outputs, and runtime metadata. However, those sources are currently connected through implicit orchestration paths rather than through an explicit, governable Context Engine.

A full Context Engine is required because AtlasClaw must do more than decide whether tools are mandatory. Once tools, providers, hooks, memory, and session history all participate in a run, the system needs a consistent way to decide:

- which context sources are relevant,
- which ones should dominate the current turn,
- how they should be transformed into model-ready evidence,
- how token budget should be managed,
- and how evidence provenance should remain inspectable.

This spec defines the **full Context Engine** as a separate Phase 2 design. It intentionally complements, but does not replace, the Phase 1 runtime policy described in:
- `docs/superpowers/specs/2026-03-31-tool-necessity-gate-design.md`

The relationship is:
- **Tool Necessity Gate** decides whether tools are required.
- **Capability Matcher** resolves what capabilities can satisfy that requirement.
- **Mandatory Tool Enforcement** prevents unsupported direct answers.
- **Context Engine** determines how grounded evidence, memory, history, provider state, and hook outputs are assembled into the final model context.

---

## 2. Scope

### 2.1 In Scope

- A unified Context Engine abstraction for AtlasClaw.
- Explicit context source registration.
- Context selection, ranking, transformation, and budgeting.
- Evidence-oriented prioritization of tool/provider/browser results.
- Integration with sessions, memory, providers, hooks, and heartbeat outputs.
- Provenance tracking for context fragments.
- Policy-based context assembly strategies.
- Observability and testing for context decisions.

### 2.2 Out of Scope

- Replacing the tool registry.
- Replacing the Hook Runtime.
- Replacing the heartbeat runtime.
- Redesigning provider SDK contracts.
- Designing a new plugin runtime.

Those systems remain inputs to the Context Engine, not replacements for it.

---

## 3. Problem Statement

Today AtlasClaw has usable context sources, but not an explicit context decision layer.

That creates several risks:
- grounded tool results may be mixed with stale conversation history and lose importance,
- old memory may dominate new evidence,
- provider outputs and hook outputs may be injected inconsistently,
- context assembly behavior may drift across run paths,
- token budgets may be consumed by low-value history instead of the most relevant evidence.

A full Context Engine is therefore required to make context assembly:
- intentional,
- explainable,
- testable,
- and policy-driven.

---

## 4. Design Goals

### 4.1 Primary Goals

- Make context assembly explicit and governable.
- Prioritize grounded evidence over stale priors when required.
- Support many context sources without ad hoc prompt growth.
- Preserve user/session/provider isolation.
- Keep provenance visible enough for debugging and future auditability.
- Integrate cleanly with the Tool Necessity Gate runtime policy.

### 4.2 Non-Goals

This spec does not:
- replace the current runner wholesale in a single step,
- force every provider to redesign its payloads immediately,
- define a graph database or vector engine requirement,
- define a final UI for evidence inspection.

---

## 5. OpenClaw Alignment and AtlasClaw Strengthening

OpenClaw's published concepts show a stronger emphasis on:
- context engines,
- grounding-backed providers,
- hooks/plugins,
- and runtime extensibility.

AtlasClaw should align with that direction by introducing an explicit Context Engine rather than leaving context assembly distributed across prompt helpers and run-time convenience paths.

AtlasClaw should also strengthen the model in two ways:
1. clearer interaction with the Tool Necessity Gate runtime,
2. explicit provenance and source-priority rules for enterprise use cases.

---

## 6. Architecture Summary

```text
Request
  -> Tool Necessity Gate / Capability Matcher / Enforcement
  -> Context Engine
       -> Source Registry
       -> Selector
       -> Ranker
       -> Transformer
       -> Budget Manager
       -> Provenance Tracker
  -> Model-ready context package
  -> Final run
```

The Context Engine is responsible for assembling the model-ready context package after runtime policy determines what evidence is required.

---

## 7. Context Source Registry

### 7.1 Responsibility

The Context Source Registry defines all context sources the runtime may use and provides a common metadata contract for them.

### 7.2 Initial Source Classes

- `user_message`
- `session_history`
- `memory_confirmed`
- `provider_context`
- `tool_results`
- `browser_extraction`
- `hooks_context`
- `heartbeat_context`
- `runtime_metadata`
- `system_prompt_material`

### 7.3 Source Metadata

Each registered source should expose metadata such as:
- `source_type`
- `scope`
- `owner_user_id`
- `session_key`
- `created_at`
- `freshness`
- `confidence`
- `sensitivity`
- `provenance_ref`
- `cost_estimate`

---

## 8. Context Selection

### 8.1 Responsibility

Selection determines which sources are candidates for the current turn.

### 8.2 Selection Inputs

- current request
- gate decision
- user/session scope
- available provider/tool outputs
- known memory candidates
- hook/heartbeat context
- current token budget

### 8.3 Required Selection Rules

1. `user_message` is always included.
2. `tool_results` become required candidates when enforcement or completed tool execution exists.
3. `provider_context` is required when the request is provider-scoped.
4. `memory_confirmed` is optional and relevance-driven.
5. `session_history` is included but may be truncated or down-ranked.
6. `hooks_context` and `heartbeat_context` are included only when relevant to the current turn.

---

## 9. Context Ranking and Prioritization

### 9.1 Responsibility

Ranking determines relative importance among selected candidates.

### 9.2 Priority Principles

1. Current grounded evidence outranks stale conversational priors.
2. User-scoped private system state outranks public search when the task is private.
3. Confirmed memory outranks speculative memory-like notes.
4. Recent corrective feedback outranks older assistant assumptions.
5. Low-value verbosity should lose against compact, high-confidence evidence.

### 9.3 Example Ranking Outcomes

- rent search query:
  - `tool_results` > `browser_extraction` > recent `session_history` > older `memory`
- Jira task query:
  - `provider_context` > recent `session_history` > `memory`
- stable conceptual question:
  - minimal `session_history` > optional `memory`; no forced external evidence

---

## 10. Context Transformation

### 10.1 Responsibility

Transformation converts heterogeneous inputs into model-ready blocks.

### 10.2 Required Block Types

- `conversation_block`
- `evidence_block`
- `memory_block`
- `provider_state_block`
- `hook_context_block`
- `system_guidance_block`
- `runtime_note_block`

### 10.3 Transformation Rules

- tool/provider/browser outputs should become `evidence_block` variants,
- confirmed long-term memory should become concise `memory_block` entries,
- free-form logs must not be dumped directly into the prompt,
- long structured results should be summarized before inclusion if budget requires it.

---

## 11. Context Budgeting and Compression

### 11.1 Responsibility

Budgeting ensures the most relevant information survives within model limits.

### 11.2 Required Strategies

- recent-window trimming for session history,
- top-k memory recall,
- tool-result summarization,
- provider payload compaction,
- hook/heartbeat summary compaction,
- hard caps per block type.

### 11.3 Budgeting Principles

- never drop the user message,
- avoid dropping high-priority grounded evidence before trimming low-value history,
- explain truncation decisions in debug/trace outputs where possible.

---

## 12. Context Policy Layer

### 12.1 Responsibility

The Context Policy Layer applies different assembly strategies based on request type and runtime classification.

### 12.2 Example Policies

- `grounded_public_lookup`
  - emphasize `tool_results`, reduce old memory/history
- `private_provider_query`
  - emphasize `provider_context`, session continuity, and recent corrections
- `browser_action`
  - emphasize current browser extraction and immediate prior task state
- `heartbeat_agent_turn`
  - emphasize heartbeat prompt, limited history, and relevant operational context
- `stable_knowledge`
  - minimal context, no unnecessary evidence inflation

---

## 13. Provenance and Evidence Tracking

### 13.1 Responsibility

The Context Engine must preserve enough provenance metadata to explain where important context came from.

### 13.2 Required Provenance Fields

- `source_type`
- `source_id`
- `origin_runtime`
- `captured_at`
- `owner_scope`
- `summary_hash` or equivalent compact fingerprint

### 13.3 Why This Matters

This is required to support:
- debugging grounded-answer failures,
- future audit surfaces,
- better tool anti-fabrication enforcement,
- learning which context sources are actually useful.

---

## 14. Interaction with Tool Necessity Gate

### 14.1 Separation of Responsibility

The Context Engine does not decide whether tools are mandatory.
That decision belongs to:
- Tool Necessity Gate,
- Capability Matcher,
- Mandatory Tool Enforcement.

### 14.2 Integration Contract

Once tool enforcement requires or produces evidence, the Context Engine must:
- select that evidence,
- rank it highly,
- transform it into evidence blocks,
- protect it during budgeting,
- and expose provenance.

This is why the full Context Engine is a required follow-on to the Phase 1 policy runtime.

---

## 15. Runtime Integration

The full Context Engine should eventually sit between:
- runtime policy and tool execution outputs,
- current prompt builder,
- current history/memory/provider/hook injectors.

Likely integration points:
- `agent/runner.py`
- `agent/prompt_builder.py`
- `agent/history_memory.py`
- `agent/runtime_events.py`
- Hook Runtime context sinks
- heartbeat context/event bridge

The migration should be incremental rather than a single rewrite.

---

## 16. Events and Observability

Suggested event taxonomy:
- `context_engine.sources_collected`
- `context_engine.selection_completed`
- `context_engine.ranking_completed`
- `context_engine.transformation_completed`
- `context_engine.budget_applied`
- `context_engine.final_package_created`
- `context_engine.provenance_attached`

Each event should include:
- `run_id`
- `session_key`
- `user_id`
- selected source counts,
- dropped source counts,
- final block summary,
- budget metrics,
- high-priority evidence summary.

---

## 17. Testing Strategy

### 17.1 Unit Tests
- source registration,
- selection rules,
- ranking rules,
- transformation contracts,
- budgeting behavior,
- provenance attachment.

### 17.2 Integration Tests
- tool-required query with grounded evidence prioritized,
- provider query with provider context dominating,
- memory + session + tool result interaction,
- browser extraction + tool result interaction,
- heartbeat turn context assembly.

### 17.3 E2E Scenarios
- time-sensitive public lookup,
- private provider workflow,
- browser-driven task,
- hook-generated context inclusion,
- heartbeat-generated context inclusion.

---

## 18. Phase Scope

### Phase 2

Implement the full Context Engine in stages:
1. source registry and context block model,
2. selection and ranking,
3. transformation and budgeting,
4. provenance and observability,
5. migration of existing prompt assembly paths.

### Dependency on Phase 1

Phase 1 should land first:
- `docs/superpowers/specs/2026-03-31-tool-necessity-gate-design.md`

Phase 1 solves policy and enforcement.
Phase 2 solves explicit context orchestration.

---

## 19. Recommended Direction

AtlasClaw should explicitly separate:
- **tool-use necessity policy**, and
- **context assembly policy**.

Doing so keeps the runtime understandable:
- the gate decides whether evidence is mandatory,
- the context engine decides how that evidence is assembled and prioritized.

Both are required for a reliable enterprise agent runtime, but they should be designed and implemented as separate, staged capabilities.
