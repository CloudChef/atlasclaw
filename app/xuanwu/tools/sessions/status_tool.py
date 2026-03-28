"""
session_status tool

Return the current session status, model information, and queue depth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def session_status_tool(
    ctx: "RunContext[SkillDeps]",
) -> dict:
    """

session

    Args:
        ctx:PydanticAI RunContext dependency injection

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    session_key = getattr(deps, "session_key", "unknown")

    details = {
        "session_key": session_key,
        "model": "unknown",
        "queue_depth": 0,
        "context_tokens": 0,
    }

    session_manager = getattr(deps, "session_manager", None)
    if session_manager:
        try:
            session = await session_manager.get_or_create(session_key)
            if hasattr(session, "model"):
                details["model"] = session.model
            if hasattr(session, "context_tokens"):
                details["context_tokens"] = session.context_tokens
        except Exception:
            pass

    return ToolResult.text(
        f"Session: {session_key}",
        details=details,
    ).to_dict()
