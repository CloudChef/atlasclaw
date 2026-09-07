# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Language policy at the actual recovery model request boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from app.atlasclaw.agent import prompt_sections
from app.atlasclaw.agent.history_memory import HistoryMemoryCoordinator
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig, PromptMode
from app.atlasclaw.agent.runner_tool.runner_execution_flow_post import RunnerExecutionFlowPostMixin
from app.atlasclaw.agent.runner_tool.runner_execution_payload import RunnerExecutionPayloadMixin


class _RecoveryRunner(RunnerExecutionPayloadMixin, RunnerExecutionFlowPostMixin):
    def __init__(self, global_locale, capture):
        async def respond(messages, info):
            capture.append((messages, info))
            return ModelResponse(parts=[TextPart("Recovered answer")])

        self.agent = Agent(FunctionModel(respond))
        self.prompt_builder = PromptBuilder(PromptBuilderConfig(response_language=global_locale))
        self.history = HistoryMemoryCoordinator(None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("global_locale,ui_locale", [("zh-CN", "ja-JP"), (None, "ja-JP"), (None, "auto")])
async def test_single_recovery_injects_runtime_language_at_model_boundary(global_locale, ui_locale):
    captured = []
    runner = _RecoveryRunner(global_locale, captured)
    result = await runner.run_single(
        "Please answer in English: create a ticket.",
        SimpleNamespace(extra={"context": {"ui_locale": ui_locale}}),
        system_prompt="No action was executed. Explain the missing capability.",
        allowed_tool_names=[],
    )

    assert result == "Recovered answer"
    messages, info = captured[0]
    assert "No action was executed" in info.instructions
    assert "Always use the locale or response language explicitly requested by the user" in info.instructions
    assert bool("Global default response language:" in info.instructions) == bool(global_locale)
    if global_locale:
        assert "Global default response language: `zh-CN`" in info.instructions
    if ui_locale == "ja-JP":
        assert "Request UI locale: `ja-JP`" in info.instructions
    else:
        assert "Request UI locale:" not in info.instructions
    assert not info.function_tools
    assert any(
        "Please answer in English" in str(part.content)
        for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, UserPromptPart)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["direct", "lookup"])
async def test_recovery_keeps_prior_explicit_language_request(recovery):
    captured = []
    runner = _RecoveryRunner("zh-CN", captured)
    kwargs = dict(
        user_message="Create a ticket.",
        invalid_output="<tool_call>invalid</tool_call>",
        deps=SimpleNamespace(extra={"context": {"ui_locale": "ja-JP"}}),
        agent=runner.agent,
        message_history=[
            {"role": "user", "content": "For this conversation, please reply in German."},
            {"role": "assistant", "content": "Verstanden."},
        ],
    )
    if recovery == "direct":
        result = await runner._generate_direct_answer_recovery_answer(workflow_system_prompt="", **kwargs)
    else:
        result = await runner._generate_lookup_dump_recovery_answer(final_messages=[], start_index=0, **kwargs)

    assert result == "Recovered answer"
    messages, info = captured[0]
    user_parts = [
        str(part.content)
        for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, UserPromptPart)
    ]
    assert user_parts[0] == "For this conversation, please reply in German."
    assert "Create a ticket." in user_parts[-1]
    assert "Global default response language: `zh-CN`" in info.instructions
    assert "Request UI locale: `ja-JP`" in info.instructions
    assert "use the user's language" not in info.instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("global_locale", [None, "zh-CN"])
async def test_workflow_recovery_does_not_duplicate_existing_language_policy(global_locale):
    captured = []
    runner = _RecoveryRunner(global_locale, captured)
    workflow_prompt = runner.prompt_builder.build(ui_locale="ja-JP", mode_override=PromptMode.MINIMAL)

    result = await runner._generate_direct_answer_recovery_answer(
        user_message="Create a ticket.",
        invalid_output="<tool_call>invalid</tool_call>",
        workflow_system_prompt=workflow_prompt,
        deps=SimpleNamespace(extra={"context": {"ui_locale": "ja-JP"}}),
        agent=runner.agent,
    )

    assert result == "Recovered answer"
    instructions = captured[0][1].instructions
    assert instructions.count("## Response Language") == 1
    assert instructions.count("Request UI locale: `ja-JP`") == 1
    assert "The current provider and skill workflow remains authorized and active" in instructions
    assert "Do not claim that any provider, skill, or tool is missing" in instructions


@pytest.mark.asyncio
async def test_recovery_heading_alone_does_not_suppress_runtime_language_policy():
    captured = []
    runner = _RecoveryRunner("zh-CN", captured)
    await runner.run_single(
        "Create a ticket.",
        SimpleNamespace(extra={"context": {"ui_locale": "ja-JP"}}),
        system_prompt="## Response Language\nA custom section without the runtime policy.",
        allowed_tool_names=[],
    )

    instructions = captured[0][1].instructions
    assert "Global default response language: `zh-CN`" in instructions
    assert "Request UI locale: `ja-JP`" in instructions
    assert "Always use the locale or response language explicitly requested by the user" in instructions


def test_language_renderer_reads_external_template_and_interpolates_only_available_locales(tmp_path, monkeypatch):
    template = tmp_path / "response_language.json"
    template.write_text(json.dumps({
        "policy": ["Custom language policy."],
        "global_default": ["Global: {response_language}"],
        "request_locale": ["UI: {ui_locale}"],
        "scope": ["Custom scope."],
    }), encoding="utf-8")
    monkeypatch.setattr(prompt_sections, "_RESPONSE_LANGUAGE_TEMPLATE_PATH", template, raising=False)

    assert prompt_sections.build_response_language("zh-CN", "ja-JP") == (
        "Custom language policy.\nGlobal: zh-CN\nUI: ja-JP\nCustom scope."
    )
    assert prompt_sections.build_response_language() == "Custom language policy.\nCustom scope."
