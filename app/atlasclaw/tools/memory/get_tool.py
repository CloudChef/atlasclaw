# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
memory_get tool

Read the long-term memory file by offset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.atlasclaw.memory.formatting import normalize_memory_get_payload
from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


async def memory_get_tool(
    ctx: "RunContext[SkillDeps]",
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """Read a slice from the current user's long-term memory file.

    Args:
        ctx: PydanticAI run context carrying request-scoped ``SkillDeps``.
        path: ``MEMORY.md`` path from a prior search result or citation.
        offset: Optional zero-based line offset for partial reads.
        limit: Optional maximum number of lines to return.

    Returns:
        Serialized ``ToolResult`` dictionary with normalized content and
        citation metadata.
    """
    deps = ctx.deps
    extra = getattr(deps, "extra", {})
    memory_manager = extra.get("memory_manager") or getattr(
        deps,
        "memory_manager",
        None,
    )

    if memory_manager is None:
        return ToolResult.error("MemoryManager not available").to_dict()

    try:
        if not hasattr(memory_manager, "get"):
            return ToolResult.error("MemoryManager get is not available").to_dict()

        content = await memory_manager.get(path, offset=offset, limit=limit)

        normalized = normalize_memory_get_payload(
            payload=content,
            path=path,
            offset=offset,
            limit=limit,
        )
        return ToolResult.text(
            normalized["content"],
            details={
                "path": normalized["path"],
                "offset": offset,
                "limit": limit,
                "start_line": normalized["start_line"],
                "end_line": normalized["end_line"],
                "citation": normalized["citation"],
            },
        ).to_dict()

    except Exception as e:
        return ToolResult.error(str(e)).to_dict()
