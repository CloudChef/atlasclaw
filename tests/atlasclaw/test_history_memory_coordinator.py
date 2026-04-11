# -*- coding: utf-8 -*-

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart

from app.atlasclaw.agent.compaction import CompactionConfig, CompactionPipeline
from app.atlasclaw.agent.history_memory import HistoryMemoryCoordinator
from app.atlasclaw.session.context import TranscriptEntry


def test_history_memory_normalize_messages_splits_system_and_user_parts():
    coordinator = HistoryMemoryCoordinator(
        session_manager=object(),
        compaction=CompactionPipeline(CompactionConfig()),
    )
    message = ModelRequest(
        parts=[
            SystemPromptPart(content="system rules"),
            UserPromptPart(content="hello atlas"),
        ]
    )

    normalized = coordinator.normalize_messages([message])

    assert normalized == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "hello atlas"},
    ]


def test_history_memory_to_model_message_history_preserves_tool_call_and_return_structure():
    coordinator = HistoryMemoryCoordinator(
        session_manager=object(),
        compaction=CompactionPipeline(CompactionConfig()),
    )

    model_history = coordinator.to_model_message_history(
        [
            {
                "role": "assistant",
                "content": "准备查询",
                "tool_calls": [
                    {"id": "call-1", "name": "smartcmp_list_pending", "args": {"limit": 3}}
                ],
            },
            {
                "role": "tool",
                "tool_name": "smartcmp_list_pending",
                "tool_call_id": "call-1",
                "content": {"items": [{"id": "REQ-1"}]},
            },
        ]
    )

    assert len(model_history) == 2
    assert isinstance(model_history[0], ModelResponse)
    assert isinstance(model_history[0].parts[1], ToolCallPart)
    assert model_history[0].parts[1].tool_name == "smartcmp_list_pending"
    assert model_history[0].parts[1].args == {"limit": 3}
    assert model_history[0].parts[1].tool_call_id == "call-1"

    assert isinstance(model_history[1], ModelRequest)
    assert isinstance(model_history[1].parts[0], ToolReturnPart)
    assert model_history[1].parts[0].tool_name == "smartcmp_list_pending"
    assert model_history[1].parts[0].tool_call_id == "call-1"
    assert model_history[1].parts[0].content == {"items": [{"id": "REQ-1"}]}


def test_history_memory_build_message_history_repairs_legacy_tool_rows_without_identity_fields():
    coordinator = HistoryMemoryCoordinator(
        session_manager=object(),
        compaction=CompactionPipeline(CompactionConfig()),
    )

    transcript = [
        TranscriptEntry(role="user", content="查下CMP待审批"),
        TranscriptEntry(
            role="assistant",
            content="我来查一下",
            tool_calls=[{"id": "call-legacy-1", "name": "smartcmp_list_pending", "args": {}}],
        ),
        TranscriptEntry(
            role="tool",
            content={"success": True, "items": [{"workflowId": "TIC20260316000001"}]},
        ),
        TranscriptEntry(role="assistant", content="查到了 1 条数据"),
    ]

    history = coordinator.build_message_history(transcript)

    assert history[2]["role"] == "tool"
    assert history[2]["tool_name"] == "smartcmp_list_pending"
    assert history[2]["tool_call_id"] == "call-legacy-1"

    model_history = coordinator.to_model_message_history(history)

    assert len(model_history) == 4
    assert isinstance(model_history[1], ModelResponse)
    assert isinstance(model_history[1].parts[1], ToolCallPart)
    assert isinstance(model_history[2], ModelRequest)
    assert isinstance(model_history[2].parts[0], ToolReturnPart)
    assert model_history[2].parts[0].tool_name == "smartcmp_list_pending"
    assert model_history[2].parts[0].tool_call_id == "call-legacy-1"


def test_history_memory_build_message_history_drops_unmatched_assistant_tool_calls():
    coordinator = HistoryMemoryCoordinator(
        session_manager=object(),
        compaction=CompactionPipeline(CompactionConfig()),
    )

    transcript = [
        TranscriptEntry(role="user", content="查下CMP详情"),
        TranscriptEntry(
            role="assistant",
            content="我来查一下",
            tool_calls=[
                {"id": "call-missing-1", "name": "smartcmp_get_request_detail", "args": {"identifier": "TIC-1"}}
            ],
        ),
        TranscriptEntry(role="assistant", content="稍等"),
    ]

    history = coordinator.build_message_history(transcript)
    model_history = coordinator.to_model_message_history(history)

    assert history == [
        {"role": "user", "content": "查下CMP详情"},
        {"role": "assistant", "content": "我来查一下"},
        {"role": "assistant", "content": "稍等"},
    ]
    assert len(model_history) == 3
    assert all(
        not any(isinstance(part, ToolCallPart) for part in getattr(message, "parts", []))
        for message in model_history
    )
