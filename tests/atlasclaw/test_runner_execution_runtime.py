# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.runner_tool.runner_execution_runtime import RunnerExecutionRuntimeMixin
from app.atlasclaw.core.deps import SkillDeps


class _FakeAgent:
    def __init__(self) -> None:
        self.web_search_tool = object()
        self.cmp_lookup_tool = object()
        self._function_toolset = SimpleNamespace(
            tools={
                "web_search": self.web_search_tool,
                "cmp_lookup": self.cmp_lookup_tool,
            }
        )
        self.override_calls: list[dict] = []
        self.iter_calls: list[dict] = []

    @contextmanager
    def override(self, **kwargs):
        self.override_calls.append(dict(kwargs))
        yield

    @asynccontextmanager
    async def iter(self, user_message, *, deps, message_history):
        self.iter_calls.append(
            {
                "user_message": user_message,
                "deps": deps,
                "message_history": list(message_history or []),
            }
        )
        yield SimpleNamespace()


class _RuntimeRunner(RunnerExecutionRuntimeMixin):
    pass


@pytest.mark.asyncio
async def test_run_iter_override_disables_tools_for_direct_answer_turns() -> None:
    runner = _RuntimeRunner()
    agent = _FakeAgent()
    deps = SkillDeps(
        session_key="agent:main:user:test",
        extra={
            "run_id": "run-direct",
            "runtime_allowed_tool_names": [],
        },
    )

    async with runner._run_iter_with_optional_override(
        agent=agent,
        user_message="我想查下上海周边的骑行公园",
        deps=deps,
        message_history=[],
        system_prompt="Answer directly.",
    ):
        pass

    assert agent.override_calls
    override_kwargs = agent.override_calls[-1]
    assert override_kwargs["instructions"] == "Answer directly."
    assert override_kwargs["tools"] == []


@pytest.mark.asyncio
async def test_run_iter_override_limits_tools_to_projected_subset() -> None:
    runner = _RuntimeRunner()
    agent = _FakeAgent()
    deps = SkillDeps(
        session_key="agent:main:user:test",
        extra={
            "run_id": "run-tools",
            "runtime_allowed_tool_names": ["cmp_lookup"],
        },
    )

    async with runner._run_iter_with_optional_override(
        agent=agent,
        user_message="查下 CMP 待审批",
        deps=deps,
        message_history=[],
        system_prompt="Use the projected tool subset.",
    ):
        pass

    assert agent.override_calls
    override_kwargs = agent.override_calls[-1]
    assert override_kwargs["instructions"] == "Use the projected tool subset."
    assert override_kwargs["tools"] == [agent.cmp_lookup_tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("global_locale", [None, "zh-CN"])
async def test_iter_shares_one_language_policy_after_workflow_instructions(global_locale):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from app.atlasclaw.agent.prompt_sections import build_response_language
    from app.atlasclaw.agent.runner_tool.runner_progress_hooks import PROGRESS_SINK

    captured = []

    async def respond(messages, info):
        captured.append(info.instructions)
        return ModelResponse(parts=[TextPart("Done")])

    runner = _RuntimeRunner()
    runner.prompt_builder = SimpleNamespace(config=SimpleNamespace(response_language=global_locale))
    policy = build_response_language(global_locale, "en-US")
    deps = SkillDeps(extra={PROGRESS_SINK: lambda *args: None, "context": {"ui_locale": "en-US"}})
    agent = Agent(FunctionModel(respond))
    async with runner._run_iter_with_optional_override(
        agent=agent, user_message="查找资源", deps=deps, message_history=[],
        system_prompt=policy + "\n\nWorkflow instructions.",
    ) as agent_run:
        async for _ in agent_run:
            pass

    instructions = captured[0]
    assert instructions.count(policy) == 1
    assert instructions.endswith(policy)
    assert instructions.index("Workflow instructions.") < instructions.index(policy)
    assert "Request UI locale: `en-US`" in instructions
    assert bool("Global default response language:" in instructions) == bool(global_locale)
