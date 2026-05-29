# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Shared helpers for provider-instance-qualified skill capability records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def unique_provider_skill_values(values: list[Any]) -> list[str]:
    """Return unique non-empty provider skill field values while preserving order."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def provider_skill_display_name(value: Any, provider_type: Any = "") -> str:
    """Return the selector-facing skill segment without provider qualification."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    bare = normalized.split(":")[-1]
    provider_prefix = f"{str(provider_type or '').strip()}__"
    if provider_prefix and bare.startswith(provider_prefix):
        bare = bare[len(provider_prefix):]
    return bare


def provider_skill_capability_name(
    *,
    provider_name: Any,
    provider_type: Any = "",
    qualified_skill_name: Any = "",
    skill_name: Any = "",
    display_skill_name: Any = "",
) -> str:
    """Return the selector-facing ``provider name.skill`` capability name."""
    normalized_provider_name = str(provider_name or "").strip()
    normalized_display_skill_name = str(display_skill_name or "").strip()
    if not normalized_display_skill_name:
        normalized_display_skill_name = provider_skill_display_name(
            qualified_skill_name or skill_name,
            provider_type,
        )
    if not normalized_provider_name or not normalized_display_skill_name:
        return ""
    return f"{normalized_provider_name}.{normalized_display_skill_name}"


def provider_skill_capability_id(
    *,
    provider_name: Any,
    provider_type: Any = "",
    qualified_skill_name: Any = "",
    skill_name: Any = "",
    display_skill_name: Any = "",
) -> str:
    """Return the natural-language selector id for one provider-bound skill."""
    capability_name = provider_skill_capability_name(
        provider_name=provider_name,
        provider_type=provider_type,
        qualified_skill_name=qualified_skill_name,
        skill_name=skill_name,
        display_skill_name=display_skill_name,
    )
    if not capability_name:
        return ""
    return f"provider_skill:{capability_name}"


def runtime_tool_skill_names(tool: Mapping[str, Any]) -> set[str]:
    """Return all skill names that may match a non-provider skill target."""
    values = {
        str(tool.get("skill_name", "") or "").strip().lower(),
        str(tool.get("qualified_skill_name", "") or "").strip().lower(),
    }
    values.discard("")
    return values


def provider_names_from_instance_refs(values: list[Any]) -> list[str]:
    """Return provider instance names from canonical ``provider_type.instance`` refs."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        provider_name = normalized.split(".", 1)[1] if "." in normalized else normalized
        provider_name = provider_name.strip()
        key = provider_name.lower()
        if not provider_name or key in seen:
            continue
        seen.add(key)
        result.append(provider_name)
    return result


def runtime_tool_provider_skill_names(
    tool: Mapping[str, Any],
    *,
    provider_names: list[Any] | None = None,
) -> set[str]:
    """Return exact selector-facing provider-skill names for runtime tool projection."""
    values = {
        str(tool.get("provider_skill_name", "") or "").strip().lower(),
    }
    display_skill_name = provider_skill_display_name(
        tool.get("qualified_skill_name", "") or tool.get("skill_name", ""),
        tool.get("provider_type", ""),
    )
    for provider_name in provider_names or []:
        provider_skill_name = provider_skill_capability_name(
            provider_name=provider_name,
            provider_type=tool.get("provider_type", ""),
            qualified_skill_name=tool.get("qualified_skill_name", ""),
            skill_name=tool.get("skill_name", ""),
            display_skill_name=display_skill_name,
        )
        values.add(provider_skill_name.lower())
    values.discard("")
    return values


def runtime_tool_allowed_by_provider_scope(
    tool: Mapping[str, Any],
    *,
    provider_types: list[Any] | set[Any],
    provider_skill_names: list[Any] | set[Any],
    provider_instance_refs: list[Any] | set[Any],
) -> bool:
    """Return whether a runtime tool is usable under the selected provider skill scope.

    Standalone tools are always allowed by this provider-scope check. Provider-bound
    tools require the caller to provide all three routing dimensions: provider type,
    provider-instance ref, and provider-name.skill target. This prevents raw provider
    tool names or provider types from widening execution outside the selected
    provider instance skill.
    """
    provider_type = str(tool.get("provider_type", "") or "").strip().lower()
    if not provider_type:
        return True

    normalized_provider_types = {
        str(item or "").strip().lower()
        for item in provider_types
        if str(item or "").strip()
    }
    normalized_provider_skill_names = provider_skill_target_match_keys(
        list(provider_skill_names or [])
    )
    normalized_provider_names = provider_names_from_instance_refs(
        list(provider_instance_refs or [])
    )
    if (
        not normalized_provider_types
        or not normalized_provider_skill_names
        or not normalized_provider_names
    ):
        return False
    return (
        provider_type in normalized_provider_types
        and bool(
            runtime_tool_provider_skill_names(
                tool,
                provider_names=normalized_provider_names,
            ).intersection(normalized_provider_skill_names)
        )
    )


def provider_skill_target_match_keys(values: list[Any]) -> set[str]:
    """Return exact match keys for selector-facing ``provider name.skill`` targets."""
    result: set[str] = set()
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized:
            continue
        result.add(normalized)
    return result


def build_provider_skill_target_fields(
    *,
    provider_type: Any,
    instance_name: Any,
    qualified_skill_name: Any,
    skill_name: Any,
    display_skill_name: Any = "",
) -> dict[str, Any]:
    """Build common execution-routing fields for one provider-name.skill capability."""
    normalized_provider_type = str(provider_type or "").strip()
    normalized_instance_name = str(instance_name or "").strip()
    normalized_qualified_skill_name = str(qualified_skill_name or "").strip()
    normalized_skill_name = str(skill_name or "").strip()
    normalized_display_skill_name = str(display_skill_name or "").strip()
    provider_skill_name = provider_skill_capability_name(
        provider_name=normalized_instance_name,
        provider_type=normalized_provider_type,
        qualified_skill_name=normalized_qualified_skill_name,
        skill_name=normalized_skill_name,
        display_skill_name=normalized_display_skill_name,
    )
    return {
        "provider_name": normalized_instance_name,
        "provider_type": normalized_provider_type,
        "instance_name": normalized_instance_name,
        "qualified_skill_name": normalized_qualified_skill_name,
        "skill_name": normalized_skill_name,
        "provider_skill_name": provider_skill_name,
        "target_provider_skill_names": unique_provider_skill_values([provider_skill_name]),
        "target_provider_instances": [
            f"{normalized_provider_type}.{normalized_instance_name}"
        ],
        "target_provider_types": [normalized_provider_type],
    }
