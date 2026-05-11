# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.routing import AgentConfig, AgentRouter, DmScope
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.request_orchestrator import RequestOrchestrator
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.context import SessionKey
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.skills.registry import SkillRegistry


class _DummyAgentFactory:
    def create(self, config: AgentConfig) -> SimpleNamespace:
        return SimpleNamespace(agent=object(), config=config, skills=[])


class _CapturingRunner:
    def __init__(self, *, captured: dict) -> None:
        self._captured = captured

    async def run(self, **kwargs):
        self._captured.update(kwargs)
        yield StreamEvent.assistant_delta("ok")


@pytest.mark.asyncio
async def test_request_orchestrator_uses_canonical_session_key(
    tmp_path,
    monkeypatch,
) -> None:
    router = AgentRouter()
    router.register_agent(AgentConfig(id="resource_agent", dm_scope=DmScope.PER_CHANNEL_PEER))
    captured: dict = {}

    monkeypatch.setattr(
        "app.atlasclaw.api.request_orchestrator.AgentRunner",
        lambda **kwargs: _CapturingRunner(captured=captured),
    )

    orchestrator = RequestOrchestrator(
        skill_registry=SkillRegistry(),
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="alice"),
        agent_router=router,
        agent_factory=_DummyAgentFactory(),
    )

    events = [
        event
        async for event in orchestrator.process(
            "query vm status",
            peer_id="team:ops/42",
            channel="web",
            user_info=UserInfo(user_id="alice", display_name="Alice"),
        )
    ]

    assert any(event.type == "assistant" for event in events)
    session_key = captured["session_key"]
    parsed = SessionKey.from_string(session_key)

    assert parsed.user_id == "alice"
    assert parsed.channel == "web"
    assert parsed.peer_id == "team:ops/42"
    assert session_key == "agent:resource_agent:user:alice:web:dm:team%3Aops/42"
