# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from app.atlasclaw.agent.runtime_events import RuntimeEventDispatcher
from app.atlasclaw.agent.runner_tool.runner_execution_loop import RunnerExecutionLoopMixin
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.core.trace import (
    bind_trace_context,
    build_http_request_log_payload,
    build_http_response_log_payload,
    resolve_trace_context,
    sanitize_log_value,
)


class _HookCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def trigger(self, event_name: str, payload: dict) -> None:
        self.calls.append((event_name, payload))


class _LoopTraceRunner(RunnerExecutionLoopMixin):
    REASONING_ONLY_MAX_RETRIES = 0

    def __init__(self) -> None:
        self.agent = object()

    async def _run_prepare_phase(self, *, state: dict[str, object], _log_step):
        _log_step("prepare_stub")
        state["should_stop"] = True
        if False:
            yield None

    async def _run_loop_phase(self, *, state: dict[str, object], _log_step):
        if False:
            yield None

    async def _run_finalize_phase(self, *, state: dict[str, object]):
        if False:
            yield None


@pytest.mark.parametrize(
    ("session_key", "expected_trace_id", "expected_thread_id"),
    [
        ("agent:main:user:u1:web:dm:peer-1:topic:thread-1", "thread-1", "thread-1"),
        ("agent:main:user:u1:web:dm:peer-1", "agent:main:user:u1:web:dm:peer-1", ""),
    ],
)
def test_resolve_trace_context_prefers_thread_id_then_session_key(
    session_key: str,
    expected_trace_id: str,
    expected_thread_id: str,
) -> None:
    trace = resolve_trace_context(session_key, run_id="run-1")

    assert trace.trace_id == expected_trace_id
    assert trace.thread_id == expected_thread_id
    assert trace.run_id == "run-1"
    assert trace.session_key == session_key


@pytest.mark.asyncio
async def test_runtime_event_dispatcher_llm_input_carries_trace_fields(caplog: pytest.LogCaptureFixture) -> None:
    hooks = _HookCollector()
    dispatcher = RuntimeEventDispatcher(hooks=hooks)
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-1"

    with caplog.at_level(logging.INFO):
        await dispatcher.trigger_llm_input(
            session_key=session_key,
            run_id="run-1",
            user_message="hello " * 120,
            system_prompt="system prompt",
            message_history=[{"role": "user", "content": "history " * 120}],
        )

    assert hooks.calls
    event_name, payload = hooks.calls[0]
    assert event_name == "llm_input"
    assert payload["trace_id"] == "thread-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["run_id"] == "run-1"
    assert payload["session_key"] == session_key
    assert any("llm_trace" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_thinking_stream_llm_output_uses_bound_trace_context() -> None:
    emitter = ThinkingStreamEmitter()
    hooks = _HookCollector()
    trace = resolve_trace_context(
        "agent:main:user:u1:web:dm:peer-1:topic:thread-2",
        run_id="run-2",
    )

    with bind_trace_context(trace):
        events = [
            event
            async for event in emitter._emit_assistant_text(
                content="assistant " * 120,
                hooks=hooks,
                session_key=trace.session_key,
            )
        ]

    assert len(events) == 1
    assert events[0].type == "assistant"
    assert hooks.calls == [
        (
            "llm_output",
            {
                "session_key": trace.session_key,
                "content": "assistant " * 120,
                "trace_id": "thread-2",
                "thread_id": "thread-2",
                "run_id": "run-2",
            },
        )
    ]


def test_http_request_log_payload_redacts_headers_and_truncates_body() -> None:
    trace = resolve_trace_context(
        "agent:main:user:u1:web:dm:peer-1:topic:thread-http",
        run_id="run-http",
    )
    body = {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "system " * 200},
            {"role": "user", "content": "user " * 300},
        ],
        "stream": True,
    }
    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/chat/completions?foo=bar",
        headers={
            "Authorization": "Bearer secret-token",
            "X-Test": "visible",
        },
        content=json.dumps(body).encode("utf-8"),
    )

    payload = build_http_request_log_payload(request, trace)

    assert payload["trace_id"] == "thread-http"
    assert payload["run_id"] == "run-http"
    assert payload["headers"]["Authorization"] == "[REDACTED]"
    assert payload["headers"]["X-Test"] == "visible"
    assert payload["body_snapshot"]["messages"][0]["content"].startswith("system system")
    assert payload["body_snapshot"]["messages"][0]["content"].endswith("...[truncated]")
    assert payload["query"] == "foo=bar"


def test_sanitize_log_value_redacts_provider_schema_sensitive_fields() -> None:
    provider_config = {
        "provider_type": "smartcmp",
        "instance_name": "default",
        "base_url": "https://cmp.example.com/platform-api",
        "auth_type": "user_token",
        "cookie": "CloudChef-Authenticate=session-cookie",
        "password": "super-secret-password",
        "user_token": "fake-smartcmp-user-token",
    }

    payload = sanitize_log_value(
        provider_config,
        provider_type="smartcmp",
        field_defaults=provider_config,
    )

    assert payload["provider_type"] == "smartcmp"
    assert payload["base_url"] == "https://cmp.example.com/platform-api"
    assert payload["cookie"] == "[REDACTED]"
    assert payload["password"] == "[REDACTED]"
    assert payload["user_token"] == "[REDACTED]"


def test_http_response_log_payload_marks_streaming_without_consuming_body() -> None:
    trace = resolve_trace_context(
        "agent:main:user:u1:web:dm:peer-1",
        run_id="run-http",
    )
    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
        content=json.dumps({"stream": True}).encode("utf-8"),
    )
    response = httpx.Response(
        200,
        request=request,
        headers={"x-request-id": "req-1"},
        stream=httpx.ByteStream(b"chunk-1\nchunk-2"),
    )

    payload = build_http_response_log_payload(response, trace)

    assert payload["trace_id"] == trace.trace_id
    assert payload["status_code"] == 200
    assert payload["streaming"] is True
    assert payload["body_snapshot"] == "[streaming response body not captured]"


@pytest.mark.asyncio
async def test_runner_run_step_log_includes_trace_fields(caplog: pytest.LogCaptureFixture) -> None:
    runner = _LoopTraceRunner()
    deps = type("Deps", (), {"extra": {}, "user_info": type("User", (), {"user_id": "u1"})(), "channel": "web"})()
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-77"

    with caplog.at_level(logging.WARNING):
        events = [
            event
            async for event in runner.run(
                session_key=session_key,
                user_message="hello",
                deps=deps,
            )
        ]

    assert events == []
    assert deps.extra["trace_id"] == "thread-77"
    assert deps.extra["thread_id"] == "thread-77"
    assert deps.extra["run_id"]
    run_step_records = [record for record in caplog.records if "run_step" in record.message]
    assert run_step_records
    payload = run_step_records[0].args
    assert payload["trace_id"] == "thread-77"
    assert payload["thread_id"] == "thread-77"
    assert payload["run_id"] == deps.extra["run_id"]
