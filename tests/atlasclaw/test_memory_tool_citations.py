# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.atlasclaw.tools.memory.get_tool import memory_get_tool
from app.atlasclaw.tools.memory.search_tool import memory_search_tool


@dataclass
class _DepsStub:
    extra: dict = field(default_factory=dict)


@dataclass
class _RunContextStub:
    deps: _DepsStub


@dataclass
class _MemoryEntryStub:
    content: str
    metadata: dict


@dataclass
class _SearchResultStub:
    entry: _MemoryEntryStub
    score: float = 0.0


class _MemoryManagerSearchStub:
    async def search(self, query: str, limit: int = 10) -> list[_SearchResultStub]:
        _ = (query, limit)
        return [
            _SearchResultStub(
                entry=_MemoryEntryStub(
                    content="Deploy procedure: restart service and verify health checks.",
                    metadata={
                        "path": "workspace/users/u1/memory/memory_001.md",
                        "start_line": 12,
                        "end_line": 18,
                    },
                ),
                score=0.92,
            )
        ]


class _MemoryManagerGetStub:
    async def get(self, path: str, offset: int | None = None, limit: int | None = None) -> dict:
        _ = (path, offset, limit)
        return {
            "content": "line one\nline two",
            "path": "workspace/users/u1/memory/memory_001.md",
            "start_line": 40,
            "end_line": 41,
        }


@pytest.mark.asyncio
async def test_memory_search_returns_structured_results_with_citation():
    ctx = _RunContextStub(deps=_DepsStub(extra={"memory_manager": _MemoryManagerSearchStub()}))
    payload = await memory_search_tool(ctx, "deploy", limit=3)

    assert payload["is_error"] is False
    details = payload.get("details", {})
    results = details.get("results", [])
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["citation"] == "workspace/users/u1/memory/memory_001.md#L12-L18"
    assert results[0]["start_line"] == 12
    assert results[0]["end_line"] == 18


@pytest.mark.asyncio
async def test_memory_get_returns_citation_in_details():
    ctx = _RunContextStub(deps=_DepsStub(extra={"memory_manager": _MemoryManagerGetStub()}))
    payload = await memory_get_tool(
        ctx,
        path="workspace/users/u1/memory/memory_001.md",
        offset=39,
        limit=2,
    )

    assert payload["is_error"] is False
    details = payload.get("details", {})
    assert details["citation"] == "workspace/users/u1/memory/memory_001.md#L40-L41"
    assert details["start_line"] == 40
    assert details["end_line"] == 41
