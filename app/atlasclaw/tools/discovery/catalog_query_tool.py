# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Runtime catalog query tool for AtlasClaw internal capability discovery."""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_kind(value: str) -> str:
    normalized = _normalize_text(value).lower()
    aliases = {
        "skill": "skills",
        "skills": "skills",
        "tool": "tools",
        "tools": "tools",
        "provider": "providers",
        "providers": "providers",
        "group": "groups",
        "groups": "groups",
        "summary": "summary",
        "all": "summary",
    }
    return aliases.get(normalized, "summary")


def _normalize_group_id(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    return normalized if normalized.startswith("group:") else f"group:{normalized}"


def _coerce_context_payload(ctx: "RunContext[SkillDeps]") -> dict[str, Any]:
    if hasattr(ctx, "deps") and hasattr(ctx.deps, "extra") and isinstance(ctx.deps.extra, dict):
        return ctx.deps.extra
    return {}


def _collect_provider_contexts(extra: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = extra.get("_service_provider_registry")
    if registry is None:
        return {}
    get_contexts = getattr(registry, "get_all_provider_contexts", None)
    if not callable(get_contexts):
        return {}
    try:
        contexts = get_contexts()
    except Exception:
        return {}
    if not isinstance(contexts, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for provider_type, raw in contexts.items():
        provider_key = _normalize_text(provider_type).lower()
        if not provider_key:
            continue
        if isinstance(raw, dict):
            normalized[provider_key] = dict(raw)
            continue
        payload: dict[str, Any] = {}
        for field_name in (
            "display_name",
            "description",
            "aliases",
            "keywords",
            "capabilities",
            "use_when",
            "avoid_when",
        ):
            payload[field_name] = getattr(raw, field_name, None)
        normalized[provider_key] = payload
    return normalized


def _provider_label(provider_type: str, provider_contexts: dict[str, dict[str, Any]]) -> str:
    provider_key = _normalize_text(provider_type).lower()
    if not provider_key:
        return "AtlasClaw"
    context = provider_contexts.get(provider_key, {})
    display_name = _normalize_text(context.get("display_name", ""))
    if display_name:
        return display_name
    return provider_key.replace("-", " ").title()


def _filter_tools(
    *,
    tools_snapshot: list[dict[str, Any]],
    provider_type: str,
    group_id: str,
) -> list[dict[str, Any]]:
    provider_key = _normalize_text(provider_type).lower()
    normalized_group_id = _normalize_group_id(group_id)
    filtered: list[dict[str, Any]] = []
    for tool in tools_snapshot:
        if not isinstance(tool, dict):
            continue
        tool_name = _normalize_text(tool.get("name", ""))
        if not tool_name:
            continue
        if provider_key:
            tool_provider = _normalize_text(tool.get("provider_type", "")).lower()
            capability = _normalize_text(tool.get("capability_class", "")).lower()
            if tool_provider != provider_key and capability != f"provider:{provider_key}":
                continue
        if normalized_group_id:
            tool_group_ids = {
                _normalize_group_id(item)
                for item in (tool.get("group_ids", []) or [])
                if _normalize_text(item)
            }
            if normalized_group_id not in tool_group_ids:
                continue
        filtered.append(tool)
    return filtered


def _filter_md_skills(
    *,
    md_skills_snapshot: list[dict[str, Any]],
    provider_type: str,
) -> list[dict[str, Any]]:
    provider_key = _normalize_text(provider_type).lower()
    filtered: list[dict[str, Any]] = []
    for entry in md_skills_snapshot:
        if not isinstance(entry, dict):
            continue
        if provider_key:
            metadata = entry.get("metadata")
            metadata_provider = ""
            if isinstance(metadata, dict):
                metadata_provider = _normalize_text(metadata.get("provider_type", "")).lower()
            entry_provider = _normalize_text(entry.get("provider", "")).lower()
            if provider_key not in {metadata_provider, entry_provider}:
                continue
        filtered.append(entry)
    return filtered


def _format_tool_line(tool: dict[str, Any]) -> str:
    tool_name = _normalize_text(tool.get("name", ""))
    description = _normalize_text(tool.get("description", ""))
    capability = _normalize_text(tool.get("capability_class", ""))
    suffix_parts = [part for part in [description, capability] if part]
    if suffix_parts:
        return f"- `{tool_name}`: {' | '.join(suffix_parts)}"
    return f"- `{tool_name}`"


def _build_skills_markdown(
    *,
    provider_label: str,
    md_skills: list[dict[str, Any]],
    tools_snapshot: list[dict[str, Any]],
) -> str:
    lines = [f"## {provider_label} Skills", ""]
    if not md_skills:
        lines.append("No matching skills found.")
        return "\n".join(lines)

    tool_index: dict[str, list[dict[str, Any]]] = {}
    for tool in tools_snapshot:
        if not isinstance(tool, dict):
            continue
        qualified_skill_name = _normalize_text(tool.get("qualified_skill_name", "")).lower()
        skill_name = _normalize_text(tool.get("skill_name", "")).lower()
        for key in {qualified_skill_name, skill_name}:
            if not key:
                continue
            tool_index.setdefault(key, []).append(tool)

    for entry in md_skills:
        qualified_name = _normalize_text(entry.get("qualified_name", ""))
        skill_name = _normalize_text(entry.get("name", ""))
        description = _normalize_text(entry.get("description", ""))
        lookup_keys = [qualified_name.lower(), skill_name.lower()]
        declared_tools: list[dict[str, Any]] = []
        seen_tools: set[str] = set()
        for key in lookup_keys:
            for tool in tool_index.get(key, []):
                tool_name = _normalize_text(tool.get("name", ""))
                if not tool_name or tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                declared_tools.append(tool)

        lines.append(f"- `{qualified_name or skill_name}`")
        if description:
            lines.append(f"  - {description}")
        if declared_tools:
            lines.append("  - tools:")
            for tool in declared_tools:
                lines.append(f"    - `{_normalize_text(tool.get('name', ''))}`")
        else:
            lines.append("  - tools: none")
        lines.append("")

    return "\n".join(lines).rstrip()


def _build_tools_markdown(
    *,
    provider_label: str,
    tools_snapshot: list[dict[str, Any]],
) -> str:
    lines = [f"## {provider_label} Tools", ""]
    if not tools_snapshot:
        lines.append("No matching tools found.")
        return "\n".join(lines)
    for tool in tools_snapshot:
        lines.append(_format_tool_line(tool))
    return "\n".join(lines)


def _build_groups_markdown(
    *,
    provider_label: str,
    tool_groups_snapshot: dict[str, list[str]],
    provider_tools: list[dict[str, Any]],
) -> str:
    lines = [f"## {provider_label} Tool Groups", ""]
    provider_tool_names = {
        _normalize_text(tool.get("name", ""))
        for tool in provider_tools
        if _normalize_text(tool.get("name", ""))
    }
    matched = []
    for group_id, members in sorted(tool_groups_snapshot.items()):
        filtered_members = [
            _normalize_text(name)
            for name in (members or [])
            if _normalize_text(name) in provider_tool_names
        ]
        if not filtered_members:
            continue
        matched.append((group_id, filtered_members))
    if not matched:
        lines.append("No matching tool groups found.")
        return "\n".join(lines)
    for group_id, members in matched:
        lines.append(f"- `{group_id}`: {', '.join(f'`{name}`' for name in members)}")
    return "\n".join(lines)


async def atlasclaw_catalog_query_tool(
    ctx: "RunContext[SkillDeps]",
    kind: str = "summary",
    provider_type: Optional[str] = None,
    group_id: Optional[str] = None,
) -> dict:
    """Query AtlasClaw runtime catalogs for tools, groups, and skills.

    Args:
        kind: One of summary, skills, tools, providers, or groups.
        provider_type: Optional provider filter, for example ``provider_type``.
        group_id: Optional group filter, for example ``group:example``.
    """
    extra = _coerce_context_payload(ctx)
    tools_snapshot = list(extra.get("tools_snapshot", []) or [])
    md_skills_snapshot = list(extra.get("md_skills_snapshot", []) or [])
    tool_groups_snapshot = dict(extra.get("tool_groups_snapshot", {}) or {})
    provider_contexts = _collect_provider_contexts(extra)

    normalized_kind = _normalize_kind(kind)
    normalized_provider_type = _normalize_text(provider_type).lower()
    normalized_group_id = _normalize_group_id(group_id or "")
    provider_label = _provider_label(normalized_provider_type, provider_contexts)

    filtered_tools = _filter_tools(
        tools_snapshot=tools_snapshot,
        provider_type=normalized_provider_type,
        group_id=normalized_group_id,
    )
    filtered_md_skills = _filter_md_skills(
        md_skills_snapshot=md_skills_snapshot,
        provider_type=normalized_provider_type,
    )

    if normalized_kind == "skills":
        text = _build_skills_markdown(
            provider_label=provider_label,
            md_skills=filtered_md_skills,
            tools_snapshot=filtered_tools,
        )
    elif normalized_kind == "tools":
        text = _build_tools_markdown(
            provider_label=provider_label,
            tools_snapshot=filtered_tools,
        )
    elif normalized_kind == "groups":
        text = _build_groups_markdown(
            provider_label=provider_label,
            tool_groups_snapshot=tool_groups_snapshot,
            provider_tools=filtered_tools,
        )
    elif normalized_kind == "providers":
        provider_lines = ["## Providers", ""]
        if provider_contexts:
            for provider_key, context in sorted(provider_contexts.items()):
                display_name = _provider_label(provider_key, provider_contexts)
                description = _normalize_text(context.get("description", ""))
                if description:
                    provider_lines.append(f"- `{provider_key}` ({display_name}): {description}")
                else:
                    provider_lines.append(f"- `{provider_key}` ({display_name})")
        else:
            provider_lines.append("No providers found.")
        text = "\n".join(provider_lines)
    else:
        section_lines = []
        section_lines.append(
            _build_skills_markdown(
                provider_label=provider_label,
                md_skills=filtered_md_skills,
                tools_snapshot=filtered_tools,
            )
        )
        section_lines.append("")
        section_lines.append(
            _build_tools_markdown(
                provider_label=provider_label,
                tools_snapshot=filtered_tools,
            )
        )
        text = "\n".join(section_lines).strip()

    return ToolResult.text(
        text,
        details={
            "kind": normalized_kind,
            "provider_type": normalized_provider_type,
            "group_id": normalized_group_id,
            "tool_count": len(filtered_tools),
            "skill_count": len(filtered_md_skills),
        },
    ).to_dict()
