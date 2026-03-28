"""

sessions_list tool

session.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def sessions_list_tool(
    ctx: "RunContext[SkillDeps]",
    filter: Optional[str] = None,
) -> dict:
    """

session

    Args:
        ctx:PydanticAI RunContext dependency injection
        filter:filteritem(optional, such as "channel:telegram")

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)

    if session_manager is None:
        return ToolResult.error("SessionManager not available").to_dict()

    try:
        sessions = await session_manager.list_sessions()

        # applyfilter
        if filter:
            key, _, value = filter.partition(":")
            sessions = [
                s for s in sessions
                if getattr(s, key, None) == value
            ]

        # for mat
        if not sessions:
            return ToolResult.text(
                "(no sessions)",
                details={"count": 0},
            ).to_dict()

        lines = []
        for s in sessions:
            key = getattr(s, "session_key", str(s))
            lines.append(str(key))

        return ToolResult.text(
            "\n".join(lines),
            details={"count": len(sessions)},
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
