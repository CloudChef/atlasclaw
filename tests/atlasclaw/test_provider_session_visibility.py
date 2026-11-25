# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider-internal sessions must never show up as user conversations."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _client(tmp_path, user_id: str = "alice") -> tuple[TestClient, APIContext]:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        request.state.user_info = UserInfo(user_id=user_id, display_name=user_id)
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app), ctx


def test_provider_runs_are_hidden_from_session_list(tmp_path) -> None:
    client, ctx = _client(tmp_path)
    manager = ctx.session_manager_router.for_user("alice")
    asyncio.run(manager.get_or_create("agent:main:user:alice:web:dm:alice:topic:abc"))
    asyncio.run(manager.get_or_create("agent:main:user:alice:provider-audit-report-7d49dadf1c04"))

    payload = client.get("/api/sessions").json()

    keys = {item["session_key"] for item in payload}
    assert "agent:main:user:alice:web:dm:alice:topic:abc" in keys
    assert not any(":provider-" in key for key in keys)
