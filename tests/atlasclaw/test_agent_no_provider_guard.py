# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Integration coverage for prompt-only no-provider agent turns."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.middleware import setup_auth_middleware
from app.atlasclaw.db import get_db_session, get_db_session_dependency
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


_test_db_manager: DatabaseManager | None = None
_FAKE_EVIDENCE = (
    "已创建",
    "刚刚新建",
    "创建时间",
    "系统日志",
    "运行时长",
    "不存在",
    "没有记录",
    "查询为空",
)


class _PromptOnlyAgentRun:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    def all_messages(self) -> list[dict]:
        return list(self._messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _PromptOnlyNoToolAgent:
    tools: list[dict] = []
    _function_toolset = SimpleNamespace(tools={})

    def __init__(self) -> None:
        self.override_calls: list[dict] = []
        self.run_calls: list[dict] = []
        self.iter_calls: list[dict] = []

    @contextmanager
    def override(self, **kwargs):
        self.override_calls.append(dict(kwargs))
        yield

    async def run(self, user_message, *, deps):
        self.run_calls.append({"user_message": user_message, "deps": deps})
        return SimpleNamespace(output="direct recovery was not expected")

    @asynccontextmanager
    async def iter(self, user_message, *, deps, message_history):
        self.iter_calls.append(
            {
                "user_message": user_message,
                "deps": deps,
                "message_history": list(message_history or []),
            }
        )
        text = str(user_message or "")
        if "确定" in text:
            answer = (
                "当前没有可用的 provider、skill 或 tool，AtlasClaw 不能验证这个请求。"
            )
        elif "申请" in text:
            answer = (
                "当前没有可用的 provider、skill 或 tool，AtlasClaw 不能执行或验证这个请求。"
            )
        else:
            answer = "Hello. I can answer general questions and explain what capabilities are visible."
        yield _PromptOnlyAgentRun([{"role": "assistant", "content": answer}])


async def _test_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    if _test_db_manager is None:
        raise RuntimeError("test database is not initialized")
    async with _test_db_manager.get_session() as session:
        yield session


def _auth_config() -> AuthConfig:
    return AuthConfig(
        provider="local",
        jwt={
            "secret_key": "test-secret-key-for-no-provider-guard",
            "issuer": "atlasclaw-test",
            "header_name": "AtlasClaw-Authenticate",
            "cookie_name": "AtlasClaw-Authenticate",
            "expires_minutes": 60,
        },
    )


def _init_database_sync(tmp_path: Path) -> DatabaseManager:
    async def _init() -> DatabaseManager:
        global _test_db_manager
        _test_db_manager = await init_database(
            DatabaseConfig(
                db_type="sqlite",
                sqlite_path=str(tmp_path / "agent-no-provider-guard.db"),
            )
        )
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
            await UserService.create(
                session,
                UserCreate(
                    username="admin",
                    password="adminpass123",
                    display_name="Test Admin",
                    email="admin@test.com",
                    roles={"admin": True},
                    auth_type="local",
                    is_active=True,
                ),
            )
        return _test_db_manager

    return asyncio.run(_init())


def _build_client(
    tmp_path: Path,
    auth_config: AuthConfig,
    *,
    test_agent: _PromptOnlyNoToolAgent | None = None,
) -> TestClient:
    session_manager = SessionManager(agents_dir=str(tmp_path / "agents"))
    test_agent = test_agent or _PromptOnlyNoToolAgent()
    session_queue = SessionQueue()
    runner = AgentRunner(
        agent=test_agent,
        session_manager=session_manager,
        session_queue=session_queue,
        prompt_builder=PromptBuilder(
            PromptBuilderConfig(workspace_path=str(tmp_path / ".atlasclaw"))
        ),
    )
    ctx = APIContext(
        session_manager=session_manager,
        session_queue=session_queue,
        skill_registry=SkillRegistry(),
        agent_runner=runner,
        provider_instances={},
        available_providers={},
    )
    set_api_context(ctx)

    app = FastAPI()
    app.state.config = SimpleNamespace(auth=auth_config)
    setup_auth_middleware(app, auth_config)
    app.include_router(create_router())
    app.include_router(api_router)
    app.state.test_agent = test_agent
    app.dependency_overrides[get_db_session] = _test_get_db_session
    app.dependency_overrides[get_db_session_dependency] = _test_get_db_session
    return TestClient(app)


def _cleanup_manager(manager: DatabaseManager) -> None:
    asyncio.run(manager.close())


def _login_as(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/local/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event: str | None = None
    current_data: str | None = None
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            current_data = line.removeprefix("data: ")
        elif not line and current_event and current_data:
            events.append((current_event, json.loads(current_data)))
            current_event = None
            current_data = None
    if current_event and current_data:
        events.append((current_event, json.loads(current_data)))
    return events


def _assert_unavailable_capability_message(text: str) -> None:
    normalized = str(text or "").lower()
    assert "provider" in normalized
    assert "skill" in normalized
    assert "tool" in normalized
    assert "不能" in text or "cannot" in normalized
    assert any(marker in text for marker in ("执行", "验证")) or any(
        marker in normalized for marker in ("perform", "verify")
    )


def _run_turn(
    client: TestClient,
    *,
    session_key: str,
    message: str,
    headers: dict[str, str],
) -> tuple[list[tuple[str, dict]], str, str]:
    run_response = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": message,
            "timeout_seconds": 30,
        },
        headers=headers,
    )
    assert run_response.status_code == 200, run_response.text
    run_id = run_response.json()["run_id"]
    with client.stream(
        "GET",
        f"/api/agent/runs/{run_id}/stream",
        headers=headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))
    return events, json.dumps(events, ensure_ascii=False), run_id


def test_no_provider_no_skill_role_uses_prompt_policy_without_hard_block(
    tmp_path: Path,
) -> None:
    manager = _init_database_sync(tmp_path)
    try:
        client = _build_client(tmp_path, _auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        headers = {"AtlasClaw-Authenticate": admin_token}

        role_response = client.post(
            "/api/roles",
            json={
                "name": "No Provider Agent User",
                "identifier": "no_provider_agent_user",
                "description": "Cannot access providers or skills",
                "permissions": {
                    "skills": {
                        "module_permissions": {
                            "view": False,
                            "enable_disable": False,
                            "manage_permissions": False,
                        },
                        "skill_permissions": [],
                    },
                    "providers": {
                        "module_permissions": {"manage_permissions": False},
                        "provider_permissions": [],
                    },
                },
                "is_active": True,
            },
            headers=headers,
        )
        assert role_response.status_code == 201, role_response.text

        user_response = client.post(
            "/api/users",
            json={
                "username": "no_provider_user",
                "password": "userpass123",
                "display_name": "No Provider User",
                "email": "no-provider@test.com",
                "roles": {"no_provider_agent_user": True},
                "is_active": True,
            },
            headers=headers,
        )
        assert user_response.status_code == 201, user_response.text

        user_token = _login_as(client, "no_provider_user", "userpass123")
        user_headers = {"AtlasClaw-Authenticate": user_token}

        session_response = client.post("/api/sessions", json={}, headers=user_headers)
        assert session_response.status_code == 200, session_response.text
        session_key = session_response.json()["session_key"]

        first_events, first_text, first_run_id = _run_turn(
            client,
            session_key=session_key,
            message="申请 1C2G Linux",
            headers=user_headers,
        )
        _assert_unavailable_capability_message(first_text)
        assert not any(event_name == "error" for event_name, _ in first_events)
        for fake_evidence in _FAKE_EVIDENCE:
            assert fake_evidence not in first_text
        status_response = client.get(
            f"/api/agent/runs/{first_run_id}",
            headers=user_headers,
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "completed"

        _, follow_up_text, _ = _run_turn(
            client,
            session_key=session_key,
            message="确定这是新建的 VM",
            headers=user_headers,
        )
        _assert_unavailable_capability_message(follow_up_text)
        for fake_evidence in _FAKE_EVIDENCE:
            assert fake_evidence not in follow_up_text

        _, greeting_text, _ = _run_turn(
            client,
            session_key=session_key,
            message="hi",
            headers=user_headers,
        )
        assert "Hello." in greeting_text
        assert "No provider, skill, or tool is available" not in greeting_text
        assert client.app.state.test_agent.iter_calls
        assert client.app.state.test_agent.run_calls == []
    finally:
        _cleanup_manager(manager)
