# Tool Necessity Gate Design

## 1. Overview

AtlasClaw already has a broad tool surface, including web search, page fetch, browser automation, session context, memory recall, provider tools, and Hook Runtime integrations. The main reliability gap is not missing tools. The gap is that the runtime does not explicitly decide **when tool usage is mandatory** before the model is allowed to produce a final answer.

This spec defines the **Phase 1 runtime policy layer** that governs whether a request may be answered directly or must first use tools/external context.

This Phase 1 design includes three coordinated layers:

1. **Tool Necessity Gate**
   - Decides whether the request can be answered directly or requires tools/external verification.
2. **Capability Matcher**
   - Maps the gate decision to the currently available AtlasClaw capabilities.
3. **Mandatory Tool Enforcement**
   - Prevents an ungrounded final answer when tool use is required.

This spec also includes a **minimal context integration requirement** so that tool results become privileged context for the current turn. It does **not** attempt to define a full standalone Context Engine. That larger design is intentionally separated into its own spec.

---

## 2. Scope

### 2.1 In Scope

- Runtime detection of when a request requires tool-backed or externally-grounded execution.
- Mapping abstract capability needs to concrete tools/provider/browser capabilities.
- Enforcement rules that block unsupported direct answers.
- Minimal context integration so tool results are prioritized in the current turn.
- Observability and testing for the policy path.

### 2.2 Out of Scope

- Full context-source selection/ranking/budgeting architecture.
- A general context graph or context pipeline rewrite.
- Full replacement of current session/memory/history orchestration.
- A new plugin runtime.

Those belong to the separate **Full Context Engine** design.

---

## 3. Problem Statement

Today AtlasClaw:
- injects current time into the prompt,
- exposes tools in the runtime prompt,
- has browser, search, session, memory, and provider capabilities,
- but still relies too much on the model to decide when those tools are necessary.

That allows failure modes such as:
- claiming current information without actual lookup,
- answering time-sensitive questions from stale priors,
- ignoring provider/browser tools even when required,
- skipping verification for dynamic market/listing/workflow questions.

The system must move from:
- "tools are available"

to:
- "the runtime knows when tools are required"

---

## 4. Design Goals

### 4.1 Primary Goals

- Prevent ungrounded answers for externally-dependent or dynamically changing questions.
- Generalize across many question types instead of building one-off weather/news patches.
- Reuse existing AtlasClaw runner, hooks, sessions, memory, and tool surfaces.
- Make the decision process observable and enforceable.

### 4.2 Non-Goals

This spec does not:
- define a complete Context Engine,
- redesign session history selection,
- redesign memory recall ranking,
- define a full evidence provenance graph,
- replace all prompt construction logic.

---

## 5. OpenClaw Alignment and AtlasClaw Strengthening

OpenClaw already exposes important primitives that reduce hallucination risk:
- web search and fetch,
- grounding-capable providers,
- context-engine concepts,
- hooks/plugins.

However, the decision of whether tool usage is mandatory is still more implicit and distributed across configuration, plugins, and runtime behavior.

AtlasClaw should align with that richer runtime direction while explicitly strengthening one missing control point:

- **the runtime policy that decides whether a direct answer is allowed at all**.

This makes AtlasClaw more explicit than a default OpenClaw setup while remaining compatible with a future richer context system.

---

## 6. Architecture Summary

```text
User Request
  -> Tool Necessity Gate
  -> Capability Matcher
  -> Mandatory Tool Enforcement
  -> Tool-first or direct-answer path
  -> Final Answer
```

### 6.1 Runtime Components

1. `ToolNecessityGate`
2. `CapabilityMatcher`
3. `ToolEnforcementPolicy`
4. `ResolvedToolPlan`
5. `ToolUseAudit`

### 6.2 Integration Points

These components integrate with:
- `agent/runner.py`
- `agent/prompt_builder.py`
- `agent/runtime_events.py`
- current tool registry / tool catalog
- session history and memory injection
- Hook Runtime for observability

---

## 7. Tool Necessity Gate

### 7.1 Responsibility

The Tool Necessity Gate classifies whether the current request may be answered directly or requires tool-backed execution before a final answer is allowed.

### 7.2 Gate Inputs

The gate should evaluate:
- current user message,
- recent message history,
- session/channel scope,
- authenticated user identity and roles,
- known provider/tool availability,
- optionally lightweight model-assisted classification.

### 7.3 Decision Schema

```json
{
  "needs_tool": true,
  "needs_live_data": true,
  "needs_private_context": false,
  "needs_external_system": false,
  "needs_browser_interaction": false,
  "needs_grounded_verification": true,
  "suggested_tool_classes": ["web_search", "web_fetch"],
  "confidence": 0.92,
  "reason": "The answer depends on current external information and should be verified before finalizing.",
  "policy": "must_use_tool"
}
```

### 7.4 Decision Dimensions

- `needs_tool`
- `needs_live_data`
- `needs_private_context`
- `needs_external_system`
- `needs_browser_interaction`
- `needs_grounded_verification`
- `suggested_tool_classes`
- `confidence`
- `reason`
- `policy`

### 7.5 Classification Strategy

The gate should use a hybrid strategy:

1. **Static signals**
- Time-sensitive language
- Dynamic listing/market language
- Action-oriented workflow language
- Private-system language

2. **Context-aware signals**
- Known provider/system names
- User-scoped resource references
- Real-world decision risk implied by the question

3. **Lightweight model-assisted classification**
- Internal control question only, for example:
  - "Can this request be answered reliably without using tools or current external/private information?"
- This is a signal, not final authority.

### 7.6 Example Decisions

#### Stable knowledge
- "法国首都是哪里？"
- `policy = answer_direct`

#### Time-sensitive public information
- "清明节上海周边会下雨吗？"
- `policy = must_use_tool`
- `suggested_tool_classes = ["web_search", "web_fetch"]`

#### Dynamic public listing
- "上海现在租房哪里便宜？"
- `policy = must_use_tool`
- `suggested_tool_classes = ["web_search", "browser"]`

#### Private provider state
- "我在 Jira 里还有哪些待处理工单？"
- `policy = must_use_tool`
- `suggested_tool_classes = ["provider:jira"]`

#### Browser workflow
- "帮我把这篇内容发到知乎"
- `policy = must_use_tool`
- `suggested_tool_classes = ["browser"]`

---

## 8. Capability Matcher

### 8.1 Responsibility

The Capability Matcher maps gate output to concrete capabilities available in the current AtlasClaw runtime.

### 8.2 Supported Capability Classes

Initial capability classes:
- `web_search`
- `web_fetch`
- `browser`
- `provider:<type>`
- `memory`
- `session`
- `hooks_context`

### 8.3 Output Shape

```json
{
  "resolved_policy": "must_use_tool",
  "tool_candidates": [
    {"name": "web_search", "class": "web_search", "priority": 100},
    {"name": "web_fetch", "class": "web_fetch", "priority": 80}
  ],
  "missing_capabilities": [],
  "reason": "Live public information is required and web search tools are available."
}
```

### 8.4 Matching Rules

1. Prefer provider-specific tools for provider-targeted requests.
2. Prefer browser capability for workflow/UI tasks.
3. Prefer private/session/provider context over public web search when the request is user-scoped.
4. If no capability exists, preserve the requirement and mark the missing capability instead of allowing fabricated completion.

---

## 9. Mandatory Tool Enforcement

### 9.1 Responsibility

If the gate and matcher determine tool use is required, the runtime must prevent a normal ungrounded final answer path.

### 9.2 Enforcement Modes

- `answer_direct`
- `prefer_tool`
- `must_use_tool`

### 9.3 Anti-Fabrication Rule

The model must not claim:
- that a search occurred,
- that an online verification happened,
- that current data was checked,

unless the runtime has actual tool execution evidence.

### 9.4 Allowed Runtime Behaviors for `must_use_tool`

1. Retry with stronger tool-required instruction.
2. Route into a controlled tool-first path.
3. Stop with an explicit explanation that verification failed.

### 9.5 Failure Behavior

If tool usage is required but no successful grounding was produced:
- do not silently fall back to a confident unsupported answer,
- state that verification could not be completed,
- expose the policy outcome in runtime events/logs.

---

## 10. Minimal Context Integration

### 10.1 Why This Exists in Phase 1

Tool enforcement without context prioritization is incomplete. If the runtime obtains tool results but injects them as ordinary low-priority text beside stale history, the model may still underuse them.

### 10.2 Minimal Integration Requirement

Phase 1 requires only a minimal context integration rule:
- when enforcement requires tools, successful tool results must become privileged context for the current turn.

That means:
- tool results should be injected before the final answer step,
- tool results should be easier for the model to prioritize than stale memory/history,
- the runtime should distinguish grounded evidence from free-form assistant reasoning.

### 10.3 What Phase 1 Does Not Do

Phase 1 does not yet define:
- full context source registration,
- context ranking across all sources,
- generalized truncation/budgeting policy,
- global context provenance graph.

Those belong to the separate Full Context Engine spec.

---

## 11. Prompt and Runtime Integration

### 11.1 Prompt Guidance

Prompt updates should explicitly state:
- some requests require tools or verification,
- the model must not invent evidence,
- if the runtime marks tool usage as mandatory, the model must follow that policy.

### 11.2 Runtime Priority

Prompt guidance is necessary but not sufficient.

The runtime policy executes before unrestricted answer generation. The prompt should reinforce, not replace, the gate and enforcement logic.

---

## 12. Events and Observability

Suggested event taxonomy:
- `tool_gate.evaluated`
- `tool_gate.required`
- `tool_gate.optional`
- `tool_matcher.resolved`
- `tool_matcher.missing_capability`
- `tool_enforcement.blocked_final_answer`
- `tool_enforcement.prefetch_started`
- `tool_enforcement.prefetch_completed`
- `tool_enforcement.prefetch_failed`

Each event should carry:
- `session_key`
- `run_id`
- `user_id`
- `decision`
- `suggested_tool_classes`
- `resolved_tools`
- `reason`
- `confidence`
- `final_outcome`

These events should integrate with the existing Hook Runtime as observable runtime outputs.

---

## 13. Testing Strategy

### 13.1 Unit Tests
- gate classification outputs,
- matcher resolution,
- missing-capability behavior,
- enforcement behavior,
- anti-fabrication validation.

### 13.2 Integration Tests
- runner + gate + matcher path,
- provider-targeted requests,
- browser-required requests,
- failed tool execution with constrained final response.

### 13.3 E2E Scenarios
- weather/forecast style query,
- dynamic listing query such as rent/jobs,
- provider-backed private query,
- browser-action request,
- stable knowledge query that should not be forced through tools.

---

## 14. Phase Scope

### Phase 1

Implement:
- Tool Necessity Gate schema and classifier,
- Capability Matcher,
- `must_use_tool` enforcement path,
- anti-fabrication rule,
- minimal privileged tool-result context integration,
- runtime events,
- prompt updates,
- unit and integration coverage.

### Later Work

A richer context system is required but is intentionally separated into a different design artifact:
- `docs/superpowers/specs/2026-03-31-full-context-engine-design.md`

---

## 15. Recommended Direction

AtlasClaw should not solve this with more ad hoc query-specific prompt rules.

The correct Phase 1 direction is:
- determine whether tools are required,
- match the requirement to actual capabilities,
- prevent unsupported direct answers,
- ensure grounded tool results receive minimal privileged context treatment.

That gives AtlasClaw a runtime policy for reliability without prematurely expanding this change into a full context-system rewrite.
