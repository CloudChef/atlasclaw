# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.atlasclaw.agent.plaintext_tool_calls import looks_like_plaintext_tool_call_attempt
from app.atlasclaw.agent.runtime_events import RuntimeEventDispatcher
from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import RunnerExecutionFlowStreamMixin
from app.atlasclaw.agent.runner_tool.runner_execution_payload import RunnerExecutionPayloadMixin
from app.atlasclaw.agent.runner_tool.runner_execution_runtime import RunnerExecutionRuntimeMixin
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan
from app.atlasclaw.core.trace import bind_trace_context, resolve_trace_context
from app.atlasclaw.hooks.runtime_models import HookEventType


class _HookCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def trigger(self, event_name: str, payload: dict) -> None:
        self.calls.append((event_name, payload))


class _HookRuntimeCollector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


class _ModelRequestNode:
    def __init__(self, *, tool_calls: list[dict] | None = None, content: str = "") -> None:
        self.tool_calls = list(tool_calls or [])
        self.content = content


class _SequencedAgentRun:
    def __init__(self, snapshots: list[list[dict]]) -> None:
        self._snapshots = [list(snapshot) for snapshot in snapshots]
        self._index = 0

    def all_messages(self) -> list[dict]:
        if not self._snapshots:
            return []
        current_index = min(self._index, len(self._snapshots) - 1)
        self._index += 1
        return list(self._snapshots[current_index])


class _StreamHistory:
    @staticmethod
    def normalize_messages(messages):
        return list(messages)

    @staticmethod
    def prune_summary_messages(messages):
        return list(messages)


class _StreamTestRunner(
    RunnerExecutionPayloadMixin,
    RunnerExecutionRuntimeMixin,
    RunnerExecutionFlowStreamMixin,
):
    def __init__(self, *, nodes: list[object], runtime_hooks: _HookCollector, assistant_hooks: _HookCollector) -> None:
        self._nodes = list(nodes)
        self.history = _StreamHistory()
        self.compaction = SimpleNamespace(
            should_compact=lambda *args, **kwargs: False,
        )
        self.context_pruning_settings = SimpleNamespace(enabled=False, mode="off")
        self.hooks = assistant_hooks
        self.runtime_events = RuntimeEventDispatcher(hooks=runtime_hooks)

    @staticmethod
    def _extract_tool_call_arguments(raw_args):
        return raw_args if isinstance(raw_args, dict) else {}

    async def _iter_agent_nodes(self, agent_run):
        if False:
            yield None
        for node in self._nodes:
            yield node


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


def test_collect_plaintext_tool_calls_parses_dsml_markup_from_model_response_text() -> None:
    dispatcher = RuntimeEventDispatcher()
    node = SimpleNamespace(
        model_response=ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "<｜DSML｜function_calls>\n"
                        "<｜DSML｜invoke name=\"openmeteo_weather\">\n"
                        "<｜DSML｜parameter name=\"location\" string=\"true\">上海</｜DSML｜parameter>\n"
                        "<｜DSML｜parameter name=\"days\" string=\"false\">2</｜DSML｜parameter>\n"
                        "<｜DSML｜parameter name=\"target_date\" string=\"true\">2026-04-15</｜DSML｜parameter>\n"
                        "</｜DSML｜invoke>\n"
                        "</｜DSML｜function_calls>"
                    )
                )
            ]
        )
    )

    tool_calls = dispatcher.collect_plaintext_tool_calls(node)

    assert tool_calls == [
        {
            "name": "openmeteo_weather",
            "args": {"location": "上海", "days": 2, "target_date": "2026-04-15"},
        }
    ]


def test_plaintext_tool_call_detection_handles_double_bar_dsml_markup() -> None:
    dispatcher = RuntimeEventDispatcher()
    content = (
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"openmeteo_weather\">\n"
        "<｜｜DSML｜｜parameter name=\"location\" string=\"true\">上海</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"days\" string=\"false\">2</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    node = SimpleNamespace(model_response=ModelResponse(parts=[TextPart(content=content)]))

    assert looks_like_plaintext_tool_call_attempt(content)
    assert dispatcher.collect_plaintext_tool_calls(node) == [
        {"name": "openmeteo_weather", "args": {"location": "上海", "days": 2}}
    ]


@pytest.mark.asyncio
async def test_runtime_event_dispatcher_llm_input_carries_loop_metadata() -> None:
    hooks = _HookCollector()
    dispatcher = RuntimeEventDispatcher(hooks=hooks)
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-1"

    await dispatcher.trigger_llm_input(
        session_key=session_key,
        run_id="run-1",
        user_message="hello",
        system_prompt="system prompt",
        message_history=[{"role": "user", "content": "history"}],
        loop_index=2,
        loop_reason="tool_result_continuation",
        selected_capability_ids=["tool:smartcmp_list_pending", "provider:smartcmp"],
    )

    assert hooks.calls
    event_name, payload = hooks.calls[0]
    assert event_name == "llm_input"
    assert payload["trace_id"] == "thread-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["run_id"] == "run-1"
    assert payload["session_key"] == session_key
    assert payload["loop_index"] == 2
    assert payload["loop_reason"] == "tool_result_continuation"
    assert payload["selected_capability_ids"] == ["tool:smartcmp_list_pending", "provider:smartcmp"]


@pytest.mark.asyncio
async def test_runtime_event_dispatcher_emits_memory_auto_write_diagnostics() -> None:
    hook_runtime = _HookRuntimeCollector()
    dispatcher = RuntimeEventDispatcher(hook_runtime=hook_runtime)
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-memory"

    await dispatcher.trigger_memory_auto_write_completed(
        session_key=session_key,
        run_id="run-memory",
        status="no_memory",
        long_term_count=0,
        diagnostics={
            "json_parse_status": "invalid_json",
            "raw_output_sha256": "abc",
            "skip_reason": "distiller_invalid_json",
        },
    )

    assert len(hook_runtime.events) == 1
    event = hook_runtime.events[0]
    assert event["event_type"] == HookEventType.MEMORY_AUTO_WRITE_COMPLETED
    assert event["user_id"] == "u1"
    assert event["session_key"] == session_key
    assert event["run_id"] == "run-memory"
    assert event["channel"] == "web"
    assert event["agent_id"] == "main"
    assert event["payload"]["trace_id"] == "thread-memory"
    assert event["payload"]["status"] == "no_memory"
    assert event["payload"]["long_term_count"] == 0
    assert event["payload"]["json_parse_status"] == "invalid_json"
    assert event["payload"]["skip_reason"] == "distiller_invalid_json"
    assert event["payload"]["raw_output_sha256"] == "abc"


@pytest.mark.asyncio
async def test_stream_emits_loop_status_for_tool_result_reentry() -> None:
    runtime_hooks = _HookCollector()
    assistant_hooks = _HookCollector()
    runner = _StreamTestRunner(
        nodes=[
            _ModelRequestNode(
                tool_calls=[{"name": "lookup_tool", "args": {"query": "alpha"}}],
            ),
            _ModelRequestNode(content="Final answer."),
        ],
        runtime_hooks=runtime_hooks,
        assistant_hooks=assistant_hooks,
    )
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-2"
    trace = resolve_trace_context(session_key, run_id="run-2")
    agent_run = _SequencedAgentRun(
        snapshots=[
            [],
            [{"role": "tool", "tool_name": "lookup_tool", "content": "ok"}],
            [{"role": "tool", "tool_name": "lookup_tool", "content": "ok"}],
        ]
    )
    state = {
        "deps": SimpleNamespace(extra={}, is_aborted=lambda: False),
        "start_time": 0.0,
        "session": None,
        "session_key": session_key,
        "session_manager": SimpleNamespace(mark_compacted=lambda *args, **kwargs: None),
        "run_id": "run-2",
        "user_message": "Find the latest result.",
        "system_prompt": "system prompt",
        "max_tool_calls": 5,
        "runtime_context_window": None,
        "session_message_history": [],
        "runtime_base_history_len": 0,
        "persist_run_output_start_index": 0,
        "synthetic_tool_messages": [],
        "thinking_emitter": ThinkingStreamEmitter(chunk_delay_seconds=0, chunk_size=1024),
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_tool_names=["lookup_tool"],
            target_capability_classes=["lookup"],
        ),
        "tool_execution_required": False,
        "buffer_direct_answer_output": False,
        "buffered_assistant_events": [],
        "tool_call_summaries": [],
        "executed_tool_names": [],
        "available_tools": [],
    }

    with bind_trace_context(trace):
        events = [
            event
            async for event in runner._run_agent_node_stream(
                agent_run=agent_run,
                state=state,
                _log_step=lambda *args, **kwargs: None,
            )
        ]

    runtime_updates = [event for event in events if event.type == "runtime"]
    initial_status = next(event for event in runtime_updates if event.content == "Analyzing request.")
    reentry_status = next(
        event for event in runtime_updates if event.content == "Re-entering model loop with tool evidence."
    )

    assert initial_status.metadata["loop_index"] == 1
    assert initial_status.metadata["loop_reason"] == "initial_request"
    assert initial_status.metadata["selected_capability_ids"] == ["tool:lookup_tool", "capability:lookup"]
    assert reentry_status.metadata["loop_index"] == 2
    assert reentry_status.metadata["loop_reason"] == "tool_result_continuation"
    assert reentry_status.metadata["tool_result_count"] == 1
    assert reentry_status.metadata["selected_capability_ids"] == ["tool:lookup_tool", "capability:lookup"]

    assert [payload["loop_index"] for event_name, payload in runtime_hooks.calls if event_name == "llm_input"] == [1, 2]
    assert [payload["loop_reason"] for event_name, payload in runtime_hooks.calls if event_name == "llm_input"] == [
        "initial_request",
        "tool_result_continuation",
    ]
    assert [
        payload["selected_capability_ids"] for event_name, payload in runtime_hooks.calls if event_name == "llm_input"
    ] == [
        ["tool:lookup_tool", "capability:lookup"],
        ["tool:lookup_tool", "capability:lookup"],
    ]
    assert assistant_hooks.calls == [
        (
            "llm_output",
            {
                "session_key": session_key,
                "content": "Final answer.",
                "trace_id": "thread-2",
                "thread_id": "thread-2",
                "run_id": "run-2",
            },
        )
    ]


@pytest.mark.asyncio
async def test_stream_buffers_preamble_when_model_response_contains_tool_call_even_without_required_flag() -> None:
    runtime_hooks = _HookCollector()
    assistant_hooks = _HookCollector()
    runner = _StreamTestRunner(
        nodes=[
            _ModelRequestNode(
                content="我来帮您查询SmartCMP当前的审批数据。",
                tool_calls=[{"name": "smartcmp_list_pending", "args": {}}],
            ),
        ],
        runtime_hooks=runtime_hooks,
        assistant_hooks=assistant_hooks,
    )
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-3"
    trace = resolve_trace_context(session_key, run_id="run-3")
    agent_run = _SequencedAgentRun(
        snapshots=[
            [],
            [
                {
                    "role": "assistant",
                    "content": "我来帮您查询SmartCMP当前的审批数据。",
                    "tool_calls": [{"id": "cmp-1", "name": "smartcmp_list_pending", "args": {}}],
                },
                {
                    "role": "tool",
                    "tool_name": "smartcmp_list_pending",
                    "content": {"output": "##APPROVAL_META_START##[]##APPROVAL_META_END##"},
                },
            ],
        ]
    )
    state = {
        "deps": SimpleNamespace(extra={}, is_aborted=lambda: False),
        "start_time": 0.0,
        "session": None,
        "session_key": session_key,
        "session_manager": SimpleNamespace(mark_compacted=lambda *args, **kwargs: None),
        "run_id": "run-3",
        "user_message": "查下CMP现在的审批数据",
        "system_prompt": "system prompt",
        "max_tool_calls": 5,
        "runtime_context_window": None,
        "session_message_history": [],
        "runtime_base_history_len": 0,
        "persist_run_output_start_index": 0,
        "synthetic_tool_messages": [],
        "thinking_emitter": ThinkingStreamEmitter(chunk_delay_seconds=0, chunk_size=1024),
        "tool_intent_plan": None,
        "tool_execution_required": False,
        "buffer_direct_answer_output": False,
        "buffered_assistant_events": [],
        "tool_call_summaries": [],
        "executed_tool_names": [],
        "available_tools": [{"name": "smartcmp_list_pending", "result_mode": "tool_only_ok"}],
    }

    with bind_trace_context(trace):
        events = [
            event
            async for event in runner._run_agent_node_stream(
                agent_run=agent_run,
                state=state,
                _log_step=lambda *args, **kwargs: None,
            )
        ]

    assistant_events = [event for event in events if event.type == "assistant"]
    tool_events = [event for event in events if event.type == "tool"]

    assert assistant_events == []
    assert [event.phase for event in tool_events] == ["start", "end"]
    assert len(state["buffered_assistant_events"]) == 1
    assert state["buffered_assistant_events"][0].content == "我来帮您查询SmartCMP当前的审批数据。"


@pytest.mark.asyncio
async def test_stream_buffers_plaintext_dsml_tool_call_attempt_without_leaking_markup() -> None:
    runtime_hooks = _HookCollector()
    assistant_hooks = _HookCollector()
    runner = _StreamTestRunner(
        nodes=[
            _ModelRequestNode(
                content=(
                    "我来为您查询明天上海的天气情况。\n\n"
                    "<｜DSML｜function_calls>\n"
                    "<｜DSML｜invoke name=\"openmeteo_weather\">\n"
                    "<｜DSML｜parameter name=\"location\" string=\"true\">上海</｜DSML｜parameter>\n"
                    "<｜DSML｜parameter name=\"days\" string=\"false\">2</｜DSML｜parameter>\n"
                    "</｜DSML｜invoke>\n"
                    "</｜DSML｜function_calls>"
                ),
            ),
        ],
        runtime_hooks=runtime_hooks,
        assistant_hooks=assistant_hooks,
    )
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-4"
    trace = resolve_trace_context(session_key, run_id="run-4")
    agent_run = _SequencedAgentRun(
        snapshots=[
            [],
            [
                {
                    "role": "assistant",
                    "content": (
                        "我来为您查询明天上海的天气情况。\n\n"
                        "<｜DSML｜function_calls>\n"
                        "<｜DSML｜invoke name=\"openmeteo_weather\">\n"
                        "<｜DSML｜parameter name=\"location\" string=\"true\">上海</｜DSML｜parameter>\n"
                        "<｜DSML｜parameter name=\"days\" string=\"false\">2</｜DSML｜parameter>\n"
                        "</｜DSML｜invoke>\n"
                        "</｜DSML｜function_calls>"
                    ),
                },
            ],
        ]
    )
    state = {
        "deps": SimpleNamespace(extra={}, is_aborted=lambda: False),
        "start_time": 0.0,
        "session": None,
        "session_key": session_key,
        "session_manager": SimpleNamespace(mark_compacted=lambda *args, **kwargs: None),
        "run_id": "run-4",
        "user_message": "上海呢",
        "system_prompt": "system prompt",
        "max_tool_calls": 5,
        "runtime_context_window": None,
        "session_message_history": [],
        "runtime_base_history_len": 0,
        "persist_run_output_start_index": 0,
        "synthetic_tool_messages": [],
        "thinking_emitter": ThinkingStreamEmitter(chunk_delay_seconds=0, chunk_size=1024),
        "tool_intent_plan": None,
        "tool_execution_required": False,
        "buffer_direct_answer_output": False,
        "buffered_assistant_events": [],
        "tool_call_summaries": [],
        "executed_tool_names": [],
        "available_tools": [{"name": "openmeteo_weather"}],
    }

    with bind_trace_context(trace):
        events = [
            event
            async for event in runner._run_agent_node_stream(
                agent_run=agent_run,
                state=state,
                _log_step=lambda *args, **kwargs: None,
            )
        ]

    assistant_events = [event for event in events if event.type == "assistant"]
    warning_events = [
        event for event in events if event.type == "runtime" and event.metadata.get("state") == "warning"
    ]

    assert assistant_events == []
    assert warning_events
    assert state["plaintext_tool_call_attempt"] is True
    assert state["tool_call_summaries"] == [
        {"name": "openmeteo_weather", "args": {"location": "上海", "days": 2}}
    ]
    assert state["buffered_assistant_events"]
