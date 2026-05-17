# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
memory_search tool

Perform semantic search on long-term memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.atlasclaw.memory.formatting import normalize_memory_search_item
from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


async def memory_search_tool(
    ctx: "RunContext[SkillDeps]",
    query: str,
    limit: int = 10,
) -> dict:
    """Search user-scoped memory and return text plus structured citations.

    Args:
        ctx: PydanticAI run context carrying request-scoped ``SkillDeps``.
        query: Search text supplied by the model or selected capability.
        limit: Maximum number of memory hits to return.

    Returns:
        Serialized ``ToolResult`` dictionary. The ``details.results`` payload is
        normalized for API/tool consumers and includes file-line citations.
    """
    deps = ctx.deps
    extra = getattr(deps, "extra", {})
    memory_manager = extra.get("memory_manager") or getattr(
        deps,
        "memory_manager",
        None,
    )

    if memory_manager is None:
        return ToolResult.text(
            "(no memories found - MemoryManager not available)",
            details={"count": 0},
        ).to_dict()

    try:
        if not hasattr(memory_manager, "search"):
            return ToolResult.error("MemoryManager search is not available").to_dict()

        results = await memory_manager.search(query, limit=limit)

        if not results:
            return ToolResult.text(
                "(no matching memories)",
                details={"count": 0, "query": query, "results": []},
            ).to_dict()

        structured_results: list[dict[str, Any]] = []
        lines: list[str] = []
        for item in results:
            normalized = normalize_memory_search_item(item, query=query)
            structured_results.append(normalized)
            display = normalized["snippet"]
            score = normalized["score"]
            citation = normalized["citation"]
            if citation:
                lines.append(f"[{score:.2f}] {display} ({citation})")
            else:
                lines.append(f"[{score:.2f}] {display}")

        return ToolResult.text(
            "\n".join(lines),
            details={
                "count": len(structured_results),
                "query": query,
                "results": structured_results,
            },
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
