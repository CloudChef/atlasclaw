"""
memory_get tool

Read a memory file by offset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

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

        normalized = _normalize_get_payload(
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


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _build_citation(path: str, start_line: int, end_line: int) -> str:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return ""
    safe_end = end_line if end_line >= start_line else start_line
    return f"{normalized_path}#L{start_line}-L{safe_end}"


def _normalize_get_payload(
    *,
    payload: Any,
    path: str,
    offset: Optional[int],
    limit: Optional[int],
) -> dict[str, Any]:
    if isinstance(payload, dict):
        raw_content = str(payload.get("content", ""))
        source_path = str(payload.get("path", path) or path)
        start_line = _safe_int(payload.get("start_line"), default=(offset or 0) + 1)
        end_line = _safe_int(payload.get("end_line"), default=start_line)
        if end_line < start_line:
            end_line = start_line
        return {
            "content": raw_content,
            "path": source_path,
            "start_line": start_line,
            "end_line": end_line,
            "citation": _build_citation(source_path, start_line, end_line),
        }

    text = str(payload or "")
    start_line = (offset or 0) + 1
    line_count = len(text.splitlines()) if text else 1
    if isinstance(limit, int) and limit > 0:
        line_count = min(line_count, limit)
    end_line = max(start_line, start_line + line_count - 1)
    source_path = str(path or "")
    return {
        "content": text,
        "path": source_path,
        "start_line": start_line,
        "end_line": end_line,
        "citation": _build_citation(source_path, start_line, end_line),
    }
