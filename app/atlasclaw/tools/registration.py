"""Register built-in tools into the skill registry."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.atlasclaw.tools.base import ToolMetadata
from app.atlasclaw.tools.catalog import GROUP_ATLASCLAW, ToolCatalog, ToolProfile
from app.atlasclaw.skills.registry import SkillRegistry, SkillMetadata

if TYPE_CHECKING:
    pass

# Registry entries map tool names to metadata and import targets.
_TOOL_REGISTRY: dict[str, tuple[ToolMetadata, str, str]] = {
    # Runtime tools
    "exec": (
        ToolMetadata(
            name="exec",
            description="Execute shell command",
            group="runtime",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.runtime.exec_tool",
        "exec_tool",
    ),
    "process": (
        ToolMetadata(
            name="process",
            description="Manage long-running process",
            group="runtime",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.runtime.process_tool",
        "process_tool",
    ),
    # Filesystem tools
    "read": (
        ToolMetadata(
            name="read",
            description="Read file content",
            group="fs",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.filesystem.read_tool",
        "read_tool",
    ),
    "write": (
        ToolMetadata(
            name="write",
            description="Write file content",
            group="fs",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.filesystem.write_tool",
        "write_tool",
    ),
    "edit": (
        ToolMetadata(
            name="edit",
            description="Edit file by string replacement",
            group="fs",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.filesystem.edit_tool",
        "edit_tool",
    ),
    "browser": (
        ToolMetadata(
            name="browser",
            description="Browser automation",
            group="ui",
            planner_visibility="general",
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
    # Session tools
    "sessions_list": (
        ToolMetadata(
            name="sessions_list",
            description="List sessions",
            group="sessions",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.list_tool",
        "sessions_list_tool",
    ),
    "sessions_history": (
        ToolMetadata(
            name="sessions_history",
            description="Get session conversation history",
            group="sessions",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.history_tool",
        "sessions_history_tool",
    ),
    "sessions_send": (
        ToolMetadata(
            name="sessions_send",
            description="Send message to other sessions",
            group="sessions",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.send_tool",
        "sessions_send_tool",
    ),
    "sessions_spawn": (
        ToolMetadata(
            name="sessions_spawn",
            description="Spawn isolated sub-agent",
            group="sessions",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.spawn_tool",
        "sessions_spawn_tool",
    ),
    "subagents": (
        ToolMetadata(
            name="subagents",
            description="Manage running sub-agents",
            group="sessions",
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.sessions.subagents_tool",
        "subagents_tool",
    ),
    "session_status": (
        ToolMetadata(
            name="session_status",
            description="Current session status",
            group="sessions",
            planner_visibility="contextual",
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
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.memory.search_tool",
        "memory_search_tool",
    ),
    "memory_get": (
        ToolMetadata(
            name="memory_get",
            description="Read memory file by offset",
            group="memory",
            planner_visibility="contextual",
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
            planner_visibility="general",
            aliases=["search", "web-search", "internet-search"],
            keywords=["latest information", "public web", "news", "price", "schedule", "search"],
            use_when=[
                "User needs public web information and no dedicated domain tool is available",
                "Request depends on current external facts from the public internet",
            ],
            avoid_when=[
                "A dedicated provider or domain tool already covers the request",
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
            planner_visibility="general",
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
            planner_visibility="general",
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
            planner_visibility="contextual",
        ),
        "app.atlasclaw.tools.providers.instance_tools",
        "list_provider_instances_tool",
    ),
    "select_provider_instance": (
        ToolMetadata(
            name="select_provider_instance",
            description="Select Provider service instance",
            group="providers",
            planner_visibility="contextual",
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
    explicit_by_name = {
        "web_search": "web_search",
        "web_fetch": "web_fetch",
        "openmeteo_weather": "weather",
        "browser": "browser",
    }
    if tool_name in explicit_by_name:
        return explicit_by_name[tool_name]

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

    registered: list[str] = []
    for tool_name in filtered_tools:
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
            source="builtin",
            group_ids=_resolve_builtin_group_ids(tool_meta),
            capability_class=_resolve_builtin_capability_class(tool_name, tool_meta),
            planner_visibility=str(tool_meta.planner_visibility or "").strip() or "contextual",
            aliases=list(tool_meta.aliases or []),
            keywords=list(tool_meta.keywords or []),
            use_when=list(tool_meta.use_when or []),
            avoid_when=list(tool_meta.avoid_when or []),
            result_mode=str(tool_meta.result_mode or "").strip() or "llm",
        )
        registry.register(skill_meta, handler)
        registered.append(tool_name)

    return registered
