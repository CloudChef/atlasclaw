"""

sessions_history tool

get session.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def sessions_history_tool(
    ctx: "RunContext[SkillDeps]",
    session_key: str,
    limit: Optional[int] = None,
) -> dict:
    """

get session

    Args:
        ctx:PydanticAI RunContext dependency injection
        session_key:session key
        limit:returnitemcount

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)

    if session_manager is None:
        return ToolResult.error("SessionManager not available").to_dict()

    try:
        transcript = await session_manager.load_transcript(session_key)

        if limit is not None:
            transcript = transcript[-limit:]

        lines = []
        for entry in transcript:
            role = getattr(entry, "role", "unknown")
            content = getattr(entry, "content", str(entry))
            ts = getattr(entry, "timestamp", "")
            lines.append(f"[{role}] {content}" + (f" ({ts})" if ts else ""))

        return ToolResult.text(
            "\n".join(lines) if lines else "(empty history)",
            details={
                "session_key": session_key,
                "count": len(transcript),
            },
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
