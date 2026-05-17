# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Shared formatting helpers for memory API and memory tools."""

from __future__ import annotations

from typing import Any, Optional


DEFAULT_MEMORY_SNIPPET_CHARS = 220


def coerce_positive_int(value: Any, default: int) -> int:
    """Return a positive integer parsed from value, or the provided default."""
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def build_file_citation(path: str, start_line: int, end_line: int) -> str:
    """Build a stable path#line citation for a memory file slice."""
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return ""
    safe_end = end_line if end_line >= start_line else start_line
    return f"{normalized_path}#L{start_line}-L{safe_end}"


def compact_text(value: str, max_chars: int = DEFAULT_MEMORY_SNIPPET_CHARS) -> str:
    """Collapse whitespace and cap text to a fixed character budget."""
    normalized = " ".join(str(value or "").split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - 3)].rstrip()}..."


def _metadata_from_item(item: Any, entry: Any) -> dict[str, Any]:
    raw_metadata = getattr(entry, "metadata", None) if entry is not None else None
    if not isinstance(raw_metadata, dict):
        raw_metadata = getattr(item, "metadata", {})
    return raw_metadata if isinstance(raw_metadata, dict) else {}


def normalize_memory_search_item(
    item: Any,
    *,
    query: str = "",
    max_chars: int = DEFAULT_MEMORY_SNIPPET_CHARS,
) -> dict[str, Any]:
    """Return a citation-aware dictionary for one memory search result."""
    entry = getattr(item, "entry", None)
    source_obj = entry if entry is not None else item
    metadata = _metadata_from_item(item, entry)
    content = str(getattr(source_obj, "content", "") or "")
    path = str(
        metadata.get("path")
        or metadata.get("source_path")
        or getattr(item, "path", "")
        or ""
    ).strip()
    start_line = coerce_positive_int(
        metadata.get("start_line") or getattr(item, "start_line", None),
        default=1,
    )
    end_line = coerce_positive_int(
        metadata.get("end_line") or getattr(item, "end_line", None),
        default=start_line,
    )
    if end_line < start_line:
        end_line = start_line
    highlights = getattr(item, "highlights", [])
    if not isinstance(highlights, list):
        highlights = []
    source = str(getattr(source_obj, "source", "") or path).strip()
    return {
        "id": str(getattr(source_obj, "id", "") or ""),
        "snippet": compact_text(content, max_chars),
        "content": content,
        "score": float(getattr(item, "score", 0.0) or 0.0),
        "source": source,
        "timestamp": getattr(source_obj, "timestamp", None),
        "highlights": [str(highlight) for highlight in highlights],
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "citation": build_file_citation(path, start_line, end_line),
        "query": query,
    }


def normalize_memory_get_payload(
    *,
    payload: Any,
    path: str,
    offset: Optional[int],
    limit: Optional[int],
) -> dict[str, Any]:
    """Return a citation-aware dictionary for one memory get payload."""
    if isinstance(payload, dict):
        raw_content = str(payload.get("content", ""))
        source_path = str(payload.get("path", path) or path)
        start_line = coerce_positive_int(payload.get("start_line"), default=(offset or 0) + 1)
        end_line = coerce_positive_int(payload.get("end_line"), default=start_line)
        if end_line < start_line:
            end_line = start_line
        return {
            "content": raw_content,
            "path": source_path,
            "start_line": start_line,
            "end_line": end_line,
            "citation": build_file_citation(source_path, start_line, end_line),
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
        "citation": build_file_citation(source_path, start_line, end_line),
    }
