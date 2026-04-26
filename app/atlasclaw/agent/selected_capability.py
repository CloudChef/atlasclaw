# Copyright 2026  Qianyun, Inc. All rights reserved.

"""Helpers for server-validated user-selected slash capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


SELECTED_CAPABILITY_KEY = "_selected_capability"


@dataclass(frozen=True)
class SelectedCapabilityTargets:
    """Normalized tool-routing targets derived from one selected capability."""

    provider_types: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    group_ids: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)

    def has_any(self) -> bool:
        """Return whether the selected capability carries any executable target."""
        return bool(
            self.provider_types
            or self.skill_names
            or self.group_ids
            or self.tool_names
        )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def unique_capability_values(values: Any) -> list[str]:
    """Normalize a scalar or list into unique, ordered non-empty strings."""
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def get_selected_capability_from_extra(extra: Any) -> dict[str, Any] | None:
    """Read only the server-validated slash capability from request extras."""
    if not isinstance(extra, Mapping):
        return None
    selected = extra.get(SELECTED_CAPABILITY_KEY)
    if selected is None:
        context = extra.get("context")
        if isinstance(context, Mapping):
            selected = context.get(SELECTED_CAPABILITY_KEY)
    return dict(selected) if isinstance(selected, Mapping) else None


def get_selected_capability_from_deps(deps: Any) -> dict[str, Any] | None:
    """Read the selected slash capability from a ``SkillDeps``-like object."""
    return get_selected_capability_from_extra(getattr(deps, "extra", None))


def selected_capability_targets(selected: Mapping[str, Any] | None) -> SelectedCapabilityTargets:
    """Extract provider, skill, group, and tool targets from a selected capability."""
    if not isinstance(selected, Mapping):
        return SelectedCapabilityTargets()

    target_provider_types = unique_capability_values(
        selected.get("target_provider_types") or selected.get("provider_type")
    )
    target_skill_names = unique_capability_values(
        selected.get("target_skill_names")
        or [
            selected.get("qualified_skill_name"),
            selected.get("skill_name"),
        ]
    )
    target_tool_names = unique_capability_values(selected.get("target_tool_names"))
    target_group_ids = unique_capability_values(selected.get("target_group_ids"))
    return SelectedCapabilityTargets(
        provider_types=target_provider_types,
        skill_names=target_skill_names,
        group_ids=target_group_ids,
        tool_names=target_tool_names,
    )


def selected_capability_provider_instance_ref(
    selected: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Return the selected provider type and instance name, if both are present."""
    if not isinstance(selected, Mapping):
        return "", ""
    return _normalize_text(selected.get("provider_type")), _normalize_text(
        selected.get("instance_name")
    )
