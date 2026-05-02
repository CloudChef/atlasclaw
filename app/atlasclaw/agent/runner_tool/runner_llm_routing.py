# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan
from app.atlasclaw.core.workspace_downloads import (
    is_safe_workspace_relative_path,
    workspace_download_reference_for_path,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _extract_artifact_kinds_from_capability_classes(values: list[str]) -> list[str]:
    kinds: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = _normalize_text(value).lower()
        if not normalized_value.startswith("artifact:"):
            continue
        kind = normalized_value.split("artifact:", 1)[-1].strip()
        if not kind or kind in seen:
            continue
        seen.add(kind)
        kinds.append(kind)
    return kinds


def _build_artifact_goal_label(kind: str) -> str:
    normalized_kind = _normalize_text(kind).lower()
    if not normalized_kind:
        return "requested artifact"
    compact = normalized_kind.replace("_", " ").replace("-", " ").strip()
    if not compact:
        return "requested artifact"
    if len(compact) <= 5:
        return f"{compact.upper()} artifact"
    return f"{compact.title()} artifact"


def resolve_artifact_goal_from_intent_plan(
    intent_plan: Optional[ToolIntentPlan],
) -> Optional[dict[str, Any]]:
    """Derive artifact expectations from explicit capability metadata, not user text."""
    if intent_plan is None:
        return None

    artifact_kinds = _extract_artifact_kinds_from_capability_classes(
        list(intent_plan.target_capability_classes or [])
    )
    if not artifact_kinds:
        return None

    kind = artifact_kinds[0]
    return {
        "kind": kind,
        "label": _build_artifact_goal_label(kind),
        "extensions": [],
    }


def _collect_artifact_path_candidates(payload: Any) -> list[str]:
    candidates: list[str] = []
    if payload is None:
        return candidates
    if isinstance(payload, dict):
        if payload.get("is_error") is True or payload.get("success") is False:
            return candidates
        for key, value in payload.items():
            normalized_key = _normalize_text(key).lower()
            if normalized_key in {"artifact_path", "download_path"}:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    normalized_value = _normalize_text(item)
                    if normalized_value:
                        candidates.append(normalized_value)
            candidates.extend(_collect_artifact_path_candidates(value))
        return candidates
    if isinstance(payload, list):
        for item in payload:
            candidates.extend(_collect_artifact_path_candidates(item))
        return candidates
    return candidates


def _is_valid_artifact_path_candidate(
    candidate: str,
    *,
    workspace_path: str | Path | None = None,
    user_id: str | None = None,
) -> bool:
    normalized = _normalize_text(candidate).replace("\\", "/")
    if not normalized:
        return False
    normalized_workspace_path = _normalize_text(workspace_path)
    normalized_user_id = _normalize_text(user_id)
    if normalized_workspace_path and normalized_user_id and normalized_user_id != "anonymous":
        return (
            workspace_download_reference_for_path(
                normalized,
                workspace_path=normalized_workspace_path,
                user_id=normalized_user_id,
            )
            is not None
        )
    if normalized.lower().startswith("workspace://"):
        normalized = unquote(normalized.split("://", 1)[1])
    if Path(normalized).is_absolute():
        return False
    return is_safe_workspace_relative_path(normalized)


def tool_output_satisfies_artifact_goal(
    *,
    tool_name: str,
    payload: Any,
    artifact_goal: Optional[dict[str, Any]],
    workspace_path: str | Path | None = None,
    user_id: str | None = None,
) -> bool:
    if not artifact_goal:
        return True

    extensions = [
        _normalize_text(item).lower()
        for item in list(artifact_goal.get("extensions", []) or [])
        if _normalize_text(item)
    ]
    path_candidates = [
        item
        for item in _collect_artifact_path_candidates(payload)
        if _is_valid_artifact_path_candidate(
            item,
            workspace_path=workspace_path,
            user_id=user_id,
        )
    ]

    if extensions:
        for candidate in path_candidates:
            if any(candidate.lower().endswith(extension) for extension in extensions):
                return True

    return bool(path_candidates)


def messages_satisfy_artifact_goal(
    *,
    messages: list[dict[str, Any]],
    start_index: int,
    target_tool_names: list[str],
    artifact_goal: Optional[dict[str, Any]],
    workspace_path: str | Path | None = None,
    user_id: str | None = None,
) -> bool:
    if not artifact_goal:
        return True

    target_names = {str(name).strip() for name in target_tool_names if str(name).strip()}
    if not target_names:
        return False

    safe_start = max(0, min(int(start_index), len(messages)))
    for message in messages[safe_start:]:
        if not isinstance(message, dict):
            continue
        role = _normalize_text(message.get("role", "")).lower()
        if role in {"tool", "toolresult", "tool_result"}:
            tool_name = _normalize_text(message.get("tool_name", "") or message.get("name", ""))
            if tool_name in target_names and tool_output_satisfies_artifact_goal(
                tool_name=tool_name,
                payload=message.get("content"),
                artifact_goal=artifact_goal,
                workspace_path=workspace_path,
                user_id=user_id,
            ):
                return True
        tool_results = message.get("tool_results")
        if not isinstance(tool_results, list):
            continue
        for result in tool_results:
            if not isinstance(result, dict):
                continue
            tool_name = _normalize_text(result.get("tool_name", "") or result.get("name", ""))
            if tool_name not in target_names:
                continue
            if tool_output_satisfies_artifact_goal(
                tool_name=tool_name,
                payload=result.get("content", result),
                artifact_goal=artifact_goal,
                workspace_path=workspace_path,
                user_id=user_id,
            ):
                return True
    return False


def selected_capability_ids_from_intent_plan(intent_plan: Optional[ToolIntentPlan]) -> list[str]:
    """Build stable capability identifiers from the current intent plan."""
    if intent_plan is None:
        return []

    selected_ids: list[str] = []
    seen: set[str] = set()

    def _append(prefix: str, values: list[str]) -> None:
        for value in values:
            normalized = _normalize_text(value)
            if not normalized:
                continue
            capability_id = f"{prefix}:{normalized}"
            if capability_id in seen:
                continue
            seen.add(capability_id)
            selected_ids.append(capability_id)

    _append("tool", list(intent_plan.target_tool_names or []))
    _append("skill", list(intent_plan.target_skill_names or []))
    _append("provider", list(intent_plan.target_provider_types or []))
    _append("capability", list(intent_plan.target_capability_classes or []))
    _append("group", list(intent_plan.target_group_ids or []))
    return selected_ids


def build_llm_first_guidance_plan(
    *,
    user_message: str,
    metadata_plan: Optional[ToolIntentPlan],
    explicit_capability_match: bool,
) -> Optional[ToolIntentPlan]:
    _ = user_message

    if metadata_plan is None or not explicit_capability_match:
        return None

    if not any(
        [
            list(metadata_plan.target_provider_types or []),
            list(metadata_plan.target_skill_names or []),
            list(metadata_plan.target_group_ids or []),
            list(metadata_plan.target_capability_classes or []),
            list(metadata_plan.target_tool_names or []),
        ]
    ):
        return None

    return ToolIntentPlan(
        action=ToolIntentAction.DIRECT_ANSWER,
        target_provider_types=list(metadata_plan.target_provider_types or []),
        target_skill_names=list(metadata_plan.target_skill_names or []),
        target_group_ids=list(metadata_plan.target_group_ids or []),
        target_capability_classes=list(metadata_plan.target_capability_classes or []),
        target_tool_names=list(metadata_plan.target_tool_names or []),
        reason=(
            "LLM-first runtime routing is active. Metadata narrows visible capability hints, "
            "but it does not decide the turn action before the main model sees the request."
        ),
    )
