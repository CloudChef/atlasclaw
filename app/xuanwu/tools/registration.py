"""Register built-in tools into the skill registry."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolMetadata
from app.xuanwu.tools.catalog import ToolCatalog, ToolProfile, GROUP_TOOLS
from app.xuanwu.skills.registry import SkillRegistry, SkillMetadata

if TYPE_CHECKING:
    pass

# Registry entries map tool names to metadata and import targets.
_TOOL_REGISTRY: dict[str, tuple[ToolMetadata, str, str]] = {
    "browser": (
        ToolMetadata(name="browser", description="Browser automation", group="ui"),
        "app.xuanwu.tools.ui.browser_tool",
        "browser_tool",
    ),
    # Runtime tools
    "xuanwu_runtime_status": (
        ToolMetadata(
            name="xuanwu_runtime_status",
            description="Inspect Xuanwu runtime availability and metadata",
            group="runtime",
        ),
        "app.xuanwu.tools.runtime.xuanwu_runtime_tools",
        "xuanwu_runtime_status_tool",
    ),
    "xuanwu_runtime_call": (
        ToolMetadata(
            name="xuanwu_runtime_call",
            description="Invoke a tool through the configured Xuanwu runtime",
            group="runtime",
        ),
        "app.xuanwu.tools.runtime.xuanwu_runtime_tools",
        "xuanwu_runtime_call_tool",
    ),
    # Session tools
    "sessions_list": (
        ToolMetadata(name="sessions_list", description="List sessions", group="sessions"),
        "app.xuanwu.tools.sessions.list_tool",
        "sessions_list_tool",
    ),
    "sessions_history": (
        ToolMetadata(name="sessions_history", description="Get session conversation history", group="sessions"),
        "app.xuanwu.tools.sessions.history_tool",
        "sessions_history_tool",
    ),
    "sessions_send": (
        ToolMetadata(name="sessions_send", description="Send message to other sessions", group="sessions"),
        "app.xuanwu.tools.sessions.send_tool",
        "sessions_send_tool",
    ),
    "sessions_spawn": (
        ToolMetadata(name="sessions_spawn", description="Spawn isolated sub-agent", group="sessions"),
        "app.xuanwu.tools.sessions.spawn_tool",
        "sessions_spawn_tool",
    ),
    "subagents": (
        ToolMetadata(name="subagents", description="Manage running sub-agents", group="sessions"),
        "app.xuanwu.tools.sessions.subagents_tool",
        "subagents_tool",
    ),
    "session_status": (
        ToolMetadata(name="session_status", description="Current session status", group="sessions"),
        "app.xuanwu.tools.sessions.status_tool",
        "session_status_tool",
    ),
    # Memory tools
    "memory_search": (
        ToolMetadata(name="memory_search", description="Semantic search long-term memory", group="memory"),
        "app.xuanwu.tools.memory.search_tool",
        "memory_search_tool",
    ),
    "memory_get": (
        ToolMetadata(name="memory_get", description="Read memory file by offset", group="memory"),
        "app.xuanwu.tools.memory.get_tool",
        "memory_get_tool",
    ),
    # Web tools
    "web_search": (
        ToolMetadata(name="web_search", description="Web search", group="web"),
        "app.xuanwu.tools.web.search_tool",
        "web_search_tool",
    ),
    "web_fetch": (
        ToolMetadata(name="web_fetch", description="Fetch webpage content", group="web"),
        "app.xuanwu.tools.web.fetch_tool",
        "web_fetch_tool",
    ),
    # Provider tools
    "list_provider_instances": (
        ToolMetadata(name="list_provider_instances", description="List Provider service instances", group="providers"),
        "app.xuanwu.tools.providers.instance_tools",
        "list_provider_instances_tool",
    ),
    "select_provider_instance": (
        ToolMetadata(name="select_provider_instance", description="Select Provider service instance", group="providers"),
        "app.xuanwu.tools.providers.instance_tools",
        "select_provider_instance_tool",
    ),
}


def _import_tool_function(module_path: str, function_name: str):
    """Import and return a tool function by module path and symbol name."""
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, function_name)


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
        )
        registry.register(skill_meta, handler)
        registered.append(tool_name)

    return registered
