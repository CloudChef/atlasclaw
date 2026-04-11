# Unified Tool Loop and Provider Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace provider fast-path short-circuit behavior with a unified tool loop that uses metadata-driven minimal toolset filtering and forbids successful provider answers without real tool execution.

**Architecture:** Keep one shared execution loop for built-in tools, provider tools, and skill tools. Add a structured intent-planning stage that consumes compact provider/skill metadata, project the policy-filtered tool universe into a minimal executable set for the turn, and enforce a success contract that only allows tool-required turns to finish after real tool dispatch.

**Tech Stack:** Python, FastAPI, PydanticAI runtime, AtlasClaw session/history runtime, pytest, real-agent E2E

---

### Task 1: Write Failing Tests for Pseudo-Tool Success Paths

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_tool_gate_integration.py`
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_provider_fast_path.py`
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_execution_loop.py`

- [ ] **Step 1: Add a failing test for provider turn with no real tool call**

Add coverage that simulates a provider-targeted turn where the model emits assistant text but no executable tool node. Assert the run does not finish as success and does not emit `Answered`.

- [ ] **Step 2: Run the targeted tests to confirm they fail for the right reason**

Run: `pytest tests/atlasclaw/agent/test_runner_tool_gate_integration.py tests/atlasclaw/agent/test_runner_provider_fast_path.py tests/atlasclaw/agent/test_runner_execution_loop.py -q`

Expected: FAIL because the current runtime still accepts provider fast-path text fallback.

- [ ] **Step 3: Add a failing test for minimal toolset projection**

Add a test that starts from a mixed policy-allowed tool universe and asserts the projected set narrows correctly by `provider_type`, `group_ids`, `capability_class`, and explicit `tool_names`, while retaining only required coordination built-ins.

- [ ] **Step 4: Re-run the targeted tests and confirm both failure classes are present**

Run: `pytest tests/atlasclaw/agent/test_runner_tool_gate_integration.py tests/atlasclaw/agent/test_runner_provider_fast_path.py tests/atlasclaw/agent/test_runner_execution_loop.py -q`

Expected: FAIL with missing projection logic and pseudo-tool success behavior still present.

- [ ] **Step 5: Review A**

Confirm the new tests map directly to sections 8, 9, and 10 of [2026-04-10-unified-tool-loop-and-provider-routing-design.md](C:\Projects\cmps\atlasclaw\docs\superpowers\specs\2026-04-10-unified-tool-loop-and-provider-routing-design.md).

- [ ] **Step 6: Review B**

Confirm the failing assertions target runtime behavior rather than test scaffolding mistakes.

### Task 2: Add Intent Plan Model and Metadata Catalog Builder

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_model.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_routing.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\skills\registry.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\skills\md_tool_runtime.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\catalog.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\registration.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_tool_gate_integration.py`

- [ ] **Step 1: Introduce normalized planner payload types**

Add a normalized in-memory planner payload that captures:
- `action`
- `target_provider_types`
- `target_skill_names`
- `target_group_ids`
- `target_capability_classes`
- `target_tool_names`
- `missing_inputs`
- `reason`

- [ ] **Step 2: Reuse existing provider and skill metadata to build compact hint docs**

Ensure runtime catalog generation reuses:
- provider `provider_type/display_name/version/aliases/keywords/capabilities/use_when/avoid_when`
- skill `name/description/provider_type/triggers/use_when/avoid_when`
- tool `group_ids/capability_class/priority`

- [ ] **Step 3: Add planner prompt and coercion logic**

Make the planner ask the model for the structured intent plan only. It must not emit tool results or final answer text.

- [ ] **Step 4: Run the relevant tests**

Run: `pytest tests/atlasclaw/agent/test_runner_tool_gate_integration.py -q`

Expected: planner normalization tests pass, older failing runtime tests still fail.

- [ ] **Step 5: Review A**

Confirm metadata reuse matches sections 6 and 7 of the spec.

- [ ] **Step 6: Review B**

Confirm no new hardcoded business-domain routing branch was introduced.

### Task 3: Implement Deterministic Minimal Toolset Projection

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_routing.py`
- Add: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_projection.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_tool_gate_integration.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_tool_projection.py`

- [ ] **Step 1: Create projection helper for minimal executable toolset**

Implement deterministic narrowing from policy-allowed tools using the plan output in the order defined by the spec.

- [ ] **Step 2: Preserve only required coordination built-ins**

Allow re-adding only the tiny fixed built-in subset required for provider coordination, not the full tool universe.

- [ ] **Step 3: Reject empty projection from silently widening**

If projection yields no executable business tools, return an explicit unresolved result for clarification or failure handling.

- [ ] **Step 4: Run the projection tests**

Run: `pytest tests/atlasclaw/agent/test_tool_projection.py tests/atlasclaw/agent/test_runner_tool_gate_integration.py -q`

Expected: projection tests pass, pseudo-tool success tests still fail until loop enforcement is changed.

- [ ] **Step 5: Review A**

Confirm projection follows section 8 of the spec exactly.

- [ ] **Step 6: Review B**

Confirm there is no fallback to the full tool universe.

### Task 4: Remove Provider Fast-Path Success and Enforce Unified Loop

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_stream.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_post.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_prepare.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_loop.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_provider_fast_path.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\agent\test_runner_execution_loop.py`

- [ ] **Step 1: Remove short-circuit success branches**

Delete or disable:
- `provider_fast_path_tool_short_circuit`
- `provider_fast_path_skip_second_model_attempt`
- `fast_path_tool_answer` as a success path

- [ ] **Step 2: Keep tool-required turns inside the same loop**

When the plan action is `use_tools`, allow only:
- real tool execution
- clarification
- explicit failure

Do not allow synthesized text-only success.

- [ ] **Step 3: Enforce success contract in post-processing**

For tool-required turns, require real executed tool evidence before:
- emitting assistant text
- persisting successful transcript
- emitting `Answered`

- [ ] **Step 4: Run the loop enforcement tests**

Run: `pytest tests/atlasclaw/agent/test_runner_provider_fast_path.py tests/atlasclaw/agent/test_runner_execution_loop.py tests/atlasclaw/agent/test_runner_tool_gate_integration.py -q`

Expected: all tests in this slice pass.

- [ ] **Step 5: Review A**

Confirm sections 9, 10, and 11 of the spec are fully implemented.

- [ ] **Step 6: Review B**

Confirm there is no remaining runtime path where provider turns can succeed without real tool dispatch.

### Task 5: Update Provider Metadata Examples and Runtime Contract Docs

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\docs\UNIFIED_PROVIDER_TOOL_CONTRACT.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\ARCHITECTURE.MD`
- Modify: `C:\Projects\cmps\atlasclaw\docs\MODULE-DETAILS.MD`
- Modify: `C:\Projects\cmps\atlasclaw-providers\providers\SmartCMP-Provider\PROVIDER.md`

- [ ] **Step 1: Document unified loop and success contract**

Update docs so the runtime model clearly states that provider, skill, and built-in tools all run in one loop.

- [ ] **Step 2: Document metadata-driven projection**

Clarify how provider metadata and skill metadata participate in per-turn minimal toolset derivation.

- [ ] **Step 3: Update SmartCMP provider metadata if any required alias/hint fields are missing**

Only add metadata fields that are part of the shared contract.

- [ ] **Step 4: Review A**

Confirm docs align with the new spec and do not describe the removed fast-path behavior.

- [ ] **Step 5: Review B**

Confirm SmartCMP metadata remains contract-driven and not special-cased in code.

### Task 6: Full Verification and Real-Agent E2E

**Files:**
- Use existing runtime and test artifacts
- Record report under: `C:\Projects\cmps\atlasclaw\docs\project\state\current.md` if needed

- [ ] **Step 1: Run focused runner and tool tests**

Run: `pytest tests/atlasclaw/agent -q`

- [ ] **Step 2: Run broader backend verification**

Run: `pytest tests/atlasclaw -q`

- [ ] **Step 3: Run real-agent SmartCMP E2E**

Required dialog:
1. `查下CMP 里目前所有待审批`
2. `我要看下TIC20260316000001的详情`
3. `还有查下CMP 里目前有的服务目录`

Required evidence:
- each turn must show real tool execution,
- each turn must return correct CMP data,
- each turn timing must be recorded,
- any model timeout counts as failure.

- [ ] **Step 4: Review A**

Check every acceptance criterion in the spec and list any gaps before claiming completion.

- [ ] **Step 5: Review B**

Perform a final code review pass for god-file regression, unsafe branches, and leftover short-circuit behavior.
