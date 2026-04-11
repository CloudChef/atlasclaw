from __future__ import annotations

from typing import Any

from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan


DEFAULT_COORDINATION_TOOL_NAMES = (
    "list_provider_instances",
    "select_provider_instance",
    "read",
    "session_status",
)


def project_minimal_toolset(
    *,
    allowed_tools: list[dict[str, Any]],
    intent_plan: ToolIntentPlan,
    coordination_tool_names: tuple[str, ...] = DEFAULT_COORDINATION_TOOL_NAMES,
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
        "action": intent_plan.action.value,
        "target_provider_types": list(intent_plan.target_provider_types),
        "target_skill_names": list(intent_plan.target_skill_names),
        "target_group_ids": list(intent_plan.target_group_ids),
        "target_capability_classes": list(intent_plan.target_capability_classes),
        "target_tool_names": list(intent_plan.target_tool_names),
        "coordination_tools": [],
    }
    if intent_plan.action is not ToolIntentAction.USE_TOOLS:
        return normalized_tools, trace

    current = list(normalized_tools)
    steps: list[dict[str, Any]] = []

    def _apply_step(label: str, predicate: Any, active: bool) -> None:
        nonlocal current
        before = len(current)
        if active:
            current = [tool for tool in current if predicate(tool)]
        steps.append(
            {
                "step": label,
                "active": active,
                "before_count": before,
                "after_count": len(current),
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

    _apply_step(
        "provider_type",
        lambda tool: str(tool.get("provider_type", "") or "").strip().lower() in target_provider_types,
        bool(target_provider_types),
    )
    _apply_step(
        "group_ids",
        lambda tool: bool(
            target_group_ids.intersection(
                {
                    _normalize_group_id(group_id)
                    for group_id in (tool.get("group_ids", []) or [])
                    if str(group_id).strip()
                }
            )
        ),
        bool(target_group_ids),
    )
    _apply_step(
        "capability_class",
        lambda tool: str(tool.get("capability_class", "") or "").strip().lower()
        in target_capability_classes,
        bool(target_capability_classes),
    )
    _apply_step(
        "tool_name",
        lambda tool: str(tool.get("name", "") or "").strip() in target_tool_names,
        bool(target_tool_names),
    )
    _apply_step(
        "skill_name",
        lambda tool: (
            str(tool.get("skill_name", "") or "").strip().lower() in target_skill_names
            or str(tool.get("qualified_skill_name", "") or "").strip().lower() in target_skill_names
        ),
        bool(target_skill_names),
    )

    coordination_tools: list[dict[str, Any]] = []
    if current:
        current_names = {str(tool.get("name", "") or "").strip() for tool in current}
        for tool in normalized_tools:
            tool_name = str(tool.get("name", "") or "").strip()
            if not tool_name or tool_name in current_names:
                continue
            if tool_name not in coordination_tool_names:
                continue
            coordination_tools.append(tool)
            current_names.add(tool_name)
        current.extend(coordination_tools)

    trace.update(
        {
            "enabled": True,
            "reason": "projection_applied" if current else "projection_empty",
            "after_count": len(current),
            "steps": steps,
            "coordination_tools": [
                str(tool.get("name", "") or "").strip() for tool in coordination_tools
            ],
        }
    )
    return current, trace


def project_planner_toolset(
    *,
    allowed_tools: list[dict[str, Any]],
    metadata_candidates: dict[str, Any] | None,
    used_follow_up_context: bool,
    min_metadata_confidence: float = 0.3,
    coordination_tool_names: tuple[str, ...] = DEFAULT_COORDINATION_TOOL_NAMES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project a smaller planner-visible tool universe before the intent-plan model call.

    For non-follow-up turns, provider/skill tools are hidden unless metadata strongly points
    to a specific provider/skill subset. This keeps planner prompts small without hardcoding
    user-topic rules.
    """
    normalized_tools = [
        dict(tool)
        for tool in allowed_tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    ]
    trace: dict[str, Any] = {
        "enabled": False,
        "reason": "planner_projection_not_required",
        "before_count": len(normalized_tools),
        "after_count": len(normalized_tools),
        "used_follow_up_context": bool(used_follow_up_context),
        "metadata_confidence": float(
            ((metadata_candidates or {}).get("confidence", 0.0) if isinstance(metadata_candidates, dict) else 0.0)
            or 0.0
        ),
        "coordination_tools": [],
    }
    if not normalized_tools:
        return [], trace

    subset = _select_tools_from_metadata_candidates(
        tools=normalized_tools,
        metadata_candidates=metadata_candidates,
        min_metadata_confidence=min_metadata_confidence,
    )
    if subset:
        subset = _append_coordination_tools(
            current=subset,
            all_tools=normalized_tools,
            coordination_tool_names=coordination_tool_names,
        )
        trace.update(
            {
                "enabled": True,
                "reason": "planner_metadata_subset",
                "after_count": len(subset),
                "coordination_tools": [
                    str(tool.get("name", "") or "").strip()
                    for tool in subset
                    if str(tool.get("name", "") or "").strip() in coordination_tool_names
                ],
            }
        )
        return subset, trace

    if used_follow_up_context:
        return normalized_tools, trace

    general_tools = [
        tool
        for tool in normalized_tools
        if (
            not _is_provider_or_skill_tool(tool)
            and str(tool.get("name", "") or "").strip() not in coordination_tool_names
            and _is_planner_general_visible_tool(tool)
        )
    ]
    if general_tools:
        trace.update(
            {
                "enabled": True,
                "reason": "planner_general_tools_only",
                "after_count": len(general_tools),
            }
        )
        return general_tools, trace

    return normalized_tools, trace


def tool_required_turn_has_real_execution(
    *,
    intent_plan: ToolIntentPlan | None,
    tool_call_summaries: list[dict[str, Any]],
    final_messages: list[dict[str, Any]],
    start_index: int = 0,
    executed_tool_names: list[str] | None = None,
) -> bool:
    """Return whether a tool-required turn has at least one real tool execution record."""
    if intent_plan is None or intent_plan.action is not ToolIntentAction.USE_TOOLS:
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


def _is_planner_general_visible_tool(tool: dict[str, Any]) -> bool:
    visibility = str(tool.get("planner_visibility", "") or "").strip().lower()
    if visibility == "general":
        return True
    if visibility:
        return False
    return False


def turn_action_requires_tool_execution(intent_plan: ToolIntentPlan | None) -> bool:
    """Return whether the current turn contract requires a real executed tool."""
    if intent_plan is None:
        return False
    return intent_plan.action is ToolIntentAction.USE_TOOLS


def _select_tools_from_metadata_candidates(
    *,
    tools: list[dict[str, Any]],
    metadata_candidates: dict[str, Any] | None,
    min_metadata_confidence: float,
) -> list[dict[str, Any]]:
    if not isinstance(metadata_candidates, dict):
        return []

    try:
        confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    has_single_tool_consensus = _metadata_candidates_have_single_tool_consensus(
        metadata_candidates=metadata_candidates,
        tools=tools,
    )
    if confidence < max(0.0, float(min_metadata_confidence or 0.0)) and not has_single_tool_consensus:
        return []

    if has_single_tool_consensus:
        preferred_tool_name = _extract_single_preferred_tool_name(
            metadata_candidates=metadata_candidates,
            tools=tools,
        )
        if preferred_tool_name:
            return [
                dict(tool)
                for tool in tools
                if str(tool.get("name", "") or "").strip() == preferred_tool_name
            ]

    target_provider_types = {
        str(item).strip().lower()
        for item in (metadata_candidates.get("preferred_provider_types", []) or [])
        if str(item).strip()
    }
    target_group_ids = {
        _normalize_group_id(item)
        for item in (metadata_candidates.get("preferred_group_ids", []) or [])
        if str(item).strip()
    }
    target_capability_classes = {
        str(item).strip().lower()
        for item in (metadata_candidates.get("preferred_capability_classes", []) or [])
        if str(item).strip()
    }
    target_tool_names = {
        str(item).strip()
        for item in (metadata_candidates.get("preferred_tool_names", []) or [])
        if str(item).strip()
    }
    target_skill_names = {
        str(item.get("qualified_skill_name", "") or item.get("skill_name", "") or "").strip().lower()
        for item in (metadata_candidates.get("skill_candidates", []) or [])
        if isinstance(item, dict)
        and str(item.get("qualified_skill_name", "") or item.get("skill_name", "") or "").strip()
    }

    if not any(
        [
            target_provider_types,
            target_group_ids,
            target_capability_classes,
            target_tool_names,
            target_skill_names,
        ]
    ):
        return []

    selected: list[dict[str, Any]] = []
    for tool in tools:
        provider_type = str(tool.get("provider_type", "") or "").strip().lower()
        capability = str(tool.get("capability_class", "") or "").strip().lower()
        name = str(tool.get("name", "") or "").strip()
        groups = {
            _normalize_group_id(group_id)
            for group_id in (tool.get("group_ids", []) or [])
            if str(group_id).strip()
        }
        skill_name = str(
            tool.get("qualified_skill_name", "") or tool.get("skill_name", "") or ""
        ).strip().lower()
        matches = False
        if target_provider_types and provider_type in target_provider_types:
            matches = True
        if target_group_ids and groups.intersection(target_group_ids):
            matches = True
        if target_capability_classes and capability in target_capability_classes:
            matches = True
        if target_tool_names and name in target_tool_names:
            matches = True
        if target_skill_names and skill_name in target_skill_names:
            matches = True
        if matches:
            selected.append(tool)
    return selected


def _extract_single_preferred_tool_name(
    *,
    metadata_candidates: dict[str, Any],
    tools: list[dict[str, Any]],
) -> str:
    allowed_tool_names = {
        str(tool.get("name", "") or "").strip()
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    preferred_tool_names = [
        str(item).strip()
        for item in (metadata_candidates.get("preferred_tool_names", []) or [])
        if str(item).strip() in allowed_tool_names
    ]
    preferred_tool_names = list(dict.fromkeys(preferred_tool_names))
    if len(preferred_tool_names) != 1:
        return ""
    return preferred_tool_names[0]


def _metadata_candidates_have_single_tool_consensus(
    *,
    metadata_candidates: dict[str, Any],
    tools: list[dict[str, Any]],
) -> bool:
    selected_tool_name = _extract_single_preferred_tool_name(
        metadata_candidates=metadata_candidates,
        tools=tools,
    )
    if not selected_tool_name:
        return False

    allowed_tool_names = {
        str(tool.get("name", "") or "").strip()
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    strong_candidate_tool_sets: list[set[str]] = []
    for key in ("provider_candidates", "skill_candidates", "tool_candidates", "builtin_tool_candidates"):
        for item in (metadata_candidates.get(key, []) or []):
            if not isinstance(item, dict) or not bool(item.get("has_strong_anchor")):
                continue
            candidate_tool_names = [
                str(name).strip()
                for name in (item.get("tool_names", []) or [])
                if str(name).strip() in allowed_tool_names
            ]
            direct_tool_name = str(item.get("tool_name", "") or "").strip()
            if direct_tool_name and direct_tool_name in allowed_tool_names:
                candidate_tool_names.append(direct_tool_name)
            candidate_tool_set = {name for name in candidate_tool_names if name}
            if candidate_tool_set:
                strong_candidate_tool_sets.append(candidate_tool_set)

    if not strong_candidate_tool_sets:
        return False

    for candidate_tool_set in strong_candidate_tool_sets:
        if selected_tool_name not in candidate_tool_set:
            return False
        if candidate_tool_set.difference({selected_tool_name}):
            return False
    return True


def _append_coordination_tools(
    *,
    current: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    coordination_tool_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    selected = list(current)
    current_names = {str(tool.get("name", "") or "").strip() for tool in selected}
    for tool in all_tools:
        tool_name = str(tool.get("name", "") or "").strip()
        if not tool_name or tool_name in current_names:
            continue
        if tool_name not in coordination_tool_names:
            continue
        selected.append(tool)
        current_names.add(tool_name)
    return selected


def _is_provider_or_skill_tool(tool: dict[str, Any]) -> bool:
    capability = str(tool.get("capability_class", "") or "").strip().lower()
    provider_type = str(tool.get("provider_type", "") or "").strip().lower()
    category = str(tool.get("category", "") or "").strip().lower()
    if capability.startswith("provider:") or capability == "skill":
        return True
    if provider_type and provider_type != "none":
        return True
    return category.startswith("provider") or category == "skill"


def _normalize_group_id(value: Any) -> str:
    group_id = str(value or "").strip()
    if not group_id:
        return ""
    if not group_id.startswith("group:"):
        group_id = f"group:{group_id}"
    return group_id
