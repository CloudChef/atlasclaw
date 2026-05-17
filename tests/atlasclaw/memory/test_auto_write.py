# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.memory.active import ActiveMemoryRecallService
from app.atlasclaw.memory.auto_write import AutomaticMemoryWriteService
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(
            enabled=True,
            active=SimpleNamespace(allowed_chat_types=["dm", "direct"]),
            auto_write=SimpleNamespace(
                enabled=True,
                allowed_chat_types=["dm", "direct"],
                timeout_ms=15000,
                max_long_term_items=3,
                max_maintained_preferences=50,
                max_item_chars=360,
            ),
        )
    )


def _session_key(user_id: str) -> str:
    return SessionKey(
        agent_id="main",
        user_id=user_id,
        channel="web",
        account_id="default",
        chat_type=ChatType.DM,
        peer_id=user_id,
    ).to_string(scope=SessionScope.PER_PEER)


def _conversation_key(user_id: str, peer_id: str) -> str:
    return SessionKey(
        agent_id="main",
        user_id=user_id,
        channel="web",
        account_id="default",
        chat_type=ChatType.DM,
        peer_id=peer_id,
    ).to_string(scope=SessionScope.PER_PEER)


def _permissions(enabled: bool = True) -> list[dict]:
    return [
        {
            "skill_id": "memory_search",
            "skill_name": "memory_search",
            "authorized": enabled,
            "enabled": enabled,
        }
    ]


def _deps(manager: MemoryManager, *, user_id: str = "alice", enabled: bool = True) -> SkillDeps:
    return SkillDeps(
        user_info=UserInfo(user_id=user_id, display_name=user_id),
        session_key=_session_key(user_id),
        memory_manager=manager,
        extra={"_user_skill_permissions": _permissions(enabled=enabled)},
    )


def _read_memory_tree(manager: MemoryManager) -> str:
    texts: list[str] = []
    if not manager.memory_dir.exists():
        return ""
    for path in sorted(manager.memory_dir.glob("*.md")):
        texts.append(path.read_text(encoding="utf-8"))
    return "\n".join(texts)


@pytest.fixture(autouse=True)
def _memory_config(monkeypatch):
    cfg = _config()
    monkeypatch.setattr("app.atlasclaw.memory.active.get_config", lambda: cfg)
    monkeypatch.setattr("app.atlasclaw.memory.auto_write.get_config", lambda: cfg)
    monkeypatch.setattr("app.atlasclaw.memory.access.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_auto_write_persists_only_long_term_from_distiller(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        assert kwargs["allowed_tool_names"] == []
        return '{"long_term":["Alice prefers TypeScript examples for frontend answers."]}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-1",
        user_message="Please remember I prefer TypeScript examples.",
        assistant_message="I will use TypeScript examples.",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert result.long_term_count == 1
    assert result.diagnostics["status"] == "ok"
    assert result.diagnostics["distiller_attempted"] is True
    assert result.diagnostics["json_parse_status"] == "ok"
    assert result.diagnostics["parsed_long_term_count"] == 1
    assert result.diagnostics["model_skip_reason"] == ""
    assert result.diagnostics["sanitized_long_term_count"] == 1
    assert result.diagnostics["written_long_term_count"] == 1
    assert result.diagnostics["skip_reason"] == "none"
    assert result.diagnostics["raw_output_chars"] > 0
    assert result.diagnostics["raw_output_sha256"]
    assert result.diagnostics["memory_path"].endswith("users/alice/memory/MEMORY.md")
    assert sorted(path.name for path in manager.memory_dir.glob("*.md")) == ["MEMORY.md"]
    assert "frontend answers" in manager.long_term_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_auto_write_skips_without_memory_permission(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()
    called = False

    async def run_single(*args, **kwargs) -> str:
        nonlocal called
        called = True
        return '{"long_term":["Alice prefers TypeScript examples."]}'

    result = await service.write_after_success(
        deps=_deps(manager, enabled=False),
        session_key=_session_key("alice"),
        run_id="run-1",
        user_message="Please remember I prefer TypeScript examples.",
        assistant_message="Ok.",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "unavailable"
    assert called is False
    assert result.diagnostics["distiller_attempted"] is False
    assert result.diagnostics["skip_reason"] == "memory_unavailable_or_denied"
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_requires_model_distiller_for_explicit_remember_request(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-no-distiller",
        user_message="请记住我喜欢中文回复",
        assistant_message="已记录。",
        final_messages=[],
        run_single=None,
    )

    assert result.status == "no_memory"
    assert result.diagnostics["distiller_attempted"] is False
    assert result.diagnostics["json_parse_status"] == "not_attempted"
    assert result.diagnostics["skip_reason"] == "distiller_not_available"
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_skips_invalid_distiller_payload(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def invalid_json(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return "not json"

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-2",
        user_message="请记住我喜欢中文回复",
        assistant_message="已记录。",
        final_messages=[],
        run_single=invalid_json,
    )

    assert result.status == "no_memory"
    assert result.diagnostics["distiller_attempted"] is True
    assert result.diagnostics["json_parse_status"] == "invalid_json"
    assert result.diagnostics["model_skip_reason"] == ""
    assert result.diagnostics["skip_reason"] == "distiller_invalid_json"
    assert result.diagnostics["raw_output_sha256"]
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_distiller_runs_without_tools_and_routing_memory_prompt(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()
    captured: dict[str, object] = {}

    async def run_single(*args, **kwargs) -> str:
        captured["prompt"] = args[0]
        captured["allowed_tool_names"] = kwargs.get("allowed_tool_names")
        captured["system_prompt"] = kwargs.get("system_prompt")
        return '{"long_term":[],"skip_reason":"task_parameter"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-prompt-boundary",
        user_message="以后申请 Linux VM 都用 /cmp.request。",
        assistant_message="我不能把这个作为工具选择偏好来记忆。",
        final_messages=[{"tool_name": "smartcmp_submit_request"}],
        run_single=run_single,
    )

    assert result.status == "no_memory"
    assert captured["allowed_tool_names"] == []
    assert "smartcmp_submit_request" in str(captured["prompt"])
    system_prompt = str(captured["system_prompt"])
    assert '{"long_term":[],"skip_reason":"none"}' not in system_prompt
    assert '{"long_term":["User prefers English replies."],"skip_reason":"none"}' in system_prompt
    assert '{"long_term":[],"skip_reason":"no_durable_memory"}' in system_prompt
    assert "Write long_term items in clear English" in system_prompt
    assert "future assistant behavior" in system_prompt
    assert "tool/skill/provider routing" in system_prompt
    assert "one-off task parameters" in system_prompt
    lowered_prompt = system_prompt.lower()
    for forbidden in ("permission", "rbac", "authorized", "can save", "memory manager"):
        assert forbidden not in lowered_prompt
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_distiller_prompt_forbids_task_execution_summaries(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()
    captured: dict[str, object] = {}

    async def run_single(*args, **kwargs) -> str:
        captured["prompt"] = args[0]
        captured["system_prompt"] = kwargs.get("system_prompt")
        return '{"long_term":[],"skip_reason":"task_execution_summary"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-task-summary-filter",
        user_message="确认提交这个 Linux VM 申请。",
        assistant_message="申请已提交，审批号 RES20260516000001。",
        final_messages=[{"tool_name": "smartcmp_submit_request"}],
        run_single=run_single,
    )

    assert result.status == "no_memory"
    prompt = str(captured["prompt"])
    system_prompt = str(captured["system_prompt"])
    assert "Do not summarize completed operations or tool results" in prompt
    assert "task execution summaries" in system_prompt
    assert "provider records" in system_prompt
    assert "tool outputs" in system_prompt
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_skips_when_distiller_explicitly_returns_no_memory(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return '{"long_term":[],"skip_reason":"no_durable_memory"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-skip-reason",
        user_message="帮我申请一台 Linux VM，规格 1C2G。",
        assistant_message="缺少系统盘配置，需要补充。",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "no_memory"
    assert result.diagnostics["distiller_attempted"] is True
    assert result.diagnostics["json_parse_status"] == "ok"
    assert result.diagnostics["model_skip_reason"] == "no_durable_memory"
    assert result.diagnostics["skip_reason"] == "no_durable_memory"
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_skip_reason", ["none", ""])
async def test_auto_write_marks_empty_long_term_with_none_reason_as_inconsistent(
    tmp_path: Path,
    raw_skip_reason: str,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return json.dumps({"long_term": [], "skip_reason": raw_skip_reason})

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-inconsistent-empty",
        user_message="Please remember: always reply in English.",
        assistant_message="I will always reply in English from now on.",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "no_memory"
    assert result.diagnostics["distiller_attempted"] is True
    assert result.diagnostics["json_parse_status"] == "ok"
    assert result.diagnostics["parsed_long_term_count"] == 0
    assert result.diagnostics["model_skip_reason"] == raw_skip_reason
    assert result.diagnostics["skip_reason"] == "distiller_inconsistent_empty"
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_timeout_fails_open_without_writing(
    tmp_path: Path,
    _memory_config,
) -> None:
    _memory_config.memory.auto_write.timeout_ms = 1
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def slow_distiller(*args, **kwargs) -> str:
        _ = (args, kwargs)
        await asyncio.sleep(0.05)
        return '{"long_term":["late"]}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-timeout",
        user_message="Please remember I prefer short answers.",
        assistant_message="Noted.",
        final_messages=[],
        run_single=slow_distiller,
    )

    assert result.status == "no_memory"
    assert result.diagnostics["distiller_attempted"] is True
    assert result.diagnostics["json_parse_status"] == "timeout"
    assert result.diagnostics["model_skip_reason"] == ""
    assert result.diagnostics["skip_reason"] == "distiller_timeout"
    assert result.diagnostics["error_type"] == "TimeoutError"
    assert not manager.memory_dir.exists()


@pytest.mark.asyncio
async def test_auto_write_stores_language_preference_as_long_term_memory(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return '{"long_term":["Alice prefers English replies."]}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-language-preference",
        user_message="请记住：我以后希望你用英文回答。",
        assistant_message="已记录。",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert result.long_term_count == 1
    assert sorted(path.name for path in manager.memory_dir.glob("*.md")) == ["MEMORY.md"]
    long_term_text = manager.long_term_path.read_text(encoding="utf-8")
    assert "English replies" in long_term_text
    assert "请记住" not in long_term_text


@pytest.mark.asyncio
async def test_auto_write_uses_model_maintenance_to_keep_latest_language_preference(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "User prefers English replies.",
        source="seed",
        section="Preferences",
    )
    service = AutomaticMemoryWriteService()
    calls: list[dict[str, object]] = []

    async def run_single(*args, **kwargs) -> str:
        calls.append({"prompt": args[0], "system_prompt": kwargs.get("system_prompt")})
        if len(calls) == 1:
            return '{"long_term":["User prefers Chinese replies."],"skip_reason":"none"}'
        return '{"preferences":["User prefers Chinese replies."],"skip_reason":"none"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-language-update",
        user_message="请记住：以后一直用中文回复。",
        assistant_message="好的，以后我会一直用中文回复。",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert len(calls) == 2
    maintenance_prompt = str(calls[1]["prompt"])
    maintenance_system_prompt = str(calls[1]["system_prompt"])
    assert "Existing MEMORY.md content" in maintenance_prompt
    assert "User prefers English replies." in maintenance_prompt
    assert "New preferences from the latest completed turn" in maintenance_prompt
    assert "User prefers Chinese replies." in maintenance_prompt
    assert "Write every preference in clear English" in maintenance_system_prompt
    assert "keep only the latest preference" in maintenance_system_prompt
    assert result.diagnostics["memory_maintainer_attempted"] is True
    assert result.diagnostics["memory_maintainer_json_parse_status"] == "ok"
    assert result.diagnostics["memory_maintainer_preference_count"] == 1
    assert result.diagnostics["maintained_long_term_count"] == 1
    assert result.diagnostics["written_long_term_count"] == 1
    long_term_text = manager.long_term_path.read_text(encoding="utf-8")
    assert "User prefers Chinese replies." in long_term_text
    assert "User prefers English replies." not in long_term_text


@pytest.mark.asyncio
async def test_auto_write_model_maintenance_keeps_unrelated_preferences(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "User prefers concise answers.",
        source="seed",
        section="Preferences",
    )
    await manager.write_long_term(
        "User prefers English replies.",
        source="seed",
        section="Preferences",
    )
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        if "Maintain the final Preferences section" in str(args[0]):
            return (
                '{"preferences":["User prefers concise answers.",'
                '"User prefers Chinese replies."],"skip_reason":"none"}'
            )
        return '{"long_term":["User prefers Chinese replies."],"skip_reason":"none"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-language-update-preserve",
        user_message="请记住：以后一直用中文回复。",
        assistant_message="好的，以后我会一直用中文回复。",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    long_term_text = manager.long_term_path.read_text(encoding="utf-8")
    assert "User prefers concise answers." in long_term_text
    assert "User prefers Chinese replies." in long_term_text
    assert "User prefers English replies." not in long_term_text


@pytest.mark.asyncio
async def test_auto_write_model_maintenance_uses_configured_preference_limit(
    tmp_path: Path,
    _memory_config,
) -> None:
    _memory_config.memory.auto_write.max_maintained_preferences = 1
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    await manager.write_long_term(
        "User prefers concise answers.",
        source="seed",
        section="Preferences",
    )
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = kwargs
        if "Maintain the final Preferences section" in str(args[0]):
            return (
                '{"preferences":["User prefers concise answers.",'
                '"User prefers Chinese replies."],"skip_reason":"none"}'
            )
        return '{"long_term":["User prefers Chinese replies."],"skip_reason":"none"}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-maintenance-limit",
        user_message="请记住：以后一直用中文回复。",
        assistant_message="好的，以后我会一直用中文回复。",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert result.diagnostics["memory_maintainer_preference_count"] == 2
    assert result.diagnostics["maintained_long_term_count"] == 1
    long_term_text = manager.long_term_path.read_text(encoding="utf-8")
    assert "User prefers concise answers." in long_term_text
    assert "User prefers Chinese replies." not in long_term_text


@pytest.mark.asyncio
async def test_auto_write_stores_assistant_nickname_preference(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return '{"long_term":["Alice uses abc as the assistant nickname."]}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-nickname-preference",
        user_message="remember, i will call you abc.",
        assistant_message="I will remember that you call me abc.",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert sorted(path.name for path in manager.memory_dir.glob("*.md")) == ["MEMORY.md"]
    assert "abc" in _read_memory_tree(manager)


@pytest.mark.asyncio
async def test_assistant_nickname_memory_recalls_across_conversations(tmp_path: Path) -> None:
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    auto_write = AutomaticMemoryWriteService()
    active_recall = ActiveMemoryRecallService()
    captured: dict[str, object] = {}

    async def run_single(*args, **kwargs) -> str:
        captured["system_prompt"] = kwargs.get("system_prompt")
        return '{"long_term":["Alice calls the assistant Atlas 小助手."]}'

    write_result = await auto_write.write_after_success(
        deps=_deps(manager),
        session_key=_conversation_key("alice", "chat-one"),
        run_id="run-agent-nickname",
        user_message="请记住，以后我叫你 Atlas 小助手。",
        assistant_message="好的，你可以叫我 Atlas 小助手。",
        final_messages=[],
        run_single=run_single,
    )

    recall_result = await active_recall.recall(
        deps=_deps(manager, user_id="alice"),
        session_key=_conversation_key("alice", "chat-two"),
        user_message="你叫什么名字？",
    )

    assert write_result.status == "ok"
    assert "assistant nickname" in str(captured["system_prompt"])
    assert recall_result.status == "ok"
    assert "Atlas 小助手" in recall_result.context
    assert "assistant nickname" in recall_result.context
    assert "choose tools" in recall_result.context


@pytest.mark.asyncio
async def test_auto_write_uses_own_chat_type_allowlist(
    tmp_path: Path,
    _memory_config,
) -> None:
    _memory_config.memory.active.allowed_chat_types = ["group"]
    _memory_config.memory.auto_write.allowed_chat_types = ["dm"]
    manager = MemoryManager(workspace=str(tmp_path), user_id="alice")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        _ = (args, kwargs)
        return '{"long_term":["Alice prefers concise replies."]}'

    result = await service.write_after_success(
        deps=_deps(manager),
        session_key=_session_key("alice"),
        run_id="run-own-chat-type",
        user_message="Please remember I prefer concise replies.",
        assistant_message="Noted.",
        final_messages=[],
        run_single=run_single,
    )

    assert result.status == "ok"
    assert "concise replies" in manager.long_term_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_auto_write_keeps_users_isolated(tmp_path: Path) -> None:
    alice = MemoryManager(workspace=str(tmp_path), user_id="alice")
    bob = MemoryManager(workspace=str(tmp_path), user_id="bob")
    service = AutomaticMemoryWriteService()

    async def run_single(*args, **kwargs) -> str:
        deps = args[1]
        user_id = getattr(getattr(deps, "user_info", None), "user_id", "")
        if user_id == "alice":
            return '{"long_term":["Alice prefers Python examples."]}'
        return '{"long_term":["Bob prefers Go examples."]}'

    await service.write_after_success(
        deps=_deps(alice, user_id="alice"),
        session_key=_session_key("alice"),
        run_id="run-alice",
        user_message="Please remember I prefer Python.",
        assistant_message="Noted.",
        final_messages=[],
        run_single=run_single,
    )
    await service.write_after_success(
        deps=_deps(bob, user_id="bob"),
        session_key=_session_key("bob"),
        run_id="run-bob",
        user_message="Please remember I prefer Go.",
        assistant_message="Noted.",
        final_messages=[],
        run_single=run_single,
    )

    assert "Python" in alice.long_term_path.read_text(encoding="utf-8")
    assert "Go" in bob.long_term_path.read_text(encoding="utf-8")
    assert "Python" not in bob.long_term_path.read_text(encoding="utf-8")
