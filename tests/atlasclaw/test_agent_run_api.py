# -*- coding: utf-8 -*-
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
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


def _build_client(tmp_path) -> TestClient:
    return _build_client_with_runner(tmp_path, _StreamingRunner())


def _build_client_with_runner(tmp_path, runner) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        agent_runner=runner,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app)


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
