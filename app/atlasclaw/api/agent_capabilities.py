# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc. All rights reserved.

"""Request-scoped chat capability catalog for slash command selection."""

from __future__ import annotations

import re
from typing import Any

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    filter_provider_instances_for_authz,
    has_permission,
)
from app.atlasclaw.skills.permission_service import skill_permission_service

from .deps_context import (
    APIContext,
    _build_md_tool_skill_refs,
    _filter_snapshot_by_permissions,
)


_COMMAND_PART_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]+")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _unique_text(values: list[Any]) -> list[str]:
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


def _command_part(value: Any) -> str:
    normalized = _COMMAND_PART_PATTERN.sub("-", _normalize_text(value)).strip("-")
    return normalized or "item"


def _bare_skill_name(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    return normalized.split(":")[-1]


def _display_skill_name(value: Any, provider_type: Any = "") -> str:
    bare = _bare_skill_name(value)
    provider_prefix = f"{_normalize_text(provider_type)}__"
    if provider_prefix and bare.startswith(provider_prefix):
        bare = bare[len(provider_prefix):]
    return bare


def _build_capability_id(item: dict[str, Any]) -> str:
    parts = [
        _normalize_text(item.get("kind")),
        _normalize_text(item.get("provider_type")),
        _normalize_text(item.get("instance_name")),
        _normalize_text(item.get("qualified_skill_name")),
        _normalize_text(item.get("skill_name")),
        _normalize_text(item.get("command")),
    ]
    return "|".join(parts).lower()


def _provider_instances_for(
    provider_instances: dict[str, dict[str, dict[str, Any]]],
    provider_type: str,
) -> dict[str, dict[str, Any]]:
    if not provider_type:
        return {}
    if provider_type in provider_instances and isinstance(provider_instances[provider_type], dict):
        return provider_instances[provider_type]
    lowered = provider_type.lower()
    for key, value in provider_instances.items():
        if _normalize_lower(key) == lowered and isinstance(value, dict):
            return value
    return {}


def _get_provider_display_name(ctx: APIContext, provider_type: str) -> str:
    registry = getattr(ctx, "service_provider_registry", None)
    context_getter = getattr(registry, "get_provider_context", None)
    if callable(context_getter):
        try:
            provider_context = context_getter(provider_type)
            display_name = _normalize_text(getattr(provider_context, "display_name", ""))
            if display_name:
                return display_name
        except Exception:
            pass
    return provider_type


def _get_registry_tools_snapshot(ctx: APIContext) -> list[dict[str, Any]]:
    builder = getattr(ctx.skill_registry, "tools_snapshot", None)
    if callable(builder):
        return [dict(item) for item in builder() if isinstance(item, dict)]
    return [dict(item) for item in ctx.skill_registry.snapshot() if isinstance(item, dict)]


def _get_registry_md_snapshot(ctx: APIContext) -> list[dict[str, Any]]:
    builder = getattr(ctx.skill_registry, "md_snapshot", None)
    if not callable(builder):
        return []
    return [dict(item) for item in builder() if isinstance(item, dict)]


def _get_visible_provider_instances(
    *,
    authz: AuthorizationContext | None,
    provider_instances: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    if authz is None:
        return {
            str(provider_type): {
                str(instance_name): dict(instance_config)
                for instance_name, instance_config in instances.items()
                if isinstance(instance_config, dict)
            }
            for provider_type, instances in (provider_instances or {}).items()
            if isinstance(instances, dict)
        }
    return filter_provider_instances_for_authz(authz, provider_instances or {})


def _get_visible_skill_snapshots(
    *,
    ctx: APIContext,
    authz: AuthorizationContext | None,
    provider_instances: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tools_snapshot = _get_registry_tools_snapshot(ctx)
    md_skills_snapshot = _get_registry_md_snapshot(ctx)

    if authz is not None and not has_permission(authz, "skills.view"):
        tools_snapshot = []
        md_skills_snapshot = []
    elif authz is not None:
        skill_permissions = authz.permissions.get("skills", {}).get("skill_permissions", [])
        if not (authz.is_admin and not skill_permissions):
            if isinstance(skill_permissions, list):
                md_tool_skill_refs = _build_md_tool_skill_refs(
                    ctx.skill_registry,
                    md_skills_snapshot,
                )
                tools_snapshot, md_skills_snapshot = _filter_snapshot_by_permissions(
                    tools_snapshot,
                    md_skills_snapshot,
                    skill_permissions,
                    md_tool_skill_refs,
                )
            else:
                tools_snapshot = []
                md_skills_snapshot = []

    tools_snapshot, md_skills_snapshot = skill_permission_service.filter_provider_bound_snapshots(
        tools_snapshot,
        md_skills_snapshot,
        provider_instances,
        enforce=bool(provider_instances),
    )
    return tools_snapshot, md_skills_snapshot


def _md_tool_names(ctx: APIContext, qualified_name: str, metadata: dict[str, Any]) -> list[str]:
    names: list[Any] = []
    md_skill_tools_map = getattr(ctx.skill_registry, "_md_skill_tools", {})
    if isinstance(md_skill_tools_map, dict):
        names.extend(md_skill_tools_map.get(qualified_name, []) or [])

    if isinstance(metadata, dict):
        names.append(metadata.get("tool_name"))
        for key, value in metadata.items():
            key_text = _normalize_text(key)
            if key_text.startswith("tool_") and key_text.endswith("_name"):
                names.append(value)

    return _unique_text(names)


def _group_ids_from_metadata(metadata: dict[str, Any], provider_type: str) -> list[str]:
    values: list[Any] = []
    for key in ("group", "groups", "tool_group", "tool_groups", "group_ids"):
        if key in metadata:
            values.append(metadata.get(key))
    if provider_type:
        values.append(f"group:{provider_type}")
    flattened: list[Any] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return _unique_text(flattened)


def _append_capability(
    items: list[dict[str, Any]],
    seen: set[str],
    item: dict[str, Any],
) -> None:
    item["id"] = _build_capability_id(item)
    key = item["id"]
    if key in seen:
        return
    seen.add(key)
    items.append(item)


def _build_standalone_skill_item(
    *,
    skill_name: str,
    qualified_skill_name: str,
    description: str,
    source: str,
    tool_names: list[str] | None = None,
    group_ids: list[str] | None = None,
) -> dict[str, Any]:
    display_name = _display_skill_name(qualified_skill_name or skill_name)
    command = f"/{_command_part(display_name)}"
    target_skill_names = _unique_text([qualified_skill_name, skill_name])
    return {
        "kind": "skill",
        "command": command,
        "label": display_name,
        "skill_name": skill_name,
        "qualified_skill_name": qualified_skill_name or skill_name,
        "description": description,
        "source": source,
        "target_skill_names": target_skill_names,
        "target_tool_names": _unique_text(tool_names or []),
        "target_group_ids": _unique_text(group_ids or []),
    }


def _build_provider_skill_items(
    *,
    ctx: APIContext,
    provider_instances: dict[str, dict[str, dict[str, Any]]],
    provider_type: str,
    skill_name: str,
    qualified_skill_name: str,
    description: str,
    source: str,
    tool_names: list[str] | None = None,
    group_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    instances = _provider_instances_for(provider_instances, provider_type)
    if not instances:
        return []

    display_skill_name = _display_skill_name(qualified_skill_name or skill_name, provider_type)
    provider_display_name = _get_provider_display_name(ctx, provider_type)
    target_skill_names = _unique_text([qualified_skill_name, skill_name])
    target_tool_names = _unique_text(tool_names or [])
    target_group_ids = _unique_text(group_ids or [])
    items: list[dict[str, Any]] = []
    for instance_name in sorted(instances.keys()):
        command = f"/{_command_part(instance_name)}.{_command_part(display_skill_name)}"
        items.append(
            {
                "kind": "provider_skill",
                "command": command,
                "label": f"{instance_name}.{display_skill_name}",
                "provider_type": provider_type,
                "provider_display_name": provider_display_name,
                "instance_name": instance_name,
                "skill_name": skill_name,
                "qualified_skill_name": qualified_skill_name or skill_name,
                "description": description,
                "source": source,
                "target_provider_types": _unique_text([provider_type]),
                "target_skill_names": target_skill_names,
                "target_tool_names": target_tool_names,
                "target_group_ids": target_group_ids,
            }
        )
    return items


def build_agent_capabilities(
    *,
    ctx: APIContext,
    authz: AuthorizationContext | None = None,
    provider_instances: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return slash-selectable skills visible to the current request."""
    visible_provider_instances = _get_visible_provider_instances(
        authz=authz,
        provider_instances=provider_instances or {},
    )
    tools_snapshot, md_skills_snapshot = _get_visible_skill_snapshots(
        ctx=ctx,
        authz=authz,
        provider_instances=visible_provider_instances,
    )

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    md_tool_names_seen: set[str] = set()

    for md_skill in md_skills_snapshot:
        metadata = md_skill.get("metadata") if isinstance(md_skill.get("metadata"), dict) else {}
        provider_type = skill_permission_service.provider_type_from_md_skill_snapshot(md_skill)
        skill_name = _normalize_text(md_skill.get("name"))
        qualified_skill_name = _normalize_text(md_skill.get("qualified_name")) or skill_name
        description = _normalize_text(md_skill.get("description"))
        tool_names = _md_tool_names(ctx, qualified_skill_name, metadata)
        md_tool_names_seen.update(_normalize_lower(name) for name in tool_names)
        group_ids = _group_ids_from_metadata(metadata, provider_type)
        if provider_type:
            for item in _build_provider_skill_items(
                ctx=ctx,
                provider_instances=visible_provider_instances,
                provider_type=provider_type,
                skill_name=skill_name,
                qualified_skill_name=qualified_skill_name,
                description=description,
                source="markdown",
                tool_names=tool_names,
                group_ids=group_ids,
            ):
                _append_capability(items, seen, item)
            continue

        _append_capability(
            items,
            seen,
            _build_standalone_skill_item(
                skill_name=skill_name,
                qualified_skill_name=qualified_skill_name,
                description=description,
                source="markdown",
                tool_names=tool_names,
                group_ids=group_ids,
            ),
        )

    for tool in tools_snapshot:
        tool_name = _normalize_text(tool.get("name"))
        if not tool_name:
            continue
        if _normalize_lower(tool_name) in md_tool_names_seen:
            continue
        if bool(tool.get("coordination_only")):
            continue

        provider_type = skill_permission_service.provider_type_from_tool_snapshot(tool)
        skill_name = _normalize_text(tool.get("skill_name")) or tool_name
        qualified_skill_name = _normalize_text(tool.get("qualified_skill_name")) or skill_name
        description = _normalize_text(tool.get("description"))
        group_ids = _unique_text(tool.get("group_ids", []) or [])
        if provider_type:
            for item in _build_provider_skill_items(
                ctx=ctx,
                provider_instances=visible_provider_instances,
                provider_type=provider_type,
                skill_name=skill_name,
                qualified_skill_name=qualified_skill_name,
                description=description,
                source=_normalize_text(tool.get("source")) or "executable",
                tool_names=[tool_name],
                group_ids=group_ids,
            ):
                _append_capability(items, seen, item)
            continue

        _append_capability(
            items,
            seen,
            _build_standalone_skill_item(
                skill_name=skill_name,
                qualified_skill_name=qualified_skill_name,
                description=description,
                source=_normalize_text(tool.get("source")) or "executable",
                tool_names=[tool_name],
                group_ids=group_ids,
            ),
        )

    items.sort(
        key=lambda item: (
            0 if item.get("kind") == "provider_skill" else 1,
            _normalize_lower(item.get("command")),
        )
    )
    return {
        "count": len(items),
        "capabilities": items,
    }


def resolve_selected_capability(
    *,
    ctx: APIContext,
    selected: Any,
    authz: AuthorizationContext | None = None,
    provider_instances: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Validate a client-selected capability against the request-visible catalog."""
    if not isinstance(selected, dict):
        return None

    catalog = build_agent_capabilities(
        ctx=ctx,
        authz=authz,
        provider_instances=provider_instances,
    )
    capabilities = [
        item for item in catalog.get("capabilities", [])
        if isinstance(item, dict)
    ]
    selected_id = _normalize_text(selected.get("id"))
    if selected_id:
        for item in capabilities:
            if _normalize_text(item.get("id")) == selected_id:
                return dict(item)
        return None

    requested_kind = _normalize_text(selected.get("kind"))
    requested_command = _normalize_text(selected.get("command"))
    requested_provider_type = _normalize_text(selected.get("provider_type"))
    requested_instance_name = _normalize_text(selected.get("instance_name"))
    requested_skill = _normalize_text(
        selected.get("qualified_skill_name") or selected.get("skill_name")
    )

    matches: list[dict[str, Any]] = []
    for item in capabilities:
        if requested_kind and _normalize_text(item.get("kind")) != requested_kind:
            continue
        if requested_command and _normalize_text(item.get("command")) != requested_command:
            continue
        if (
            requested_provider_type
            and _normalize_text(item.get("provider_type")) != requested_provider_type
        ):
            continue
        if (
            requested_instance_name
            and _normalize_text(item.get("instance_name")) != requested_instance_name
        ):
            continue
        if requested_skill:
            item_skill_names = {
                _normalize_lower(item.get("qualified_skill_name")),
                _normalize_lower(item.get("skill_name")),
                *{
                    _normalize_lower(name)
                    for name in item.get("target_skill_names", [])
                    if _normalize_text(name)
                },
            }
            if _normalize_lower(requested_skill) not in item_skill_names:
                continue
        matches.append(item)

    if len(matches) != 1:
        return None
    return dict(matches[0])
