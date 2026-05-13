# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.routing import AgentConfig, AgentRouter, DmScope
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.api.request_orchestrator import (
    IntentRecognizer,
    IntentResult,
    RequestOrchestrator,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.context import SessionKey, SessionScope
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


class _StaticIntentRecognizer:
    def __init__(self, result: IntentResult) -> None:
        self._result = result

    async def recognize(self, user_input: str) -> IntentResult:
        return self._result


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
        intent_recognizer=_StaticIntentRecognizer(
            IntentResult(
                confidence=0.95,
                agent_id="resource_agent",
            )
        ),
        agent_factory=_DummyAgentFactory(),
    )

    events = [
        event
        async for event in orchestrator.process(
            "please route this request",
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
    assert session_key == SessionKey(
        agent_id="resource_agent",
        user_id="alice",
        channel="web",
        chat_type=parsed.chat_type,
        peer_id="team:ops/42",
    ).to_string(scope=SessionScope.PER_CHANNEL_PEER)


@pytest.mark.asyncio
async def test_intent_recognizer_without_llm_returns_unknown() -> None:
    recognizer = IntentRecognizer()

    result = await recognizer.recognize("please route this request")

    assert result.confidence == 0.0
    assert result.agent_id == ""


@pytest.mark.asyncio
async def test_intent_recognizer_uses_llm_response_without_keyword_shortcuts() -> None:
    def _llm_caller(prompt: str) -> str:
        assert "please route this request" in prompt
        return '{"agent_id": "resource_agent", "confidence": 0.93, "entities": {"scope": "status"}}'

    recognizer = IntentRecognizer(llm_caller=_llm_caller)

    result = await recognizer.recognize("please route this request")

    assert result.agent_id == "resource_agent"
    assert result.confidence == 0.93
    assert result.extracted_entities == {"scope": "status"}


@pytest.mark.asyncio
async def test_intent_recognizer_invalid_llm_response_returns_unknown() -> None:
    recognizer = IntentRecognizer(llm_caller=lambda prompt: "not-json")

    result = await recognizer.recognize("create request")

    assert result.confidence == 0.0
    assert result.agent_id == ""
    assert result.raw_response == "not-json"
