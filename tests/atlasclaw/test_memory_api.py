# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    get_current_user,
    get_optional_authorization_context,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.orm.role import build_default_permissions
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _authz(user_id: str, *, memory_enabled: bool) -> AuthorizationContext:
    permissions = build_default_permissions()
    permissions["skills"]["skill_permissions"] = [
        {
            "skill_id": "group:memory",
            "skill_name": "group:memory",
            "authorized": memory_enabled,
            "enabled": memory_enabled,
        }
    ]
    return AuthorizationContext(
        user=UserInfo(user_id=user_id, display_name=user_id, auth_type="test"),
        permissions=permissions,
    )


def _build_client(
    tmp_path: Path,
    *,
    authz: AuthorizationContext | None,
    memory_manager: MemoryManager | None = None,
    current_user: UserInfo | None = None,
) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        memory_manager=memory_manager,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    app.dependency_overrides[get_optional_authorization_context] = lambda: authz
    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    elif authz is not None:
        app.dependency_overrides[get_current_user] = lambda: authz.user
    return TestClient(app)


def test_memory_search_returns_501_when_memory_runtime_missing(tmp_path: Path) -> None:
    client = _build_client(tmp_path, authz=_authz("alice", memory_enabled=True))

    response = client.post("/api/memory/search", json={"query": "typescript"})

    assert response.status_code == 501


def test_memory_write_requires_authenticated_user_without_rbac_context(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    client = _build_client(
        tmp_path,
        authz=None,
        memory_manager=memory_manager,
    )

    response = client.post(
        "/api/memory/write",
        json={"content": "anonymous should not write", "memory_type": "long_term"},
    )

    assert response.status_code == 401
    assert not (tmp_path / "users" / "default" / "memory").exists()


def test_memory_write_no_rbac_uses_authenticated_user_directory(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    client = _build_client(
        tmp_path,
        authz=None,
        memory_manager=memory_manager,
        current_user=UserInfo(user_id="local-dev", display_name="Local Dev", auth_type="none"),
    )

    response = client.post(
        "/api/memory/write",
        json={"content": "Local dev prefers concise answers.", "memory_type": "long_term"},
    )

    assert response.status_code == 200
    local_memory_dir = tmp_path / "users" / "local-dev" / "memory"
    assert local_memory_dir.exists()
    assert "Local dev prefers concise answers." in "".join(
        path.read_text(encoding="utf-8") for path in local_memory_dir.glob("*.md")
    )
    assert not (tmp_path / "users" / "default" / "memory").exists()


def test_memory_write_denies_user_without_memory_permission(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    client = _build_client(
        tmp_path,
        authz=_authz("alice", memory_enabled=False),
        memory_manager=memory_manager,
    )

    response = client.post(
        "/api/memory/write",
        json={"content": "Alice prefers TypeScript.", "memory_type": "long_term"},
    )

    assert response.status_code == 403
    assert not (tmp_path / "users" / "alice" / "memory").exists()


def test_memory_search_denies_user_without_memory_permission(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    alice_manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    client = _build_client(
        tmp_path,
        authz=_authz("alice", memory_enabled=False),
        memory_manager=memory_manager,
    )

    asyncio.run(
        alice_manager.write_long_term(
            "Alice prefers English replies.",
            section="Preferences",
        )
    )
    response = client.post("/api/memory/search", json={"query": "English replies", "top_k": 5})

    assert response.status_code == 403


def test_memory_write_uses_current_user_directory(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    client = _build_client(
        tmp_path,
        authz=_authz("alice", memory_enabled=True),
        memory_manager=memory_manager,
    )

    response = client.post(
        "/api/memory/write",
        json={"content": "Alice prefers TypeScript.", "memory_type": "long_term"},
    )

    assert response.status_code == 200
    alice_memory_dir = tmp_path / "users" / "alice" / "memory"
    assert alice_memory_dir.exists()
    assert "Alice prefers TypeScript." in "".join(
        path.read_text(encoding="utf-8") for path in alice_memory_dir.glob("*.md")
    )
    assert not (tmp_path / "users" / "default" / "memory").exists()


def test_memory_search_returns_real_citation_results(tmp_path: Path) -> None:
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="default")
    alice_manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    client = _build_client(
        tmp_path,
        authz=_authz("alice", memory_enabled=True),
        memory_manager=memory_manager,
    )

    asyncio.run(
        alice_manager.write_long_term(
            "Alice prefers TypeScript examples for frontend work.",
            section="Preferences",
        )
    )

    response = client.post("/api/memory/search", json={"query": "TypeScript", "top_k": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "TypeScript"
    assert payload["results"]
    first = payload["results"][0]
    assert "TypeScript examples" in first["snippet"]
    assert first["path"] == "users/alice/memory/MEMORY.md"
    assert first["citation"].startswith("users/alice/memory/MEMORY.md#L")
