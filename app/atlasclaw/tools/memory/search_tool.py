"""
memory_search tool

Perform semantic search on long-term memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


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
        if hasattr(memory_manager, "search"):
            results = await memory_manager.search(query, limit=limit)
        else:
            results = []

        if not results:
            return ToolResult.text(
                "(no matching memories)",
                details={"count": 0, "query": query, "results": []},
            ).to_dict()

        structured_results: list[dict[str, Any]] = []
        lines: list[str] = []
        for item in results:
            normalized = _normalize_search_item(item, query=query)
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


def _compact_snippet(text: str, max_chars: int = 220) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - 3)]}..."


def _normalize_search_item(item: Any, *, query: str) -> dict[str, Any]:
    entry = getattr(item, "entry", None)
    metadata = {}
    content = ""
    if entry is not None:
        metadata = getattr(entry, "metadata", {}) if isinstance(getattr(entry, "metadata", {}), dict) else {}
        content = str(getattr(entry, "content", "") or "")
    else:
        metadata = getattr(item, "metadata", {}) if isinstance(getattr(item, "metadata", {}), dict) else {}
        content = str(getattr(item, "content", "") or "")

    path = str(
        metadata.get("path")
        or metadata.get("source_path")
        or getattr(item, "path", "")
        or ""
    ).strip()
    start_line = _safe_int(
        metadata.get("start_line") or getattr(item, "start_line", None),
        default=1,
    )
    end_line = _safe_int(
        metadata.get("end_line") or getattr(item, "end_line", None),
        default=start_line,
    )
    score = float(getattr(item, "score", 0.0) or 0.0)
    snippet = _compact_snippet(content)
    citation = _build_citation(path, start_line, end_line)

    return {
        "snippet": snippet,
        "path": path,
        "start_line": start_line,
        "end_line": end_line if end_line >= start_line else start_line,
        "citation": citation,
        "score": score,
        "query": query,
    }
