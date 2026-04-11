# Unified Tool Loop and Provider Routing Design

## 1. Goal

Align AtlasClaw runtime with a chat-first, OpenClaw-style execution model:

- One unified LLM tool loop for built-in tools, provider tools, and skill tools.
- No provider-only or web-only short-circuit answer path.
- Provider and skill metadata drive per-turn minimal toolset filtering.
- Requests that require external systems or live data must not succeed without real tool execution evidence.
- Real tool results stay inside the same loop and are used by the model to decide whether to continue, clarify, or answer.

## 2. Problem Statement

The current runtime still allows provider turns to terminate without a real tool call:

1. `provider_fast_path_tool_short_circuit` can stop the loop after a synthesized fallback text is built.
2. `fast_path_tool_answer` can be accepted as the final answer in post-processing.
3. `PREFER_TOOL` turns currently emit warnings for missing tool evidence instead of blocking success.
4. This permits "pseudo tool output" where the model writes text that looks like tool usage without any executed tool call.

This is incompatible with the desired contract for enterprise providers such as SmartCMP, ServiceNow, Datadog, and CyberArk.

## 3. Scope

### In scope

1. Remove provider fast-path short-circuit success behavior.
2. Introduce a structured per-turn intent plan before the main loop.
3. Reuse provider and skill metadata to derive a minimal executable toolset for the turn.
4. Keep all tools in one unified loop.
5. Enforce "no successful provider/live-data answer without executed tool evidence".
6. Add real-agent E2E coverage for SmartCMP.

### Out of scope

1. Replacing the LLM with a deterministic workflow engine.
2. Rewriting unrelated search/fetch ranking logic in this change set.
3. Adding a UI for policy editing.

## 4. Design Principles

1. Chat-first remains the product model.
   - Users may ask anything.
   - Direct answers remain allowed for stable, tool-free requests.
2. Runtime must not force business-domain tool calls through hardcoded user-text keyword branches.
3. Provider and skill metadata are reusable routing inputs, not passive documentation only.
4. Runtime decides which tools are available in the turn.
5. The model decides whether to answer, clarify, or call tools within that filtered set.
6. Successful answers that claim external facts must be backed by executed tools.

## 5. Target Execution Model

```text
user turn
  -> policy-filtered tool universe
  -> metadata-driven intent planning
  -> minimal executable toolset for this turn
  -> unified LLM loop
       -> direct_answer
       -> ask_clarification
       -> tool_call(s)
       -> loop continues with tool results
  -> final answer only if contract satisfied
```

## 6. Structured Intent Plan

Before the main loop, AtlasClaw asks the model for a structured intent plan.

### 6.1 Contract

The planning response must normalize to:

```json
{
  "action": "direct_answer | ask_clarification | use_tools",
  "target_provider_types": [],
  "target_skill_names": [],
  "target_group_ids": [],
  "target_capability_classes": [],
  "target_tool_names": [],
  "missing_inputs": [],
  "reason": ""
}
```

### 6.2 Allowed semantics

1. `direct_answer`
   - The request is stable and does not require external verification or system interaction.
2. `ask_clarification`
   - The request needs more user input before tool execution is safe or meaningful.
3. `use_tools`
   - The request needs provider tools, skill tools, live data tools, or other tool-backed execution.

### 6.3 Planning input

The planner receives:

1. The user message.
2. A compact recent context window.
3. A compact provider catalog derived from `PROVIDER.md`.
4. A compact skill catalog derived from `SKILL.md`.
5. The currently policy-allowed tool descriptors.

The planner must not receive full `SKILL.md` bodies by default.

## 7. Metadata Reuse Rules

AtlasClaw must reuse the existing metadata contract already defined in:

- `docs/UNIFIED_PROVIDER_TOOL_CONTRACT.md`
- provider `PROVIDER.md`
- skill `SKILL.md`

### 7.1 Provider metadata fields

Required:

- `provider_type`
- `display_name`
- `version`

Recommended:

- `aliases`
- `keywords`
- `capabilities`
- `use_when`
- `avoid_when`

### 7.2 Skill metadata fields

Required:

- `name`
- `description`

Recommended for provider and executable skills:

- `provider_type`
- `instance_required`
- `triggers`
- `use_when`
- `avoid_when`

Executable tool declarations:

- `tool_<id>_name`
- `tool_<id>_entrypoint`
- `tool_<id>_groups`
- `tool_<id>_capability_class`
- `tool_<id>_priority`

### 7.3 Runtime normalization

Every available executable tool must normalize to:

- `name`
- `description`
- `source`
- `provider_type`
- `skill_name`
- `group_ids`
- `capability_class`
- `priority`
- `instance_required`

## 8. Minimal Toolset Filtering

Minimal toolset filtering is deterministic and runs after policy filtering, but before the main loop.

### 8.1 Inputs

1. Policy-filtered allowed tool universe.
2. Structured intent plan.
3. Current active provider context.
4. Runtime-required built-ins.

### 8.2 Filtering order

1. Start from the policy-filtered allowed tool set.
2. If `target_provider_types` is non-empty, narrow to matching `provider_type`.
3. If `target_group_ids` is non-empty, narrow to matching `group_ids`.
4. If `target_capability_classes` is non-empty, narrow to matching `capability_class`.
5. If `target_tool_names` is non-empty, narrow to explicit tool names.
6. If `target_skill_names` is non-empty, narrow to tools declared by those skills.
7. Re-add only essential built-ins required for runtime coordination:
   - `list_provider_instances`
   - `select_provider_instance`
   - `read` when skill detail loading is required
   - optionally `session_status` if already allowed and needed

The set may only shrink from steps 1-6. Step 7 adds only the tiny fixed coordination subset.

### 8.3 Empty-set rule

If filtering yields no executable business tools:

1. The runtime must not fall back to the full tool universe.
2. The turn must either:
   - emit `ask_clarification`, or
   - fail explicitly.

## 9. Unified Loop Contract

All executable tools use one loop:

- provider tools
- skill tools
- built-in tools
- web tools
- weather tools
- session tools

There must be no special provider-only answer short-circuit and no web-only controlled path in this execution model.

## 10. Enforcement Rules

### 10.1 Tool-required turns

A turn is tool-required if either condition holds:

1. The intent plan action is `use_tools`.
2. The turn requires external system access or live data verification.

### 10.2 Success rule

For tool-required turns, a successful final answer is allowed only if at least one real tool execution occurred in the turn.

Real tool execution means:

1. a tool call was emitted,
2. runtime dispatched that tool,
3. tool result messages are present in the run state/transcript slice.

### 10.3 Forbidden success paths

For tool-required turns, AtlasClaw must not:

1. accept `fast_path_tool_answer`,
2. accept a synthesized fallback answer before a real tool dispatch,
3. emit `Answered` when no real tool execution occurred,
4. persist pseudo-tool assistant text as a successful answer.

### 10.4 Allowed recovery actions

If the model does not produce a usable tool call in a tool-required turn:

1. retry once with stronger steering toward `tool_call` or `ask_clarification`,
2. optionally reduce the toolset further using the same plan result,
3. if still unresolved, return clarification or fail.

Runtime must not convert that failure into a fake final answer.

## 11. Removal of Existing Problematic Paths

This change must remove or neutralize:

1. `provider_fast_path_tool_short_circuit`
2. `provider_fast_path_skip_second_model_attempt`
3. `fast_path_tool_answer` as a success path
4. provider-specific post-run fallback answer synthesis that bypasses real tool execution

## 12. Logging and Auditability

Each run must log:

1. policy-filtered tool count
2. intent plan output
3. minimal toolset count and members
4. per-attempt LLM payload profile
5. executed tool calls
6. whether success contract was satisfied

The intent plan and minimal toolset decision must be auditable from runtime logs.

## 13. Testing Requirements

### 13.1 Unit and integration tests

Must cover:

1. provider turn with no tool call does not end as success
2. metadata-driven minimal toolset projection
3. direct-answer turns still work without tools
4. clarification turns are allowed without tools
5. provider turns with real tool execution can answer successfully

### 13.2 Real-agent E2E

Must use the real agent, not fake agents.

Required SmartCMP conversation:

1. `查下CMP 里目前所有待审批`
2. `我要看下TIC20260316000001的详情`
3. `还有查下CMP 里目前有的服务目录`

Acceptance:

1. each turn executes real tool calls,
2. each turn returns correct CMP-backed data,
3. no model timeout is accepted as success,
4. timing per turn is recorded.

## 14. Review Protocol

Each implementation slice must complete two explicit reviews:

1. Review A: spec alignment
2. Review B: code and verification completeness

No task is complete until both reviews pass.

## 15. Acceptance Criteria

1. No provider turn can succeed through pseudo-tool text alone.
2. Provider, skill, and built-in tools run in one unified loop.
3. Existing provider/skill metadata is actively reused to derive the per-turn minimal toolset.
4. The planner and filter do not rely on hardcoded business text branches.
5. SmartCMP real-agent E2E passes with real tool execution in all three required turns.
6. Any model timeout in the required E2E is treated as failure.
