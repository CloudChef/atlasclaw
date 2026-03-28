"""
memory_search tool

Perform semantic search on long-term memory.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def memory_search_tool(
    ctx: "RunContext[SkillDeps]",
    query: str,
    limit: int = 10,
) -> dict:
    """

search

    Args:
        ctx:PydanticAI RunContext dependency injection
        query:search
        limit:multireturnitemcount

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    deps = ctx.deps
    extra = getattr(deps, "extra", {})
    memory_manager = extra.get("memory_manager")

    if memory_manager is None:
        return ToolResult.text(
            "(no memories found - MemoryManager not available)",
            details={"count": 0},
        ).to_dict()

    try:
        results = await memory_manager.search(query, limit=limit)

        if not results:
            return ToolResult.text(
                "(no matching memories)",
                details={"count": 0, "query": query},
            ).to_dict()

        lines = []
        for r in results:
            content = getattr(r, "content", str(r))
            score = getattr(r, "score", 0.0)
            lines.append(f"[{score:.2f}] {content}")

        return ToolResult.text(
            "\n".join(lines),
            details={"count": len(results), "query": query},
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
