"""
memory_get tool

Read a memory file by offset.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def memory_get_tool(
    ctx: "RunContext[SkillDeps]",
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """



    Args:
        ctx:PydanticAI RunContext dependency injection
        path:file path
        offset:
        limit:

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    extra = getattr(deps, "extra", {})
    memory_manager = extra.get("memory_manager")

    if memory_manager is None:
        return ToolResult.error("MemoryManager not available").to_dict()

    try:
        if hasattr(memory_manager, "get"):
            content = await memory_manager.get(path, offset=offset, limit=limit)
        else:
            content = f"(memory_get not supported for path: {path})"

        return ToolResult.text(
            str(content),
            details={"path": path, "offset": offset, "limit": limit},
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
