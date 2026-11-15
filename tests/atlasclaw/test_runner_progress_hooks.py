# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Generic model updates reuse native context and preserve execution semantics."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.atlasclaw.agent.runner_tool.runner_progress_hooks import PROGRESS_SINK, build_progress_hooks
from app.atlasclaw.core.deps import SkillDeps


@pytest.mark.asyncio
async def test_native_model_commentary_is_generic_and_does_not_add_calls():
    name = "example_lookup"
    events = []
    model_calls = 0
    tool_calls = 0

    def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart(name, {}, "read-1")])
        if model_calls == 2:
            return ModelResponse(parts=[TextPart("The confirmed item is blue; I will check its available choices."),
                                        ToolCallPart(name, {}, "read-2")])
        return ModelResponse(parts=[TextPart("The final answer.")])

    agent = Agent(FunctionModel(model), deps_type=SkillDeps)
    async def lookup():
        nonlocal tool_calls
        tool_calls += 1
        return {"success": True, "items": [{"color": "blue"}]}
    agent.tool_plain(lookup, name=name)
    deps = SkillDeps(extra={PROGRESS_SINK: lambda *args: events.append(args),
                           "tools_snapshot": [{"name": name, "read_only": True}]})
    async with agent.iter("Inspect the item", deps=deps, capabilities=[build_progress_hooks(deps)]) as run:
        async for _ in run:
            pass
    updates = [message for _, message, meta in events if meta["source"] == "agent_model"]
    assert updates == ["The confirmed item is blue; I will check its available choices."]
    assert run.result.output == "The final answer."
    assert model_calls == 3
    assert tool_calls == 2


@pytest.mark.asyncio
async def test_partial_tool_validation_failure_prevents_publishing_batch_commentary():
    events = []
    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {}, "valid"),
                                        ToolCallPart("lookup", {"unexpected": 1}, "invalid")])
        if requests == 2:
            return ModelResponse(parts=[TextPart("All checks completed."), ToolCallPart("lookup", {}, "retry")])
        return ModelResponse(parts=[TextPart("Final answer")])

    agent = Agent(FunctionModel(model), deps_type=SkillDeps)
    @agent.tool_plain
    async def lookup():
        return {"success": True}
    deps = SkillDeps(extra={PROGRESS_SINK: lambda *args: events.append(args),
                           "tools_snapshot": [{"name": "lookup", "read_only": True}]})
    async with agent.iter("Check", deps=deps, capabilities=[build_progress_hooks(deps)]) as run:
        async for _ in run:
            pass
    assert run.result.output == "Final answer"
    assert not any(meta["source"] == "agent_model" for _, _, meta in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["plain_text_result", "failure", "accepted", "write", "earlier_write", "final_only", "old_run", "raw_echo"])
async def test_native_message_context_controls_early_output(variant):
    events = []
    snapshot = [{"name": "lookup", "read_only": variant != "write"}]
    deps = SkillDeps(extra={PROGRESS_SINK: lambda *args: events.append(args), "tools_snapshot": snapshot})
    call = ToolCallPart("lookup", {}, "a")
    previous = ModelResponse(parts=[call], run_id="run")
    content = {"success": True}
    if variant == "failure": content = {"success": False}
    if variant == "accepted": content = {"success": True, "status": "accepted"}
    if variant == "plain_text_result": content = "A confirmed blue item"
    result = ToolReturnPart("lookup", content, tool_call_id="a")
    returned = ModelRequest(parts=[result], run_id="old" if variant == "old_run" else "run")
    messages = [previous, returned]
    if variant == "earlier_write":
        messages.insert(0, ModelResponse(parts=[ToolCallPart("mutate", {}, "write")], run_id="run"))
    parts = [TextPart('{"success":true,"_internal":{"credential":"private"}}' if variant == "raw_echo" else "A confirmed item is blue.")]
    if variant != "final_only": parts.append(ToolCallPart("lookup", {}, "b"))
    response = ModelResponse(parts=parts)
    request = SimpleNamespace(messages=messages)
    ctx = SimpleNamespace(run_step=2, run_id="run")
    hooks = build_progress_hooks(deps)
    assert await hooks.after_model_request(ctx, request_context=request, response=response) is response
    assert bool(events) is (variant == "plain_text_result")
    assert all(meta["source"] == "agent_model" for _, _, meta in events)
