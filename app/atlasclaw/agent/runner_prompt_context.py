# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Prompt-context helpers for AgentRunner."""

from __future__ import annotations

import inspect
import json
from typing import Any, Optional

from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import (
    is_silent_backend_tool,
    normalize_tool_description,
    normalize_tool_result_mode,
)
from app.atlasclaw.tools.catalog import STANDARD_SKILL_RUNTIME_TOOL_NAMES


def build_system_prompt(
    prompt_builder,
    session: Any,
    deps,
    *,
    agent: Optional[Any] = None,
    context_window_tokens: Optional[int] = None,
    prompt_mode: Optional[Any] = None,
) -> str:
    """Build the runtime system prompt for the current session."""
    kwargs = {
        "session": session,
        "skills": collect_skills_snapshot(deps),
        "tools": collect_tools_snapshot(agent=agent, deps=deps),
        "md_skills": collect_md_skills_snapshot(deps),
        "capability_index": collect_capability_index_snapshot(agent=agent, deps=deps),
        "target_md_skill": collect_target_md_skill(deps),
        "tool_policy": collect_tool_policy(deps),
        "user_info": deps.user_info,
        "provider_contexts": collect_provider_contexts(deps),
        "provider_auth_diagnostics": collect_provider_auth_diagnostics(deps),
        "context_window_tokens": context_window_tokens,
        "mode_override": prompt_mode,
        "transcript_skill_hint": collect_transcript_skill_hint(deps),
        "current_follow_up_context": collect_current_follow_up_context(deps),
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


def collect_capability_index_snapshot(*, agent: Any, deps) -> list[dict]:
    """Build a compact capability index snapshot for prompt rendering."""
    capability_index: list[dict] = []
    tool_names: set[str] = set()
    provider_capabilities: dict[str, dict[str, Any]] = {}

    for item in collect_md_skills_snapshot(deps):
        name = str(item.get("qualified_name") or item.get("name") or "unknown").strip()
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        capability_index.append(
            {
                "capability_id": _build_capability_id("md_skill", name or "unknown"),
                "kind": "md_skill",
                "name": name or "unknown",
                "description": _build_md_skill_capability_description(item),
                "locator": str(item.get("file_path", "") or "").strip() or name or "unknown",
                "provider_type": _normalize_optional_text(
                    metadata.get("provider_type", ""),
                    item.get("provider", ""),
                    _qualified_name_provider(name),
                ),
                "artifact_types": _infer_artifact_types(
                    name=name,
                    description=str(item.get("description", "") or "").strip(),
                    capability_class=_normalize_optional_text(metadata.get("capability_class", "")),
                    metadata=metadata,
                ),
                "declared_tool_names": _extract_md_tool_names(item),
                "declares_executable_tools": _metadata_declares_executable_tool(metadata),
            }
        )

    for item in collect_tools_snapshot(agent=agent, deps=deps):
        routing_visibility = _normalize_optional_text(
            item.get("routing_visibility", item.get("planner_visibility", ""))
        )
        if routing_visibility in {"hidden", "internal"}:
            continue
        tool_name = str(item.get("name") or "unknown").strip() or "unknown"
        provider_type = _normalize_optional_text(item.get("provider_type", ""))
        if provider_type and provider_type.lower() != "none":
            provider_record = provider_capabilities.setdefault(
                provider_type,
                {
                    "capability_id": _build_capability_id("provider", provider_type),
                    "kind": "provider",
                    "name": provider_type,
                    "description": "",
                    "locator": provider_type,
                    "provider_type": provider_type,
                    "artifact_types": [],
                    "declared_tool_names": [],
                },
            )
            provider_record["declared_tool_names"].append(tool_name)
            tool_description = str(item.get("description", "") or "").strip()
            if tool_description and not provider_record["description"]:
                provider_record["description"] = tool_description
        tool_names.add(tool_name)
        capability_index.append(
            {
                "capability_id": _build_capability_id("tool", tool_name),
                "kind": "tool",
                "name": tool_name,
                "description": str(item.get("description", "") or "").strip(),
                "locator": _format_tool_locator(item),
                "provider_type": provider_type,
                "artifact_types": _infer_artifact_types(
                    name=tool_name,
                    description=str(item.get("description", "") or "").strip(),
                    capability_class=_normalize_optional_text(item.get("capability_class", "")),
                    metadata=item,
                ),
                "declared_tool_names": [tool_name],
            }
        )

    for provider_type in sorted(provider_capabilities.keys()):
        provider_record = provider_capabilities[provider_type]
        provider_record["declared_tool_names"] = sorted(
            {
                str(name).strip()
                for name in provider_record.get("declared_tool_names", [])
                if str(name).strip()
            }
        )
        if not provider_record["description"]:
            provider_record["description"] = "Authorized provider capability."
        capability_index.append(provider_record)

    for item in collect_skills_snapshot(deps):
        skill_name = str(item.get("name") or "unknown").strip() or "unknown"
        if skill_name in tool_names:
            continue
        capability_index.append(
            {
                "capability_id": _build_capability_id("skill", skill_name),
                "kind": "skill",
                "name": skill_name,
                "description": str(item.get("description", "") or "").strip(),
                "locator": (
                    str(item.get("location") or item.get("category") or "built-in").strip()
                    or "built-in"
                ),
                "provider_type": _normalize_optional_text(item.get("provider_type", "")),
                "artifact_types": _infer_artifact_types(
                    name=skill_name,
                    description=str(item.get("description", "") or "").strip(),
                    capability_class=_normalize_optional_text(item.get("capability_class", "")),
                    metadata=item,
                ),
                "declared_tool_names": _normalize_string_list(
                    [item.get("qualified_skill_name", ""), item.get("skill_name", "")]
                ),
            }
        )

    return capability_index


def _build_capability_id(kind: str, name: str) -> str:
    normalized_kind = str(kind or "").strip().lower()
    normalized_name = str(name or "").strip()
    if normalized_kind == "tool":
        prefix = "tool"
    elif normalized_kind == "provider":
        prefix = "provider"
    elif normalized_kind in {"capability", "group"}:
        prefix = normalized_kind
    else:
        prefix = "skill"
    return f"{prefix}:{normalized_name or 'unknown'}"


def _infer_artifact_types(
    *,
    name: str,
    description: str,
    capability_class: str,
    metadata: dict[str, Any],
) -> list[str]:
    artifact_types: list[str] = []
    explicit_capability = str(capability_class or "").strip().lower()
    if explicit_capability.startswith("artifact:"):
        artifact_suffix = explicit_capability.split("artifact:", 1)[-1].strip()
        if artifact_suffix:
            artifact_types.append(artifact_suffix)

    explicit_values: list[Any] = []
    if isinstance(metadata, dict):
        explicit_values.append(metadata.get("artifact_types"))
        for key, value in metadata.items():
            if not str(key or "").strip().lower().endswith("capability_class"):
                continue
            explicit_values.append(value)

    for value in explicit_values:
        if isinstance(value, str):
            normalized_value = value.strip().lower()
            if normalized_value.startswith("artifact:"):
                artifact_suffix = normalized_value.split("artifact:", 1)[-1].strip()
                if artifact_suffix:
                    artifact_types.append(artifact_suffix)
            elif normalized_value:
                artifact_types.append(normalized_value)
            continue
        if isinstance(value, list):
            for item in value:
                normalized_item = str(item or "").strip().lower()
                if not normalized_item:
                    continue
                if normalized_item.startswith("artifact:"):
                    artifact_suffix = normalized_item.split("artifact:", 1)[-1].strip()
                    if artifact_suffix:
                        artifact_types.append(artifact_suffix)
                    continue
                artifact_types.append(normalized_item)
    return _normalize_string_list(artifact_types)


def collect_target_md_skill(deps) -> Optional[dict]:
    """Read a targeted markdown-skill descriptor from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("target_md_skill")
    return value if isinstance(value, dict) else None


def collect_transcript_skill_hint(deps) -> Optional[str]:
    """Read the transcript-based skill continuation hint from `deps.extra`.

    This is a non-binding hint produced by transcript analysis. It is
    injected into the prompt as advisory context - the LLM decides whether
    to follow it.
    """
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    hint = extra.get("transcript_skill_continuation_hint")
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    return None


def collect_current_follow_up_context(deps) -> Optional[str]:
    """Read contextualized current follow-up text for low-information replies."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("current_follow_up_context")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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


def collect_provider_auth_diagnostics(deps) -> dict[str, dict]:
    """Read request-scoped provider authentication diagnostics from `deps.extra`."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("provider_auth_diagnostics")
    return value if isinstance(value, dict) else {}


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
        routing_visibility: Any = None,
        aliases: Any = None,
        keywords: Any = None,
        use_when: Any = None,
        avoid_when: Any = None,
        result_mode: Any = None,
        live_data: Any = None,
        browser_interaction: Any = None,
        public_web: Any = None,
        success_contract: Any = None,
    ) -> None:
        normalized_name = str(name or "").strip()
        if not normalized_name or normalized_name in seen_names:
            return
        if (
            normalized_name in STANDARD_SKILL_RUNTIME_TOOL_NAMES
            and not (
                isinstance(extra, dict)
                and bool(extra.get("standard_skill_runtime_tools_visible"))
            )
        ):
            return
        indexed_meta = skill_meta_index.get(normalized_name, {})
        silent_backend = is_silent_backend_tool(
            {
                "description": description,
                "result_mode": result_mode if result_mode is not None else indexed_meta.get("result_mode", ""),
                "routing_visibility": (
                    routing_visibility
                    if routing_visibility is not None
                    else indexed_meta.get("routing_visibility", indexed_meta.get("planner_visibility", ""))
                ),
            }
        )
        normalized_description = normalize_tool_description(
            description=description,
            silent_backend=silent_backend,
        )
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
        normalized_routing_visibility = _normalize_optional_text(
            routing_visibility if routing_visibility is not None else indexed_meta.get("routing_visibility", ""),
            indexed_meta.get("planner_visibility", ""),
        )
        if normalized_routing_visibility:
            tool_record["routing_visibility"] = normalized_routing_visibility
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
        if silent_backend:
            normalized_result_mode = normalize_tool_result_mode(
                {
                    "description": normalized_description,
                    "result_mode": normalized_result_mode,
                    "routing_visibility": normalized_routing_visibility,
                }
            )
        if normalized_result_mode:
            tool_record["result_mode"] = normalized_result_mode
        normalized_success_contract = _normalize_metadata_object(
            success_contract if success_contract is not None else indexed_meta.get("success_contract", {})
        )
        if normalized_success_contract:
            tool_record["success_contract"] = normalized_success_contract
        if bool(live_data if live_data is not None else indexed_meta.get("live_data", False)):
            tool_record["live_data"] = True
        if bool(
            browser_interaction
            if browser_interaction is not None
            else indexed_meta.get("browser_interaction", False)
        ):
            tool_record["browser_interaction"] = True
        if bool(public_web if public_web is not None else indexed_meta.get("public_web", False)):
            tool_record["public_web"] = True

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
            routing_visibility=tool.get("routing_visibility", tool.get("planner_visibility")),
            aliases=tool.get("aliases"),
            keywords=tool.get("keywords"),
            use_when=tool.get("use_when"),
            avoid_when=tool.get("avoid_when"),
            result_mode=tool.get("result_mode"),
            live_data=tool.get("live_data"),
            browser_interaction=tool.get("browser_interaction"),
            public_web=tool.get("public_web"),
            success_contract=tool.get("success_contract"),
        )

    if tools_snapshot_authoritative:
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
            routing_visibility = tool.get("routing_visibility", tool.get("planner_visibility"))
            aliases = tool.get("aliases")
            keywords = tool.get("keywords")
            use_when = tool.get("use_when")
            avoid_when = tool.get("avoid_when")
            result_mode = tool.get("result_mode")
            success_contract = tool.get("success_contract")
            live_data = tool.get("live_data")
            browser_interaction = tool.get("browser_interaction")
            public_web = tool.get("public_web")
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
            routing_visibility = (
                getattr(tool, "routing_visibility", None)
                or getattr(getattr(tool, "metadata", None), "routing_visibility", None)
                or getattr(tool, "planner_visibility", None)
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
            success_contract = (
                getattr(tool, "success_contract", None)
                or getattr(getattr(tool, "metadata", None), "success_contract", None)
            )
            live_data = (
                getattr(tool, "live_data", None)
                or getattr(getattr(tool, "metadata", None), "live_data", None)
            )
            browser_interaction = (
                getattr(tool, "browser_interaction", None)
                or getattr(getattr(tool, "metadata", None), "browser_interaction", None)
            )
            public_web = (
                getattr(tool, "public_web", None)
                or getattr(getattr(tool, "metadata", None), "public_web", None)
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
            routing_visibility=routing_visibility,
            aliases=aliases,
            keywords=keywords,
            use_when=use_when,
            avoid_when=avoid_when,
            result_mode=result_mode,
            live_data=live_data,
            browser_interaction=browser_interaction,
            public_web=public_web,
            success_contract=success_contract,
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
            routing_visibility=item.get("routing_visibility", item.get("planner_visibility")),
            aliases=item.get("aliases"),
            keywords=item.get("keywords"),
            use_when=item.get("use_when"),
            avoid_when=item.get("avoid_when"),
            result_mode=item.get("result_mode"),
            live_data=item.get("live_data"),
            browser_interaction=item.get("browser_interaction"),
            public_web=item.get("public_web"),
            success_contract=item.get("success_contract"),
        )

    return tools


def _format_tool_locator(tool: dict[str, Any]) -> str:
    """Render a compact tool signature for capability-index rendering."""
    name = str(tool.get("name", "unknown") or "unknown").strip() or "unknown"
    parameters_schema = tool.get("parameters_schema", {})
    if not isinstance(parameters_schema, dict):
        return name
    properties = parameters_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return name
    required = {
        str(item).strip()
        for item in (parameters_schema.get("required", []) or [])
        if str(item).strip()
    }
    parts: list[str] = []
    for param_name in properties.keys():
        normalized_name = str(param_name or "").strip()
        if not normalized_name:
            continue
        suffix = "" if normalized_name in required else "?"
        parts.append(f"{normalized_name}{suffix}")
    if not parts:
        return name
    return f"{name}({', '.join(parts)})"


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
            "routing_visibility": _normalize_optional_text(
                item.get("routing_visibility", ""),
                item.get("planner_visibility", ""),
            ),
            "aliases": _normalize_string_list(item.get("aliases", [])),
            "keywords": _normalize_string_list(item.get("keywords", [])),
            "use_when": _normalize_string_list(item.get("use_when", [])),
            "avoid_when": _normalize_string_list(item.get("avoid_when", [])),
            "result_mode": _normalize_optional_text(item.get("result_mode", "")),
            "success_contract": _normalize_metadata_object(item.get("success_contract", {})),
            "live_data": bool(item.get("live_data", False)),
            "browser_interaction": bool(item.get("browser_interaction", False)),
            "public_web": bool(item.get("public_web", False)),
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
                "routing_visibility": _normalize_optional_text(
                    metadata.get("routing_visibility", ""),
                    metadata.get("planner_visibility", ""),
                ),
                "aliases": _normalize_string_list(
                    [entry.get("qualified_name", ""), metadata.get("tool_aliases", [])]
                ),
                "keywords": _normalize_string_list(metadata.get("triggers", [])),
                "use_when": _normalize_string_list(metadata.get("use_when", [])),
                "avoid_when": _normalize_string_list(metadata.get("avoid_when", [])),
                "result_mode": _normalize_optional_text(metadata.get("result_mode", "")),
                "success_contract": _normalize_metadata_object(
                    _extract_md_tool_metadata_dict(
                        entry,
                        tool_name=tool_name,
                        key_suffix="success_contract",
                    )
                ),
                "live_data": bool(metadata.get("live_data", False)),
                "browser_interaction": bool(metadata.get("browser_interaction", False)),
                "public_web": bool(metadata.get("public_web", False)),
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
    return _normalize_string_list(names)


def _build_md_skill_capability_description(entry: dict[str, Any]) -> str:
    """Compose a compact capability description with raw provider-declared hints."""
    description = str(entry.get("description", "") or "").strip()
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    use_when = _collect_md_skill_hint_lines(metadata.get("use_when", []), max_items=2)
    avoid_when = _collect_md_skill_hint_lines(metadata.get("avoid_when", []), max_items=1)

    hint_sections: list[str] = []
    if use_when:
        hint_sections.append("use when " + "; ".join(use_when))
    if avoid_when:
        hint_sections.append("avoid when " + "; ".join(avoid_when))

    if not hint_sections:
        return description
    if description:
        return f"{description} Routing hints: {'; '.join(hint_sections)}."
    return f"Routing hints: {'; '.join(hint_sections)}."


def _collect_md_skill_hint_lines(values: Any, *, max_items: int) -> list[str]:
    normalized = _normalize_string_list(values)
    if not normalized or max_items <= 0:
        return []

    selected: list[str] = []
    seen: set[str] = set()
    for text in normalized:
        cleaned = _trim_md_skill_hint_line(text)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        selected.append(cleaned)
        if len(selected) >= max_items:
            break
    return selected


def _trim_md_skill_hint_line(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) > 140:
        text = text[:137].rstrip(" ,;:-") + "..."
    return text.rstrip(" .;:")


def _metadata_declares_executable_tool(metadata: dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("tool_name", "") or "").strip() and str(
        metadata.get("entrypoint", "") or ""
    ).strip():
        return True

    ids: set[str] = set()
    for key in metadata.keys():
        key_text = str(key or "")
        if key_text.startswith("tool_") and key_text.endswith("_name"):
            ids.add(key_text[len("tool_") : -len("_name")])
        elif key_text.startswith("tool_") and key_text.endswith("_entrypoint"):
            ids.add(key_text[len("tool_") : -len("_entrypoint")])

    for tool_id in ids:
        if str(metadata.get(f"tool_{tool_id}_name", "") or "").strip() and str(
            metadata.get(f"tool_{tool_id}_entrypoint", "") or ""
        ).strip():
            return True
    return False


def _extract_md_tool_metadata_dict(
    entry: dict[str, Any],
    *,
    tool_name: str,
    key_suffix: str,
) -> dict[str, Any]:
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}

    normalized_tool_name = str(tool_name or "").strip()
    if normalized_tool_name:
        for key, value in metadata.items():
            key_text = str(key or "").strip()
            if not key_text.startswith("tool_") or not key_text.endswith("_name"):
                continue
            if str(value or "").strip() != normalized_tool_name:
                continue
            tool_id = key_text[len("tool_") : -len("_name")]
            candidate = metadata.get(f"tool_{tool_id}_{key_suffix}", {})
            if isinstance(candidate, dict):
                return dict(candidate)

    direct_value = metadata.get(key_suffix, {})
    if isinstance(direct_value, dict):
        return dict(direct_value)
    return {}


def _qualified_name_provider(qualified_name: str) -> str:
    normalized = str(qualified_name or "").strip()
    if ":" not in normalized:
        return ""
    return normalized.split(":", 1)[0].strip()


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
    if lowered_name == "web_search":
        return "web_search"
    if lowered_name == "web_fetch":
        return "web_fetch"
    if "weather" in lowered_name or "openmeteo" in lowered_name:
        return "weather"
    if "weather" in lowered_description or "forecast" in lowered_description:
        return "weather"
    if "provider:" in lowered_description or lowered_category.startswith("provider"):
        return "provider:generic"
    if "skill" in lowered_category or lowered_category == "md_skill":
        return "skill"
    if "skill" in lowered_description:
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
    routing_visibility = _normalize_optional_text(
        item.get("routing_visibility", ""),
        item.get("planner_visibility", ""),
    )
    aliases = _normalize_string_list(item.get("aliases", []))
    keywords = _normalize_string_list(item.get("keywords", []))
    use_when = _normalize_string_list(item.get("use_when", []))
    avoid_when = _normalize_string_list(item.get("avoid_when", []))
    silent_backend = is_silent_backend_tool(item)
    if silent_backend:
        normalized["description"] = normalize_tool_description(
            description=normalized["description"],
            silent_backend=True,
        )
    result_mode = normalize_tool_result_mode(item) if silent_backend else _normalize_optional_text(item.get("result_mode", ""))
    live_data = bool(item.get("live_data", False))
    browser_interaction = bool(item.get("browser_interaction", False))
    public_web = bool(item.get("public_web", False))

    if provider_type:
        normalized["provider_type"] = provider_type
    if category:
        normalized["category"] = category
    if source:
        normalized["source"] = source
    if not capability_class:
        capability_class = _infer_capability_class(
            name=normalized["name"],
            description=normalized["description"],
            provider_type=provider_type,
            category=category,
        )
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
    if routing_visibility:
        normalized["routing_visibility"] = routing_visibility
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
    if live_data:
        normalized["live_data"] = True
    if browser_interaction:
        normalized["browser_interaction"] = True
    if public_web:
        normalized["public_web"] = True
    parameters_schema = _normalize_parameters_schema(item.get("parameters_schema", {}))
    if parameters_schema:
        normalized["parameters_schema"] = parameters_schema
    success_contract = _normalize_metadata_object(item.get("success_contract", {}))
    if success_contract:
        normalized["success_contract"] = success_contract
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


def _normalize_metadata_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        payload = value.strip()
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except Exception:
            return {}
    return dict(value) if isinstance(value, dict) else {}

