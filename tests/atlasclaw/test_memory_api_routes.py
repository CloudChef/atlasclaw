# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path, user_id: str = "default") -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        memory_manager=MemoryManager(workspace=str(tmp_path), user_id="default"),
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.user_info = UserInfo(user_id=user_id, display_name=user_id)
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("memory_type", ["daily", "not-a-type", "ephemeral"])
def test_memory_write_rejects_unsupported_memory_type(tmp_path, memory_type: str) -> None:
    client = _build_client(tmp_path)

    response = client.post(
        "/api/memory/write",
        json={"content": "should not be stored", "memory_type": memory_type},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"Invalid memory_type: {memory_type}"
    assert not (tmp_path / "users" / "default" / "memory" / "MEMORY.md").exists()


def test_memory_write_accepts_long_term_memory_type(tmp_path) -> None:
    client = _build_client(tmp_path)

    response = client.post(
        "/api/memory/write",
        json={"content": "memory content", "memory_type": "long_term"},
    )

    assert response.status_code == 200
    assert response.json()["memory_type"] == "long_term"


def test_memory_write_defaults_to_long_term(tmp_path) -> None:
    client = _build_client(tmp_path)

    response = client.post(
        "/api/memory/write",
        json={"content": "memory content"},
    )

    assert response.status_code == 200
    assert response.json()["memory_type"] == "long_term"


def test_memory_write_uses_authenticated_user_memory_dir(tmp_path) -> None:
    client = _build_client(tmp_path, user_id="alice")

    response = client.post(
        "/api/memory/write",
        json={"content": "alice secret", "memory_type": "long_term"},
    )

    assert response.status_code == 200
    assert (tmp_path / "users" / "alice" / "memory" / "MEMORY.md").exists()
    assert not (tmp_path / "users" / "default" / "memory" / "MEMORY.md").exists()
