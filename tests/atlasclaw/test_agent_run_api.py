# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""Agent run API streaming regression tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.atlasclaw.agent.selected_capability import SELECTED_CAPABILITY_KEY
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.api.service_provider_schemas import register_provider_schema_definition
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.api.services import run_service
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry
from app.atlasclaw.session.context import SessionKey
from tests.atlasclaw.provider_schema_fixtures import managed_provider_definition


class _StreamingRunner:
    async def run(self, session_key, user_message, deps, timeout_seconds=600, **kwargs):
        yield StreamEvent.lifecycle_start()
        yield StreamEvent.assistant_delta(f"reply:{user_message}")
        yield StreamEvent.runtime_update("answered", "Final answer ready.")
        yield StreamEvent.lifecycle_end()


class _FailingRunner:
    async def run(self, session_key, user_message, deps, timeout_seconds=600, **kwargs):
        yield StreamEvent.lifecycle_start()
        yield StreamEvent.runtime_update("failed", "tool execution failed")
        yield StreamEvent.error_event("agent_error: tool execution failed")
        yield StreamEvent.lifecycle_end()


class _RecordingRunner(_StreamingRunner):
    def __init__(self):
        self.called = False
        self.last_deps = None

    async def run(self, *args, **kwargs):
        self.called = True
        self.last_deps = args[2] if len(args) > 2 else kwargs.get("deps")
        async for event in super().run(*args, **kwargs):
            yield event


def _build_client(tmp_path) -> TestClient:
    return _build_client_with_runner(tmp_path, _StreamingRunner())


def _build_client_with_runner(tmp_path, runner, *, user_id: str = "anonymous") -> TestClient:
    client, _ = _build_client_and_context(tmp_path, runner, user_id=user_id)
    return client


def _build_client_and_context(
    tmp_path,
    runner,
    *,
    user_id: str = "anonymous",
) -> tuple[TestClient, APIContext]:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        agent_runner=runner,
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        request.state.user_info = UserInfo(user_id=user_id, display_name=user_id)
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app), ctx


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


def _assert_static_slash_denial(client, ctx, runner, session_key: str, run) -> None:
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert runner.called is False
    run_id = run.json()["run_id"]
    status_response = client.get(f"/api/agent/runs/{run_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    entries = asyncio.run(
        ctx.session_manager_router.for_session_key(session_key).load_transcript(session_key)
    )
    assert [entry.role for entry in entries] == ["user", "assistant"]
    assert "没有可用的 provider、skill 或工具" in entries[-1].content


class _ConfirmationDbSession:
    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None


class _ConfirmationDbManager:
    is_initialized = True

    def __init__(self):
        self._session_factory = _ConfirmationDbSession

    @asynccontextmanager
    async def get_session(self):
        yield _ConfirmationDbSession()


def _install_confirmation_auth(monkeypatch, user: UserInfo) -> None:
    manager = _ConfirmationDbManager()
    authz = AuthorizationContext(
        user=user,
        permissions={
            "skills": {"allow_all": True},
            "providers": {"allow_all": True},
        },
    )

    async def resolve_authz(_session, _user):
        return authz

    monkeypatch.setattr(
        "app.atlasclaw.db.database.get_db_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        "app.atlasclaw.auth.guards.resolve_authorization_context",
        resolve_authz,
    )
    monkeypatch.setattr(run_service, "get_db_manager", lambda: manager)
    monkeypatch.setattr(run_service, "resolve_authorization_context", resolve_authz)


def _register_confirmed_fake_tool(ctx: APIContext, calls: list[dict]) -> SkillMetadata:
    register_provider_schema_definition(managed_provider_definition(provider_type="provider"))

    async def handler(ctx=None, **kwargs):
        calls.append(dict(kwargs))
        return {"success": True}

    metadata = SkillMetadata(
        name="provider_mutation",
        source="provider",
        provider_type="provider",
        owner_skill_ref="provider:resource",
        effect="mutate",
        requires_approval=True,
        parameters_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
        },
    )
    ctx.skill_registry.register(metadata, handler)
    ctx.provider_instances = {
        "provider": {
            "primary": {
                "provider_type": "provider",
                "instance_name": "primary",
                "base_url": "https://provider.example.com",
                "auth_type": "provider_token",
                "provider_token": "test-provider-token",
            }
        }
    }
    return metadata


def test_agent_run_claims_ticket_and_executes_frozen_tool_once(tmp_path, monkeypatch) -> None:
    user = UserInfo(user_id="alice", display_name="Alice")
    client, ctx = _build_client_and_context(
        tmp_path,
        _StreamingRunner(),
        user_id=user.user_id,
    )
    _install_confirmation_auth(monkeypatch, user)
    calls: list[dict] = []
    metadata = _register_confirmed_fake_tool(ctx, calls)
    session = client.post("/api/sessions", json={})
    session_key = session.json()["session_key"]
    agent_id = SessionKey.from_string(session_key).agent_id or "main"
    ticket = ctx.tool_confirmation_store.issue(
        owner_user_id=user.user_id,
        session_key=session_key,
        agent_id=agent_id,
        tool_name=metadata.name,
        owner_skill_ref=metadata.owner_skill_ref,
        contract_fingerprint=ctx.skill_registry._tool_contract_fingerprint(metadata),
        arguments={"resource_id": "resource-frozen"},
        provider_type="provider",
        provider_instance="primary",
    )

    response = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "execute confirmed operation",
            "timeout_seconds": 30,
            "context": {"tool_confirmation_token": ticket.token},
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    with client.stream("GET", f"/api/agent/runs/{run_id}/stream") as stream:
        events = _parse_sse_events("".join(stream.iter_text()))
    status_payload = client.get(f"/api/agent/runs/{run_id}").json()
    assert status_payload["status"] == "completed", (status_payload, events)
    assert calls == [{"resource_id": "resource-frozen"}]
    assert [(event, payload.get("phase")) for event, payload in events] == [
        ("lifecycle", "start"),
        ("tool", "start"),
        ("tool", "end"),
        ("assistant", None),
        ("runtime", "confirmed_tool_completed"),
        ("lifecycle", "end"),
    ]
    transcript = asyncio.run(
        ctx.session_manager_router.for_session_key(session_key).load_transcript(session_key)
    )
    assert transcript[-1].role == "assistant"
    assert transcript[-1].metadata["confirmed_tool"] == metadata.name


@pytest.mark.parametrize("failure", ["forged", "replay", "cross_user", "cross_session"])
def test_agent_run_rejects_invalid_confirmation_ticket(
    tmp_path,
    monkeypatch,
    failure: str,
) -> None:
    user = UserInfo(user_id="alice", display_name="Alice")
    client, ctx = _build_client_and_context(
        tmp_path,
        _StreamingRunner(),
        user_id=user.user_id,
    )
    _install_confirmation_auth(monkeypatch, user)
    calls: list[dict] = []
    metadata = _register_confirmed_fake_tool(ctx, calls)
    session_key = client.post("/api/sessions", json={}).json()["session_key"]
    agent_id = SessionKey.from_string(session_key).agent_id or "main"
    if failure == "forged":
        token = "forged-token"
    else:
        ticket = ctx.tool_confirmation_store.issue(
            owner_user_id="bob" if failure == "cross_user" else user.user_id,
            session_key="agent:main:user:alice:other" if failure == "cross_session" else session_key,
            agent_id=agent_id,
            tool_name=metadata.name,
            owner_skill_ref=metadata.owner_skill_ref,
            contract_fingerprint=ctx.skill_registry._tool_contract_fingerprint(metadata),
            arguments={"resource_id": "resource-frozen"},
            provider_type="provider",
            provider_instance="primary",
        )
        token = ticket.token
        if failure == "replay":
            ctx.tool_confirmation_store.claim(
                token,
                owner_user_id=user.user_id,
                session_key=session_key,
                agent_id=agent_id,
            )

    response = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "execute confirmed operation",
            "timeout_seconds": 30,
            "context": {"tool_confirmation_token": token},
        },
    )

    assert response.status_code == 409
    assert calls == []


def test_agent_run_stream_does_not_duplicate_lifecycle_or_assistant_events(tmp_path):
    client = _build_client(tmp_path)

    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={"session_key": session_key, "message": "hi", "timeout_seconds": 30},
    )
    assert run.status_code == 200
    run_id = run.json()["run_id"]

    with client.stream("GET", f"/api/agent/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert events == [
        ("lifecycle", {"phase": "start"}),
        ("assistant", {"text": "reply:hi", "is_delta": True}),
        ("runtime", {"state": "answered", "message": "Final answer ready."}),
        ("lifecycle", {"phase": "end"}),
    ]


def test_agent_run_status_is_error_when_stream_reports_failure(tmp_path):
    client = _build_client_with_runner(tmp_path, _FailingRunner())

    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={"session_key": session_key, "message": "hi", "timeout_seconds": 30},
    )
    assert run.status_code == 200
    run_id = run.json()["run_id"]

    with client.stream("GET", f"/api/agent/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        _ = "".join(response.iter_text())

    status_response = client.get(f"/api/agent/runs/{run_id}")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "error"
    assert "tool execution failed" in str(payload.get("error", ""))


def test_agent_run_rejects_other_users_session_key_before_runner_starts(tmp_path):
    bob_client = _build_client_with_runner(tmp_path, _StreamingRunner(), user_id="bob")
    bob_session = bob_client.post("/api/sessions", json={})
    assert bob_session.status_code == 200
    bob_session_key = bob_session.json()["session_key"]

    alice_runner = _RecordingRunner()
    alice_client = _build_client_with_runner(tmp_path, alice_runner, user_id="alice")

    response = alice_client.post(
        "/api/agent/run",
        json={"session_key": bob_session_key, "message": "hi", "timeout_seconds": 30},
    )

    assert response.status_code == 404
    assert alice_runner.called is False


def test_agent_run_rejects_missing_current_user_session_key(tmp_path):
    runner = _RecordingRunner()
    client = _build_client_with_runner(tmp_path, runner, user_id="alice")
    missing_session_key = "agent:main:user:alice:web:dm:alice:topic:missing-thread"

    response = client.post(
        "/api/agent/run",
        json={"session_key": missing_session_key, "message": "hi", "timeout_seconds": 30},
    )

    assert response.status_code == 404
    assert runner.called is False


def test_agent_run_accepts_current_users_existing_session_key(tmp_path):
    runner = _RecordingRunner()
    client = _build_client_with_runner(tmp_path, runner, user_id="alice")
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    response = client.post(
        "/api/agent/run",
        json={"session_key": session_key, "message": "hi", "timeout_seconds": 30},
    )

    assert response.status_code == 200
    assert response.json()["session_key"] == session_key
    assert runner.called is True


def test_agent_run_denies_unavailable_slash_command_without_runner(tmp_path):
    runner = _RecordingRunner()
    client, ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "/pptx 生成一个 PPTX 文件",
            "timeout_seconds": 30,
        },
    )

    _assert_static_slash_denial(client, ctx, runner, session_key, run)


def test_agent_run_denies_unavailable_slash_with_stale_selected_capability(tmp_path):
    runner = _RecordingRunner()
    client, ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    ctx.skill_registry.register(
        SkillMetadata(name="safe-tool", description="A selectable test skill."),
        lambda: "ok",
    )
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "/forbidden run with stale context",
            "timeout_seconds": 30,
            "context": {
                "selected_capability": {
                    "kind": "skill",
                    "command": "/safe-tool",
                },
            },
        },
    )

    _assert_static_slash_denial(client, ctx, runner, session_key, run)


def test_agent_run_denies_mismatched_slash_and_selected_capability(tmp_path):
    runner = _RecordingRunner()
    client, ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    for skill_name in ("safe-tool", "other-tool"):
        ctx.skill_registry.register(
            SkillMetadata(name=skill_name, description=f"Selectable {skill_name}."),
            lambda: "ok",
        )
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "/other-tool run with stale context",
            "timeout_seconds": 30,
            "context": {
                "selected_capability": {
                    "kind": "skill",
                    "command": "/safe-tool",
                },
            },
        },
    )

    _assert_static_slash_denial(client, ctx, runner, session_key, run)


def test_agent_run_binds_available_slash_without_client_selected_capability(tmp_path):
    runner = _RecordingRunner()
    client, ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    ctx.skill_registry.register(
        SkillMetadata(name="safe-tool", description="A selectable test skill."),
        lambda: "ok",
    )
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "/safe-tool run from text slash",
            "timeout_seconds": 30,
        },
    )

    assert run.status_code == 200
    assert run.json()["status"] == "running"
    assert runner.called is True
    selected = runner.last_deps.extra[SELECTED_CAPABILITY_KEY]
    assert selected["kind"] == "skill"
    assert selected["command"] == "/safe-tool"


def test_agent_run_status_rejects_other_users_run_id(tmp_path):
    client, ctx = _build_client_and_context(tmp_path, _StreamingRunner(), user_id="bob")
    ctx.active_runs["run-alice"] = {
        "status": "running",
        "session_key": "agent:main:user:alice:main",
        "started_at": datetime.now(timezone.utc),
        "message": "secret",
        "timeout_seconds": 30,
    }

    response = client.get("/api/agent/runs/run-alice")

    assert response.status_code == 404


def test_agent_run_stream_rejects_other_users_run_id(tmp_path):
    client, ctx = _build_client_and_context(tmp_path, _StreamingRunner(), user_id="bob")
    ctx.active_runs["run-alice"] = {
        "status": "running",
        "session_key": "agent:main:user:alice:main",
        "started_at": datetime.now(timezone.utc),
        "message": "secret",
        "timeout_seconds": 30,
    }

    with client.stream("GET", "/api/agent/runs/run-alice/stream") as response:
        assert response.status_code == 404


def test_agent_run_abort_rejects_other_users_run_id_without_mutating(tmp_path):
    client, ctx = _build_client_and_context(tmp_path, _StreamingRunner(), user_id="bob")
    ctx.active_runs["run-alice"] = {
        "status": "running",
        "session_key": "agent:main:user:alice:main",
        "started_at": datetime.now(timezone.utc),
        "message": "secret",
        "timeout_seconds": 30,
    }

    response = client.post("/api/agent/runs/run-alice/abort")

    assert response.status_code == 404
    assert ctx.active_runs["run-alice"]["status"] == "running"
