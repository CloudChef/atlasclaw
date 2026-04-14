from __future__ import annotations

from typing import Any

from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan


def tool_is_coordination_support(tool: dict[str, Any]) -> bool:
    """Return whether the tool is declared as a coordination helper."""
    return bool(tool.get("coordination_only"))


def tool_is_generic_filesystem_helper(tool: dict[str, Any]) -> bool:
    """Return whether the tool is a generic filesystem helper capability."""
    capability_class = str(tool.get("capability_class", "") or "").strip().lower()
    return capability_class.startswith("fs_")


def _artifact_turn_has_explicit_targets(intent_plan: ToolIntentPlan) -> bool:
    if intent_plan.action is not ToolIntentAction.CREATE_ARTIFACT:
        return False
    if any(str(item).strip() for item in intent_plan.target_tool_names):
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
        "target_provider_types": list(intent_plan.target_provider_types) if intent_plan is not None else [],
        "target_skill_names": list(intent_plan.target_skill_names) if intent_plan is not None else [],
        "target_group_ids": list(intent_plan.target_group_ids) if intent_plan is not None else [],
        "target_capability_classes": list(intent_plan.target_capability_classes) if intent_plan is not None else [],
        "target_tool_names": list(intent_plan.target_tool_names) if intent_plan is not None else [],
        "coordination_tools": [],
    }
    if intent_plan is None:
        return normalized_tools, trace
    if intent_plan.action is not ToolIntentAction.USE_TOOLS and not _artifact_turn_has_explicit_targets(
        intent_plan
    ):
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

    explicit_target_mode = bool(target_tool_names)
    if explicit_target_mode:
        _apply_step(
            "tool_name",
            lambda tool: str(tool.get("name", "") or "").strip() in target_tool_names,
            True,
        )
        target_provider_types = set()
        target_skill_names = set()
        target_group_ids = set()
        target_capability_classes = set()

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
    if not explicit_target_mode:
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
    if current and intent_plan.action is ToolIntentAction.USE_TOOLS:
        current_names = {str(tool.get("name", "") or "").strip() for tool in current}
        for tool in normalized_tools:
            tool_name = str(tool.get("name", "") or "").strip()
            if not tool_name or tool_name in current_names:
                continue
            if not tool_is_coordination_support(tool):
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
            "explicit_target_mode": explicit_target_mode,
            "coordination_tools": [
                str(tool.get("name", "") or "").strip() for tool in coordination_tools
            ],
        }
    )
    return current, trace


def compress_candidate_toolset(
    *,
    allowed_tools: list[dict[str, Any]],
    metadata_candidates: dict[str, Any] | None,
    used_follow_up_context: bool,
    min_metadata_confidence: float = 0.3,
    compression_threshold: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Optionally shrink the visible toolset without deciding the turn action.

    Metadata recall is allowed to reduce the candidate surface when the visible
    toolset is large, but it must not force `use_tools` or any other action.
    """
    normalized_tools = [
        dict(tool)
        for tool in allowed_tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    ]
    trace: dict[str, Any] = {
        "enabled": False,
        "reason": "candidate_compression_not_required",
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

    if len(normalized_tools) <= max(1, int(compression_threshold or 0)):
        return normalized_tools, trace

    subset = _select_tools_from_metadata_candidates(
        tools=normalized_tools,
        metadata_candidates=metadata_candidates,
        min_metadata_confidence=min_metadata_confidence,
    )
    if not subset or len(subset) >= len(normalized_tools):
        return normalized_tools, trace

    subset = _append_coordination_tools(
        current=subset,
        all_tools=normalized_tools,
    )
    trace.update(
        {
            "enabled": True,
            "reason": "candidate_compression_applied",
            "after_count": len(subset),
            "coordination_tools": [
                str(tool.get("name", "") or "").strip()
                for tool in subset
                if tool_is_coordination_support(tool)
            ],
        }
    )
    return subset, trace


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

    artifact_tools = _select_explicit_artifact_tools_from_metadata_candidates(
        metadata_candidates=metadata_candidates,
        tools=tools,
    )
    if artifact_tools:
        return artifact_tools

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
    if any(_tool_declares_explicit_artifact(tool) for tool in selected):
        selected = [
            tool for tool in selected if not tool_is_generic_filesystem_helper(tool)
        ] or selected
    return selected


def _select_explicit_artifact_tools_from_metadata_candidates(
    *,
    metadata_candidates: dict[str, Any],
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_tools = {
        str(tool.get("name", "") or "").strip(): tool
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    ranked_candidates: list[tuple[int, int, str]] = []
    for item in (metadata_candidates.get("tool_candidates", []) or []):
        if not isinstance(item, dict) or not bool(item.get("has_strong_anchor")):
            continue
        tool_name = str(
            item.get("tool_name", "")
            or next(iter(item.get("tool_names", []) or []), "")
            or ""
        ).strip()
        tool = allowed_tools.get(tool_name)
        if tool is None or not _tool_declares_explicit_artifact(tool):
            continue
        try:
            score = int(item.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0
        try:
            priority = int(tool.get("priority", 100) or 100)
        except (TypeError, ValueError):
            priority = 100
        ranked_candidates.append((score, priority, tool_name))
    ranked_candidates.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
    return [allowed_tools[item[2]] for item in ranked_candidates]


def _tool_declares_explicit_artifact(tool: dict[str, Any]) -> bool:
    capability_class = str(tool.get("capability_class", "") or "").strip().lower()
    return capability_class.startswith("artifact:")


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
) -> list[dict[str, Any]]:
    selected = list(current)
    current_names = {str(tool.get("name", "") or "").strip() for tool in selected}
    for tool in all_tools:
        tool_name = str(tool.get("name", "") or "").strip()
        if not tool_name or tool_name in current_names:
            continue
        if not tool_is_coordination_support(tool):
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
