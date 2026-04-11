# -*- coding: utf-8 -*-
"""Prompt-context helpers for AgentRunner."""

from __future__ import annotations

import inspect
import json
from typing import Any, Optional


def build_system_prompt(
    prompt_builder,
    session: Any,
    deps,
    *,
    agent: Optional[Any] = None,
    context_window_tokens: Optional[int] = None,
) -> str:
    """Build the runtime system prompt for the current session."""
    kwargs = {
        "session": session,
        "skills": collect_skills_snapshot(deps),
        "tools": collect_tools_snapshot(agent=agent, deps=deps),
        "md_skills": collect_md_skills_snapshot(deps),
        "target_md_skill": collect_target_md_skill(deps),
        "tool_policy": collect_tool_policy(deps),
        "user_info": deps.user_info,
        "provider_contexts": collect_provider_contexts(deps),
        "context_window_tokens": context_window_tokens,
    }
    build_fn = prompt_builder.build
    try:
        signature = inspect.signature(build_fn)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        accepted = set(signature.parameters.keys())
        kwargs = {key: value for key, value in kwargs.items() if key in accepted}

    return build_fn(**kwargs)


def collect_skills_snapshot(deps) -> list[dict]:
    """Read a structured skills snapshot from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    for key in ("skills_snapshot", "skills"):
        value = extra.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def collect_md_skills_snapshot(deps) -> list[dict]:
    """Read a Markdown-skill snapshot from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    for key in ("md_skills_snapshot", "md_skills"):
        value = extra.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def collect_target_md_skill(deps) -> Optional[dict]:
    """Read a targeted markdown-skill descriptor from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("target_md_skill")
    return value if isinstance(value, dict) else None


def collect_provider_contexts(deps) -> dict[str, dict]:
    """Collect provider LLM contexts from ServiceProviderRegistry."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    registry = extra.get("_service_provider_registry")
    if registry is None:
        return {}

    get_contexts = getattr(registry, "get_all_provider_contexts", None)
    if get_contexts is None:
        return {}

    try:
        contexts = get_contexts()
        result = {}
        for provider_type, ctx in contexts.items():
            if hasattr(ctx, "__dict__"):
                result[provider_type] = {
                    "display_name": getattr(ctx, "display_name", ""),
                    "description": getattr(ctx, "description", ""),
                    "keywords": getattr(ctx, "keywords", []),
                    "capabilities": getattr(ctx, "capabilities", []),
                    "use_when": getattr(ctx, "use_when", []),
                    "avoid_when": getattr(ctx, "avoid_when", []),
                }
            elif isinstance(ctx, dict):
                result[provider_type] = ctx
        return result
    except Exception:
        return {}


def collect_tool_policy(deps) -> Optional[dict]:
    """Read a structured tool policy from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("tool_policy")
    return value if isinstance(value, dict) else None


def collect_tool_groups_snapshot(deps) -> dict[str, list[str]]:
    """Read normalized tool-group snapshot from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("tool_groups_snapshot")
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, members in value.items():
        group_id = str(key or "").strip()
        if not group_id:
            continue
        if isinstance(members, list):
            tool_names = [str(item or "").strip() for item in members if str(item or "").strip()]
        else:
            tool_names = []
        normalized[group_id] = tool_names
    return normalized


def collect_tools_snapshot(*, agent: Any, deps=None) -> list[dict]:
    """Collect tool name and description pairs for prompt building."""
    extra = getattr(deps, "extra", {}) if deps is not None else {}
    normalized_extra_tools: list[dict] = []
    tools_snapshot_authoritative = False
    if isinstance(extra, dict):
        tools_snapshot_authoritative = bool(extra.get("tools_snapshot_authoritative"))
        extra_tools = extra.get("tools_snapshot")
        if isinstance(extra_tools, list):
            for item in extra_tools:
                if not isinstance(item, dict):
                    continue
                payload = _normalize_snapshot_tool(item)
                if payload:
                    normalized_extra_tools.append(payload)

    skills_snapshot = collect_skills_snapshot(deps) if deps is not None else []
    md_skills_snapshot = collect_md_skills_snapshot(deps) if deps is not None else []
    skill_meta_index = _build_skill_metadata_index(
        skills_snapshot,
        md_skills_snapshot,
    )
    tools: list[dict] = []
    seen_names: set[str] = set()

    def _append_tool_record(
        *,
        name: Any,
        description: Any,
        provider_type: Any = None,
        category: Any = None,
        source: Any = None,
        group_ids: Any = None,
        capability_class: Any = None,
        priority: Any = None,
        skill_name: Any = None,
        qualified_skill_name: Any = None,
        parameters_schema: Any = None,
        planner_visibility: Any = None,
        aliases: Any = None,
        keywords: Any = None,
        use_when: Any = None,
        avoid_when: Any = None,
        result_mode: Any = None,
    ) -> None:
        normalized_name = str(name or "").strip()
        if not normalized_name or normalized_name in seen_names:
            return
        normalized_description = str(description or "").strip()
        indexed_meta = skill_meta_index.get(normalized_name, {})
        normalized_provider_type = _normalize_optional_text(
            provider_type,
            indexed_meta.get("provider_type", ""),
        )
        normalized_category = _normalize_optional_text(
            category,
            indexed_meta.get("category", ""),
        )
        normalized_source = _normalize_optional_text(
            source,
            indexed_meta.get("source", ""),
        )
        resolved_group_ids = _normalize_group_ids(
            group_ids
            if group_ids is not None
            else indexed_meta.get("group_ids", []),
        )
        tool_record = {
            "name": normalized_name,
            "description": normalized_description,
        }
        if normalized_provider_type:
            tool_record["provider_type"] = normalized_provider_type
        if normalized_category:
            tool_record["category"] = normalized_category
        if normalized_source:
            tool_record["source"] = normalized_source
        if resolved_group_ids:
            tool_record["group_ids"] = resolved_group_ids
        resolved_priority = _normalize_priority(priority if priority is not None else indexed_meta.get("priority"))
        if resolved_priority is not None:
            tool_record["priority"] = resolved_priority
        normalized_skill_name = _normalize_optional_text(
            skill_name,
            indexed_meta.get("skill_name", ""),
        )
        normalized_qualified_skill_name = _normalize_optional_text(
            qualified_skill_name,
            indexed_meta.get("qualified_skill_name", ""),
        )
        if normalized_skill_name:
            tool_record["skill_name"] = normalized_skill_name
        if normalized_qualified_skill_name:
            tool_record["qualified_skill_name"] = normalized_qualified_skill_name
        normalized_parameters_schema = _normalize_parameters_schema(
            parameters_schema
            if parameters_schema is not None
            else indexed_meta.get("parameters_schema", {}),
        )
        if normalized_parameters_schema:
            tool_record["parameters_schema"] = normalized_parameters_schema
        normalized_planner_visibility = _normalize_optional_text(
            planner_visibility if planner_visibility is not None else indexed_meta.get("planner_visibility", ""),
        )
        if normalized_planner_visibility:
            tool_record["planner_visibility"] = normalized_planner_visibility
        normalized_aliases = _normalize_string_list(
            aliases if aliases is not None else indexed_meta.get("aliases", [])
        )
        if normalized_aliases:
            tool_record["aliases"] = normalized_aliases
        normalized_keywords = _normalize_string_list(
            keywords if keywords is not None else indexed_meta.get("keywords", [])
        )
        if normalized_keywords:
            tool_record["keywords"] = normalized_keywords
        normalized_use_when = _normalize_string_list(
            use_when if use_when is not None else indexed_meta.get("use_when", [])
        )
        if normalized_use_when:
            tool_record["use_when"] = normalized_use_when
        normalized_avoid_when = _normalize_string_list(
            avoid_when if avoid_when is not None else indexed_meta.get("avoid_when", [])
        )
        if normalized_avoid_when:
            tool_record["avoid_when"] = normalized_avoid_when
        normalized_result_mode = _normalize_optional_text(
            result_mode if result_mode is not None else indexed_meta.get("result_mode", ""),
        )
        if normalized_result_mode:
            tool_record["result_mode"] = normalized_result_mode

        explicit_capability_class = _normalize_optional_text(
            capability_class,
            indexed_meta.get("capability_class", ""),
        )
        inferred_capability_class = _infer_capability_class(
            name=normalized_name,
            description=normalized_description,
            provider_type=normalized_provider_type,
            category=normalized_category,
        )
        capability = explicit_capability_class or inferred_capability_class
        if capability:
            tool_record["capability_class"] = capability

        tools.append(tool_record)
        seen_names.add(normalized_name)

    for tool in normalized_extra_tools:
        _append_tool_record(
            name=tool.get("name"),
            description=tool.get("description", ""),
            provider_type=tool.get("provider_type"),
            category=tool.get("category"),
            source=tool.get("source"),
            group_ids=tool.get("group_ids"),
            capability_class=tool.get("capability_class"),
            priority=tool.get("priority"),
            skill_name=tool.get("skill_name"),
            qualified_skill_name=tool.get("qualified_skill_name"),
            parameters_schema=tool.get("parameters_schema"),
            planner_visibility=tool.get("planner_visibility"),
            aliases=tool.get("aliases"),
            keywords=tool.get("keywords"),
            use_when=tool.get("use_when"),
            avoid_when=tool.get("avoid_when"),
            result_mode=tool.get("result_mode"),
        )

    if tools_snapshot_authoritative and tools:
        return tools

    for tool in _iter_tool_entries(agent):
        if isinstance(tool, dict):
            name = tool.get("name")
            description = tool.get("description", "")
            provider_type = tool.get("provider_type")
            category = tool.get("category")
            source = tool.get("source")
            group_ids = tool.get("group_ids")
            capability_class = tool.get("capability_class")
            priority = tool.get("priority")
            parameters_schema = tool.get("parameters_schema")
            planner_visibility = tool.get("planner_visibility")
            aliases = tool.get("aliases")
            keywords = tool.get("keywords")
            use_when = tool.get("use_when")
            avoid_when = tool.get("avoid_when")
            result_mode = tool.get("result_mode")
        else:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            description = getattr(tool, "description", "") or getattr(tool, "__doc__", "") or ""
            provider_type = (
                getattr(tool, "provider_type", None)
                or getattr(getattr(tool, "metadata", None), "provider_type", None)
            )
            category = (
                getattr(tool, "category", None)
                or getattr(getattr(tool, "metadata", None), "category", None)
            )
            source = (
                getattr(tool, "source", None)
                or getattr(getattr(tool, "metadata", None), "source", None)
            )
            group_ids = (
                getattr(tool, "group_ids", None)
                or getattr(getattr(tool, "metadata", None), "group_ids", None)
            )
            capability_class = (
                getattr(tool, "capability_class", None)
                or getattr(getattr(tool, "metadata", None), "capability_class", None)
            )
            priority = (
                getattr(tool, "priority", None)
                or getattr(getattr(tool, "metadata", None), "priority", None)
            )
            parameters_schema = (
                getattr(tool, "parameters_schema", None)
                or getattr(getattr(tool, "metadata", None), "parameters_schema", None)
                or getattr(tool, "parameters_json_schema", None)
            )
            planner_visibility = (
                getattr(tool, "planner_visibility", None)
                or getattr(getattr(tool, "metadata", None), "planner_visibility", None)
            )
            aliases = (
                getattr(tool, "aliases", None)
                or getattr(getattr(tool, "metadata", None), "aliases", None)
            )
            keywords = (
                getattr(tool, "keywords", None)
                or getattr(getattr(tool, "metadata", None), "keywords", None)
            )
            use_when = (
                getattr(tool, "use_when", None)
                or getattr(getattr(tool, "metadata", None), "use_when", None)
            )
            avoid_when = (
                getattr(tool, "avoid_when", None)
                or getattr(getattr(tool, "metadata", None), "avoid_when", None)
            )
            result_mode = (
                getattr(tool, "result_mode", None)
                or getattr(getattr(tool, "metadata", None), "result_mode", None)
            )
        _append_tool_record(
            name=name,
            description=description,
            provider_type=provider_type,
            category=category,
            source=source,
            group_ids=group_ids,
            capability_class=capability_class,
            priority=priority,
            parameters_schema=parameters_schema,
            planner_visibility=planner_visibility,
            aliases=aliases,
            keywords=keywords,
            use_when=use_when,
            avoid_when=avoid_when,
            result_mode=result_mode,
        )

    # Fallback/merge path: when pydantic-ai internal tool exposure is partial or missing,
    # recover from the runtime skills snapshot so capability matching remains stable.
    for item in skills_snapshot:
        if not isinstance(item, dict):
            continue
        _append_tool_record(
            name=item.get("name"),
            description=item.get("description", ""),
            provider_type=item.get("provider_type"),
            category=item.get("category"),
            source=item.get("source"),
            group_ids=item.get("group_ids", []),
            capability_class=item.get("capability_class"),
            priority=item.get("priority"),
            skill_name=item.get("skill_name"),
            qualified_skill_name=item.get("qualified_skill_name"),
            parameters_schema=item.get("parameters_schema"),
            planner_visibility=item.get("planner_visibility"),
            aliases=item.get("aliases"),
            keywords=item.get("keywords"),
            use_when=item.get("use_when"),
            avoid_when=item.get("avoid_when"),
            result_mode=item.get("result_mode"),
        )

    return tools


def _iter_tool_entries(agent: Any):
    """Iterate tool entries from legacy and current pydantic_ai agent shapes."""
    raw_tools = getattr(agent, "tools", None)
    if isinstance(raw_tools, dict):
        for tool in raw_tools.values():
            yield tool
    elif isinstance(raw_tools, (list, tuple, set)):
        for tool in raw_tools:
            yield tool

    # pydantic_ai (>=1.5x) exposes tools via toolsets rather than agent.tools
    toolsets = getattr(agent, "toolsets", None)
    if isinstance(toolsets, (list, tuple)):
        for toolset in toolsets:
            toolset_tools = getattr(toolset, "tools", None)
            if isinstance(toolset_tools, dict):
                for tool in toolset_tools.values():
                    yield tool
            elif isinstance(toolset_tools, (list, tuple, set)):
                for tool in toolset_tools:
                    yield tool

    # Defensive fallback for internal function-toolset storage.
    function_toolset = getattr(agent, "_function_toolset", None)
    internal_tools = getattr(function_toolset, "tools", None)
    if isinstance(internal_tools, dict):
        for tool in internal_tools.values():
            yield tool


def _build_skill_metadata_index(
    skills_snapshot: list[dict],
    md_skills_snapshot: list[dict],
) -> dict[str, dict[str, Any]]:
    """Build a per-tool metadata index from skill snapshots."""
    index: dict[str, dict[str, Any]] = {}
    for item in skills_snapshot:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        index[name] = {
            "provider_type": _normalize_optional_text(item.get("provider_type", "")),
            "category": _normalize_optional_text(item.get("category", "")),
            "source": _normalize_optional_text(item.get("source", "")),
            "group_ids": _normalize_group_ids(item.get("group_ids", [])),
            "capability_class": str(item.get("capability_class", "")).strip(),
            "priority": _normalize_priority(item.get("priority")),
            "skill_name": _normalize_optional_text(item.get("skill_name", "")),
            "qualified_skill_name": _normalize_optional_text(item.get("qualified_skill_name", "")),
            "parameters_schema": _normalize_parameters_schema(item.get("parameters_schema", {})),
            "planner_visibility": _normalize_optional_text(item.get("planner_visibility", "")),
            "aliases": _normalize_string_list(item.get("aliases", [])),
            "keywords": _normalize_string_list(item.get("keywords", [])),
            "use_when": _normalize_string_list(item.get("use_when", [])),
            "avoid_when": _normalize_string_list(item.get("avoid_when", [])),
            "result_mode": _normalize_optional_text(item.get("result_mode", "")),
        }

    for entry in md_skills_snapshot:
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        provider_type = _normalize_optional_text(
            metadata.get("provider_type", ""),
            entry.get("provider", ""),
        )
        category = _normalize_optional_text(metadata.get("category", "skill")) or "skill"
        for tool_name in _extract_md_tool_names(entry):
            index[tool_name] = {
                "provider_type": provider_type,
                "category": category,
                "source": "provider" if provider_type else "md_skill",
                "group_ids": _normalize_group_ids(metadata.get("group_ids", [])),
                "capability_class": _normalize_optional_text(metadata.get("capability_class", "")),
                "priority": _normalize_priority(metadata.get("priority")),
                "skill_name": _normalize_optional_text(entry.get("name", "")),
                "qualified_skill_name": _normalize_optional_text(entry.get("qualified_name", "")),
                "planner_visibility": _normalize_optional_text(metadata.get("planner_visibility", "")),
                "aliases": _normalize_string_list(
                    [entry.get("qualified_name", ""), metadata.get("tool_aliases", [])]
                ),
                "keywords": _normalize_string_list(metadata.get("triggers", [])),
                "use_when": _normalize_string_list(metadata.get("use_when", [])),
                "avoid_when": _normalize_string_list(metadata.get("avoid_when", [])),
                "result_mode": _normalize_optional_text(metadata.get("result_mode", "")),
            }

    return index


def _extract_md_tool_names(entry: dict) -> list[str]:
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    names: list[str] = []
    single_name = str(metadata.get("tool_name", "")).strip()
    if single_name:
        names.append(single_name)
    for key, value in metadata.items():
        key_str = str(key)
        if not key_str.startswith("tool_") or not key_str.endswith("_name"):
            continue
        tool_name = str(value or "").strip()
        if tool_name:
            names.append(tool_name)

    fallback_name = str(entry.get("name", "")).strip()
    if fallback_name and fallback_name not in names:
        names.append(fallback_name)
    return names


def _infer_capability_class(
    *,
    name: str,
    description: str,
    provider_type: str,
    category: str,
) -> str:
    lowered_name = (name or "").strip().lower()
    lowered_description = (description or "").strip().lower()
    lowered_category = (category or "").strip().lower()
    lowered_provider = (provider_type or "").strip().lower()

    if lowered_provider and lowered_provider != "none":
        return f"provider:{lowered_provider}"
    if lowered_name in {"web_search", "web_fetch"}:
        return lowered_name
    if lowered_name == "openmeteo_weather":
        return "weather"
    if lowered_name == "browser":
        return "browser"
    if "jira" in lowered_name or "jira" in lowered_description:
        return "provider:jira"
    if "provider:" in lowered_description or lowered_category.startswith("provider"):
        return "provider:generic"
    if "skill" in lowered_category or lowered_category == "md_skill":
        return "skill"
    if "skill" in lowered_description and lowered_name not in {"web_search", "web_fetch"}:
        return "skill"
    return ""


def _normalize_snapshot_tool(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "name": str(item.get("name", "")).strip(),
        "description": str(item.get("description", "")).strip(),
    }
    if not normalized["name"]:
        return {}

    provider_type = _normalize_optional_text(item.get("provider_type", ""))
    category = _normalize_optional_text(item.get("category", ""))
    source = _normalize_optional_text(item.get("source", ""))
    capability_class = _normalize_optional_text(item.get("capability_class", ""))
    skill_name = _normalize_optional_text(item.get("skill_name", ""))
    qualified_skill_name = _normalize_optional_text(item.get("qualified_skill_name", ""))
    group_ids = _normalize_group_ids(item.get("group_ids", []))
    priority = _normalize_priority(item.get("priority"))
    planner_visibility = _normalize_optional_text(item.get("planner_visibility", ""))
    aliases = _normalize_string_list(item.get("aliases", []))
    keywords = _normalize_string_list(item.get("keywords", []))
    use_when = _normalize_string_list(item.get("use_when", []))
    avoid_when = _normalize_string_list(item.get("avoid_when", []))
    result_mode = _normalize_optional_text(item.get("result_mode", ""))

    if provider_type:
        normalized["provider_type"] = provider_type
    if category:
        normalized["category"] = category
    if source:
        normalized["source"] = source
    if capability_class:
        normalized["capability_class"] = capability_class
    if skill_name:
        normalized["skill_name"] = skill_name
    if qualified_skill_name:
        normalized["qualified_skill_name"] = qualified_skill_name
    if group_ids:
        normalized["group_ids"] = group_ids
    if priority is not None:
        normalized["priority"] = priority
    if planner_visibility:
        normalized["planner_visibility"] = planner_visibility
    if aliases:
        normalized["aliases"] = aliases
    if keywords:
        normalized["keywords"] = keywords
    if use_when:
        normalized["use_when"] = use_when
    if avoid_when:
        normalized["avoid_when"] = avoid_when
    if result_mode:
        normalized["result_mode"] = result_mode
    parameters_schema = _normalize_parameters_schema(item.get("parameters_schema", {}))
    if parameters_schema:
        normalized["parameters_schema"] = parameters_schema
    return normalized


def _normalize_string_list(values: Any) -> list[str]:
    if isinstance(values, list):
        raw_values = values
    elif values:
        raw_values = [values]
    else:
        raw_values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        if isinstance(value, list):
            nested = _normalize_string_list(value)
            for item in nested:
                if item in seen:
                    continue
                seen.add(item)
                normalized.append(item)
            continue
        item = str(value or "").strip()
        if item.lower() == "none":
            continue
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _normalize_optional_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text.lower() == "none":
            continue
        return text
    return ""


def _normalize_group_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        if not normalized.startswith("group:"):
            normalized = f"group:{normalized}"
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_priority(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_parameters_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        payload = value.strip()
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except Exception:
            return {}
    if not isinstance(value, dict):
        return {}
    schema_type = str(value.get("type", "") or "").strip().lower()
    if schema_type and schema_type != "object":
        return {}
    properties = value.get("properties")
    if not isinstance(properties, dict) or not properties:
        return {}
    normalized: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    required = value.get("required")
    if isinstance(required, list):
        normalized["required"] = [str(item) for item in required if str(item).strip()]
    return normalized
