# Provider-Driven Request Workflow Contract Design

## Summary

This design keeps the current LLM-first runtime behavior and removes the two
remaining SmartCMP-specific decisions from AtlasClaw core:

- tool success/finalize rules must no longer be hard-coded by tool name
- workflow parent-skill preference must no longer be inferred from a skill-name suffix

The change is intentionally minimal. AtlasClaw core will add two small metadata
channels:

- `success_contract` on tools
- `workflow_role` on markdown skills

SmartCMP will then declare its submit-workflow contract and request-parent role
through provider metadata instead of relying on core heuristics. The existing
`internal_request_trace_id` model, follow-up metadata recall behavior, and
scoped provider-config merge all remain in place.

This work spans two repositories:

- `atlasclaw`: metadata plumbing, generic runtime handling, and core tests
- `atlasclaw-providers`: SmartCMP metadata declaration and provider-side tests

---

## Goals

- preserve the current LLM-first routing model
- keep the existing follow-up and workflow-trace improvements
- remove the hard-coded `smartcmp_submit_request` success rule from core
- remove the `:request` suffix preference from core workflow skill inference
- tighten workflow trace isolation when an active trace already exists
- keep the patch small enough to land as a direct follow-up to the current PR

## Non-Goals

- no redesign of the tool loop
- no new planner or local capability override logic
- no new structured tool return protocol
- no changes to SmartCMP submit script output format unless required by tests
- no attempt to generalize all possible provider workflow contracts in one pass

---

## Product Rules

### LLM-First Boundary

Core may recover workflow context and load better reference documents, but it
must not use provider-specific metadata to force runtime routing. The LLM still
decides the next action from the prompt, visible tools, and recovered context.

### Provider-Driven Semantics

Provider-specific workflow meaning must be declared through metadata owned by
the provider or markdown skill, not inferred from tool names or skill names in
core code.

### Trace Isolation

If a current workflow has an active `internal_request_trace_id`, recovered
workflow context should prefer same-trace metadata only. Untraced legacy
metadata becomes an explicit fallback path, not a default mix-in.

---

## Metadata Contract

### Tool `success_contract`

AtlasClaw core will support an optional tool metadata field:

```json
{
  "type": "identifier_presence",
  "fields": ["id", "requestId", "request_id", "workflowId", "workflow_id"],
  "text_labels": ["Request ID", "Workflow ID"]
}
```

Rules:

- absent `success_contract` keeps current generic success behavior unchanged
- present `success_contract` activates contract-specific success validation
- phase 1 supports only `type=identifier_presence`
- identifier-presence validation may inspect nested dict/list content and plain
  text output
- contract validation is generic and must not mention SmartCMP in core

### Skill `workflow_role`

AtlasClaw core will support an optional markdown-skill metadata field:

- `workflow_role: request_parent`

Rules:

- absent `workflow_role` keeps current generic matching behavior
- when multiple workflow-context tools match different skills, a skill with
  `workflow_role=request_parent` wins over a plain match
- core must not prefer skill names ending with `:request`

---

## AtlasClaw Core Changes

### 1. Tool Metadata Plumbing

`atlasclaw` must carry `success_contract` through the existing metadata path for
markdown-derived tools:

- markdown frontmatter parsing
- registry registration
- tool snapshot generation
- runtime tool lookup used by finalize checks

Built-in tools do not need to declare this field now, but the metadata shape
should support it uniformly.

### 2. Generic Tool Success Validation

The current tool-result success check in core will be refactored so that:

- it reads the selected tool's metadata
- if `success_contract` is missing, it uses the existing generic rules
- if `success_contract.type == identifier_presence`, it validates presence of a
  meaningful identifier by field names and optional text labels

Core must no longer branch on:

- `tool_name == "smartcmp_submit_request"`

### 3. Workflow Parent Skill Inference

Workflow-context skill recovery will be updated so that:

- matching still starts from recent tool metadata
- if one or more matched skills declare `workflow_role=request_parent`, prefer
  that skill
- otherwise keep the current first-match fallback

Core must no longer branch on:

- `qualified_name.endswith(":request")`

### 4. Trace Isolation Tightening

When `build_target_md_skill_workflow_context()` has an active trace:

- include metadata entries with the same trace
- exclude entries with a different trace
- exclude entries with no trace by default

Fallback:

- if no same-trace entries survive, fall back to the legacy untraced behavior
  so older providers do not lose all workflow context

This keeps the current backward-compatibility promise while stopping silent
mixing during traced workflows.

---

## SmartCMP Provider Changes

### Request Skill Frontmatter

`atlasclaw-providers/providers/SmartCMP-Provider/skills/request/SKILL.md` will
declare:

- `workflow_role: request_parent`
- `tool_submit_success_contract` with the identifier-presence shape

The SmartCMP provider remains responsible for defining which identifiers prove a
real submitted request. AtlasClaw core only executes the generic contract.

### Submit Script

The submit script does not need a behavior change for this design. Its current
text output already exposes request identifiers in the successful cases that the
workflow depends on.

---

## Data Flow

### Submit Finalization

1. SmartCMP request skill declares `tool_submit_success_contract`
2. markdown tool registration carries that metadata into the runtime tool record
3. finalize logic resolves the planned tool metadata from the runtime tool index
4. tool success validation applies generic `identifier_presence` rules
5. finalize succeeds only if the declared contract is satisfied

### Workflow Continuation Hint

1. workflow context is reconstructed from recent tool metadata
2. matched tools are mapped back to declaring markdown skills
3. if a matched skill declares `workflow_role=request_parent`, prefer it
4. the chosen skill remains a soft hint for skill-doc loading only
5. LLM runtime routing remains authoritative

### Trace Recovery

1. active trace id is inferred from recent tool metadata
2. workflow context keeps same-trace entries only
3. untraced entries are ignored while same-trace entries exist
4. if nothing remains, fall back to legacy untraced collection

---

## Error Handling

### Missing or Invalid `success_contract`

If a tool declares malformed `success_contract` metadata:

- core should ignore the invalid contract and fall back to generic success
  behavior
- malformed contract metadata should not crash registration or runtime

Phase 1 can keep this silent or debug-log only. It does not need a new user
facing error path.

### Missing `workflow_role`

If no skill declares `workflow_role=request_parent`, workflow skill recovery
falls back to normal tool-to-skill matching without any special preference.

### Legacy Providers

Providers with no trace id and no new metadata continue to work with the
existing generic behavior.

---

## Testing

### AtlasClaw

Add or update tests for:

- tool success contract is metadata-driven rather than tool-name-driven
- identifier-presence contract accepts structured identifiers from dict payloads
- identifier-presence contract accepts declared plain-text labels
- workflow-role preference beats plain matched skills without using name suffixes
- active trace excludes untraced metadata when same-trace metadata exists
- legacy fallback still works when no same-trace metadata is available

Relevant suites:

- `tests/atlasclaw/test_runner_tool_execution_contract.py`
- `tests/atlasclaw/test_runner_tool_gate_behavior.py`
- `tests/atlasclaw/test_runner_prompt_context.py`
- `tests/atlasclaw/test_workflow_trace_isolation.py`

### AtlasClaw Providers

Add provider-side tests for:

- request skill frontmatter exposes `workflow_role`
- submit tool frontmatter exposes `success_contract`

If an existing metadata-layout test already covers frontmatter parsing, extend
it rather than creating a parallel fixture-heavy suite.

---

## Implementation Notes

### Why This Is The Smallest Clean Fix

This design keeps the current PR's valuable behavior:

- `internal_request_trace_id`
- follow-up metadata recall from recent user messages
- workflow metadata used as a hint instead of a routing override
- merged scoped provider config

The only semantic shift is where provider meaning is declared. SmartCMP keeps
its workflow rules, but it declares them in provider-owned metadata instead of
requiring core to know SmartCMP names.

### Expected Review Outcome

After this change, the current PR feedback should resolve to:

- keep the multi-turn workflow improvements
- remove SmartCMP-specific tool-name hard-coding from core
- remove skill-name suffix preference from core
- reduce cross-trace contamination risk without breaking legacy providers
