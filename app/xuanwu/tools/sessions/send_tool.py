"""
sessions_send tool

Send a message to another session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def sessions_send_tool(
    ctx: "RunContext[SkillDeps]",
    session_key: str,
    message: str,
) -> dict:
    """

session-Send a message

    Args:
        ctx:PydanticAI RunContext dependency injection
        session_key:session key
        message:Message content

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)

    if session_manager is None:
        return ToolResult.error("SessionManager not available").to_dict()

    try:
        # check session at
        session = await session_manager.get_or_create(session_key)
        if session is None:
            return ToolResult.error(
                f"Session not found: {session_key}",
                details={"session_key": session_key},
            ).to_dict()

        # through Session-Queue(such as available)
        queue = getattr(deps, "extra", {}).get("session_queue")
        if queue:
            queue.push(session_key, message)
            return ToolResult.text(
                f"Message queued to {session_key}",
                details={"session_key": session_key, "queued": True},
            ).to_dict()

        # to
        from app.xuanwu.session.context import TranscriptEntry
        entry = TranscriptEntry(role="user", content=message)
        await session_manager.append_transcript(session_key, entry)

        return ToolResult.text(
            f"Message sent to {session_key}",
            details={"session_key": session_key, "queued": True},
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
