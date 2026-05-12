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


def _build_client(tmp_path) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        memory_manager=MemoryManager(workspace=str(tmp_path), user_id="default"),
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("memory_type", ["not-a-type", "ephemeral"])
def test_memory_write_rejects_unsupported_memory_type(tmp_path, memory_type: str) -> None:
    client = _build_client(tmp_path)

    response = client.post(
        "/api/memory/write",
        json={"content": "should not be stored", "memory_type": memory_type},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"Invalid memory_type: {memory_type}"
    assert not (tmp_path / "users" / "default" / "memory" / "MEMORY.md").exists()


@pytest.mark.parametrize("memory_type", ["daily", "long_term"])
def test_memory_write_accepts_supported_memory_type(tmp_path, memory_type: str) -> None:
    client = _build_client(tmp_path)

    response = client.post(
        "/api/memory/write",
        json={"content": "memory content", "memory_type": memory_type},
    )

    assert response.status_code == 200
    assert response.json()["memory_type"] == memory_type
