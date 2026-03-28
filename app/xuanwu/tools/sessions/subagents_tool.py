"""Management tool for running subagents."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def subagents_tool(
    ctx: "RunContext[SkillDeps]",
    action: str,
    subagent_id: Optional[str] = None,
    message: Optional[str] = None,
) -> dict:
    """List, kill, or steer running subagents.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        action: Operation to perform: `list`, `kill`, or `steer`.
        subagent_id: Target subagent ID for `kill` or `steer`.
        message: Steering message used by the `steer` action.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    if action == "list":
        return ToolResult.text(
            "(subagent listing - not yet implemented)",
            details={"action": "list", "subagents": []},
        ).to_dict()

    if action == "kill":
        if not subagent_id:
            return ToolResult.error("subagent_id is required for kill").to_dict()
        return ToolResult.text(
            f"Subagent {subagent_id} terminated",
            details={"action": "kill", "subagent_id": subagent_id, "status": "killed"},
        ).to_dict()

    if action == "steer":
        if not subagent_id:
            return ToolResult.error("subagent_id is required for steer").to_dict()
        if not message:
            return ToolResult.error("message is required for steer").to_dict()
        return ToolResult.text(
            f"Steer message sent to {subagent_id}",
            details={
                "action": "steer",
                "subagent_id": subagent_id,
                "message": message,
            },
        ).to_dict()

    return ToolResult.error(f"unknown action: {action}").to_dict()
