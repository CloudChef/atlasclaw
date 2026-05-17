# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.memory.active import ActiveMemoryRecallService
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope


def _config(
    *,
    timeout_ms: int = 15000,
    allowed_chat_types: list[str] | None = None,
) -> SimpleNamespace:
    active = SimpleNamespace(
        enabled=True,
        allowed_chat_types=allowed_chat_types or ["dm", "direct"],
        timeout_ms=timeout_ms,
        max_summary_chars=220,
        cache_ttl_ms=15000,
        circuit_breaker_max_timeouts=3,
        circuit_breaker_cooldown_ms=60000,
    )
    return SimpleNamespace(
        memory=SimpleNamespace(
            enabled=True,
            max_results=6,
            active=active,
        )
    )


def _session_key(*, user_id: str = "alice", chat_type: ChatType = ChatType.DM) -> str:
    return SessionKey(
        agent_id="main",
        user_id=user_id,
        channel="web",
        account_id="default",
        chat_type=chat_type,
        peer_id=user_id,
    ).to_string(scope=SessionScope.PER_PEER)


def _deps(
    manager: MemoryManager | object,
    *,
    user_id: str = "alice",
    permissions: list[dict] | None = None,
) -> SkillDeps:
    extra = {}
    if permissions is not None:
        extra["_user_skill_permissions"] = permissions
    return SkillDeps(
        user_info=UserInfo(user_id=user_id, display_name=user_id),
        session_key=_session_key(user_id=user_id),
        memory_manager=manager,
        extra=extra,
    )


def _memory_permissions(enabled: bool = True) -> list[dict]:
    return [
        {
            "skill_id": "group:memory",
            "skill_name": "group:memory",
            "authorized": enabled,
            "enabled": enabled,
        }
    ]


@pytest.fixture(autouse=True)
def _memory_config(monkeypatch):
    cfg = _config()
    monkeypatch.setattr("app.atlasclaw.memory.active.get_config", lambda: cfg)
    monkeypatch.setattr("app.atlasclaw.memory.access.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_active_memory_injects_user_scoped_summary_with_citation(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "Alice prefers TypeScript examples for frontend work.",
        section="Preferences",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="For this UI task, should examples use TypeScript?",
    )

    assert result.status == "ok"
    assert "Alice prefers TypeScript examples" in result.context
    assert "users/alice/memory/MEMORY.md#L" in result.context


@pytest.mark.asyncio
async def test_active_memory_injects_usage_profile_with_citation(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "User has used provider: smartcmp.",
        section="Usage Profile",
    )
    await manager.write_long_term(
        "User has used skill: smartcmp:preapproval-agent.",
        section="Usage Profile",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="我常用哪些系统？",
    )

    assert result.status == "ok"
    assert "Usage Profile:" in result.context
    assert "User has used provider: smartcmp." in result.context
    assert "smartcmp:preapproval-agent" in result.context
    assert "users/alice/memory/MEMORY.md#L" in result.context
    assert "not a routing instruction" in result.context


@pytest.mark.asyncio
async def test_usage_profile_routing_recall_is_low_priority_hint(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "User has used provider: smartcmp.",
        section="Usage Profile",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall_usage_profile_for_routing(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
    )

    assert result.status == "ok"
    assert "User has used provider: smartcmp." in result.context
    assert "low-priority past usage signals" in result.context
    assert "authorized, currently available provider or skill capabilities" in result.context
    assert "must not override" in result.context


def test_capability_selector_prompt_includes_usage_profile_as_low_priority_hint() -> None:
    mixin = RunnerToolGateModelMixin()

    prompt = mixin._build_capability_selector_prompt(
        capability_index=[
            {
                "capability_id": "provider:smartcmp",
                "kind": "provider",
                "name": "smartcmp",
                "description": "Submit service requests.",
            }
        ],
        usage_profile_context=(
            "Untrusted long-term Usage Profile hints.\n"
            "<usage_profile_hints>\n"
            "Usage Profile:\n"
            "- User has used provider: smartcmp.\n"
            "</usage_profile_hints>"
        ),
    )

    assert "Past Usage Profile hints:" in prompt
    assert "User has used provider: smartcmp." in prompt
    assert "low-priority tie-breakers only" in prompt
    assert "Choose only capability IDs listed below" in prompt
    assert "must not override the user's explicit request" in prompt


def test_capability_selector_rejects_usage_profile_unavailable_provider() -> None:
    mixin = RunnerToolGateModelMixin()

    plan = mixin._coerce_capability_selector_payload(
        payload={
            "action": "use_tools",
            "targets": ["provider:smartcmp"],
            "reason": "Usage Profile says smartcmp was used before.",
        },
        capability_index=[
            {
                "capability_id": "provider:jira",
                "kind": "provider",
                "name": "jira",
                "description": "Read issues.",
            }
        ],
    )

    assert plan is None


@pytest.mark.asyncio
async def test_active_memory_gives_assistant_nickname_identity_priority(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "The assistant's nickname is MomoY.",
        section="Preferences",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="你好",
    )

    assert result.status == "ok"
    assert "MomoY" in result.context
    assert "highest-priority user-facing assistant name" in result.context
    assert "self-introductions" in result.context


@pytest.mark.asyncio
async def test_active_memory_recalls_language_preference_for_same_user_only(
    tmp_path: Path,
) -> None:
    alice_manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    bob_manager = MemoryManager(workspace=str(tmp_path), user_id="bob")
    await alice_manager.write_long_term(
        "Alice prefers English replies for future conversations.",
        section="Preferences",
    )
    service = ActiveMemoryRecallService()

    alice_result = await service.recall(
        deps=_deps(alice_manager, user_id="alice", permissions=_memory_permissions()),
        session_key=_session_key(user_id="alice"),
        user_message="请解释一下 memory 的作用。",
    )
    bob_result = await service.recall(
        deps=_deps(bob_manager, user_id="bob", permissions=_memory_permissions()),
        session_key=_session_key(user_id="bob"),
        user_message="请解释一下 memory 的作用。",
    )

    assert alice_result.status == "ok"
    assert "English replies" in alice_result.context
    assert "Do not use it to infer task intent" in alice_result.context
    assert "users/alice/memory/MEMORY.md#L" in alice_result.context
    assert bob_result.context == ""
    assert "English replies" not in bob_result.context


@pytest.mark.asyncio
async def test_active_memory_does_not_inject_preference_like_text_outside_preferences_section(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "Alice prefers billing export tool output for invoice tasks.",
        section="General",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="Please list production billing invoices.",
    )

    assert result.status == "no_relevant_memory"
    assert result.context == ""
    assert "billing export tool" not in result.summary


@pytest.mark.asyncio
async def test_active_memory_ignores_non_long_term_files_even_when_preference_like(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    non_long_term_path = manager.memory_dir / "2026-05-16.md"
    non_long_term_path.parent.mkdir(parents=True, exist_ok=True)
    non_long_term_path.write_text(
        "# Legacy Note\n\nAlice prefers TypeScript examples for frontend work.\n",
        encoding="utf-8",
    )
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="For this UI task, should examples use TypeScript?",
    )

    assert result.status == "no_relevant_memory"
    assert result.context == ""
    assert "TypeScript examples" not in result.summary


@pytest.mark.asyncio
async def test_active_memory_skips_when_rbac_denies_memory(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions(enabled=False)),
        session_key=_session_key(),
        user_message="Use TypeScript?",
    )

    assert result.status == "unavailable"
    assert result.context == ""


@pytest.mark.asyncio
async def test_active_memory_skips_group_chat_by_default(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = ActiveMemoryRecallService()

    result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(chat_type=ChatType.GROUP),
        user_message="Use TypeScript?",
    )

    assert result.status == "chat_type_skipped"
    assert result.context == ""


@pytest.mark.asyncio
async def test_active_memory_timeout_circuit_breaker_fails_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _config(timeout_ms=1)
    monkeypatch.setattr("app.atlasclaw.memory.active.get_config", lambda: cfg)
    monkeypatch.setattr("app.atlasclaw.memory.access.get_config", lambda: cfg)
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "Alice prefers TypeScript examples for frontend work.",
        section="Preferences",
    )
    service = ActiveMemoryRecallService()
    read_calls = 0

    async def slow_read_sections(*args, **kwargs) -> list[tuple[str, int, str]]:
        nonlocal read_calls
        _ = (args, kwargs)
        read_calls += 1
        await asyncio.sleep(0.05)
        return [("Preferences", 3, "Alice prefers TypeScript examples.")]

    monkeypatch.setattr(manager, "read_long_term_sections", slow_read_sections)

    for _ in range(3):
        result = await service.recall(
            deps=_deps(manager, permissions=_memory_permissions()),
            session_key=_session_key(),
            user_message="Use TypeScript?",
        )
        assert result.status == "timeout"

    circuit_result = await service.recall(
        deps=_deps(manager, permissions=_memory_permissions()),
        session_key=_session_key(),
        user_message="Use TypeScript?",
    )

    assert circuit_result.status == "timeout"
    assert read_calls == 3
