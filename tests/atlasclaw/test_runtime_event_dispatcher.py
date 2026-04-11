# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.atlasclaw.agent.runtime_events import RuntimeEventDispatcher


def test_collect_tool_calls_reads_tool_call_parts_from_model_response_node():
    dispatcher = RuntimeEventDispatcher()
    node = SimpleNamespace(
        model_response=ModelResponse(
            parts=[
                TextPart(content="先查一下"),
                ToolCallPart("smartcmp_request_detail", {"request_id": "TIC20260316000001"}, tool_call_id="call-1"),
            ]
        )
    )

    tool_calls = dispatcher.collect_tool_calls(node)

    assert tool_calls == [
        {
            "id": "call-1",
            "name": "smartcmp_request_detail",
            "args": {"request_id": "TIC20260316000001"},
        }
    ]
