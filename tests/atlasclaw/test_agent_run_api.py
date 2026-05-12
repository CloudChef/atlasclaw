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

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


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

    async def run(self, *args, **kwargs):
        self.called = True
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
