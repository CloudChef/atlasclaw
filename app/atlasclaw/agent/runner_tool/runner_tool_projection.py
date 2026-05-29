# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from typing import Any

from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan
from app.atlasclaw.core.provider_skill_capability import (
    provider_names_from_instance_refs,
    provider_skill_target_match_keys,
    runtime_tool_allowed_by_provider_scope,
    runtime_tool_provider_skill_names,
    runtime_tool_skill_names,
)


def tool_is_coordination_support(tool: dict[str, Any]) -> bool:
    """Return whether the tool is declared as a coordination helper."""
    return bool(tool.get("coordination_only"))


def _artifact_turn_has_explicit_targets(intent_plan: ToolIntentPlan) -> bool:
    if intent_plan.action is not ToolIntentAction.CREATE_ARTIFACT:
        return False
    if any(str(item).strip() for item in intent_plan.target_tool_names):
        return True
    if any(str(item).strip() for item in intent_plan.target_provider_skill_names):
        return True
    return any(
        str(item).strip().lower().startswith("artifact:")
        for item in intent_plan.target_capability_classes
    )


def project_minimal_toolset(
    *,
    allowed_tools: list[dict[str, Any]],
    intent_plan: ToolIntentPlan | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project the policy-allowed tool universe into the minimal executable set for this turn."""
    normalized_tools = [
        dict(tool)
        for tool in allowed_tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    ]
    trace: dict[str, Any] = {
        "enabled": False,
        "reason": "projection_not_required",
        "before_count": len(normalized_tools),
        "after_count": len(normalized_tools),
        "action": intent_plan.action.value if intent_plan is not None else "",
        "target_provider_instances": list(intent_plan.target_provider_instances) if intent_plan is not None else [],
        "target_provider_types": list(intent_plan.target_provider_types) if intent_plan is not None else [],
        "target_provider_skill_names": list(intent_plan.target_provider_skill_names) if intent_plan is not None else [],
        "target_skill_names": list(intent_plan.target_skill_names) if intent_plan is not None else [],
        "target_group_ids": list(intent_plan.target_group_ids) if intent_plan is not None else [],
        "target_capability_classes": list(intent_plan.target_capability_classes) if intent_plan is not None else [],
        "target_tool_names": list(intent_plan.target_tool_names) if intent_plan is not None else [],
        "coordination_tools": [],
    }
    if intent_plan is None:
        return normalized_tools, trace
    if (
        intent_plan.action in {
            ToolIntentAction.DIRECT_ANSWER,
            ToolIntentAction.ASK_CLARIFICATION,
        }
        and not any(
            [
                list(intent_plan.target_provider_instances or []),
                list(intent_plan.target_provider_types or []),
                list(intent_plan.target_provider_skill_names or []),
                list(intent_plan.target_skill_names or []),
                list(intent_plan.target_group_ids or []),
                list(intent_plan.target_capability_classes or []),
                list(intent_plan.target_tool_names or []),
            ]
        )
    ):
        trace.update(
            {
                "enabled": True,
                "reason": "projection_empty",
                "after_count": 0,
                "steps": [],
                "explicit_target_mode": False,
            }
        )
        return [], trace
    has_provider_skill_targets = any(
        str(item).strip() for item in intent_plan.target_provider_skill_names
    )
    if (
        intent_plan.action is not ToolIntentAction.USE_TOOLS
        and not _artifact_turn_has_explicit_targets(intent_plan)
        and not has_provider_skill_targets
    ):
        return normalized_tools, trace

    steps: list[dict[str, Any]] = []

    def _record_step(label: str, active: bool, count: int) -> None:
        steps.append(
            {
                "step": label,
                "active": active,
                "before_count": len(normalized_tools),
                "after_count": count,
            }
        )

    target_provider_types = {
        str(item).strip().lower()
        for item in intent_plan.target_provider_types
        if str(item).strip()
    }
    target_skill_names = {
        str(item).strip().lower()
        for item in intent_plan.target_skill_names
        if str(item).strip()
    }
    target_provider_skill_names = provider_skill_target_match_keys(
        list(intent_plan.target_provider_skill_names or [])
    )
    target_provider_names = provider_names_from_instance_refs(
        list(intent_plan.target_provider_instances or [])
    )
    target_group_ids = {
        _normalize_group_id(item)
        for item in intent_plan.target_group_ids
        if str(item).strip()
    }
    target_capability_classes = {
        str(item).strip().lower()
        for item in intent_plan.target_capability_classes
        if str(item).strip()
    }
    target_tool_names = {
        str(item).strip()
        for item in intent_plan.target_tool_names
        if str(item).strip()
    }

    def _allowed_by_provider_scope(tool: dict[str, Any]) -> bool:
        return runtime_tool_allowed_by_provider_scope(
            tool,
            provider_types=target_provider_types,
            provider_skill_names=target_provider_skill_names,
            provider_instance_refs=list(intent_plan.target_provider_instances or []),
        )

    explicit_target_mode = bool(target_tool_names)
    current = []
    current_names: set[str] = set()

    def _append_matches(label: str, active: bool, predicate: Any) -> None:
        if active:
            for tool in normalized_tools:
                tool_name = str(tool.get("name", "") or "").strip()
                if not tool_name or tool_name in current_names:
                    continue
                if predicate(tool):
                    current.append(tool)
                    current_names.add(tool_name)
        _record_step(label, active, len(current))

    _append_matches(
        "tool_name",
        explicit_target_mode,
        lambda tool: (
            str(tool.get("name", "") or "").strip() in target_tool_names
            and _allowed_by_provider_scope(tool)
        ),
    )

    provider_skill_intersection = bool(
        target_provider_types and target_provider_skill_names and target_provider_names
    )
    if provider_skill_intersection:
        _append_matches(
            "provider_skill",
            True,
            lambda tool: (
                str(tool.get("provider_type", "") or "").strip().lower()
                in target_provider_types
                and bool(
                    runtime_tool_provider_skill_names(
                        tool,
                        provider_names=target_provider_names,
                    ).intersection(target_provider_skill_names)
                )
            ),
        )
        _append_matches(
            "standalone_skill_name",
            True,
            lambda tool: (
                not str(tool.get("provider_type", "") or "").strip()
                and bool(runtime_tool_skill_names(tool).intersection(target_skill_names))
            ),
        )
        _record_step("provider_type", False, len(current))
        _record_step("group_ids", False, len(current))
        _record_step("capability_class", False, len(current))
        _record_step("skill_name", False, len(current))
    elif explicit_target_mode:
        _append_matches(
            "skill_name",
            bool(target_skill_names),
            lambda tool: (
                not str(tool.get("provider_type", "") or "").strip()
                and bool(runtime_tool_skill_names(tool).intersection(target_skill_names))
            ),
        )
        _record_step("provider_type", False, len(current))
        _record_step("group_ids", False, len(current))
        _record_step("capability_class", False, len(current))
    else:
        _record_step("provider_type", False, len(current))
        _append_matches(
            "group_ids",
            bool(target_group_ids),
            lambda tool: bool(
                target_group_ids.intersection(
                    {
                        _normalize_group_id(group_id)
                        for group_id in (tool.get("group_ids", []) or [])
                        if str(group_id).strip()
                    }
                )
                and _allowed_by_provider_scope(tool)
            ),
        )
        _append_matches(
            "capability_class",
            bool(target_capability_classes),
            lambda tool: (
                str(tool.get("capability_class", "") or "").strip().lower()
                in target_capability_classes
                and _allowed_by_provider_scope(tool)
            ),
        )
        _append_matches(
            "skill_name",
            bool(target_skill_names),
            lambda tool: (
                not str(tool.get("provider_type", "") or "").strip()
                and bool(runtime_tool_skill_names(tool).intersection(target_skill_names))
            ),
        )

    trace.update(
        {
            "enabled": True,
            "reason": "projection_applied" if current else "projection_empty",
            "after_count": len(current),
            "steps": steps,
            "explicit_target_mode": explicit_target_mode,
            "coordination_tools": [],
        }
    )
    return current, trace


def tool_required_turn_has_real_execution(
    *,
    intent_plan: ToolIntentPlan | None,
    tool_call_summaries: list[dict[str, Any]],
    final_messages: list[dict[str, Any]],
    start_index: int = 0,
    executed_tool_names: list[str] | None = None,
) -> bool:
    """Return whether a tool-required turn has at least one real tool execution record."""
    if intent_plan is None or not turn_action_requires_tool_execution(intent_plan):
        return True

    if executed_tool_names:
        normalized_executed = {
            str(name or "").strip()
            for name in executed_tool_names
            if str(name or "").strip()
        }
        if normalized_executed:
            return True

    if tool_call_summaries:
        normalized_summaries = {
            str(item.get("name", "") or "").strip()
            for item in tool_call_summaries
            if isinstance(item, dict)
        }
        normalized_summaries = {name for name in normalized_summaries if name}
        if normalized_summaries and not final_messages:
            return True

    safe_start = max(0, min(int(start_index), len(final_messages)))
    for message in final_messages[safe_start:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").strip().lower()
        if role in {"tool", "toolresult", "tool_result"}:
            if str(message.get("tool_name", "") or message.get("name", "")).strip():
                return True
            if message.get("content") is not None:
                return True
        tool_results = message.get("tool_results")
        if isinstance(tool_results, list) and tool_results:
            for result in tool_results:
                if not isinstance(result, dict):
                    return True
                if str(result.get("tool_name", "") or result.get("name", "")).strip():
                    return True
                if result.get("content") is not None:
                    return True
    return False

def turn_action_requires_tool_execution(intent_plan: ToolIntentPlan | None) -> bool:
    """Return whether the current turn contract requires a real executed tool."""
    if intent_plan is None:
        return False
    if intent_plan.action is ToolIntentAction.USE_TOOLS:
        return True
    return _artifact_turn_has_explicit_targets(intent_plan)


def _normalize_group_id(value: Any) -> str:
    group_id = str(value or "").strip()
    if not group_id:
        return ""
    if not group_id.startswith("group:"):
        group_id = f"group:{group_id}"
    return group_id
