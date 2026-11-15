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
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.agent.selected_capability import SELECTED_CAPABILITY_KEY
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.api.services.run_service import abort_run, execute_agent_run, init_run
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.embed.models import ContextSnapshot, ResolvedObject
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry


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


class _BlockingRunner:
    """Runner fixture that exposes whether abort cancels its active await."""

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, session_key, user_message, deps, timeout_seconds=600, **kwargs):
        """Yield a start event, then block until the consumer cancels iteration."""
        yield StreamEvent.lifecycle_start()
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


class _TerminalCleanupRunner:
    """Runner fixture that records deterministic iterator closure after terminal output."""

    def __init__(self):
        self.closed = asyncio.Event()

    async def run(self, session_key, user_message, deps, timeout_seconds=600, **kwargs):
        """Yield a successful terminal sequence and record generator finalization."""
        try:
            yield StreamEvent.lifecycle_start()
            yield StreamEvent.assistant_delta("done")
            yield StreamEvent.runtime_update("answered", "Final answer ready.")
            yield StreamEvent.lifecycle_end()
        finally:
            self.closed.set()


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
    assert "No provider, skill, or tool is available" in entries[-1].content


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


def test_embed_run_keeps_page_default_soft_for_explicit_other_skill(tmp_path):
    """A valid page default must not restrict an otherwise authorized selection."""
    runner = _RecordingRunner()
    client, ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    ctx.skill_registry.register(
        SkillMetadata(name="safe-tool", description="A selectable test skill."),
        lambda: "ok",
    )
    integration = SimpleNamespace(
        config=SimpleNamespace(provider_type="example", provider_instance="default"),
        agent_id="main",
        session_scope="example",
    )
    now = datetime.now(timezone.utc)
    snapshot = ContextSnapshot(
        context_id="ctx-stale-default",
        owner_user_id="alice",
        surface_id="a" * 22,
        generation=7,
        provider_type="example",
        provider_instance="default",
        page_type="item-detail",
        skill_ref="example:item",
        skill_name="item",
        object=ResolvedObject(type="item", id="ITEM-7", name="Item 7"),
        created_at=now,
    )
    ctx.embed_integration_registry = SimpleNamespace(get=lambda: integration)
    ctx.embed_context_store = SimpleNamespace(
        get=lambda *args, **kwargs: snapshot,
        is_latest=lambda *args, **kwargs: True,
    )
    ctx.provider_instances = {"example": {"default": {"base_url": "https://example.test"}}}

    session = client.post(
        "/api/sessions/threads",
        json={"account_id": "example", "peer_id": "alice"},
    )
    assert session.status_code == 200
    session_key = session.json()["session_key"]

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": "Run the explicitly selected safe skill.",
            "timeout_seconds": 30,
            "context": {
                "embed_context_id": snapshot.context_id,
                "context_generation": snapshot.generation,
                "selected_capability": {
                    "kind": "skill",
                    "command": "/safe-tool",
                },
            },
        },
    )

    assert run.status_code == 200
    assert runner.called is True
    assert runner.last_deps.extra[SELECTED_CAPABILITY_KEY]["command"] == "/safe-tool"
    turn_context = runner.last_deps.extra["context"]["turn_context"]
    assert turn_context["default_skill"]["ref"] == "example:item"
    assert turn_context["object"]["id"] == "ITEM-7"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("allowed_page_skill_refs", ["example:item"]),
        (
            "embed_scope",
            {
                "provider_type": "example",
                "provider_instance": "default",
                "object_id": "ITEM-7",
            },
        ),
    ],
)
def test_agent_run_rejects_retired_page_control_fields(
    tmp_path,
    field_name,
    field_value,
):
    """Retired page controls must never reach ordinary runner dependencies."""
    runner = _RecordingRunner()
    client, _ctx = _build_client_and_context(tmp_path, runner, user_id="alice")
    session = client.post(
        "/api/sessions/threads",
        json={"account_id": "example", "peer_id": "alice"},
    )
    assert session.status_code == 200

    run = client.post(
        "/api/agent/run",
        json={
            "session_key": session.json()["session_key"],
            "message": "Run an ordinary request.",
            "timeout_seconds": 30,
            "context": {field_name: field_value},
        },
    )

    assert run.status_code == 422
    assert run.json()["detail"] == "Page-scoped capability controls are not accepted."
    assert runner.called is False


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


def test_abort_completed_run_preserves_original_terminal_state(tmp_path):
    client, ctx = _build_client_and_context(tmp_path, _StreamingRunner(), user_id="alice")
    run_id = "run-already-completed"
    session_key = "agent:main:user:alice:main"
    init_run(ctx, run_id, session_key, "done", 30)
    completed_at = datetime.now(timezone.utc)
    ctx.active_runs[run_id]["status"] = "completed"
    ctx.active_runs[run_id]["completed_at"] = completed_at
    ctx.sse_manager.push_lifecycle(run_id, "end")
    ctx.sse_manager.close_stream(run_id)

    response = client.post(f"/api/agent/runs/{run_id}/abort")

    assert response.status_code == 200
    assert response.json() == {"status": "completed", "run_id": run_id}
    assert ctx.active_runs[run_id]["status"] == "completed"
    assert ctx.active_runs[run_id]["completed_at"] is completed_at
    stream = ctx.sse_manager.get_stream(run_id)
    assert stream is not None
    terminal_phases = [
        event.data.get("phase")
        for event in stream.events
        if event.data.get("phase") in {"end", "error", "timeout", "aborted"}
    ]
    assert terminal_phases == ["end"]


@pytest.mark.asyncio
async def test_terminal_lifecycle_closes_runner_iterator_before_return(tmp_path):
    runner = _TerminalCleanupRunner()
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        agent_runner=runner,
    )
    run_id = "run-terminal-cleanup"
    session_key = "agent:main:user:alice:main"
    init_run(ctx, run_id, session_key, "finish", 30)

    await execute_agent_run(
        ctx,
        run_id,
        session_key,
        "finish",
        30,
        UserInfo(user_id="alice", display_name="Alice"),
    )

    assert runner.closed.is_set()
    assert ctx.active_runs[run_id]["status"] == "completed"


@pytest.mark.asyncio
async def test_abort_run_cancels_active_runner_and_preserves_aborted_status(tmp_path):
    runner = _BlockingRunner()
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        agent_runner=runner,
    )
    run_id = "run-abort-active"
    session_key = "agent:main:user:alice:main"
    init_run(ctx, run_id, session_key, "wait", 30)

    execution = asyncio.create_task(
        execute_agent_run(
            ctx,
            run_id,
            session_key,
            "wait",
            30,
            UserInfo(user_id="alice", display_name="Alice"),
        )
    )
    await asyncio.wait_for(runner.started.wait(), timeout=1)

    abort_run(ctx, run_id)
    await asyncio.wait_for(execution, timeout=1)
    completed_at = ctx.active_runs[run_id]["completed_at"]
    assert abort_run(ctx, run_id) == "aborted"

    assert runner.cancelled.is_set()
    assert ctx.active_runs[run_id]["abort_signal"].is_set()
    assert ctx.active_runs[run_id]["status"] == "aborted"
    assert ctx.active_runs[run_id]["completed_at"] is completed_at
    stream = ctx.sse_manager.get_stream(run_id)
    assert stream is not None
    assert stream.closed is True
    aborted_events = [
        event
        for event in stream.events
        if event.data.get("phase") == "aborted"
    ]
    assert len(aborted_events) == 1


@pytest.mark.asyncio
async def test_live_progress_publishes_while_runner_is_blocked_and_detaches(tmp_path):
    from app.atlasclaw.agent.runner_tool.runner_progress_hooks import PROGRESS_SINK

    entered, release = asyncio.Event(), asyncio.Event()
    captured = {}

    class ProgressRunner:
        async def run(self, *, deps, **kwargs):
            captured['deps'] = deps
            captured['sink'] = deps.extra[PROGRESS_SINK]
            yield StreamEvent.lifecycle_start()
            captured['sink']('tool_running', 'Running operation', {
                'source': 'execution_hook', 'phase': 'tool_start', 'tool_call_id': 'a',
            })
            entered.set()
            await release.wait()
            yield StreamEvent.assistant_delta('done')
            yield StreamEvent.runtime_update('answered', 'Final answer ready.')
            # Even before lifecycle end, the committed answer blocks late progress.
            captured['sink']('reasoning', 'late', {'source': 'execution_hook'})
            yield StreamEvent.lifecycle_end()

    _, ctx = _build_client_and_context(tmp_path, ProgressRunner())
    session = 'agent:main:user:anonymous'
    init_run(ctx, 'progress-run', session, 'lookup', 30)
    task = asyncio.create_task(execute_agent_run(ctx, 'progress-run', session, 'lookup', 30,
                                               request_context={PROGRESS_SINK: 'untrusted'}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        stream = ctx.sse_manager.get_stream('progress-run')
        assert any(e.data.get('phase') == 'tool_start' for e in stream.events)
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert not any(e.data.get('message') == 'late' for e in stream.events)
    count = len(stream.events)
    captured['sink']('reasoning', 'after cleanup', {})
    assert len(stream.events) == count
    assert PROGRESS_SINK not in captured['deps'].extra
    assert ctx.active_runs['progress-run']['status'] == 'completed'


@pytest.fixture
def native_direct_context(tmp_path, monkeypatch):
    from app.atlasclaw.agent.runner import AgentRunner
    from app.atlasclaw.agent.tool_gate_models import ConversationTurnPlan
    from app.atlasclaw.api.services import run_service

    monkeypatch.setattr(run_service, "get_config", lambda: SimpleNamespace(
        agent_defaults=SimpleNamespace(direct_answer_streaming=True)))

    def build(agent):
        manager = SessionManager(agents_dir=str(tmp_path / "agents"))
        runner = AgentRunner(agent, manager)
        runner.active_memory = runner.auto_memory = None
        async def plan(**kwargs):
            return ConversationTurnPlan(route="ordinary", action="respond")
        async def no_title(**kwargs):
            pass
        runner._plan_conversation_turn_with_model = plan
        runner._maybe_finalize_title = no_title
        ctx = APIContext(session_manager=manager, session_queue=SessionQueue(),
                         skill_registry=SkillRegistry(), agent_runner=runner)
        published = asyncio.Event()
        push = ctx.sse_manager.push_assistant
        def capture(*args, **kwargs):
            result = push(*args, **kwargs)
            published.set()
            return result
        monkeypatch.setattr(ctx.sse_manager, "push_assistant", capture)
        return ctx, runner, published
    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["finish", "abort_before", "abort_after", "error", "timeout"])
async def test_native_direct_answer_stream_and_cleanup(native_direct_context, outcome):
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests, failover_attempts = [], []
    async def model(messages, info):
        requests.append(info)
        entered.set()
        try:
            if outcome == "abort_before":
                await release.wait()
            yield "First "
            await release.wait()
            if outcome == "error":
                raise RuntimeError("Invalid response from openai chat completions endpoint")
            yield "second."
        finally:
            closed.set()

    agent = Agent(FunctionModel(stream_function=model))
    @agent.tool_plain
    def forbidden_tool() -> str:
        raise AssertionError("A direct answer must not execute tools")
    ctx, runner, published = native_direct_context(agent)
    key, run_id = "agent:main:user:anonymous", "native-stream"
    timeout = 0.5 if outcome == "timeout" else 30
    init_run(ctx, run_id, key, "Explain", timeout)
    task = asyncio.create_task(execute_agent_run(ctx, run_id, key, "Explain", timeout,
                                               request_context={"assistant_replace": True}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if outcome != "abort_before":
            await asyncio.wait_for(published.wait(), 3)
            assert not task.done()
            assert ctx.active_runs[run_id]["status"] == "running"
        if outcome.startswith("abort"):
            abort_run(ctx, run_id)
        elif outcome in {"finish", "error"}:
            runner.token_policy = SimpleNamespace(
                token_pool=SimpleNamespace(tokens={"first": object(), "fallback": object()}),
                get_or_select_session_token=lambda *a, **kw: failover_attempts.append(kw),
            ) if outcome == "error" else None
            release.set()
        await asyncio.wait_for(task, 3)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runner._await_background_post_success_tasks()
    events = ctx.sse_manager.get_stream(run_id).events
    answers = [e.data for e in events if e.event_type.value == "assistant"]
    assert closed.is_set() and len(requests) == 1 and not requests[0].function_tools
    assert failover_attempts == []
    if outcome == "finish":
        assert [a["text"] for a in answers if a["is_delta"]] == ["First ", "second."]
        assert [a["text"] for a in answers if not a["is_delta"]] == ["First second."]
        assert ctx.active_runs[run_id]["status"] == "completed"
        history = await ctx.session_manager.load_transcript(key)
        assert [e.content for e in history if e.role == "assistant"] == ["First second."]
    else:
        assert ctx.active_runs[run_id]["status"] == ("aborted" if outcome.startswith("abort") else "error")
        assert not any(a["is_delta"] is False for a in answers)
        assert not any(e.data.get("phase") == "end" or e.data.get("state") == "answered" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["finish", "followup", "abort", "error"])
async def test_read_only_stream_preserves_native_tool_chain(native_direct_context, monkeypatch, outcome):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from app.atlasclaw.agent.tool_gate_models import ConversationTurnPlan

    results = []
    for enabled in [False, True]:
        calls, requests = [], []
        entered, published, release, closed = (asyncio.Event() for _ in range(4))

        def record_request(info):
            requests.append([(tool.name, tool.parameters_json_schema) for tool in info.function_tools])

        async def finish_request():
            if len(requests) == 2:
                entered.set()
                await release.wait()
                if outcome == "error":
                    raise RuntimeError("Connection interrupted")
                if outcome == "followup":
                    return ModelResponse(parts=[TextPart("Initial finding. "),
                        ToolCallPart("lookup", {"query": "second"}, "two")])
                return ModelResponse(parts=[TextPart("Initial finding. Complete answer.")])
            return ModelResponse(parts=[TextPart("Complete answer.")])

        async def respond(messages, info):
            record_request(info)
            if len(requests) == 1:
                return ModelResponse(parts=[ToolCallPart("lookup", {"query": "first"}, "one")])
            try:
                return await finish_request()
            finally:
                closed.set()

        async def stream(messages, info):
            record_request(info)
            try:
                if len(requests) == 2:
                    yield "Initial finding. "
                response = await finish_request()
                for part in response.parts:
                    if isinstance(part, ToolCallPart):
                        yield {0: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str(), tool_call_id=part.tool_call_id)}
                    else:
                        yield part.content.removeprefix("Initial finding. ")
            finally:
                closed.set()

        async def lookup(query: str):
            calls.append({"query": query})
            return {"success": True, "item": query}

        ctx, runner, _ = native_direct_context(Agent(FunctionModel(respond, stream_function=stream)))
        ctx.skill_registry.register(SkillMetadata(
            name="lookup", description="Read an item.", read_only=True,
            parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        ), lookup)
        ctx.skill_registry.register_to_agent(runner.agent)
        async def plan(**kwargs):
            return ConversationTurnPlan(route="start_new", action="use_tools", target_capability_ids=["tool:lookup"])
        runner._plan_conversation_turn_with_model = plan
        push = ctx.sse_manager.push_runtime
        def capture(*args, **kwargs):
            result = push(*args, **kwargs)
            if (kwargs.get("metadata") or {}).get("phase") == "tool_answer_stream":
                published.set()
            return result
        monkeypatch.setattr(ctx.sse_manager, "push_runtime", capture)
        key, run_id = f"agent:main:user:anonymous:web:dm:anonymous:topic:{enabled}", f"read-{enabled}"
        init_run(ctx, run_id, key, "Read item", 30)
        ctx.active_runs[run_id]["read_only_answer_streaming"] = enabled
        task = asyncio.create_task(execute_agent_run(ctx, run_id, key, "Read item", 30,
            request_context={"read_only_answer_stream": True}))
        try:
            await asyncio.wait_for((published if enabled else entered).wait(), 3)
            assert not task.done() and ctx.active_runs[run_id]["status"] == "running"
            if outcome == "abort":
                abort_run(ctx, run_id)
            else:
                release.set()
            await asyncio.wait_for(task, 3)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runner._await_background_post_success_tasks()
        events = ctx.sse_manager.get_stream(run_id).events
        status = ctx.active_runs[run_id]["status"]
        assert status == ("aborted" if outcome == "abort" else "completed")
        assert closed.is_set()
        assert calls == ([{"query": "first"}, {"query": "second"}] if outcome == "followup" else [{"query": "first"}])
        assert len(requests) == (3 if outcome == "followup" else 2)
        assert published.is_set() is enabled
        if outcome == "error":
            assert any(e.data.get("phase") == "tool_only_fallback" for e in events)
        if outcome == "abort":
            assert not any(e.data.get("state") == "answered" for e in events)
        history = await ctx.session_manager.load_transcript(key)
        answers = [e.content for e in history if e.role == "assistant"]
        if outcome in {"finish", "followup"}:
            assert [answer for answer in answers if answer] == [
                "Complete answer." if outcome == "followup" else "Initial finding. Complete answer."]
        results.append((calls, requests, status, answers,
            [e.data for e in events if e.event_type.value in {"assistant", "tool", "lifecycle"}]))
    assert results[0] == results[1]
