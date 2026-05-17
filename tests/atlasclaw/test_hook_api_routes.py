# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.hooks.runtime import HookRuntime, HookRuntimeContext
from app.atlasclaw.hooks.runtime_builtin import RUNTIME_AUDIT_MODULE, register_builtin_hook_handlers
from app.atlasclaw.hooks.runtime_models import HookEventType
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.session.router import SessionManagerRouter
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path, user_id: str = "alice") -> TestClient:
    session_manager = SessionManager(workspace_path=str(tmp_path), user_id="default")
    session_router = SessionManagerRouter.from_manager(session_manager)
    hook_state_store = HookStateStore(workspace_path=str(tmp_path))
    memory_sink = MemorySink(str(tmp_path))
    context_sink = ContextSink(hook_state_store)
    hook_runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=str(tmp_path),
            hook_state_store=hook_state_store,
            memory_sink=memory_sink,
            context_sink=context_sink,
            session_manager_router=session_router,
        )
    )
    register_builtin_hook_handlers(hook_runtime)

    ctx = APIContext(
        session_manager=session_manager,
        session_manager_router=session_router,
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        hook_state_store=hook_state_store,
        memory_sink=memory_sink,
        context_sink=context_sink,
        hook_runtime=hook_runtime,
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        request.state.user_info = UserInfo(user_id=user_id, display_name=user_id)
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app)


def test_hook_routes_list_events_and_pending(tmp_path):
    client = _build_client(tmp_path, user_id="alice")

    from app.atlasclaw.api.deps_context import get_api_context

    runtime = get_api_context().hook_runtime
    assert runtime is not None

    runtime_loop = runtime

    import asyncio

    async def _seed():
        await runtime_loop.emit(
            event_type=HookEventType.RUN_FAILED,
            user_id="alice",
            session_key="agent:main:user:alice:web:dm:alice:topic:test",
            run_id="run-1",
            channel="web",
            agent_id="main",
            payload={"error": "simulated failure"},
        )

    asyncio.run(_seed())

    events_response = client.get(f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/events")
    pending_response = client.get(f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/pending")

    assert events_response.status_code == 200
    assert len(events_response.json()) == 1
    assert events_response.json()[0]["event_type"] == HookEventType.RUN_FAILED.value

    assert pending_response.status_code == 200
    assert len(pending_response.json()) == 1
    assert pending_response.json()[0]["summary"] == "simulated failure"


def test_hook_routes_confirm_and_reject_pending(tmp_path):
    client = _build_client(tmp_path, user_id="alice")
    from app.atlasclaw.api.deps_context import get_api_context
    import asyncio

    runtime = get_api_context().hook_runtime
    assert runtime is not None

    async def _seed():
        await runtime.emit(
            event_type=HookEventType.RUN_FAILED,
            user_id="alice",
            session_key="agent:main:user:alice:web:dm:alice:topic:test",
            run_id="run-1",
            channel="web",
            agent_id="main",
            payload={"error": "needs confirm"},
        )
        await runtime.emit(
            event_type=HookEventType.RUN_FAILED,
            user_id="alice",
            session_key="agent:main:user:alice:web:dm:alice:topic:test-2",
            run_id="run-2",
            channel="web",
            agent_id="main",
            payload={"error": "needs reject"},
        )

    asyncio.run(_seed())

    pending_response = client.get(f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/pending")
    assert pending_response.status_code == 200
    pending_items = pending_response.json()
    assert len(pending_items) == 2

    confirm_id = pending_items[0]["id"]
    reject_id = pending_items[1]["id"]

    confirm_response = client.post(
        f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/pending/{confirm_id}/confirm",
        json={"note": "promote"},
    )
    reject_response = client.post(
        f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/pending/{reject_id}/reject",
        json={"note": "skip"},
    )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "confirmed"
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"

    assert (tmp_path / "users" / "alice" / "memory" / "MEMORY.md").exists()

    pending_after = client.get(f"/api/hooks/{quote(RUNTIME_AUDIT_MODULE, safe='')}/pending")
    assert pending_after.status_code == 200
    assert pending_after.json() == []
