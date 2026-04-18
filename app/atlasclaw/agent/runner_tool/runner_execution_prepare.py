from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
import time
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.prompt_builder import PromptMode
from app.atlasclaw.agent.context_pruning import prune_context_messages, should_apply_context_pruning
from app.atlasclaw.agent.context_window_guard import evaluate_context_window_guard
from app.atlasclaw.agent.runner_prompt_context import (
    build_system_prompt,
    collect_capability_index_snapshot,
    collect_tool_groups_snapshot,
    collect_tools_snapshot,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import (
    build_llm_first_guidance_plan,
    resolve_artifact_goal_from_intent_plan,
    selected_capability_ids_from_intent_plan,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import normalize_tool_result_mode
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    compress_candidate_toolset,
    project_minimal_toolset,
    tool_is_coordination_support,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)
from app.atlasclaw.core.deps import SkillDeps


logger = logging.getLogger(__name__)


def select_execution_prompt_mode(
    *,
    intent_action: str,
    is_follow_up: bool,
    projected_tool_count: int,
    has_target_md_skill: bool = False,
) -> PromptMode:
    """Choose a lighter prompt for explicit tool turns with a small projected toolset."""
    normalized_action = str(intent_action or "").strip().lower()
    safe_projected_count = max(0, int(projected_tool_count or 0))
    if has_target_md_skill:
        return PromptMode.MINIMAL
    if not normalized_action and safe_projected_count == 0:
        return PromptMode.MINIMAL
    if (
        normalized_action in {
            ToolIntentAction.DIRECT_ANSWER.value,
            ToolIntentAction.ASK_CLARIFICATION.value,
        }
        and safe_projected_count == 0
    ):
        return PromptMode.MINIMAL
    if normalized_action != ToolIntentAction.USE_TOOLS.value:
        return PromptMode.FULL
    if is_follow_up:
        return PromptMode.FULL
    if 0 < safe_projected_count <= 12:
        return PromptMode.MINIMAL
    return PromptMode.FULL


def should_resolve_target_md_skill(intent_plan: ToolIntentPlan | None) -> bool:
    """Load the target markdown skill whenever the turn has an explicit md-skill target."""
    if intent_plan is None:
        return False
    if any(str(item).strip() for item in (intent_plan.target_skill_names or [])):
        return True
    if any(str(item).strip() for item in (intent_plan.target_tool_names or [])):
        return True
    return turn_action_requires_tool_execution(intent_plan)


def select_explicit_tool_execution_target(
    *,
    intent_plan: ToolIntentPlan | None,
    is_follow_up: bool,
    projected_tools: list[dict[str, Any]],
    has_target_md_skill: bool = False,
) -> Optional[dict[str, Any]]:
    """Return the single direct-execution tool for low-noise explicit tool turns."""
    actionable_turn = (
        intent_plan is not None
        and intent_plan.action in {ToolIntentAction.USE_TOOLS, ToolIntentAction.CREATE_ARTIFACT}
    )
    if not actionable_turn:
        return None

    candidate_tools: list[dict[str, Any]] = []
    for tool in projected_tools or []:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("name", "") or "").strip()
        if not tool_name or tool_is_coordination_support(tool):
            continue
        candidate_tools.append(tool)

    if len(candidate_tools) != 1:
        return None

    target_tool = candidate_tools[0]
    normalized_result_mode = normalize_tool_result_mode(target_tool)
    if normalized_result_mode == "silent_ok":
        # When the runtime has already narrowed execution to exactly one silent tool,
        # prefer the compact single-tool prompt so the model performs the tool call
        # instead of drifting into extra narration.
        return dict(target_tool)
    if is_follow_up:
        return None
    if has_target_md_skill:
        return None
    if normalized_result_mode != "tool_only_ok":
        return None
    return dict(target_tool)


def build_explicit_tool_execution_prompt(
    *,
    tool: dict[str, Any],
    now_local: Optional[datetime] = None,
) -> str:
    """Build a tiny system prompt for single-tool explicit execution turns."""
    tool_name = str(tool.get("name", "") or "").strip() or "tool"
    description = str(tool.get("description", "") or "").strip() or "No description provided."
    capability_class = str(tool.get("capability_class", "") or "").strip()
    provider_type = str(tool.get("provider_type", "") or "").strip()
    result_mode = normalize_tool_result_mode(tool) or "llm"
    parameters_schema = tool.get("parameters_schema", {})
    required_fields: list[str] = []
    properties: dict[str, Any] = {}
    if isinstance(parameters_schema, dict):
        raw_properties = parameters_schema.get("properties")
        if isinstance(raw_properties, dict):
            properties = raw_properties
        required_fields = [
            str(item).strip()
            for item in (parameters_schema.get("required", []) or [])
            if str(item).strip()
        ]

    local_now = (now_local or datetime.now().astimezone()).isoformat(timespec="seconds")
    argument_lines: list[str] = []
    for field_name, field_spec in properties.items():
        if not isinstance(field_spec, dict):
            continue
        type_name = str(field_spec.get("type", "") or "string").strip()
        field_desc = str(field_spec.get("description", "") or "").strip()
        required_label = "required" if field_name in required_fields else "optional"
        line = f"- {field_name} ({type_name}, {required_label})"
        if field_desc:
            line += f": {field_desc}"
        argument_lines.append(line)
    if not argument_lines:
        argument_lines.append("- no explicit arguments")

    capability_line = capability_class or "unknown"
    if provider_type:
        capability_line = f"{capability_line}; provider={provider_type}"

    prompt = (
        "You are AtlasClaw.\n"
        "This turn has already been narrowed to exactly one allowed runtime tool.\n"
        "Your valid actions are:\n"
        "1) If the tool has not been called yet this turn, call the allowed tool exactly once with concrete arguments.\n"
        "2) If the tool result is already available in the conversation, use that evidence to continue the workflow.\n"
        "3) Ask one concise clarification question only if required inputs are still missing.\n"
        "Do not answer from memory.\n"
        "Do not mention hidden reasoning.\n"
        "Do not mention any other tool.\n\n"
        f"Current local time:\n{local_now}\n\n"
        "Allowed tool:\n"
        f"- name: {tool_name}\n"
        f"- description: {description}\n"
        f"- capability: {capability_line}\n"
        f"- result_mode: {result_mode}\n"
        "Arguments:\n"
        f"{chr(10).join(argument_lines)}\n"
    )
    if result_mode == "silent_ok":
        prompt += (
            "If you call this tool, continue directly to the next user-facing step afterward.\n"
            "Do not call the same tool again with the same arguments after its result is available.\n"
            "Do not mention the tool call to the user and do not surface its raw output.\n"
        )
    return prompt


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_non_empty_text(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def recent_history_has_tool_activity(
    *,
    recent_history: list[dict[str, Any]],
    window: int = 6,
) -> bool:
    """Return whether the most recent transcript window shows active tool workflow state."""
    safe_window = max(1, int(window or 0))
    for message in (recent_history or [])[-safe_window:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").strip().lower()
        if role in {"tool", "toolresult", "tool_result"}:
            return True
        if message.get("tool_calls") or message.get("tool_results"):
            return True
    return False


def _artifact_classes_for_entry(entry: dict[str, Any]) -> set[str]:
    return {
        f"artifact:{str(item).strip().lower()}"
        for item in (entry.get("artifact_types", []) or [])
        if str(item).strip()
    }


def _match_selected_md_skill_entry(
    *,
    entry: dict[str, Any],
    selected_capability_ids: set[str],
    target_skill_names: set[str],
    target_tool_names: set[str],
    target_capability_classes: set[str],
) -> bool:
    capability_id = _normalize_text(entry.get("capability_id", "")).lower()
    name = _normalize_text(entry.get("name", "")).lower()
    declared_tool_names = {
        _normalize_text(item).lower()
        for item in (entry.get("declared_tool_names", []) or [])
        if _normalize_text(item)
    }
    artifact_classes = _artifact_classes_for_entry(entry)

    if capability_id and capability_id in selected_capability_ids:
        return True
    if name and name in target_skill_names:
        return True
    if declared_tool_names and declared_tool_names.intersection(target_tool_names):
        return True
    if artifact_classes and artifact_classes.intersection(target_capability_classes):
        return True
    return False


def _rank_selected_md_skill_entry(
    *,
    entry: dict[str, Any],
    original_index: int,
    selected_capability_ids: set[str],
    target_skill_order: dict[str, int],
    target_tool_order: dict[str, int],
    target_capability_classes: set[str],
) -> tuple[int, int, int, int, int]:
    capability_id = _normalize_text(entry.get("capability_id", "")).lower()
    name = _normalize_text(entry.get("name", "")).lower()
    declared_tool_names = [
        _normalize_text(item).lower()
        for item in (entry.get("declared_tool_names", []) or [])
        if _normalize_text(item)
    ]
    artifact_classes = _artifact_classes_for_entry(entry)

    capability_rank = 0 if capability_id and capability_id in selected_capability_ids else 1
    skill_rank = target_skill_order.get(name, len(target_skill_order) + 1)
    tool_rank = min(
        (target_tool_order.get(item, len(target_tool_order) + 1) for item in declared_tool_names),
        default=len(target_tool_order) + 1,
    )
    artifact_rank = 0 if artifact_classes and artifact_classes.intersection(target_capability_classes) else 1
    return (capability_rank, skill_rank, tool_rank, artifact_rank, original_index)


def _load_target_md_skill_content(
    *,
    file_path: str,
    max_file_bytes: int,
) -> tuple[str, bool]:
    normalized_path = _normalize_text(file_path)
    if not normalized_path:
        return "", False

    try:
        raw = Path(normalized_path).read_bytes()
    except Exception:
        return "", False

    safe_limit = max(1, int(max_file_bytes or 0))
    if len(raw) <= safe_limit:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:safe_limit].decode("utf-8", errors="replace"), True


def resolve_selected_md_skill_target(
    *,
    agent: Any,
    deps: SkillDeps,
    intent_plan: ToolIntentPlan | None,
    max_file_bytes: int,
) -> Optional[dict[str, Any]]:
    """Resolve the selected markdown skill for stage-two prompt expansion."""
    if intent_plan is None:
        return None

    capability_index = collect_capability_index_snapshot(agent=agent, deps=deps)
    if not capability_index:
        return None

    selected_capability_ids = {
        _normalize_text(item).lower()
        for item in selected_capability_ids_from_intent_plan(intent_plan)
        if _normalize_text(item)
    }
    target_skill_names_ordered = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_skill_names or [])
        if _normalize_text(item)
    ]
    target_skill_names = set(target_skill_names_ordered)
    target_tool_names_ordered = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_tool_names or [])
        if _normalize_text(item)
    ]
    target_tool_names = set(target_tool_names_ordered)
    target_capability_classes = {
        _normalize_text(item).lower()
        for item in (intent_plan.target_capability_classes or [])
        if _normalize_text(item)
    }
    target_skill_order = {
        name: index
        for index, name in enumerate(target_skill_names_ordered)
    }
    target_tool_order = {
        name: index
        for index, name in enumerate(target_tool_names_ordered)
    }

    matching_entries: list[tuple[tuple[int, int, int, int, int], dict[str, Any]]] = []
    for original_index, entry in enumerate(capability_index):
        if not isinstance(entry, dict):
            continue
        if _normalize_text(entry.get("kind", "")).lower() != "md_skill":
            continue
        file_path = _normalize_text(entry.get("locator", ""))
        if not file_path:
            continue
        if not _match_selected_md_skill_entry(
            entry=entry,
            selected_capability_ids=selected_capability_ids,
            target_skill_names=target_skill_names,
            target_tool_names=target_tool_names,
            target_capability_classes=target_capability_classes,
        ):
            continue
        matching_entries.append(
            (
                _rank_selected_md_skill_entry(
                    entry=entry,
                    original_index=original_index,
                    selected_capability_ids=selected_capability_ids,
                    target_skill_order=target_skill_order,
                    target_tool_order=target_tool_order,
                    target_capability_classes=target_capability_classes,
                ),
                entry,
            )
        )

    if not matching_entries:
        return None

    _, selected_entry = min(matching_entries, key=lambda item: item[0])
    file_path = _normalize_text(selected_entry.get("locator", ""))
    content, truncated = _load_target_md_skill_content(
        file_path=file_path,
        max_file_bytes=max_file_bytes,
    )
    return {
        "provider": _normalize_text(selected_entry.get("provider_type", "")),
        "qualified_name": _normalize_text(selected_entry.get("name", "")),
        "file_path": file_path,
        "content": content,
        "content_truncated": truncated,
    }


def enrich_target_md_skill_with_workflow_context(
    *,
    target_md_skill: Optional[dict[str, Any]],
    workflow_trace: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Backward-compatible no-op wrapper; runtime workflow context is not injected here."""
    del workflow_trace
    if not isinstance(target_md_skill, dict):
        return target_md_skill
    return dict(target_md_skill)


def build_retry_tool_intent_plan(
    *,
    retry_missing_tools: list[str],
    available_tools: list[dict[str, Any]],
) -> ToolIntentPlan | None:
    """Build a strict retry plan from the model's previously attempted tool names."""
    normalized_retry_tools = [
        str(name).strip() for name in (retry_missing_tools or []) if str(name).strip()
    ]
    if not normalized_retry_tools:
        return None
    available_by_name = {
        str(tool.get("name", "") or "").strip(): dict(tool)
        for tool in (available_tools or [])
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    matched_names = [name for name in normalized_retry_tools if name in available_by_name]
    if not matched_names:
        return None
    target_capability_classes: list[str] = []
    target_provider_types: list[str] = []
    for name in matched_names:
        tool = available_by_name[name]
        capability_class = str(tool.get("capability_class", "") or "").strip()
        provider_type = str(tool.get("provider_type", "") or "").strip()
        if capability_class and capability_class not in target_capability_classes:
            target_capability_classes.append(capability_class)
        if provider_type and provider_type not in target_provider_types:
            target_provider_types.append(provider_type)
    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_tool_names=matched_names,
        target_capability_classes=target_capability_classes,
        target_provider_types=target_provider_types,
        reason=(
            "Retrying this turn after the previous model attempt emitted plaintext tool-call markup "
            "instead of executing a real structured tool call."
        ),
    )


def build_recent_follow_up_tool_intent_plan(
    *,
    recent_history: list[dict[str, Any]],
    available_tools: list[dict[str, Any]],
) -> ToolIntentPlan | None:
    """Reuse the recent workflow scope for low-information or structured follow-up turns."""
    available_by_name = {
        str(tool.get("name", "") or "").strip(): dict(tool)
        for tool in (available_tools or [])
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    if not available_by_name:
        return None

    recent_tool_names: list[str] = []
    seen: set[str] = set()

    for message in reversed(recent_history or []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").strip().lower()
        candidate_names: list[str] = []
        if role == "assistant":
            for tool_call in reversed(message.get("tool_calls", []) or []):
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(
                    tool_call.get("name", tool_call.get("tool_name", "")) or ""
                ).strip()
                if tool_name:
                    candidate_names.append(tool_name)
        if role in {"tool", "toolresult", "tool_result"}:
            tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
            if tool_name:
                candidate_names.append(tool_name)
        tool_results = message.get("tool_results")
        if isinstance(tool_results, list):
            for result in reversed(tool_results):
                if not isinstance(result, dict):
                    continue
                tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
                if tool_name:
                    candidate_names.append(tool_name)
        for tool_name in candidate_names:
            if tool_name not in available_by_name or tool_name in seen:
                continue
            seen.add(tool_name)
            recent_tool_names.append(tool_name)
            if len(recent_tool_names) >= 6:
                break
        if len(recent_tool_names) >= 6:
            break

    if not recent_tool_names:
        return None

    matched_tools = [available_by_name[name] for name in recent_tool_names]
    target_skill_names = _dedupe_non_empty_text(
        [
            tool.get("qualified_skill_name", "") or tool.get("skill_name", "")
            for tool in matched_tools
        ]
    )
    target_provider_types = _dedupe_non_empty_text(
        [tool.get("provider_type", "") for tool in matched_tools]
    )
    target_group_ids = _dedupe_non_empty_text(
        [
            group_id
            for tool in matched_tools
            for group_id in (tool.get("group_ids", []) or [])
        ]
    )
    target_capability_classes = _dedupe_non_empty_text(
        [tool.get("capability_class", "") for tool in matched_tools]
    )
    target_tool_names = _dedupe_non_empty_text(recent_tool_names)

    if len(target_skill_names) > 1:
        return None
    if not target_skill_names and len(target_provider_types) > 1:
        return None
    if (
        not target_skill_names
        and not target_provider_types
        and len(target_capability_classes) > 1
    ):
        return None

    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=target_provider_types,
        target_skill_names=target_skill_names,
        target_group_ids=target_group_ids,
        target_capability_classes=target_capability_classes,
        target_tool_names=target_tool_names,
        reason=(
            "Follow-up context reused the recent workflow scope from the current thread so the "
            "main model can continue the same evidence-driven workflow."
        ),
    )


def prune_auto_selected_provider_instance_tools(
    *,
    available_tools: list[dict[str, Any]],
    deps: Optional[SkillDeps],
    intent_plan: ToolIntentPlan | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove redundant provider-selector tools when the target provider has one instance."""
    trace: dict[str, Any] = {
        "enabled": False,
        "removed_tools": [],
        "target_provider_types": [],
        "auto_selected_provider_types": [],
    }
    if not available_tools:
        return list(available_tools or []), trace
    if deps is None or not isinstance(getattr(deps, "extra", None), dict):
        return list(available_tools), trace

    extra = deps.extra
    provider_instances = extra.get("provider_instances")
    if not isinstance(provider_instances, dict) or not provider_instances:
        return list(available_tools), trace

    target_provider_types: list[str] = []
    if intent_plan is not None:
        for item in (intent_plan.target_provider_types or []):
            provider_type = str(item or "").strip().lower()
            if provider_type and provider_type not in target_provider_types:
                target_provider_types.append(provider_type)

    if not target_provider_types:
        selected_provider_type = ""
        provider_instance = extra.get("provider_instance")
        if isinstance(provider_instance, dict):
            selected_provider_type = str(
                provider_instance.get("provider_type", "") or ""
            ).strip().lower()
        if not selected_provider_type:
            selected_provider_type = str(extra.get("provider_type", "") or "").strip().lower()
        if selected_provider_type:
            target_provider_types.append(selected_provider_type)

    if not target_provider_types:
        visible_provider_types: list[str] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            if tool_is_coordination_support(tool):
                continue
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            if provider_type and provider_type not in visible_provider_types:
                visible_provider_types.append(provider_type)
        if len(visible_provider_types) == 1:
            target_provider_types = list(visible_provider_types)

    if not target_provider_types:
        return list(available_tools), trace

    auto_selected_provider_types = [
        provider_type
        for provider_type in target_provider_types
        if (
            isinstance(provider_instances.get(provider_type), dict)
            and len(provider_instances.get(provider_type) or {}) == 1
        )
    ]
    if not auto_selected_provider_types:
        return list(available_tools), trace

    filtered_tools: list[dict[str, Any]] = []
    removed_tools: list[str] = []
    for tool in available_tools:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("name", "") or "").strip()
        if tool_name in {"select_provider_instance", "list_provider_instances"}:
            removed_tools.append(tool_name)
            continue
        filtered_tools.append(dict(tool))

    trace.update(
        {
            "enabled": bool(removed_tools),
            "removed_tools": removed_tools,
            "target_provider_types": list(target_provider_types),
            "auto_selected_provider_types": auto_selected_provider_types,
        }
    )
    if not removed_tools:
        return list(available_tools), trace
    return filtered_tools, trace




class RunnerExecutionPreparePhaseMixin:
    async def _run_prepare_phase(self, *, state: dict[str, Any], _log_step: Any) -> AsyncIterator[StreamEvent]:
        """Prepare runtime/session/prompt/tool-gate phase before model loop."""
        session_key = state.get("session_key")
        user_message = state.get("user_message")
        deps = state.get("deps")
        max_tool_calls = state.get("max_tool_calls")
        timeout_seconds = state.get("timeout_seconds")
        _token_failover_attempt = state.get("_token_failover_attempt")
        _emit_lifecycle_bounds = state.get("_emit_lifecycle_bounds")
        start_time = state.get("start_time")
        tool_calls_count = state.get("tool_calls_count")
        compaction_applied = state.get("compaction_applied")
        thinking_emitter = state.get("thinking_emitter")
        persist_override_messages = state.get("persist_override_messages")
        persist_override_base_len = state.get("persist_override_base_len")
        runtime_agent = state.get("runtime_agent")
        selected_token_id = state.get("selected_token_id")
        release_slot = state.get("release_slot")
        flushed_memory_signatures = state.get("flushed_memory_signatures")
        extra = state.get("extra")
        run_id = state.get("run_id")
        tool_execution_retry_count = state.get("tool_execution_retry_count")
        run_failed = state.get("run_failed")
        message_history = state.get("message_history")
        system_prompt = state.get("system_prompt")
        final_assistant = state.get("final_assistant")
        context_history_for_hooks = state.get("context_history_for_hooks")
        tool_call_summaries = state.get("tool_call_summaries")
        session_title = state.get("session_title")
        buffered_assistant_events = state.get("buffered_assistant_events")
        assistant_output_streamed = state.get("assistant_output_streamed")
        tool_request_message = state.get("tool_request_message")
        tool_intent_plan = state.get("tool_intent_plan")
        tool_gate_decision = state.get("tool_gate_decision")
        tool_match_result = state.get("tool_match_result")
        current_model_attempt = state.get("current_model_attempt")
        current_attempt_started_at = state.get("current_attempt_started_at")
        current_attempt_has_text = state.get("current_attempt_has_text")
        current_attempt_has_tool = state.get("current_attempt_has_tool")
        reasoning_retry_count = state.get("reasoning_retry_count")
        run_output_start_index = state.get("run_output_start_index")
        tool_execution_required = state.get("tool_execution_required")
        reasoning_retry_limit = state.get("reasoning_retry_limit")
        model_stream_timed_out = state.get("model_stream_timed_out")
        model_timeout_error_message = state.get("model_timeout_error_message")
        runtime_context_window_info = state.get("runtime_context_window_info")
        runtime_context_guard = state.get("runtime_context_guard")
        runtime_context_window = state.get("runtime_context_window")
        session_manager = state.get("session_manager")
        session = state.get("session")
        transcript = state.get("transcript")
        all_available_tools = state.get("all_available_tools")
        tool_groups_snapshot = state.get("tool_groups_snapshot")
        available_tools = state.get("available_tools")
        candidate_visible_tools = state.get("candidate_visible_tools")
        toolset_filter_trace = state.get("toolset_filter_trace")
        tool_projection_trace = state.get("tool_projection_trace")
        candidate_tool_compression_trace = state.get("candidate_tool_compression_trace")
        used_toolset_fallback = state.get("used_toolset_fallback")
        provider_hint_docs = state.get("provider_hint_docs")
        skill_hint_docs = state.get("skill_hint_docs")
        tool_hint_docs = state.get("tool_hint_docs")
        metadata_candidates = state.get("metadata_candidates")
        ranking_trace = state.get("ranking_trace")
        artifact_goal = state.get("artifact_goal")
        runtime_message_history = state.get("runtime_message_history")
        session_message_history = state.get("session_message_history")
        runtime_base_history_len = state.get("runtime_base_history_len")
        persist_run_output_start_index = state.get("persist_run_output_start_index")
        try:
            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_start()
            _log_step("lifecycle_start")
            yield StreamEvent.runtime_update(
                "reasoning",
                "Starting response analysis.",
                metadata={"phase": "start", "attempt": 0, "elapsed": 0.0},
            )

            runtime_agent, selected_token_id, release_slot = await self._resolve_runtime_agent(session_key, deps)
            logger.warning(
                "runtime token resolved: session=%s selected_token_id=%s managed_tokens=%s",
                session_key,
                selected_token_id,
                len(self.token_policy.token_pool.tokens) if self.token_policy is not None else 0,
            )
            runtime_context_window_info = self._resolve_runtime_context_window_info(selected_token_id, deps)
            runtime_context_guard = evaluate_context_window_guard(
                tokens=runtime_context_window_info.tokens,
                source=runtime_context_window_info.source,
            )
            runtime_context_window = runtime_context_guard.tokens
            _log_step(
                "context_guard_evaluated",
                tokens=runtime_context_guard.tokens,
                source=runtime_context_guard.source,
                should_warn=runtime_context_guard.should_warn,
                should_block=runtime_context_guard.should_block,
            )
            if runtime_context_guard.should_warn:
                yield StreamEvent.runtime_update(
                    "warning",
                    (
                        "Model context window is below the warning threshold. "
                        f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                    ),
                    metadata={
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "warn",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
            if runtime_context_guard.should_block:
                failure_message = (
                    "Model context window is below the minimum safety threshold. "
                    f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                )
                run_failed = True
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                await self.runtime_events.trigger_run_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "block",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                state["should_stop"] = True
                return
            session_manager = self._resolve_session_manager(session_key, deps)

            # --:session + build prompt --

            session = await session_manager.get_or_create(session_key)
            _log_step("session_get_or_create_done")
            transcript = await session_manager.load_transcript(session_key)
            _log_step("session_load_transcript_done", transcript_entries=len(transcript))
            message_history = self.history.build_message_history(transcript)
            message_history = self.history.prune_summary_messages(message_history)
            if should_apply_context_pruning(settings=self.context_pruning_settings, session=session):
                message_history = prune_context_messages(
                    messages=message_history,
                    settings=self.context_pruning_settings,
                    context_window_tokens=runtime_context_window,
                )
            message_history = self._deduplicate_message_history(message_history)
            context_history_for_hooks = list(message_history)
            session_title = str(getattr(session, "title", "") or "")
            await self.runtime_events.trigger_message_received(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_message_received_dispatched")
            await self.runtime_events.trigger_run_started(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_run_started_dispatched")
            await self._maybe_set_draft_title(
                session_manager=session_manager,
                session_key=session_key,
                session=session,
                transcript=transcript,
                user_message=user_message,
            )
            _log_step("session_draft_title_done")
            all_available_tools = collect_tools_snapshot(agent=runtime_agent, deps=deps)
            _log_step("tools_snapshot_collected", all_tools_count=len(all_available_tools))
            tool_groups_snapshot = collect_tool_groups_snapshot(deps)
            _log_step("tool_groups_snapshot_collected", group_count=len(tool_groups_snapshot))
            available_tools, toolset_filter_trace, used_toolset_fallback = self._build_turn_toolset(
                deps=deps,
                session_key=session_key,
                all_tools=all_available_tools,
                tool_groups=tool_groups_snapshot,
            )
            _log_step(
                "toolset_policy_applied",
                total_tools=len(all_available_tools),
                filtered_tools=len(available_tools),
                used_fallback=used_toolset_fallback,
                policy_layers=len(toolset_filter_trace),
            )
            if isinstance(deps.extra, dict):
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["toolset_policy_trace"] = list(toolset_filter_trace)
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            provider_hint_docs = self._build_provider_hint_docs(
                deps=deps,
                available_tools=available_tools,
            )
            skill_hint_docs = self._build_skill_hint_docs(
                deps=deps,
                available_tools=available_tools,
            )
            tool_hint_docs = self._build_tool_hint_docs(
                available_tools=available_tools,
            )
            if isinstance(deps.extra, dict):
                deps.extra["provider_hint_docs"] = provider_hint_docs
                deps.extra["skill_hint_docs"] = skill_hint_docs
                deps.extra["tool_hint_docs"] = tool_hint_docs
            _log_step(
                "hint_docs_built",
                provider_hint_count=len(provider_hint_docs),
                skill_hint_count=len(skill_hint_docs),
                tool_hint_count=len(tool_hint_docs),
            )
            tool_request_message, used_follow_up_context = self._resolve_contextual_tool_request(
                user_message=user_message,
                recent_history=message_history,
            )
            _log_step(
                "tool_request_resolved",
                used_follow_up_context=used_follow_up_context,
                raw_user_message=user_message,
                resolved_tool_request=tool_request_message,
            )
            metadata_candidates = self._recall_provider_skill_candidates_from_metadata(
                user_message=tool_request_message,
                recent_history=message_history,
                used_follow_up_context=used_follow_up_context,
                available_tools=available_tools,
                provider_hint_docs=provider_hint_docs,
                skill_hint_docs=skill_hint_docs,
                tool_hint_docs=tool_hint_docs,
                top_k_provider=self.TOOL_METADATA_PROVIDER_TOP_K,
                top_k_skill=self.TOOL_METADATA_SKILL_TOP_K,
            )
            ranking_trace = {
                "status": "metadata_recall",
                "reason": str(metadata_candidates.get("reason", "") or "metadata_recall"),
                "confidence": float(metadata_candidates.get("confidence", 0.0) or 0.0),
                "preferred_provider_types": list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                "preferred_group_ids": list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                "preferred_capability_classes": list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                "preferred_tool_names": list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            }
            if isinstance(deps.extra, dict):
                deps.extra["tool_metadata_candidates"] = dict(metadata_candidates)
                deps.extra["tool_ranking_trace"] = dict(ranking_trace)
            _log_step(
                "tool_metadata_recalled",
                confidence=float(metadata_candidates.get("confidence", 0.0) or 0.0),
                preferred_provider_types=list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                preferred_group_ids=list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                preferred_capability_classes=list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                preferred_tool_names=list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            )
            metadata_tool_intent_plan = self._build_metadata_fallback_tool_intent_plan(
                metadata_candidates=metadata_candidates,
                available_tools=available_tools,
            )
            retry_missing_tools = []
            if isinstance(getattr(deps, "extra", None), dict):
                candidate_retry_tools = deps.extra.get("tool_execution_retry_missing_tools")
                if isinstance(candidate_retry_tools, list):
                    retry_missing_tools = [
                        str(name).strip()
                        for name in candidate_retry_tools
                        if str(name).strip()
                    ]
            retry_tool_intent_plan = build_retry_tool_intent_plan(
                retry_missing_tools=retry_missing_tools,
                available_tools=available_tools,
            )
            if retry_tool_intent_plan is not None:
                metadata_tool_intent_plan = retry_tool_intent_plan
            if metadata_tool_intent_plan is not None:
                _log_step(
                    "tool_metadata_hint_resolved",
                    action=metadata_tool_intent_plan.action.value,
                    target_provider_types=list(metadata_tool_intent_plan.target_provider_types),
                    target_skill_names=list(metadata_tool_intent_plan.target_skill_names),
                    target_capability_classes=list(metadata_tool_intent_plan.target_capability_classes),
                    target_tool_names=list(metadata_tool_intent_plan.target_tool_names),
                )
            pre_compression_tools = list(available_tools)
            available_tools, candidate_compression_trace = compress_candidate_toolset(
                allowed_tools=available_tools,
                metadata_candidates=metadata_candidates,
                used_follow_up_context=used_follow_up_context,
            )
            # Group co-retention after compression (same logic as projection)
            if available_tools and len(available_tools) < len(pre_compression_tools):
                _cg_ids: set[str] = set()
                for _ct in available_tools:
                    for _g in (_ct.get("group_ids", []) or []):
                        _gs = str(_g).strip()
                        if _gs:
                            _cg_ids.add(_gs)
                if _cg_ids:
                    _cex = {str(_t.get("name", "")).strip() for _t in available_tools}
                    for _ct2 in pre_compression_tools:
                        _cn = str(_ct2.get("name", "")).strip()
                        if _cn in _cex:
                            continue
                        _ctg = {
                            str(_g).strip()
                            for _g in (_ct2.get("group_ids", []) or [])
                            if str(_g).strip()
                        }
                        if _ctg.intersection(_cg_ids):
                            available_tools.append(_ct2)
                            _cex.add(_cn)
            candidate_visible_tools = list(available_tools)
            candidate_tool_compression_trace = dict(candidate_compression_trace)
            if isinstance(deps.extra, dict):
                deps.extra["candidate_tool_compression_trace"] = dict(candidate_compression_trace)
            _log_step(
                "candidate_toolset_compressed",
                before_count=int(candidate_compression_trace.get("before_count", 0) or 0),
                after_count=int(candidate_compression_trace.get("after_count", 0) or 0),
                reason=str(candidate_compression_trace.get("reason", "") or ""),
                coordination_tools=list(candidate_compression_trace.get("coordination_tools", []) or []),
            )
            has_recent_tool_activity = recent_history_has_tool_activity(
                recent_history=message_history,
            )
            explicit_capability_match = self._metadata_plan_represents_explicit_capability_match(
                metadata_candidates=metadata_candidates,
                metadata_plan=metadata_tool_intent_plan,
                available_tools=available_tools,
            )
            tool_intent_plan = build_llm_first_guidance_plan(
                user_message=tool_request_message,
                metadata_plan=metadata_tool_intent_plan,
                explicit_capability_match=explicit_capability_match,
            )
            if tool_intent_plan is None and (used_follow_up_context or has_recent_tool_activity):
                follow_up_tool_intent_plan = build_recent_follow_up_tool_intent_plan(
                    recent_history=message_history,
                    available_tools=available_tools,
                )
                if follow_up_tool_intent_plan is not None:
                    tool_intent_plan = build_llm_first_guidance_plan(
                        user_message=tool_request_message,
                        metadata_plan=follow_up_tool_intent_plan,
                        explicit_capability_match=True,
                    )
                    _log_step(
                        "follow_up_tool_context_reused",
                        action=follow_up_tool_intent_plan.action.value,
                        target_provider_types=list(
                            follow_up_tool_intent_plan.target_provider_types
                        ),
                        target_capability_classes=list(
                                follow_up_tool_intent_plan.target_capability_classes
                            ),
                            target_tool_names=list(follow_up_tool_intent_plan.target_tool_names),
                        )
            artifact_goal = resolve_artifact_goal_from_intent_plan(metadata_tool_intent_plan)
            if isinstance(deps.extra, dict):
                if artifact_goal is not None:
                    deps.extra["artifact_goal"] = dict(artifact_goal)
                else:
                    deps.extra.pop("artifact_goal", None)
            _log_step(
                "artifact_goal_resolved",
                artifact_kind=str((artifact_goal or {}).get("kind", "") or ""),
                artifact_label=str((artifact_goal or {}).get("label", "") or ""),
                source="metadata_capability",
            )
            if isinstance(deps.extra, dict):
                if tool_intent_plan is not None:
                    deps.extra["tool_intent_plan"] = tool_intent_plan.model_dump(mode="python")
                else:
                    deps.extra.pop("tool_intent_plan", None)
            _log_step(
                "routing_guidance_built",
                enabled=tool_intent_plan is not None,
                action=(
                    tool_intent_plan.action.value
                    if tool_intent_plan is not None
                    else ""
                ),
                target_provider_types=list(tool_intent_plan.target_provider_types or [])
                if tool_intent_plan is not None
                else [],
                target_skill_names=list(tool_intent_plan.target_skill_names or [])
                if tool_intent_plan is not None
                else [],
                target_group_ids=list(tool_intent_plan.target_group_ids or [])
                if tool_intent_plan is not None
                else [],
                target_capability_classes=list(tool_intent_plan.target_capability_classes or [])
                if tool_intent_plan is not None
                else [],
                target_tool_names=list(tool_intent_plan.target_tool_names or [])
                if tool_intent_plan is not None
                else [],
                missing_inputs=list(tool_intent_plan.missing_inputs or [])
                if tool_intent_plan is not None
                else [],
            )
            # Detect active multi-step workflows from recent history.
            # If the conversation has recent tool activity, keep tools
            # available so the model can continue the same workflow.
            if (
                tool_intent_plan is None
                and not explicit_capability_match
                and not used_follow_up_context
                and not has_recent_tool_activity
            ):
                available_tools = []
                _log_step(
                    "direct_answer_tools_hidden",
                    reason="no_strong_capability_match",
                )
            elif (
                tool_intent_plan is None
                and not explicit_capability_match
                and (has_recent_tool_activity or used_follow_up_context)
            ):
                _log_step(
                    "workflow_tools_retained",
                    reason="recent_tool_activity_or_follow_up_detected",
                )

            if tool_intent_plan is not None:
                tool_gate_decision = self._normalize_tool_gate_decision(
                    self._build_tool_gate_decision_from_intent_plan(
                        tool_intent_plan,
                        available_tools=available_tools,
                    )
                )
            else:
                tool_gate_decision = self._normalize_tool_gate_decision(
                    ToolGateDecision(
                        needs_tool=False,
                        reason=(
                            "LLM-first runtime routing is active. The main model decides this "
                            "turn after capability pruning."
                        ),
                        policy=ToolPolicyMode.ANSWER_DIRECT,
                    )
                )
            pre_projection_tools = list(available_tools)
            available_tools, tool_projection_trace = project_minimal_toolset(
                allowed_tools=available_tools,
                intent_plan=tool_intent_plan,
            )
            # Group co-retention: if any tool from a group was projected,
            # include all tools from that group so multi-step skill
            # workflows keep the rest of the sequence available.
            if available_tools and pre_projection_tools:
                projected_group_ids: set[str] = set()
                for _pt in available_tools:
                    for _gid in (_pt.get("group_ids", []) or []):
                        _gid_s = str(_gid).strip()
                        if _gid_s:
                            projected_group_ids.add(_gid_s)
                if projected_group_ids:
                    _existing = {str(_t.get("name", "")).strip() for _t in available_tools}
                    for _pt2 in pre_projection_tools:
                        _tname = str(_pt2.get("name", "")).strip()
                        if _tname in _existing:
                            continue
                        _tgroups = {
                            str(_g).strip()
                            for _g in (_pt2.get("group_ids", []) or [])
                            if str(_g).strip()
                        }
                        if _tgroups.intersection(projected_group_ids):
                            available_tools.append(_pt2)
                            _existing.add(_tname)
            runtime_visible_tools = list(available_tools)
            provider_instance_pruning_trace: dict[str, Any] = {}
            runtime_visible_tools, provider_instance_pruning_trace = (
                prune_auto_selected_provider_instance_tools(
                    available_tools=runtime_visible_tools,
                    deps=deps,
                    intent_plan=tool_intent_plan,
                )
            )
            if provider_instance_pruning_trace.get("enabled"):
                _log_step(
                    "provider_instance_tools_pruned",
                    removed_tools=list(
                        provider_instance_pruning_trace.get("removed_tools", []) or []
                    ),
                    target_provider_types=list(
                        provider_instance_pruning_trace.get("target_provider_types", []) or []
                    ),
                    auto_selected_provider_types=list(
                        provider_instance_pruning_trace.get(
                            "auto_selected_provider_types", []
                        )
                        or []
                    ),
                )
            runtime_allowed_tool_names = [
                str(tool.get("name", "") or "").strip()
                for tool in runtime_visible_tools
                if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
            ]
            available_tools = runtime_visible_tools
            if isinstance(deps.extra, dict):
                deps.extra["tool_projection_trace"] = dict(tool_projection_trace)
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["runtime_allowed_tool_names"] = list(runtime_allowed_tool_names)
                deps.extra["provider_instance_pruning_trace"] = dict(
                    provider_instance_pruning_trace
                )
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            _log_step(
                "tool_projection_applied",
                before_count=int(tool_projection_trace.get("before_count", 0) or 0),
                after_count=int(tool_projection_trace.get("after_count", 0) or 0),
                runtime_visible_count=len(available_tools),
                reason=str(tool_projection_trace.get("reason", "") or ""),
                coordination_tools=list(tool_projection_trace.get("coordination_tools", []) or []),
            )
            target_md_skill = None
            if should_resolve_target_md_skill(tool_intent_plan):
                target_md_skill = resolve_selected_md_skill_target(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                    intent_plan=tool_intent_plan,
                    max_file_bytes=int(
                        getattr(self.prompt_builder.config, "md_skills_max_file_bytes", 262144)
                        or 262144
                    ),
                )
            if isinstance(deps.extra, dict):
                if isinstance(target_md_skill, dict):
                    deps.extra["target_md_skill"] = dict(target_md_skill)
                else:
                    deps.extra.pop("target_md_skill", None)
            _log_step(
                "target_md_skill_resolved",
                enabled=bool(target_md_skill),
                qualified_name=(
                    str(target_md_skill.get("qualified_name", "") or "")
                    if isinstance(target_md_skill, dict)
                    else ""
                ),
                loaded_content=bool(
                    isinstance(target_md_skill, dict)
                    and str(target_md_skill.get("content", "") or "").strip()
                ),
            )
            tool_match_result = CapabilityMatcher(available_tools=available_tools).match(
                tool_gate_decision.suggested_tool_classes
            )
            logger.warning(
                "tool_intent decision: session=%s action=%s policy=%s needs_external=%s needs_live_data=%s suggested=%s candidates=%s",
                session_key,
                tool_intent_plan.action.value if tool_intent_plan is not None else "llm_first",
                tool_gate_decision.policy.value,
                bool(tool_gate_decision.needs_external_system),
                bool(tool_gate_decision.needs_live_data),
                list(tool_gate_decision.suggested_tool_classes),
                [
                    str(getattr(candidate, "name", "") or "").strip()
                    for candidate in tool_match_result.tool_candidates
                    if str(getattr(candidate, "name", "") or "").strip()
                ],
            )
            _log_step(
                "tool_gate_decided",
                action=tool_intent_plan.action.value if tool_intent_plan is not None else "llm_first",
                policy=tool_gate_decision.policy.value,
                needs_tool=bool(tool_gate_decision.needs_tool),
                needs_external=bool(tool_gate_decision.needs_external_system),
                needs_live_data=bool(tool_gate_decision.needs_live_data),
                suggested_classes=list(tool_gate_decision.suggested_tool_classes),
                candidate_count=len(tool_match_result.tool_candidates),
                missing_capabilities=list(tool_match_result.missing_capabilities),
            )
            tool_execution_required = turn_action_requires_tool_execution(tool_intent_plan)
            reasoning_retry_limit = self.REASONING_ONLY_MAX_RETRIES
            if tool_execution_required:
                reasoning_retry_limit = 0
            self._inject_tool_policy(
                deps=deps,
                intent_plan=tool_intent_plan,
                available_tools=available_tools,
            )
            _log_step(
                "tool_policy_injected",
                tool_execution_required=tool_execution_required,
                reasoning_retry_limit=reasoning_retry_limit,
            )
            prompt_mode = select_execution_prompt_mode(
                intent_action=tool_intent_plan.action.value if tool_intent_plan is not None else "",
                is_follow_up=used_follow_up_context,
                projected_tool_count=len(available_tools),
                has_target_md_skill=bool(target_md_skill),
            )
            explicit_tool_execution_target = select_explicit_tool_execution_target(
                intent_plan=tool_intent_plan,
                is_follow_up=used_follow_up_context,
                projected_tools=available_tools,
                has_target_md_skill=bool(target_md_skill),
            )
            if isinstance(explicit_tool_execution_target, dict):
                explicit_tool_execution_target = dict(explicit_tool_execution_target)
            _log_step(
                "execution_prompt_mode_selected",
                mode="explicit_tool_execution" if explicit_tool_execution_target else prompt_mode.value,
                projected_tool_count=len(available_tools),
                used_follow_up_context=used_follow_up_context,
                explicit_tool_name=(
                    str(explicit_tool_execution_target.get("name", "") or "").strip()
                    if isinstance(explicit_tool_execution_target, dict)
                    else ""
                ),
            )
            await self.runtime_events.trigger_tool_gate_evaluated(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
            )
            await self.runtime_events.trigger_tool_matcher_resolved(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
                match_result=tool_match_result,
            )

            if tool_execution_required and not available_tools:
                failure_message = (
                    "This turn requires real tool execution, but no executable tools remained "
                    "after policy and metadata filtering."
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={"phase": "gate", "elapsed": round(time.monotonic() - start_time, 1)},
                )
                yield StreamEvent.error_event(failure_message)
                state["run_failed"] = True
                state["should_stop"] = True
                return

            if explicit_tool_execution_target is not None:
                system_prompt = build_explicit_tool_execution_prompt(
                    tool=explicit_tool_execution_target,
                )
            else:
                system_prompt = build_system_prompt(
                    self.prompt_builder,
                    session=session,
                    deps=deps,
                    agent=runtime_agent or self.agent,
                    context_window_tokens=runtime_context_window,
                    prompt_mode=prompt_mode,
                )
                consume_prompt_warnings = getattr(self.prompt_builder, "consume_warnings", None)
                if callable(consume_prompt_warnings):
                    for warning_message in consume_prompt_warnings():
                        if not self._should_surface_prompt_warning(warning_message):
                            logger.debug("Suppressing prompt-context warning: %s", warning_message)
                            continue
                        yield StreamEvent.runtime_update(
                            "warning",
                            warning_message,
                            metadata={
                                "phase": "prompt_context",
                                "elapsed": round(time.monotonic() - start_time, 1),
                            },
                        )

            if self.hooks:
                prompt_ctx = await self.hooks.trigger(
                    "before_prompt_build",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                        "system_prompt": system_prompt,
                    },
                )
                system_prompt = prompt_ctx.get("system_prompt", system_prompt)

            # at iter,.
            if self.compaction.should_memory_flush(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                await self.history.flush_history_to_timestamped_memory(
                    session_key=session_key,
                    messages=message_history,
                    deps=deps,
                    session=session,
                    context_window=runtime_context_window,
                    flushed_signatures=flushed_memory_signatures,
                )

            if message_history and self.compaction.should_compact(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                if self.hooks:
                    await self.hooks.trigger(
                        "before_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )
                yield StreamEvent.compaction_start()
                compressed_history = await self.compaction.compact(message_history, session)
                message_history = self.history.normalize_messages(compressed_history)
                message_history = await self.history.inject_memory_recall(message_history, deps)
                context_history_for_hooks = list(message_history)
                await session_manager.mark_compacted(session_key)
                compaction_applied = True
                yield StreamEvent.compaction_end()
                if self.hooks:
                    await self.hooks.trigger(
                        "after_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )

            # -- hook:before_agent_start --
            if self.hooks:
                start_ctx = await self.hooks.trigger(
                    "before_agent_start",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                    },
                )
                user_message = start_ctx.get("user_message", user_message)
            session_message_history = list(message_history)
            runtime_message_history = self._build_runtime_message_history_for_turn(
                session_message_history=session_message_history,
                used_follow_up_context=used_follow_up_context,
                intent_plan=tool_intent_plan,
            )
            runtime_base_history_len = len(runtime_message_history)
            persist_run_output_start_index = len(session_message_history)
            if runtime_base_history_len != len(session_message_history):
                _log_step(
                    "runtime_message_history_trimmed",
                    session_history_count=len(session_message_history),
                    runtime_history_count=runtime_base_history_len,
                    used_follow_up_context=used_follow_up_context,
                    action=getattr(tool_intent_plan, "action", None).value if tool_intent_plan else "",
                )
        finally:
            resolved_runtime_message_history = (
                list(runtime_message_history)
                if runtime_message_history is not None
                else list(message_history)
            )
            resolved_session_message_history = (
                list(session_message_history)
                if session_message_history is not None
                else list(message_history)
            )
            state.update({
                "session_key": session_key,
                "user_message": user_message,
                "deps": deps,
                "max_tool_calls": max_tool_calls,
                "timeout_seconds": timeout_seconds,
                "_token_failover_attempt": _token_failover_attempt,
                "_emit_lifecycle_bounds": _emit_lifecycle_bounds,
                "start_time": start_time,
                "tool_calls_count": tool_calls_count,
                "compaction_applied": compaction_applied,
                "thinking_emitter": thinking_emitter,
                "persist_override_messages": persist_override_messages,
                "persist_override_base_len": persist_override_base_len,
                "runtime_agent": runtime_agent,
                "selected_token_id": selected_token_id,
                "release_slot": release_slot,
                "flushed_memory_signatures": flushed_memory_signatures,
                "extra": extra,
                "run_id": run_id,
                "tool_execution_retry_count": tool_execution_retry_count,
                "run_failed": run_failed,
                "message_history": message_history,
                "runtime_message_history": resolved_runtime_message_history,
                "session_message_history": resolved_session_message_history,
                "runtime_base_history_len": runtime_base_history_len if runtime_base_history_len is not None else len(resolved_runtime_message_history),
                "persist_run_output_start_index": persist_run_output_start_index if persist_run_output_start_index is not None else len(message_history),
                "system_prompt": system_prompt,
                "final_assistant": final_assistant,
                "context_history_for_hooks": context_history_for_hooks,
                "tool_call_summaries": tool_call_summaries,
                "session_title": session_title,
                "buffered_assistant_events": buffered_assistant_events,
                "assistant_output_streamed": assistant_output_streamed,
                "tool_request_message": tool_request_message,
                "tool_intent_plan": tool_intent_plan,
                "tool_gate_decision": tool_gate_decision,
                "tool_match_result": tool_match_result,
                "current_model_attempt": current_model_attempt,
                "current_attempt_started_at": current_attempt_started_at,
                "current_attempt_has_text": current_attempt_has_text,
                "current_attempt_has_tool": current_attempt_has_tool,
                "reasoning_retry_count": reasoning_retry_count,
                "run_output_start_index": run_output_start_index,
                "tool_execution_required": tool_execution_required,
                "buffer_direct_answer_output": (not tool_execution_required and not bool(available_tools)),
                "reasoning_retry_limit": reasoning_retry_limit,
                "model_stream_timed_out": model_stream_timed_out,
                "model_timeout_error_message": model_timeout_error_message,
                "runtime_context_window_info": runtime_context_window_info,
                "runtime_context_guard": runtime_context_guard,
                "runtime_context_window": runtime_context_window,
                "session_manager": session_manager,
                "session": session,
                "transcript": transcript,
                "all_available_tools": all_available_tools,
                "tool_groups_snapshot": tool_groups_snapshot,
                "available_tools": available_tools,
                "candidate_visible_tools": candidate_visible_tools or list(available_tools),
                "toolset_filter_trace": toolset_filter_trace,
                "tool_projection_trace": tool_projection_trace,
                "candidate_tool_compression_trace": candidate_tool_compression_trace,
                "used_toolset_fallback": used_toolset_fallback,
                "provider_hint_docs": provider_hint_docs,
                "skill_hint_docs": skill_hint_docs,
                "tool_hint_docs": tool_hint_docs,
                "metadata_candidates": metadata_candidates,
                "ranking_trace": ranking_trace,
                "artifact_goal": artifact_goal,
                "prompt_mode": prompt_mode,
            })

    @staticmethod
    def _build_runtime_message_history_for_turn(
        *,
        session_message_history: list[dict[str, Any]],
        used_follow_up_context: bool,
        intent_plan: ToolIntentPlan | None,
    ) -> list[dict[str, Any]]:
        if not session_message_history:
            return []
        return list(session_message_history)

