# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Register built-in tools into the skill registry."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.atlasclaw.tools.base import ToolMetadata
from app.atlasclaw.tools.catalog import (
    GROUP_ATLASCLAW,
    GROUP_CATALOG,
    INTERNAL_TOOL_NAMES,
    STANDARD_SKILL_RUNTIME_TOOL_NAMES,
    ToolCatalog,
    ToolProfile,
)
from app.atlasclaw.skills.registry import SkillRegistry, SkillMetadata

if TYPE_CHECKING:
    pass

# Registry entries map tool names to metadata and import targets.
_TOOL_REGISTRY: dict[str, tuple[ToolMetadata, str, str]] = {
    # Filesystem tools
    "read": (
        ToolMetadata(
            name="read",
            description="Read file content",
            group="fs",
            capability_class="fs_read",
            routing_visibility="contextual",
            coordination_only=True,
            aliases=["read file", "open file", "show file", "view file"],
            keywords=["read", "file", "open", "show", "view", "content"],
            use_when=[
                "User asks to inspect or read a local file",
                "A workspace task needs file contents before analysis or editing",
            ],
            avoid_when=[
                "The task only needs filesystem metadata without reading content",
            ],
        ),
        "app.atlasclaw.tools.filesystem.read_tool",
        "read_tool",
    ),
    "browser": (
        ToolMetadata(
            name="browser",
            description="Browser automation",
            group="ui",
            capability_class="browser",
            live_data=True,
            browser_interaction=True,
            public_web=True,
            routing_visibility="general",
            aliases=["browser", "web-browser", "chromium"],
            keywords=["open website", "click", "fill form", "navigate", "screenshot"],
            use_when=[
                "User needs interactive browser actions on a webpage or app",
                "Web task requires clicking, filling, logging in, or screenshot capture",
            ],
            avoid_when=[
                "A direct structured API, provider tool, or static fetch/search tool can answer faster",
            ],
        ),
        "app.atlasclaw.tools.ui.browser_tool",
        "browser_tool",
    ),
    "atlasclaw_catalog_query": (
        ToolMetadata(
            name="atlasclaw_catalog_query",
            description="Query AtlasClaw runtime catalogs for available providers, skills, tools, and groups",
            group="catalog",
            capability_class="atlasclaw_catalog",
            routing_visibility="internal",
            aliases=["catalog", "skills catalog", "tool catalog", "provider catalog", "runtime catalog"],
            keywords=[
                "available skills",
                "available tools",
                "providers",
                "catalog",
                "capabilities",
                "可用技能",
                "可用工具",
                "能力目录",
                "技能目录",
            ],
            use_when=[
                "User asks what AtlasClaw can use, expose, or support at runtime",
                "User asks which skills or tools are available for a specific provider",
                "User asks for current provider, skill, tool, or group catalog information",
            ],
            avoid_when=[
                "The user wants to execute an external provider action rather than inspect the runtime catalog",
            ],
            result_mode="tool_only_ok",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.discovery.catalog_query_tool",
        "atlasclaw_catalog_query_tool",
    ),
    "skill_read": (
        ToolMetadata(
            name="skill_read",
            description="Internal skill runtime: read from the current work_dir or selected skill files",
            group="skill_runtime",
            capability_class="skill_runtime:read",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_read_tool",
    ),
    "skill_write": (
        ToolMetadata(
            name="skill_write",
            description="Internal skill runtime: write text files under the current work_dir",
            group="skill_runtime",
            capability_class="skill_runtime:write",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_write_tool",
    ),
    "skill_edit": (
        ToolMetadata(
            name="skill_edit",
            description="Internal skill runtime: edit text files under the current work_dir",
            group="skill_runtime",
            capability_class="skill_runtime:edit",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_edit_tool",
    ),
    "skill_delete": (
        ToolMetadata(
            name="skill_delete",
            description="Internal skill runtime: delete files under the current work_dir",
            group="skill_runtime",
            capability_class="skill_runtime:delete",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_delete_tool",
    ),
    "skill_exec": (
        ToolMetadata(
            name="skill_exec",
            description=(
                "Internal skill runtime: execute shell commands for the selected skill. "
                "Commands run from the current work_dir by default. If the command creates "
                "a user-facing downloadable file, download_paths is required and must contain "
                "only that final file's work_dir-relative path. Do not use this tool to install "
                "packages or run package managers."
            ),
            group="skill_runtime",
            capability_class="skill_runtime:exec",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_exec_tool",
    ),
    "skill_process": (
        ToolMetadata(
            name="skill_process",
            description="Internal skill runtime: manage background processes for the selected skill",
            group="skill_runtime",
            capability_class="skill_runtime:process",
            routing_visibility="internal",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.skill_runtime_tools",
        "skill_process_tool",
    ),
    # Session tools
    "sessions_list": (
        ToolMetadata(
            name="sessions_list",
            description="List sessions",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.list_tool",
        "sessions_list_tool",
    ),
    "sessions_history": (
        ToolMetadata(
            name="sessions_history",
            description="Get session conversation history",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.history_tool",
        "sessions_history_tool",
    ),
    "sessions_send": (
        ToolMetadata(
            name="sessions_send",
            description="Send message to other sessions",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.send_tool",
        "sessions_send_tool",
    ),
    "sessions_spawn": (
        ToolMetadata(
            name="sessions_spawn",
            description="Spawn isolated sub-agent",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.spawn_tool",
        "sessions_spawn_tool",
    ),
    "subagents": (
        ToolMetadata(
            name="subagents",
            description="Manage running sub-agents",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.subagents_tool",
        "subagents_tool",
    ),
    "session_status": (
        ToolMetadata(
            name="session_status",
            description="Current session status",
            group="sessions",
            capability_class="session",
            routing_visibility="contextual",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.sessions.status_tool",
        "session_status_tool",
    ),
    # Memory tools
    "memory_search": (
        ToolMetadata(
            name="memory_search",
            description="Semantic search long-term memory",
            group="memory",
            capability_class="memory",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.memory.search_tool",
        "memory_search_tool",
    ),
    "memory_get": (
        ToolMetadata(
            name="memory_get",
            description="Read memory file by offset",
            group="memory",
            capability_class="memory",
            routing_visibility="contextual",
        ),
        "app.atlasclaw.tools.memory.get_tool",
        "memory_get_tool",
    ),
    # Web tools
    "web_search": (
        ToolMetadata(
            name="web_search",
            description="Web search",
            group="web",
            capability_class="web_search",
            live_data=True,
            public_web=True,
            routing_visibility="general",
            aliases=[
                "search",
                "web-search",
                "internet-search",
                "搜索",
                "联网搜索",
                "网页搜索",
            ],
            keywords=[
                "latest information",
                "public web",
                "news",
                "price",
                "schedule",
                "search",
                "来源",
                "出处",
                "链接",
                "引用",
                "网页",
                "互联网",
            ],
            use_when=[
                "User needs public web information and no dedicated domain tool is available",
                "Request depends on current external facts from the public internet",
                "User explicitly asks to search the web or provide public sources/links/citations",
            ],
            avoid_when=[
                "A dedicated provider or domain tool already covers the request",
                "The request is broad general knowledge or a recommendation that can be answered directly without public-web verification",
            ],
        ),
        "app.atlasclaw.tools.web.search_tool",
        "web_search_tool",
    ),
    "web_fetch": (
        ToolMetadata(
            name="web_fetch",
            description="Fetch webpage content",
            group="web",
            capability_class="web_fetch",
            live_data=True,
            public_web=True,
            routing_visibility="general",
            aliases=["fetch", "read webpage", "scrape webpage"],
            keywords=["fetch page", "read webpage", "extract content", "open article"],
            use_when=[
                "A specific public URL should be fetched and its content extracted",
                "A search result needs follow-up page content extraction",
            ],
            avoid_when=[
                "No specific URL or citation target is available yet",
            ],
        ),
        "app.atlasclaw.tools.web.fetch_tool",
        "web_fetch_tool",
    ),
    "openmeteo_weather": (
        ToolMetadata(
            name="openmeteo_weather",
            description="Get current and forecast weather via Open-Meteo APIs",
            group="web",
            capability_class="weather",
            live_data=True,
            public_web=True,
            routing_visibility="general",
            aliases=["weather", "forecast", "openmeteo"],
            keywords=["weather", "forecast", "temperature", "rain", "wind", "天气", "预报", "气温", "降雨"],
            use_when=[
                "User asks for current or forecast weather conditions for a place and date",
                "Weather information can be satisfied by a dedicated forecast tool without public web search",
            ],
            avoid_when=[
                "The request is not about weather or forecast data",
            ],
            result_mode="tool_only_ok",
        ),
        "app.atlasclaw.tools.web.openmeteo_weather_tool",
        "openmeteo_weather_tool",
    ),
    # Provider tools
    "list_provider_instances": (
        ToolMetadata(
            name="list_provider_instances",
            description="List Provider service instances",
            group="providers",
            capability_class="provider:generic",
            routing_visibility="contextual",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.providers.instance_tools",
        "list_provider_instances_tool",
    ),
    "select_provider_instance": (
        ToolMetadata(
            name="select_provider_instance",
            description="Select Provider service instance",
            group="providers",
            capability_class="provider:generic",
            routing_visibility="contextual",
            coordination_only=True,
        ),
        "app.atlasclaw.tools.providers.instance_tools",
        "select_provider_instance_tool",
    ),
}


def _import_tool_function(module_path: str, function_name: str):
    """Import and return a tool function by module path and symbol name."""
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, function_name)


def _resolve_builtin_group_ids(tool_meta: ToolMetadata) -> list[str]:
    result: list[str] = []
    if tool_meta.group:
        result.append(f"group:{tool_meta.group}")
    result.append(GROUP_ATLASCLAW)
    deduped: list[str] = []
    seen: set[str] = set()
    for group_id in result:
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        deduped.append(group_id)
    return deduped


def _resolve_builtin_capability_class(tool_name: str, tool_meta: ToolMetadata) -> str:
    explicit = str(tool_meta.capability_class or "").strip()
    if explicit:
        return explicit

    if tool_meta.group == "memory":
        return "memory"
    if tool_meta.group == "sessions":
        return "session"
    if tool_meta.group == "providers":
        return "provider:generic"
    return ""


def register_builtin_tools(
    registry: SkillRegistry,
    profile: str | ToolProfile = ToolProfile.FULL,
    allow: Optional[list[str]] = None,
    deny: Optional[list[str]] = None,
    tools_exclusive: Optional[list[str]] = None,
) -> list[str]:
    """Register built-in tools into the skill registry.

    Args:
        registry: Target skill registry.
        profile: Tool profile used as the base selection.
        allow: Optional allowlist of tools or groups.
        deny: Optional denylist of tools or groups.

    Returns:
        Names of tools that were successfully registered.
    """
    # Resolve the base tool set from the requested profile.
    profile_tools = ToolCatalog.get_tools_by_profile(profile)

    # Apply allow/deny filtering on top of the profile selection.
    filtered_tools = ToolCatalog.filter_tools(profile_tools, allow=allow, deny=deny)
    if tools_exclusive:
        filtered_tools = ToolCatalog.filter_tools(filtered_tools, deny=list(tools_exclusive))

    filtered_tool_names = list(filtered_tools)
    excluded_tool_names = set(ToolCatalog.expand_groups(list(tools_exclusive or [])))
    raw_exclusions = {
        str(item or "").strip()
        for item in (tools_exclusive or [])
        if str(item or "").strip()
    }
    excluded_tool_names.update(item for item in raw_exclusions if not item.startswith("group:"))
    if GROUP_CATALOG in raw_exclusions or GROUP_ATLASCLAW in raw_exclusions:
        excluded_tool_names.update(INTERNAL_TOOL_NAMES)
    if GROUP_ATLASCLAW in raw_exclusions:
        excluded_tool_names.update(STANDARD_SKILL_RUNTIME_TOOL_NAMES)
    for internal_tool_name in sorted(INTERNAL_TOOL_NAMES | STANDARD_SKILL_RUNTIME_TOOL_NAMES):
        if "*" in excluded_tool_names or internal_tool_name in excluded_tool_names:
            continue
        if internal_tool_name in _TOOL_REGISTRY and internal_tool_name not in filtered_tool_names:
            filtered_tool_names.append(internal_tool_name)

    registered: list[str] = []
    for tool_name in filtered_tool_names:
        if tool_name not in _TOOL_REGISTRY:
            continue

        tool_meta, module_path, func_name = _TOOL_REGISTRY[tool_name]

        try:
            handler = _import_tool_function(module_path, func_name)
        except (ImportError, AttributeError):
            continue

        skill_meta = SkillMetadata(
            name=tool_name,
            description=tool_meta.description,
            category=f"builtin:{tool_meta.group}",
            location="built-in",
            source=(
                "internal_runtime"
                if tool_name in STANDARD_SKILL_RUNTIME_TOOL_NAMES
                else "builtin"
            ),
            group_ids=_resolve_builtin_group_ids(tool_meta),
            capability_class=_resolve_builtin_capability_class(tool_name, tool_meta),
            routing_visibility=str(tool_meta.routing_visibility or "").strip() or "contextual",
            aliases=list(tool_meta.aliases or []),
            keywords=list(tool_meta.keywords or []),
            use_when=list(tool_meta.use_when or []),
            avoid_when=list(tool_meta.avoid_when or []),
            result_mode=str(tool_meta.result_mode or "").strip() or "llm",
            success_contract=dict(tool_meta.success_contract or {}),
            coordination_only=bool(tool_meta.coordination_only),
            live_data=bool(tool_meta.live_data),
            browser_interaction=bool(tool_meta.browser_interaction),
            public_web=bool(tool_meta.public_web),
        )
        registry.register(skill_meta, handler)
        registered.append(tool_name)

    return registered
