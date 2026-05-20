# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from app.atlasclaw.agent.compaction import (
    COMPACTION_SUMMARY_PREFIX,
    CompactionConfig,
    CompactionPipeline,
)


def _build_long_messages(*, history_count: int, history_chars: int) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": "system prompt"}]
    for idx in range(history_count):
        role = "user" if idx % 2 == 0 else "assistant"
        messages.append(
            {
                "role": role,
                "content": f"history-{idx} " + ("A" * history_chars),
            }
        )
    messages.extend(
        [
            {"role": "user", "content": "latest user"},
            {"role": "assistant", "content": "latest assistant"},
        ]
    )
    return messages


@pytest.mark.asyncio
async def test_compaction_uses_staged_summary_for_large_history():
    calls: list[list[dict]] = []

    async def _summarizer(batch: list[dict]) -> str:
        calls.append(batch)
        return f"summary(len={len(batch)})"

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=2000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=1.0,
            safeguard_enabled=False,
        ),
        summarizer=_summarizer,
    )
    messages = _build_long_messages(history_count=12, history_chars=420)

    compacted = await pipeline.compact(messages)

    assert len(calls) >= 2
    assert compacted[1]["role"] == "system"
    assert "summary(" in str(compacted[1]["content"])


@pytest.mark.asyncio
async def test_compaction_adapts_history_share_and_summarizes_dropped_prefix():
    calls: list[list[dict]] = []

    async def _summarizer(batch: list[dict]) -> str:
        calls.append(batch)
        return f"summary(len={len(batch)})"

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=2000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=0.25,
            safeguard_enabled=False,
        ),
        summarizer=_summarizer,
    )
    messages = _build_long_messages(history_count=24, history_chars=360)

    compacted = await pipeline.compact(messages)
    summary_text = str(compacted[1]["content"])

    assert len(calls) >= 2
    assert "## Older History" in summary_text
    assert "## Retained History" in summary_text
    assert compacted[-2:] == messages[-2:]


@pytest.mark.asyncio
async def test_compaction_keeps_original_history_when_summary_fails():
    async def _broken_summarizer(batch: list[dict]) -> str:
        raise RuntimeError("summary failed")

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=2000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=0.3,
            safeguard_enabled=False,
        ),
        summarizer=_broken_summarizer,
    )
    messages = _build_long_messages(history_count=20, history_chars=400)

    compacted = await pipeline.compact(messages)

    assert compacted == messages


@pytest.mark.asyncio
async def test_compaction_merges_existing_summary_instead_of_accumulating():
    previous_summary = f"{COMPACTION_SUMMARY_PREFIX}\nprevious compressed facts"
    captured_batches: list[list[dict]] = []

    async def _summarizer(batch: list[dict]) -> str:
        captured_batches.append(batch)
        return "merged summary"

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=4000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=1.0,
            safeguard_enabled=False,
        ),
        summarizer=_summarizer,
    )
    messages = [
        {"role": "system", "content": previous_summary},
        {"role": "user", "content": "older user"},
        {"role": "assistant", "content": "older assistant"},
        {"role": "user", "content": "latest user"},
        {"role": "assistant", "content": "latest assistant"},
    ]

    compacted = await pipeline.compact(messages)

    summary_messages = [
        message
        for message in compacted
        if message.get("role") == "system"
        and str(message.get("content", "")).startswith(COMPACTION_SUMMARY_PREFIX)
    ]
    assert len(summary_messages) == 1
    assert compacted == [
        {"role": "system", "content": f"{COMPACTION_SUMMARY_PREFIX}\nmerged summary"},
        {"role": "user", "content": "latest user"},
        {"role": "assistant", "content": "latest assistant"},
    ]
    assert captured_batches
    assert captured_batches[0][0] == {"role": "system", "content": previous_summary}


@pytest.mark.asyncio
async def test_compaction_appends_workspace_critical_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# Intro\nIgnore this.\n\n## Session Startup\nRule-A\n\n## Red Lines\nRule-B\n",
        encoding="utf-8",
    )

    async def _summarizer(batch: list[dict]) -> str:
        return "summary"

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=2000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            safeguard_enabled=True,
            workspace_path=str(tmp_path),
        ),
        summarizer=_summarizer,
    )
    messages = _build_long_messages(history_count=12, history_chars=420)

    compacted = await pipeline.compact(messages)
    summary_text = str(compacted[1]["content"])

    assert "## Workspace Critical Rules" in summary_text
    assert "Rule-A" in summary_text
    assert "Rule-B" in summary_text
    assert "Ignore this." not in summary_text


@pytest.mark.asyncio
async def test_compaction_strips_tool_result_details_before_summary():
    captured_batches: list[list[dict]] = []
    large_detail = "D" * 8000

    async def _summarizer(batch: list[dict]) -> str:
        captured_batches.append(batch)
        return "summary"

    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=2500,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=1.0,
            safeguard_enabled=False,
        ),
        summarizer=_summarizer,
    )
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "调用工具", "tool_calls": [{"id": "call-1", "name": "web_fetch"}]},
        {
            "role": "tool",
            "tool_name": "web_fetch",
            "tool_call_id": "call-1",
            "content": {
                "status": "ok",
                "summary": "天气详情",
                "details": {"raw": large_detail, "html": large_detail},
            },
        },
        {"role": "assistant", "content": "latest assistant"},
        {"role": "user", "content": "latest user"},
    ]

    await pipeline.compact(messages)

    tool_messages = [
        message
        for batch in captured_batches
        for message in batch
        if str(message.get("role", "")).strip().lower() == "tool"
    ]
    assert tool_messages
    serialized = str(tool_messages[0].get("content", ""))
    assert large_detail[:120] not in serialized
    assert "[omitted]" in serialized


def test_prune_history_share_repairs_orphan_tool_results():
    pipeline = CompactionPipeline(
        CompactionConfig(
            context_window=4000,
            reserve_tokens_floor=200,
            soft_threshold_tokens=100,
            keep_recent_turns=1,
            max_history_share=1.0,
            safeguard_enabled=False,
        ),
    )

    messages = [
        {"role": "assistant", "content": "call tool", "tool_calls": [{"id": "keep-call", "name": "web_fetch"}]},
        {"role": "tool", "tool_name": "web_fetch", "tool_call_id": "keep-call", "content": "ok"},
        {"role": "tool", "tool_name": "web_fetch", "tool_call_id": "orphan-call", "content": "orphan"},
        {"role": "assistant", "content": "done"},
    ]

    result = pipeline._prune_history_for_context_share(messages)
    repaired_tool_ids = {
        str(msg.get("tool_call_id", "")).strip()
        for msg in result.messages
        if str(msg.get("role", "")).strip().lower() == "tool"
    }

    assert "keep-call" in repaired_tool_ids
    assert "orphan-call" not in repaired_tool_ids
