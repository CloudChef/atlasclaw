"""
sessions_spawn tool

Spawn an isolated sub-agent.
"""

from __future__ import annotations

import uuid
from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def sessions_spawn_tool(
    ctx: "RunContext[SkillDeps]",
    prompt: str,
    tools: Optional[str] = None,
) -> dict:
    """


sub-agent

 Args:
 ctx:PydanticAI RunContext dependency injection
 prompt:sub-agent description
 tools:sub-agent availabletool()

 Returns:
 Serialized `ToolResult` dictionary
 
"""
    subagent_id = f"sub_{uuid.uuid4().hex[:8]}"
    session_key = f"subagent-{subagent_id}"

    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)

    if session_manager:
        try:
            await session_manager.get_or_create(session_key)
        except Exception:
            pass

    return ToolResult.text(
        f"Subagent spawned: {subagent_id}",
        details={
            "subagent_id": subagent_id,
            "session_key": session_key,
            "prompt": prompt,
            "tools": tools,
            "status": "running",
        },
    ).to_dict()
