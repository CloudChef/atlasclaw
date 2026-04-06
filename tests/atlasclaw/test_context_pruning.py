# -*- coding: utf-8 -*-

from __future__ import annotations

from app.atlasclaw.agent.compaction_safeguard import build_safeguarded_summary
from app.atlasclaw.agent.context_pruning import (
    ContextPruningSettings,
    HardClearConfig,
    SoftTrimConfig,
    prune_context_messages,
)


def _base_messages_with_large_tool(content: str) -> list[dict]:
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "tool_name": "web_fetch", "content": content},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]


def test_soft_trim_tool_result_keeps_head_tail():
    original = "A" * 2000 + "B" * 2000
    settings = ContextPruningSettings(
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=400, tail_chars=400),
    )
    pruned = prune_context_messages(
        messages=_base_messages_with_large_tool(original),
        settings=settings,
        context_window_tokens=1200,
    )

    tool_message = next(msg for msg in pruned if msg.get("role") == "tool")
    text = str(tool_message.get("content", ""))
    assert "Tool result trimmed" in text
    assert text.startswith("A" * 50)
    assert "B" * 50 in text
    assert len(text) < len(original)


def test_pruning_keeps_recent_assistant_tail():
    messages = _base_messages_with_large_tool("X" * 6000)
    settings = ContextPruningSettings(keep_last_assistants=3)
    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=1000,
    )

    original_tail = [msg for msg in messages if msg.get("role") == "assistant"][-3:]
    pruned_tail = [msg for msg in pruned if msg.get("role") == "assistant"][-3:]
    assert pruned_tail == original_tail


def test_pruning_preserves_failed_tool_outputs():
    messages = _base_messages_with_large_tool("Y" * 9000)
    messages[2]["metadata"] = {"is_error": True}
    settings = ContextPruningSettings()

    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=900,
    )

    tool_message = next(msg for msg in pruned if msg.get("role") == "tool")
    assert tool_message.get("content") == "Y" * 9000


def test_hard_clear_replaces_payload_when_pressure_is_high():
    large_messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
    ]
    for idx in range(6):
        large_messages.append({"role": "tool", "tool_name": f"t{idx}", "content": "Z" * 22000})
        large_messages.append({"role": "assistant", "content": f"a{idx}"})
    large_messages.extend(
        [
            {"role": "user", "content": "latest user"},
            {"role": "assistant", "content": "latest assistant"},
            {"role": "assistant", "content": "latest assistant 2"},
            {"role": "assistant", "content": "latest assistant 3"},
        ]
    )

    settings = ContextPruningSettings(
        keep_last_assistants=3,
        hard_clear_ratio=0.5,
        min_prunable_tool_chars=1_000,
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=300, tail_chars=300),
        hard_clear=HardClearConfig(
            enabled=True,
            placeholder="[Tool result cleared to save context space]",
        ),
    )
    pruned = prune_context_messages(
        messages=large_messages,
        settings=settings,
        context_window_tokens=2000,
    )

    cleared_tools = [
        msg for msg in pruned if msg.get("role") == "tool" and msg.get("content") == settings.hard_clear.placeholder
    ]
    assert cleared_tools


def test_safeguard_extracts_tool_failures_into_summary():
    messages = [
        {"role": "user", "content": "请帮我查天气"},
        {"role": "assistant", "content": "我开始查询"},
        {
            "role": "tool",
            "tool_name": "web_search",
            "content": "timeout",
            "metadata": {"status": "error"},
            "tool_call_id": "c1",
        },
    ]
    safeguarded = build_safeguarded_summary(messages=messages, base_summary="Base summary")
    assert "## Critical History" in safeguarded
    assert "## Tool Failures" in safeguarded
    assert "web_search" in safeguarded
