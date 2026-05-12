# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _build_memory_client(memory_manager: MemoryManager, tmp_path) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        memory_manager=memory_manager,
    )
    set_api_context(ctx)
    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app)


def test_memory_search_route_returns_written_memory(tmp_path):
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    client = _build_memory_client(manager, tmp_path)

    write_response = client.post(
        "/api/memory/write",
        json={
            "memory_type": "long_term",
            "content": "AtlasClaw deployment uses canary release gates",
            "source": "test",
            "tags": ["deployment"],
            "section": "Operations",
        },
    )
    assert write_response.status_code == 200

    search_response = client.post(
        "/api/memory/search",
        json={"query": "canary release", "top_k": 5},
    )

    assert search_response.status_code == 200
    results = search_response.json()["results"]
    assert results
    assert "canary release" in results[0]["content"].lower()
    assert results[0]["source"] == "users/alice/memory/MEMORY.md"


@pytest.mark.asyncio
async def test_memory_manager_search_loads_persisted_memory(tmp_path):
    writer = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await writer.write_long_term(
        "AtlasClaw deployment uses canary release gates",
        source="test",
        tags=["deployment"],
        section="Operations",
    )
    reader = MemoryManager(workspace=str(tmp_path), user_id="alice")

    results = await reader.search("canary release", limit=5)

    assert results
    assert "canary release" in results[0].entry.content.lower()
    assert results[0].entry.metadata["path"] == "users/alice/memory/MEMORY.md"
    assert results[0].entry.metadata["start_line"] > 0


@pytest.mark.asyncio
async def test_memory_manager_get_returns_line_slice(tmp_path):
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "AtlasClaw deployment uses canary release gates",
        section="Operations",
    )

    payload = await manager.get("users/alice/memory/MEMORY.md", offset=4, limit=1)

    assert payload["content"] == "AtlasClaw deployment uses canary release gates"
    assert payload["path"] == "users/alice/memory/MEMORY.md"
    assert payload["start_line"] == 5
    assert payload["end_line"] == 5
