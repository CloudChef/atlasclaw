"""Built-in tool groups and profile definitions."""

from __future__ import annotations

from enum import Enum
from typing import Optional


# Group identifiers used by tool profiles.
GROUP_FS = "group:fs"
GROUP_RUNTIME = "group:runtime"
GROUP_WEB = "group:web"
GROUP_MEMORY = "group:memory"
GROUP_SESSIONS = "group:sessions"
GROUP_UI = "group:ui"
GROUP_PROVIDERS = "group:providers"

# Tools included in each logical group.
GROUP_TOOLS: dict[str, list[str]] = {
    GROUP_FS: [],
    GROUP_RUNTIME: ["xuanwu_runtime_status", "xuanwu_runtime_call"],
    GROUP_WEB: ["web_search", "web_fetch"],
    GROUP_MEMORY: ["memory_search", "memory_get"],
    GROUP_SESSIONS: [
        "sessions_list",
        "sessions_history",
        "sessions_send",
        "sessions_spawn",
        "subagents",
        "session_status",
    ],
    GROUP_UI: ["browser"],
    GROUP_PROVIDERS: ["list_provider_instances", "select_provider_instance"],
}

# Flattened list of all registered tool names.
ALL_TOOLS: list[str] = [tool for tools in GROUP_TOOLS.values() for tool in tools]


class ToolProfile(str, Enum):
    """Named tool bundles exposed to different runtime modes."""

    MINIMAL = "minimal"
    CODING = "coding"
    MESSAGING = "messaging"
    FULL = "full"


# Tool selections associated with each profile.
PROFILE_DEFINITIONS: dict[ToolProfile, list[str]] = {
    ToolProfile.MINIMAL: ["session_status"],
    ToolProfile.CODING: [
        GROUP_FS,
        GROUP_RUNTIME,
        GROUP_SESSIONS,
        GROUP_MEMORY,
        GROUP_UI,
    ],
    ToolProfile.MESSAGING: [
        "sessions_list",
        "sessions_history",
        "sessions_send",
        "session_status",
    ],
    ToolProfile.FULL: list(GROUP_TOOLS.keys()),  # Enable every built-in tool group.
}


class ToolCatalog:
    """Resolve tool profiles and apply allow/deny filters."""

    @staticmethod
    def expand_groups(items: list[str]) -> list[str]:
        """Expand `group:*` entries into concrete tool names."""
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item.startswith("group:"):
                group_tools = GROUP_TOOLS.get(item, [])
                for tool in group_tools:
                    if tool not in seen:
                        result.append(tool)
                        seen.add(tool)
            else:
                if item not in seen:
                    result.append(item)
                    seen.add(item)
        return result

    @staticmethod
    def get_tools_by_profile(profile: str | ToolProfile) -> list[str]:
        """Return the concrete tools enabled by a profile."""
        if isinstance(profile, str):
            try:
                profile = ToolProfile(profile)
            except ValueError:
                return []

        definition = PROFILE_DEFINITIONS.get(profile, [])
        return ToolCatalog.expand_groups(definition)

    @staticmethod
    def filter_tools(
        tools: list[str],
        allow: Optional[list[str]] = None,
        deny: Optional[list[str]] = None,
    ) -> list[str]:
        """Apply allow/deny rules to a concrete tool list.

        Rules:
        - `allow` restricts the list to the named tools or groups
        - `deny` removes tools from the current result
        - `deny=["*"]` clears all tools unless `allow` re-adds them
        """
        result = list(tools)

        if allow is not None:
            expanded_allow = ToolCatalog.expand_groups(allow)
            result = [t for t in result if t in expanded_allow]

        if deny is not None:
            if "*" in deny:
                # `deny=["*"]` clears the set unless `allow` explicitly restores items.
                if allow is not None:
                    expanded_allow = ToolCatalog.expand_groups(allow)
                    result = [t for t in result if t in expanded_allow]
                else:
                    result = []
            else:
                expanded_deny = ToolCatalog.expand_groups(deny)
                result = [t for t in result if t not in expanded_deny]

        return result
