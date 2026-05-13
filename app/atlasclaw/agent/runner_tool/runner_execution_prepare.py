# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
from datetime import datetime
import logging
from pathlib import Path
import re
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
    resolve_artifact_goal_from_intent_plan,
    selected_capability_ids_from_intent_plan,
)
from app.atlasclaw.agent.selected_capability import (
    SELECTED_CAPABILITY_KEY,
    get_selected_capability_from_deps,
    selected_capability_provider_instance_ref,
    selected_capability_targets,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import normalize_tool_result_mode
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    project_minimal_toolset,
    tool_is_coordination_support,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
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


def build_user_selected_tool_intent_plan(deps: SkillDeps) -> ToolIntentPlan | None:
    """Translate a validated slash capability into a runtime tool plan."""
    selected = get_selected_capability_from_deps(deps)
    if not selected:
        return None

    targets = selected_capability_targets(selected)
    if not targets.has_any():
        return None

    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=targets.provider_types,
        target_skill_names=targets.skill_names,
        target_group_ids=targets.group_ids,
        target_tool_names=targets.tool_names,
        reason="user_selected_capability",
    )


def build_preselected_md_skill_intent_plan(deps: SkillDeps) -> ToolIntentPlan | None:
    """Translate a request-scoped target markdown skill into a hard skill plan."""
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict):
        return None

    target_md_skill = extra.get("target_md_skill")
    if not isinstance(target_md_skill, dict):
        return None

    # Webhook dispatch stores a validated provider-qualified skill here. Require
    # the canonical name so this hard plan has no legacy or display-name fallback.
    qualified_name = _normalize_text(target_md_skill.get("qualified_name"))
    if not qualified_name:
        return None

    provider_type = _normalize_text(target_md_skill.get("provider"))
    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=[provider_type] if provider_type else [],
        target_skill_names=[qualified_name],
        target_group_ids=[f"group:{provider_type}"] if provider_type else [],
        reason="preselected_target_md_skill",
    )


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


def build_transcript_skill_prompt_intent_plan(
    *,
    active_skill: Optional[str],
) -> ToolIntentPlan | None:
    """Build a non-forcing skill continuation plan from transcript tool evidence.

    The plan carries the active Markdown skill into low-information follow-up
    turns without requiring a tool call. The main model still decides whether
    the next step is a tool call, a preview, or another clarification.
    """
    normalized = _normalize_text(active_skill)
    if not normalized:
        return None
    return ToolIntentPlan(
        action=ToolIntentAction.DIRECT_ANSWER,
        target_skill_names=[normalized],
        reason="transcript_skill_continuation_prompt_only",
    )


def toolset_has_only_coordination_support_tools(tools: list[dict[str, Any]]) -> bool:
    """Return whether the projected set contains only non-executing support tools."""
    normalized_tools = [
        tool
        for tool in (tools or [])
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    ]
    if not normalized_tools:
        return False
    for tool in normalized_tools:
        group = str(tool.get("group", "") or "").strip()
        capability_class = str(tool.get("capability_class", "") or "").strip()
        if group == "skill_runtime" or capability_class.startswith("skill_runtime:"):
            return False
    return all(tool_is_coordination_support(tool) for tool in normalized_tools)


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
            "If you call this tool, continue directly to the next user-facing question or confirmation afterward.\n"
            "If this tool is an internal lookup step, do not stop at the lookup result.\n"
            "Phrase that next user-facing step naturally instead of narrating the lookup itself.\n"
            "Use only the resolved facts from the lookup. Never quote scaffolding phrases such as "
            "'Found N ...', replay numbered raw dumps, or surface raw JSON or unlabeled UUID/ID dumps.\n"
            "Do not call the same tool again with the same arguments after its result is available.\n"
            "Do not mention the tool call to the user and do not surface its raw output.\n"
        )
    return prompt


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(values: Any) -> list[str]:
    if isinstance(values, list):
        raw_values = values
    elif values in (None, "", (), {}):
        raw_values = []
    else:
        raw_values = [values]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = _normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _extract_target_md_skill_scope_hints(target_md_skill: Any) -> dict[str, Any]:
    if not isinstance(target_md_skill, dict):
        return {
            "qualified_name": "",
            "provider": "",
            "use_when": [],
            "avoid_when": [],
        }

    metadata = target_md_skill.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "qualified_name": _normalize_text(
            target_md_skill.get("qualified_name") or target_md_skill.get("name")
        ),
        "provider": _normalize_text(target_md_skill.get("provider")),
        "use_when": _normalize_text_list(
            target_md_skill.get("use_when", []) or metadata.get("use_when", [])
        ),
        "avoid_when": _normalize_text_list(
            target_md_skill.get("avoid_when", []) or metadata.get("avoid_when", [])
        ),
    }


def _build_md_skill_tool_index(
    *,
    md_skills_snapshot: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Build a qualified skill -> declared tool names index."""
    skill_tool_index: dict[str, set[str]] = {}
    for skill in md_skills_snapshot:
        if not isinstance(skill, dict):
            continue
        qname = str(
            skill.get("qualified_name") or skill.get("name") or ""
        ).strip().lower()
        if not qname:
            continue
        metadata = skill.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        declared: set[str] = set()
        for key, value in metadata.items():
            key_str = str(key)
            if key_str.startswith("tool_") and key_str.endswith("_name"):
                tool_name = str(value or "").strip().lower()
                if tool_name:
                    declared.add(tool_name)
        single = str(metadata.get("tool_name", "")).strip().lower()
        if single:
            declared.add(single)
        for raw_list_key in ("declared_tool_names", "tool_names"):
            for item in (metadata.get(raw_list_key) or skill.get(raw_list_key) or []):
                tool_name = str(item).strip().lower()
                if tool_name:
                    declared.add(tool_name)
        if declared:
            skill_tool_index[qname] = declared
    return skill_tool_index


def _infer_active_skill_from_transcript(
    *,
    message_history: list[dict[str, Any]],
    md_skills_snapshot: list[dict[str, Any]],
    max_scan: int = 20,
) -> Optional[str]:
    """Scan recent transcript tool calls to infer the currently active md skill.

    Returns the qualified_name of the md_skill whose declared tools appear
    most recently in the conversation.  This is used ONLY for SKILL.md
    documentation loading during follow-up turns — it does NOT affect routing
    or tool visibility.
    """
    if not message_history or not md_skills_snapshot:
        return None

    def _append_tool_name(raw_name: Any) -> None:
        tool_name = str(raw_name or "").strip().lower()
        if tool_name and tool_name not in recent_tool_names:
            recent_tool_names.append(tool_name)

    # Collect recent tool names from transcript (newest first). Different
    # persistence and model adapters store tool evidence in different shapes,
    # so treat assistant tool calls, standalone tool messages, and embedded
    # tool_results/tool_call_summaries as equivalent transcript evidence.
    recent_tool_names: list[str] = []
    for msg in reversed(message_history[-max_scan:]):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "").strip().lower()
        if role in {"tool", "toolresult", "tool_result"}:
            _append_tool_name(msg.get("tool_name", "") or msg.get("name", ""))
        for tool_result in reversed(msg.get("tool_results", []) or []):
            if not isinstance(tool_result, dict):
                continue
            _append_tool_name(tool_result.get("tool_name", "") or tool_result.get("name", ""))
        for tool_call in reversed(msg.get("tool_calls", []) or []):
            if not isinstance(tool_call, dict):
                continue
            _append_tool_name(tool_call.get("name", "") or tool_call.get("tool_name", ""))
        for summary in reversed(msg.get("tool_call_summaries", []) or []):
            if not isinstance(summary, dict):
                continue
            _append_tool_name(summary.get("name", "") or summary.get("tool_name", ""))

    if not recent_tool_names:
        return None

    skill_tool_index = _build_md_skill_tool_index(md_skills_snapshot=md_skills_snapshot)
    if not skill_tool_index:
        return None

    # Pick the skill whose declared tools match the most-recent transcript tool
    for tool_name in recent_tool_names:
        for qname, declared_tools in skill_tool_index.items():
            if tool_name in declared_tools:
                return qname

    return None


def _intent_plan_has_explicit_targets(intent_plan: ToolIntentPlan | None) -> bool:
    if intent_plan is None:
        return False
    return any(
        [
            list(intent_plan.target_provider_types or []),
            list(intent_plan.target_skill_names or []),
            list(intent_plan.target_group_ids or []),
            list(intent_plan.target_capability_classes or []),
            list(intent_plan.target_tool_names or []),
        ]
    )


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
) -> tuple[int, int, int, int, int, int]:
    capability_id = _normalize_text(entry.get("capability_id", "")).lower()
    name = _normalize_text(entry.get("name", "")).lower()
    declared_tool_names = [
        _normalize_text(item).lower()
        for item in (entry.get("declared_tool_names", []) or [])
        if _normalize_text(item)
    ]
    artifact_classes = _artifact_classes_for_entry(entry)

    capability_rank = 0 if capability_id and capability_id in selected_capability_ids else 1
    standard_skill_rank = 1 if bool(entry.get("declares_executable_tools")) else 0
    skill_rank = target_skill_order.get(name, len(target_skill_order) + 1)
    tool_rank = min(
        (target_tool_order.get(item, len(target_tool_order) + 1) for item in declared_tool_names),
        default=len(target_tool_order) + 1,
    )
    artifact_rank = 0 if artifact_classes and artifact_classes.intersection(target_capability_classes) else 1
    return (
        capability_rank,
        standard_skill_rank,
        skill_rank,
        tool_rank,
        artifact_rank,
        original_index,
    )


def _load_target_md_skill_full_instructions(
    *,
    file_path: str,
) -> str:
    """Load selected SKILL.md instructions for execution-stage prompting."""
    normalized_path = _normalize_text(file_path)
    if not normalized_path:
        return ""

    try:
        text = Path(normalized_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    text = re.sub(r"^---\s.*?---\s*", "", text, count=1, flags=re.DOTALL).strip()
    return text


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
    provider = _normalize_text(selected_entry.get("provider_type", ""))
    instructions = _load_target_md_skill_full_instructions(
        file_path=file_path,
    )
    return {
        "provider": provider,
        "qualified_name": _normalize_text(selected_entry.get("name", "")),
        "description": _normalize_text(selected_entry.get("description", "")),
        "artifact_types": list(selected_entry.get("artifact_types", []) or []),
        "file_path": file_path,
        "instructions": instructions,
    }


def enrich_target_md_skill_with_workflow_context(
    *,
    target_md_skill: Optional[dict[str, Any]],
    workflow_trace: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Attach current-turn workflow context to the selected markdown skill prompt."""
    if not isinstance(target_md_skill, dict):
        return target_md_skill
    enriched = dict(target_md_skill)
    if isinstance(workflow_trace, dict) and workflow_trace:
        enriched["workflow_context"] = dict(workflow_trace)
    return enriched


def _parse_target_md_skill_workflow_metadata(value: Any) -> Any:
    """Normalize runtime-only metadata into a compact prompt-safe structure."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return text
    return str(value)


def _infer_active_request_trace_id(
    recent_history: list[dict[str, Any]],
) -> Optional[str]:
    """Infer the active internal_request_trace_id from recent tool metadata.

    Scans message history in reverse to find the most recent tool result
    that carries an internal_request_trace_id in its _internal metadata.
    Returns the trace ID string or None if not found.
    """
    if not isinstance(recent_history, list):
        return None
    for message in reversed(recent_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").strip().lower() != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            continue
        internal = content.get("_internal")
        if internal is None:
            continue
        # _internal may be a JSON string or a dict/list
        if isinstance(internal, str):
            try:
                internal = json.loads(internal)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        # Could be a list of entries or a single dict
        if isinstance(internal, list):
            for item in reversed(internal):
                if isinstance(item, dict):
                    trace_id = item.get("internal_request_trace_id")
                    if isinstance(trace_id, str) and trace_id.strip():
                        return trace_id.strip()
        elif isinstance(internal, dict):
            trace_id = internal.get("internal_request_trace_id")
            if isinstance(trace_id, str) and trace_id.strip():
                return trace_id.strip()
    return None


def _extract_trace_id_from_metadata(metadata: Any) -> Optional[str]:
    """Extract internal_request_trace_id from a parsed metadata value."""
    if isinstance(metadata, dict):
        trace_id = metadata.get("internal_request_trace_id")
        if isinstance(trace_id, str) and trace_id.strip():
            return trace_id.strip()
    elif isinstance(metadata, list):
        for item in metadata:
            if isinstance(item, dict):
                trace_id = item.get("internal_request_trace_id")
                if isinstance(trace_id, str) and trace_id.strip():
                    return trace_id.strip()
    return None


def _extract_workflow_candidate_items_from_metadata(
    metadata: Any,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Return the candidate container key and candidate items when metadata is a single list payload."""
    if isinstance(metadata, list) and all(isinstance(item, dict) for item in metadata):
        return "__root__", [dict(item) for item in metadata]
    if not isinstance(metadata, dict):
        return None, []

    list_keys = [
        key
        for key, value in metadata.items()
        if isinstance(value, list) and all(isinstance(item, dict) for item in value)
    ]
    if len(list_keys) != 1:
        return None, []
    key = list_keys[0]
    return key, [dict(item) for item in metadata.get(key, [])]


def _workflow_candidate_selection_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("id", "entityId", "key", "code"):
        value = str(item.get(key) or "").strip()
        if value:
            tokens.add(value)
    return tokens


def _normalize_workflow_candidate_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def _workflow_candidate_mention_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("name", "nameZh", "label", "title", "displayName", "display_name"):
        token = _normalize_workflow_candidate_text(item.get(key))
        if len(token) >= 2 and not token.isdigit():
            tokens.add(token)
    return tokens


def _collect_explicit_selection_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, dict):
        for nested in value.values():
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, list):
        for nested in value:
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return tokens
        if text[:1] in {"{", "["}:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                tokens.update(_collect_explicit_selection_tokens(parsed))
                return tokens
        tokens.add(text)
        return tokens
    if isinstance(value, (int, float)):
        tokens.add(str(value))
        return tokens
    return tokens


def _narrow_target_md_skill_workflow_metadata(
    metadata: Any,
    *,
    following_messages: list[dict[str, Any]],
) -> Any:
    """Narrow candidate-list metadata to the explicitly selected item for active workflow context."""
    if not following_messages:
        return metadata

    container_key, candidates = _extract_workflow_candidate_items_from_metadata(metadata)
    if not container_key or len(candidates) <= 1:
        return metadata

    candidate_lookup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for token in _workflow_candidate_selection_tokens(candidate):
            candidate_lookup.setdefault(token, candidate)
    if not candidate_lookup:
        return metadata

    matched: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    mention_candidates = [
        (candidate, _workflow_candidate_mention_tokens(candidate)) for candidate in candidates
    ]
    for message in following_messages:
        if str(message.get("role", "")).strip().lower() != "assistant":
            continue
        for call in message.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            args = call.get("args", call.get("arguments"))
            for token in _collect_explicit_selection_tokens(args):
                candidate = candidate_lookup.get(token)
                if not candidate:
                    continue
                signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                matched.append(dict(candidate))
        normalized_content = _normalize_workflow_candidate_text(message.get("content"))
        if not normalized_content:
            continue
        for candidate, mention_tokens in mention_candidates:
            if not mention_tokens or not any(token in normalized_content for token in mention_tokens):
                continue
            signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            matched.append(dict(candidate))

    if len(matched) != 1:
        return metadata
    if container_key == "__root__":
        return matched
    if not isinstance(metadata, dict):
        return metadata

    narrowed = dict(metadata)
    narrowed[container_key] = matched
    return narrowed


def _collect_same_flow_following_messages(
    *,
    recent_history: list[dict[str, Any]],
    start_index: int,
    entry_trace_id: Optional[str],
) -> list[dict[str, Any]]:
    if not isinstance(recent_history, list):
        return []
    following_messages: list[dict[str, Any]] = []
    for message in recent_history[start_index + 1 :]:
        if entry_trace_id and isinstance(message, dict):
            if str(message.get("role", "") or "").strip().lower() == "tool":
                content = message.get("content")
                if isinstance(content, dict) and "_internal" in content:
                    parsed_metadata = _parse_target_md_skill_workflow_metadata(content.get("_internal"))
                    following_trace_id = _extract_trace_id_from_metadata(parsed_metadata)
                    if following_trace_id and following_trace_id != entry_trace_id:
                        break
        following_messages.append(message)
    return following_messages


def build_target_md_skill_workflow_context(
    *,
    recent_history: list[dict[str, Any]],
    active_trace_id: Optional[str] = None,
    max_entries: int = 6,
    max_chars: int = 12000,
) -> Optional[dict[str, Any]]:
    """Collect recent tool metadata for the current selected markdown skill only.

    When an active_trace_id is provided (or inferred from recent history),
    only metadata entries belonging to the same trace are collected.  This
    ensures that multiple request flow instances within the same session do
    not cross-contaminate each other's workflow context.

    If no trace ID is available (legacy providers), falls back to collecting
    all recent _internal metadata (backward compatible).
    """
    if not isinstance(recent_history, list) or not recent_history:
        return None

    # Determine the active trace ID
    resolved_trace_id: Optional[str] = None
    if isinstance(active_trace_id, str) and active_trace_id.strip():
        resolved_trace_id = active_trace_id.strip()
    else:
        resolved_trace_id = _infer_active_request_trace_id(recent_history)

    safe_max_entries = max(1, int(max_entries or 0))
    safe_max_chars = max(512, int(max_chars or 0))
    same_trace_metadata: list[dict[str, Any]] = []
    same_trace_size = 0
    legacy_metadata: list[dict[str, Any]] = []
    legacy_size = 0

    for message_index in range(len(recent_history) - 1, -1, -1):
        message = recent_history[message_index]
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").strip().lower() != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            continue
        if "_internal" not in content:
            continue

        metadata = _parse_target_md_skill_workflow_metadata(content.get("_internal"))
        if metadata is None:
            continue

        # Filter by trace ID if one is active
        if resolved_trace_id:
            entry_trace_id = _extract_trace_id_from_metadata(metadata)
            if entry_trace_id and entry_trace_id != resolved_trace_id:
                # Belongs to a different request flow instance — skip
                continue

        entry_trace_id = _extract_trace_id_from_metadata(metadata)
        metadata = _narrow_target_md_skill_workflow_metadata(
            metadata,
            following_messages=_collect_same_flow_following_messages(
                recent_history=recent_history,
                start_index=message_index,
                entry_trace_id=entry_trace_id,
            ),
        )

        entry = {
            "tool_name": str(message.get("tool_name", "") or message.get("name", "")).strip(),
            "metadata": metadata,
        }
        serialized_entry = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        if len(serialized_entry) > safe_max_chars:
            continue
        if resolved_trace_id:
            entry_trace_id = _extract_trace_id_from_metadata(metadata)
            if entry_trace_id == resolved_trace_id:
                if same_trace_metadata and same_trace_size + len(serialized_entry) > safe_max_chars:
                    break
                same_trace_metadata.append(entry)
                same_trace_size += len(serialized_entry)
                if len(same_trace_metadata) >= safe_max_entries:
                    break
                continue
            if entry_trace_id:
                continue
            if legacy_metadata and legacy_size + len(serialized_entry) > safe_max_chars:
                continue
            if len(legacy_metadata) >= safe_max_entries:
                continue
            legacy_metadata.append(entry)
            legacy_size += len(serialized_entry)
            continue
        if legacy_metadata and legacy_size + len(serialized_entry) > safe_max_chars:
            break
        legacy_metadata.append(entry)
        legacy_size += len(serialized_entry)
        if len(legacy_metadata) >= safe_max_entries:
            break

    recent_tool_metadata = same_trace_metadata if same_trace_metadata else legacy_metadata
    if not recent_tool_metadata:
        return None

    recent_tool_metadata.reverse()
    result: dict[str, Any] = {"recent_tool_metadata": recent_tool_metadata}
    if resolved_trace_id:
        result["internal_request_trace_id"] = resolved_trace_id
    return result


def prune_auto_selected_provider_instance_tools(
    *,
    available_tools: list[dict[str, Any]],
    deps: Optional[SkillDeps],
    intent_plan: ToolIntentPlan | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove provider-selector tools once the provider instance is fixed."""
    trace: dict[str, Any] = {
        "enabled": False,
        "removed_tools": [],
        "target_provider_types": [],
        "auto_selected_provider_types": [],
        "explicit_selected_provider_types": [],
        "explicit_selected_instances": [],
    }
    if not available_tools:
        return list(available_tools or []), trace
    if deps is None or not isinstance(getattr(deps, "extra", None), dict):
        return list(available_tools), trace

    extra = deps.extra
    provider_instances = extra.get("provider_instances")
    if not isinstance(provider_instances, dict) or not provider_instances:
        return list(available_tools), trace

    explicit_selected_provider_types: list[str] = []
    explicit_selected_instances: list[str] = []
    selected_capability = extra.get(SELECTED_CAPABILITY_KEY)
    if isinstance(selected_capability, dict):
        selected_provider_type, selected_instance_name = selected_capability_provider_instance_ref(
            selected_capability
        )
        selected_provider_type = selected_provider_type.lower()
        if selected_provider_type and selected_instance_name:
            explicit_selected_provider_types.append(selected_provider_type)
            explicit_selected_instances.append(selected_instance_name)

    target_provider_types: list[str] = []
    if intent_plan is not None:
        for item in (intent_plan.target_provider_types or []):
            provider_type = str(item or "").strip().lower()
            if provider_type and provider_type not in target_provider_types:
                target_provider_types.append(provider_type)

    for provider_type in explicit_selected_provider_types:
        if provider_type not in target_provider_types:
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
    prune_provider_types = set(auto_selected_provider_types)
    prune_provider_types.update(explicit_selected_provider_types)
    if not prune_provider_types:
        return list(available_tools), trace

    filtered_tools: list[dict[str, Any]] = []
    removed_tools: list[str] = []
    for tool in available_tools:
        if not isinstance(tool, dict):
            continue
        normalized_group_ids = {
            str(group_id or "").strip().lower()
            for group_id in (tool.get("group_ids", []) or [])
            if str(group_id or "").strip()
        }
        capability_class = str(tool.get("capability_class", "") or "").strip().lower()
        is_provider_selector = bool(tool.get("coordination_only")) and (
            "group:providers" in normalized_group_ids or capability_class == "provider:generic"
        )
        tool_name = str(tool.get("name", "") or "").strip()
        if is_provider_selector:
            removed_tools.append(tool_name or "<unnamed>")
            continue
        filtered_tools.append(dict(tool))

    trace.update(
        {
            "enabled": bool(removed_tools),
            "removed_tools": removed_tools,
            "target_provider_types": list(target_provider_types),
            "auto_selected_provider_types": auto_selected_provider_types,
            "explicit_selected_provider_types": explicit_selected_provider_types,
            "explicit_selected_instances": explicit_selected_instances,
        }
    )
    if not removed_tools:
        return list(available_tools), trace
    return filtered_tools, trace


def _target_md_skill_declares_tools(
    *,
    target_md_skill: dict[str, Any],
    md_skills_snapshot: list[dict[str, Any]],
) -> bool:
    """Return whether the selected markdown skill already registers executable tools."""
    qname = _normalize_text(target_md_skill.get("qualified_name")).lower()
    if not qname:
        return False
    for skill in md_skills_snapshot or []:
        if not isinstance(skill, dict):
            continue
        skill_qname = _normalize_text(
            skill.get("qualified_name") or skill.get("name")
        ).lower()
        if skill_qname != qname:
            continue
        metadata = skill.get("metadata")
        if not isinstance(metadata, dict):
            return False
        if _normalize_text(metadata.get("tool_name")) and _normalize_text(
            metadata.get("entrypoint")
        ):
            return True
        ids: set[str] = set()
        for key in metadata.keys():
            key_text = str(key or "")
            if key_text.startswith("tool_") and key_text.endswith("_name"):
                ids.add(key_text[len("tool_") : -len("_name")])
            elif key_text.startswith("tool_") and key_text.endswith("_entrypoint"):
                ids.add(key_text[len("tool_") : -len("_entrypoint")])
        for tool_id in ids:
            if _normalize_text(metadata.get(f"tool_{tool_id}_name")) and _normalize_text(
                metadata.get(f"tool_{tool_id}_entrypoint")
            ):
                return True
        return False
    return False


def _target_md_skill_is_provider_bound(
    *,
    target_md_skill: dict[str, Any],
    md_skills_snapshot: list[dict[str, Any]],
) -> bool:
    """Return whether the selected markdown skill depends on a provider instance."""
    provider = _normalize_text(target_md_skill.get("provider"))
    if provider:
        return True
    qname = _normalize_text(target_md_skill.get("qualified_name")).lower()
    if not qname:
        return False
    for skill in md_skills_snapshot or []:
        if not isinstance(skill, dict):
            continue
        skill_qname = _normalize_text(
            skill.get("qualified_name") or skill.get("name")
        ).lower()
        if skill_qname != qname:
            continue
        metadata = skill.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return bool(
            _normalize_text(metadata.get("provider_type"))
            or _normalize_text(skill.get("provider"))
        )
    return False


def inject_standard_skill_runtime_tools(
    *,
    available_tools: list[dict[str, Any]],
    deps: SkillDeps,
    target_md_skill: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Append internal runtime helpers for docs-only standard markdown skills.

    Provider-bound skills and skills that declare AtlasClaw executable metadata
    use their own registered tools. The internal runtime is reserved for
    selected standard markdown skills that otherwise only provide instructions.
    """
    trace: dict[str, Any] = {
        "enabled": False,
        "reason": "no_target_md_skill",
        "tool_names": [],
    }
    if not isinstance(target_md_skill, dict):
        if isinstance(getattr(deps, "extra", None), dict):
            deps.extra.pop("standard_skill_runtime_enabled", None)
            deps.extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    extra = deps.extra if isinstance(getattr(deps, "extra", None), dict) else {}
    md_skills_snapshot = extra.get("md_skills_snapshot")
    if not isinstance(md_skills_snapshot, list):
        md_skills_snapshot = []
    if _target_md_skill_is_provider_bound(
        target_md_skill=target_md_skill,
        md_skills_snapshot=md_skills_snapshot,
    ):
        trace["reason"] = "target_skill_provider_bound"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill
    if _target_md_skill_declares_tools(
        target_md_skill=target_md_skill,
        md_skills_snapshot=md_skills_snapshot,
    ):
        trace["reason"] = "target_skill_has_executable_tool"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    runtime_tools = extra.get("internal_runtime_tools_snapshot")
    if not isinstance(runtime_tools, list) or not runtime_tools:
        trace["reason"] = "internal_runtime_tools_unavailable"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    existing = {
        str(tool.get("name", "") or "").strip()
        for tool in (available_tools or [])
        if isinstance(tool, dict)
    }
    result = list(available_tools or [])
    added: list[str] = []
    for tool in runtime_tools:
        if not isinstance(tool, dict):
            continue
        tool_name = _normalize_text(tool.get("name"))
        if not tool_name or tool_name in existing:
            continue
        result.append(dict(tool))
        existing.add(tool_name)
        added.append(tool_name)

    if not added:
        trace["reason"] = "internal_runtime_tools_already_present"
        return result, trace, target_md_skill

    enriched_target = dict(target_md_skill)
    enriched_target["standard_runtime_enabled"] = True
    enriched_target["standard_runtime_tool_names"] = list(added)
    extra["standard_skill_runtime_enabled"] = True
    extra["standard_skill_runtime_tools_visible"] = True
    extra["target_md_skill"] = dict(enriched_target)
    trace.update(
        {
            "enabled": True,
            "reason": "target_standard_skill_selected",
            "tool_names": list(added),
        }
    )
    return result, trace, enriched_target




class RunnerExecutionPreparePhaseMixin:
    async def _should_keep_preselected_md_skill_plan(
        self,
        *,
        agent: Any,
        deps: SkillDeps,
        user_message: str,
        target_md_skill: dict[str, Any],
    ) -> tuple[bool, str]:
        """Check whether a preselected markdown skill remains in scope for this turn.

        This guard is intentionally narrow: it only prevents hard-forcing a
        preselected skill when the skill's own routing hints clearly say the
        current request is out of scope. When uncertain, keep the preselection.
        """
        scope_hints = _extract_target_md_skill_scope_hints(target_md_skill)
        use_when = list(scope_hints.get("use_when", []) or [])
        avoid_when = list(scope_hints.get("avoid_when", []) or [])
        if not use_when and not avoid_when:
            return True, "no_scope_hints"

        runner = getattr(self, "_run_single_with_optional_override", None)
        if not callable(runner):
            return True, "scope_guard_unavailable"

        system_prompt = (
            "You are AtlasClaw's internal preselected markdown-skill scope guard.\n"
            "Do not answer the user and do not call tools. Return one JSON object only.\n\n"
            "Task:\n"
            "Decide whether an authenticated preselected markdown skill should remain hard-selected "
            "for the current request.\n\n"
            "Rules:\n"
            "- Use only the provided skill routing hints and the current user request.\n"
            "- If the request clearly matches an avoid_when boundary or clearly falls outside the "
            "skill boundary, return allow=false.\n"
            "- If the request matches the skill boundary or you are uncertain, return allow=true.\n"
            "- This guard only decides whether to keep the hard preselection. When allow=false, "
            "normal routing will decide the turn.\n\n"
            "Return JSON fields exactly:\n"
            "{\n"
            '  "allow": true | false,\n'
            '  "reason": string\n'
            "}\n"
        )
        selector_message = (
            f"Preselected skill: {scope_hints.get('qualified_name', '') or 'unknown'}\n"
            f"Provider: {scope_hints.get('provider', '') or '-'}\n"
            f"Use when:\n- " + ("\n- ".join(use_when) if use_when else "none") + "\n"
            f"Avoid when:\n- " + ("\n- ".join(avoid_when) if avoid_when else "none") + "\n\n"
            f"Current user request:\n{user_message}"
        )
        try:
            raw_output = await runner(
                agent=agent,
                user_message=selector_message,
                deps=deps,
                system_prompt=system_prompt,
                purpose="preselected_md_skill_scope_guard",
                allowed_tool_names=[],
            )
        except Exception:
            logger.warning("preselected_md_skill_scope_guard_failed", exc_info=True)
            return True, "scope_guard_failed"

        parsed = self._extract_json_object(raw_output)
        if not parsed:
            return True, "scope_guard_unparsed"
        try:
            payload = json.loads(parsed)
        except Exception:
            return True, "scope_guard_invalid_json"
        if not isinstance(payload, dict):
            return True, "scope_guard_invalid_payload"

        allow = payload.get("allow")
        if not isinstance(allow, bool):
            return True, "scope_guard_missing_allow"
        reason = _normalize_text(payload.get("reason")) or "preselected_md_skill_scope_guard"
        return allow, reason

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
        toolset_filter_trace = state.get("toolset_filter_trace")
        tool_projection_trace = state.get("tool_projection_trace")
        used_toolset_fallback = state.get("used_toolset_fallback")
        metadata_candidates = state.get("metadata_candidates")
        ranking_trace = state.get("ranking_trace")
        artifact_goal = state.get("artifact_goal")
        runtime_message_history = state.get("runtime_message_history")
        session_message_history = state.get("session_message_history")
        runtime_base_history_len = state.get("runtime_base_history_len")
        persist_run_output_start_index = state.get("persist_run_output_start_index")
        prompt_mode = state.get("prompt_mode") or ""
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
            # Apply skill permission filtering: remove handler tools whose skill
            # is disabled in the user's role.  Two filtering strategies:
            # 1. _disabled_tool_names: exact tool name match (from md_skill metadata)
            # 2. _disabled_skill_ids: match by skill_name/qualified_skill_name field
            _extra = deps.extra or {}
            _disabled_tools = _extra.get("_disabled_tool_names")
            _disabled_sids = _extra.get("_disabled_skill_ids")
            if (isinstance(_disabled_tools, set) and _disabled_tools) or (
                isinstance(_disabled_sids, set) and _disabled_sids
            ):
                _dt = _disabled_tools if isinstance(_disabled_tools, set) else set()
                _ds = _disabled_sids if isinstance(_disabled_sids, set) else set()
                def _tool_allowed(t: dict) -> bool:
                    tname = str(t.get("name", "") or "").strip()
                    if tname and tname in _dt:
                        return False
                    if _ds:
                        sname = str(t.get("skill_name", "") or "").strip().lower()
                        qsname = str(t.get("qualified_skill_name", "") or "").strip().lower()
                        bare_sname = sname.split(":")[-1] if sname else ""
                        bare_qsname = qsname.split(":")[-1] if qsname else ""
                        if (sname and (sname in _ds or bare_sname in _ds)) or (
                            qsname and (qsname in _ds or bare_qsname in _ds)
                        ):
                            return False
                    return True
                all_available_tools = [t for t in all_available_tools if _tool_allowed(t)]
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
            tool_request_message, used_follow_up_context = self._resolve_contextual_tool_request(
                user_message=user_message,
                recent_history=message_history,
            )
            if isinstance(deps.extra, dict):
                if used_follow_up_context and tool_request_message != user_message:
                    deps.extra["current_follow_up_context"] = tool_request_message
                else:
                    deps.extra.pop("current_follow_up_context", None)
            model_user_message = (
                tool_request_message
                if used_follow_up_context and tool_request_message != user_message
                else user_message
            )
            _log_step(
                "tool_request_resolved",
                used_follow_up_context=used_follow_up_context,
                raw_user_message=user_message,
                resolved_tool_request=tool_request_message,
            )
            target_md_skill_workflow_context = build_target_md_skill_workflow_context(
                recent_history=message_history,
            )
            transcript_active_skill = None
            md_skills_snapshot = (
                list(deps.extra.get("md_skills_snapshot") or [])
                if isinstance(deps.extra, dict)
                else []
            )
            if used_follow_up_context:
                transcript_active_skill = _infer_active_skill_from_transcript(
                    message_history=message_history,
                    md_skills_snapshot=md_skills_snapshot,
                )
                if transcript_active_skill:
                    if isinstance(deps.extra, dict):
                        deps.extra["transcript_skill_continuation_hint"] = (
                            transcript_active_skill
                        )
                    _log_step(
                        "transcript_skill_continuation_hint_computed",
                        reason="transcript_tool_calls_suggest_active_skill",
                        hint_skill=transcript_active_skill,
                    )
            selected_tool_intent_plan = build_user_selected_tool_intent_plan(deps)
            capability_selector_intent_plan: ToolIntentPlan | None = None
            if selected_tool_intent_plan is not None:
                metadata_candidates = {
                    "reason": "user_selected_capability",
                    "confidence": 1.0,
                    "preferred_provider_types": list(selected_tool_intent_plan.target_provider_types),
                    "preferred_group_ids": list(selected_tool_intent_plan.target_group_ids),
                    "preferred_capability_classes": list(
                        selected_tool_intent_plan.target_capability_classes
                    ),
                    "preferred_tool_names": list(selected_tool_intent_plan.target_tool_names),
                    "preferred_skill_names": list(selected_tool_intent_plan.target_skill_names),
                }
                _log_step(
                    "user_selected_capability_applied",
                    target_provider_types=list(selected_tool_intent_plan.target_provider_types),
                    target_skill_names=list(selected_tool_intent_plan.target_skill_names),
                    target_tool_names=list(selected_tool_intent_plan.target_tool_names),
                )
            else:
                capability_index = collect_capability_index_snapshot(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                )
                capability_selector_intent_plan = await self._select_capability_intent_plan_with_model(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                    user_message=tool_request_message,
                    recent_history=message_history,
                    capability_index=capability_index,
                )
                metadata_candidates = {
                    "reason": "llm_capability_selector",
                    "confidence": 1.0 if capability_selector_intent_plan is not None else 0.0,
                    "preferred_provider_types": (
                        list(capability_selector_intent_plan.target_provider_types)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_group_ids": (
                        list(capability_selector_intent_plan.target_group_ids)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_capability_classes": (
                        list(capability_selector_intent_plan.target_capability_classes)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_tool_names": (
                        list(capability_selector_intent_plan.target_tool_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_skill_names": (
                        list(capability_selector_intent_plan.target_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                }
                _log_step(
                    "capability_selector_resolved",
                    enabled=capability_selector_intent_plan is not None,
                    action=(
                        capability_selector_intent_plan.action.value
                        if capability_selector_intent_plan is not None
                        else ""
                    ),
                    target_provider_types=(
                        list(capability_selector_intent_plan.target_provider_types)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_skill_names=(
                        list(capability_selector_intent_plan.target_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_tool_names=(
                        list(capability_selector_intent_plan.target_tool_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                )
            ranking_trace = {
                "status": "capability_selector",
                "reason": str(metadata_candidates.get("reason", "") or "capability_selector"),
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
                "capability_selector_recorded",
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
            if selected_tool_intent_plan is not None:
                metadata_tool_intent_plan = selected_tool_intent_plan
            else:
                metadata_tool_intent_plan = capability_selector_intent_plan
            transcript_skill_intent_plan = None
            if (
                selected_tool_intent_plan is None
                and used_follow_up_context
                and not _intent_plan_has_explicit_targets(metadata_tool_intent_plan)
            ):
                transcript_skill_intent_plan = build_transcript_skill_prompt_intent_plan(
                    active_skill=transcript_active_skill,
                )
                if transcript_skill_intent_plan is not None:
                    metadata_tool_intent_plan = transcript_skill_intent_plan
                    _log_step(
                        "transcript_skill_continuation_plan_applied",
                        reason=transcript_skill_intent_plan.reason,
                        target_skill_names=list(transcript_skill_intent_plan.target_skill_names),
                    )
            if metadata_tool_intent_plan is not None:
                _log_step(
                    "capability_selector_plan_resolved",
                    action=metadata_tool_intent_plan.action.value,
                    target_provider_types=list(metadata_tool_intent_plan.target_provider_types),
                    target_skill_names=list(metadata_tool_intent_plan.target_skill_names),
                    target_capability_classes=list(metadata_tool_intent_plan.target_capability_classes),
                    target_tool_names=list(metadata_tool_intent_plan.target_tool_names),
                )
            if selected_tool_intent_plan is not None:
                explicit_capability_match = True
                tool_intent_plan = selected_tool_intent_plan
            else:
                tool_intent_plan = metadata_tool_intent_plan
                explicit_capability_match = bool(
                    tool_intent_plan is not None
                    and any(
                        [
                            list(tool_intent_plan.target_provider_types or []),
                            list(tool_intent_plan.target_skill_names or []),
                            list(tool_intent_plan.target_group_ids or []),
                            list(tool_intent_plan.target_capability_classes or []),
                            list(tool_intent_plan.target_tool_names or []),
                        ]
                    )
                )
            artifact_goal = resolve_artifact_goal_from_intent_plan(tool_intent_plan)
            if isinstance(deps.extra, dict):
                if artifact_goal is not None:
                    deps.extra["artifact_goal"] = dict(artifact_goal)
                else:
                    deps.extra.pop("artifact_goal", None)
            _log_step(
                "artifact_goal_resolved",
                artifact_kind=str((artifact_goal or {}).get("kind", "") or ""),
                artifact_label=str((artifact_goal or {}).get("label", "") or ""),
                source="runtime_intent_plan",
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
            if not explicit_capability_match:
                _log_step(
                    "unmatched_intent_tools_preserved",
                    reason="capability_selector_returned_no_target_preserve_authorized_tools",
                    available_tool_count=len(available_tools),
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
            available_tools, tool_projection_trace = project_minimal_toolset(
                allowed_tools=available_tools,
                intent_plan=tool_intent_plan,
            )
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
            # ── SKILL.md resolution ──────────────────────────────────────
            # SKILL.md loading follows the routing plan as-is.  Runtime does
            # not force tool execution from transcript alone. When recent tool
            # evidence identifies the active skill for a low-information
            # follow-up, it is carried as a non-forcing skill target so the
            # main model can continue the same workflow instead of losing the
            # selected skill.
            #
            # Webhook-selected skills are an authenticated routing decision, so
            # they take precedence over classifier/transcript skill hints.
            preselected_md_skill_plan = build_preselected_md_skill_intent_plan(deps)
            if preselected_md_skill_plan is not None:
                preselected_target_md_skill = (
                    deps.extra.get("target_md_skill")
                    if isinstance(getattr(deps, "extra", None), dict)
                    else None
                )
                keep_preselected_plan, preselected_scope_reason = (
                    await self._should_keep_preselected_md_skill_plan(
                        agent=runtime_agent or self.agent,
                        deps=deps,
                        user_message=model_user_message,
                        target_md_skill=preselected_target_md_skill
                        if isinstance(preselected_target_md_skill, dict)
                        else {},
                    )
                )
                if not keep_preselected_plan:
                    _log_step(
                        "target_md_skill_preselected_rejected",
                        reason=preselected_scope_reason,
                        target_skill_names=list(preselected_md_skill_plan.target_skill_names),
                    )
                    preselected_md_skill_plan = None
            if preselected_md_skill_plan is not None:
                _log_step(
                    "target_md_skill_preselected",
                    reason=preselected_md_skill_plan.reason,
                    target_skill_names=list(preselected_md_skill_plan.target_skill_names),
                )
            routed_skill_resolution_plan = (
                tool_intent_plan if should_resolve_target_md_skill(tool_intent_plan) else None
            )
            transcript_skill_resolution_plan = None
            if (
                used_follow_up_context
                and preselected_md_skill_plan is None
                and routed_skill_resolution_plan is None
            ):
                transcript_skill_resolution_plan = build_transcript_skill_prompt_intent_plan(
                    active_skill=transcript_active_skill,
                )
                if transcript_skill_resolution_plan is not None:
                    _log_step(
                        "target_md_skill_from_transcript",
                        reason=transcript_skill_resolution_plan.reason,
                        target_skill_names=list(
                            transcript_skill_resolution_plan.target_skill_names
                        ),
                    )
            skill_resolution_plan = (
                preselected_md_skill_plan
                or routed_skill_resolution_plan
                or transcript_skill_resolution_plan
            )
            if should_resolve_target_md_skill(skill_resolution_plan):
                target_md_skill = resolve_selected_md_skill_target(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                    intent_plan=skill_resolution_plan,
                    max_file_bytes=int(
                        getattr(self.prompt_builder.config, "md_skills_max_file_bytes", 262144)
                        or 262144
                    ),
                )
            target_md_skill = enrich_target_md_skill_with_workflow_context(
                target_md_skill=target_md_skill,
                workflow_trace=target_md_skill_workflow_context,
            )
            if isinstance(deps.extra, dict):
                if isinstance(target_md_skill, dict):
                    deps.extra["target_md_skill"] = dict(target_md_skill)
                else:
                    deps.extra.pop("target_md_skill", None)
                # Store active trace ID so tool execution can inject it as env var
                if isinstance(target_md_skill_workflow_context, dict):
                    _active_trace = target_md_skill_workflow_context.get(
                        "internal_request_trace_id"
                    )
                    if _active_trace:
                        deps.extra["active_internal_request_trace_id"] = _active_trace
                    else:
                        deps.extra.pop("active_internal_request_trace_id", None)
                else:
                    deps.extra.pop("active_internal_request_trace_id", None)
            available_tools, standard_skill_runtime_trace, target_md_skill = (
                inject_standard_skill_runtime_tools(
                    available_tools=available_tools,
                    deps=deps,
                    target_md_skill=target_md_skill,
                )
            )
            runtime_allowed_tool_names = [
                str(tool.get("name", "") or "").strip()
                for tool in available_tools
                if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
            ]
            if isinstance(deps.extra, dict):
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["runtime_allowed_tool_names"] = list(runtime_allowed_tool_names)
                deps.extra["standard_skill_runtime_trace"] = dict(
                    standard_skill_runtime_trace
                )
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            _log_step(
                "standard_skill_runtime_injected",
                enabled=bool(standard_skill_runtime_trace.get("enabled")),
                reason=str(standard_skill_runtime_trace.get("reason", "") or ""),
                tool_names=list(standard_skill_runtime_trace.get("tool_names", []) or []),
                runtime_visible_count=len(available_tools),
            )
            _log_step(
                "target_md_skill_resolved",
                enabled=bool(target_md_skill),
                qualified_name=(
                    str(target_md_skill.get("qualified_name", "") or "")
                    if isinstance(target_md_skill, dict)
                    else ""
                ),
                loaded_instructions=bool(
                    isinstance(target_md_skill, dict)
                    and str(target_md_skill.get("instructions", "") or "").strip()
                ),
                workflow_context_entries=len(
                    (
                        target_md_skill.get("workflow_context", {}).get(
                            "recent_tool_metadata", []
                        )
                        if isinstance(target_md_skill, dict)
                        else []
                    )
                ),
            )
            if (
                turn_action_requires_tool_execution(tool_intent_plan)
                and not bool(standard_skill_runtime_trace.get("enabled"))
                and toolset_has_only_coordination_support_tools(available_tools)
            ):
                available_tools = []
                tool_intent_plan = ToolIntentPlan(
                    action=ToolIntentAction.DIRECT_ANSWER,
                    reason=(
                        "No executable provider, skill, or tool is available; "
                        "coordination-only helpers are not enough to perform this request."
                    ),
                )
                tool_gate_decision = self._build_tool_gate_decision_from_intent_plan(
                    tool_intent_plan,
                    available_tools=available_tools,
                )
                runtime_allowed_tool_names = []
                if isinstance(deps.extra, dict):
                    deps.extra["tools_snapshot"] = []
                    deps.extra["tools_snapshot_authoritative"] = True
                    deps.extra["runtime_allowed_tool_names"] = []
                    deps.extra["tool_groups_snapshot"] = {}
                _log_step(
                    "coordination_only_toolset_dropped",
                    reason="no_executable_runtime_capability",
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
                "model_user_message": model_user_message,
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
                "toolset_filter_trace": toolset_filter_trace,
                "tool_projection_trace": tool_projection_trace,
                "used_toolset_fallback": used_toolset_fallback,
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
