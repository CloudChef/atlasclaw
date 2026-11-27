# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider visual bridge tests for multi-image order and response completeness."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pydantic_ai.exceptions as pydantic_ai_exceptions

import app.atlasclaw.core.provider_http_runtime as visual_runtime_module
from app.atlasclaw.core.provider_http_runtime import (
    ProviderHttpContext,
    ProviderHttpRuntimeError,
    ProviderVisualRuntime,
)
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.token_pool import TokenEntry


class _AgentRunResult:
    def __init__(self, output: str, finish_reason: str) -> None:
        self.output = output
        self._messages = [SimpleNamespace(kind="response", finish_reason=finish_reason)]

    def new_messages(self):
        return self._messages


class _VisualAgent:
    def __init__(self, result: _AgentRunResult) -> None:
        self.result = result
        self.received = None

    async def run(self, parts, deps=None):
        self.received = parts
        return self.result


def _runtime(monkeypatch: pytest.MonkeyPatch, result: _AgentRunResult):
    captured = {}
    visual_agent = _VisualAgent(result)

    def build_agent(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return visual_agent

    monkeypatch.setattr(visual_runtime_module, "Agent", build_agent)
    model = object()
    main_agent = SimpleNamespace(
        model=model,
        model_settings={"max_tokens": 49152, "extra_body": {"reasoning": {"effort": "low"}}},
    )
    return ProviderVisualRuntime(SimpleNamespace(agent=main_agent)), visual_agent, captured


@pytest.mark.asyncio
async def test_visual_runtime_sends_multiple_images_in_order_and_inherits_model_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, visual_agent, captured = _runtime(monkeypatch, _AgentRunResult("complete", "stop"))
    images = [
        {"data": b"one", "media_type": "image/png", "identifier": "page-1"},
        {"data": b"two", "media_type": "image/png", "identifier": "page-2"},
    ]

    result = await runtime.run_visual(
        images=images, prompt="Review", system_prompt="visual system", timeout_seconds=10,
    )

    assert result == "complete"
    assert visual_agent.received[0] == "Review"
    assert visual_agent.received[1] == "图像标识：page-1"
    assert visual_agent.received[2].identifier == "page-1"
    assert visual_agent.received[2].data == b"one"
    assert visual_agent.received[3] == "图像标识：page-2"
    assert visual_agent.received[4].identifier == "page-2"
    assert visual_agent.received[4].data == b"two"
    assert captured["model_settings"] == {
        "max_tokens": 49152, "extra_body": {"reasoning": {"effort": "low"}}, "timeout": None,
    }


@pytest.mark.asyncio
async def test_visual_runtime_preserves_legacy_single_image_call(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, visual_agent, _captured = _runtime(monkeypatch, _AgentRunResult("ok", "stop"))

    result = await runtime.run_visual(
        data=b"legacy", media_type="image/png", prompt="Review", system_prompt="visual system",
        identifier="page-7", timeout_seconds=10,
    )

    assert result == "ok"
    assert len(visual_agent.received) == 2
    assert visual_agent.received[1].identifier == "page-7"
    assert visual_agent.received[1].data == b"legacy"


@pytest.mark.asyncio
async def test_visual_runtime_with_no_timeout_awaits_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, visual_agent, captured = _runtime(monkeypatch, _AgentRunResult("ok", "stop"))

    async def reject_wait_for(*_args, **_kwargs):
        pytest.fail("no-timeout visual calls must not use asyncio.wait_for")

    monkeypatch.setattr(visual_runtime_module.asyncio, "wait_for", reject_wait_for)
    result = await runtime.run_visual(
        images=[{"data": b"page", "media_type": "image/png", "identifier": "page-1"}],
        prompt="Review", system_prompt="visual system",
    )

    assert result == "ok"
    assert len(visual_agent.received) == 3
    assert captured["model_settings"]["timeout"] is None


@pytest.mark.asyncio
async def test_visual_runtime_explicit_timeout_still_maps_to_504(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, visual_agent, _captured = _runtime(monkeypatch, _AgentRunResult("ok", "stop"))

    async def wait_forever(_parts):
        await asyncio.sleep(5)

    visual_agent.run = wait_forever
    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_visual(
            data=b"page", media_type="image/png", prompt="Review", system_prompt="visual system",
            timeout_seconds=0.01,
        )

    assert exc.value.status_code == 504
    assert exc.value.code == "visual_model_timeout"


@pytest.mark.asyncio
async def test_http_context_forwards_multiple_images_to_visual_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, visual_agent, _captured = _runtime(monkeypatch, _AgentRunResult("ok", "stop"))
    context = ProviderHttpContext(
        provider_type="audit-report", provider_instance="default", provider_config={},
        path_params={}, user_id="user", tenant_id="tenant", is_admin=False,
        visual_runtime=runtime,
    )

    result = await context.run_visual(
        images=[{"data": b"page", "media_type": "image/png", "identifier": "page-4"}],
        prompt="Review", system_prompt="visual system", timeout_seconds=10,
    )

    assert result == "ok"
    assert visual_agent.received[1] == "图像标识：page-4"
    assert visual_agent.received[2].identifier == "page-4"


@pytest.mark.asyncio
async def test_http_context_forwards_no_visual_timeout() -> None:
    class Runtime:
        def __init__(self):
            self.timeout_seconds = "unset"

        @property
        def available(self):
            return True

        async def run_visual(self, **kwargs):
            self.timeout_seconds = kwargs["timeout_seconds"]
            return "ok"

    runtime = Runtime()
    context = ProviderHttpContext(
        provider_type="audit-report", provider_instance="default", provider_config={},
        path_params={}, user_id="user", tenant_id="tenant", is_admin=False,
        visual_runtime=runtime,
    )

    result = await context.run_visual(
        images=[{"data": b"page", "media_type": "image/png", "identifier": "page-1"}],
        prompt="Review", system_prompt="visual system", timeout_seconds=None,
    )

    assert result == "ok"
    assert runtime.timeout_seconds is None


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error"])
async def test_visual_runtime_rejects_incomplete_response_even_with_text(
    monkeypatch: pytest.MonkeyPatch, finish_reason: str,
) -> None:
    runtime, _visual_agent, _captured = _runtime(
        monkeypatch, _AgentRunResult("partial private text", finish_reason)
    )

    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_visual(
            data=b"image", media_type="image/png", prompt="Review", system_prompt="visual system",
            identifier="page-7", timeout_seconds=10,
        )

    assert exc.value.code == "visual_model_incomplete"
    assert "partial private text" not in str(exc.value)

class _TextAgent:
    def __init__(self, output: str = "text result", finish_reason: str = "stop") -> None:
        self.output = output
        self.finish_reason = finish_reason
        self.calls = []

    async def run(self, prompt, deps=None):
        self.calls.append(prompt)
        return _AgentRunResult(self.output, self.finish_reason)


def _text_runtime(monkeypatch: pytest.MonkeyPatch, *, output="text result", finish_reason="stop"):
    captured = {}
    text_agent = _TextAgent(output, finish_reason)

    def build_agent(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return text_agent

    monkeypatch.setattr(visual_runtime_module, "Agent", build_agent)
    model = object()
    model_settings = {"max_tokens": 65536, "temperature": 0.1}
    main_agent = SimpleNamespace(model=model, model_settings=model_settings)
    return ProviderVisualRuntime(SimpleNamespace(agent=main_agent)), text_agent, captured, model_settings


@pytest.mark.asyncio
async def test_context_run_text_uses_one_core_model_call_and_inherits_settings_without_host_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, text_agent, captured, model_settings = _text_runtime(monkeypatch)

    class HostAgentMustNotRun:
        async def start_run(self, **kwargs):
            raise AssertionError("run_text must not enter ProviderAgentHost chat routing")

    context = ProviderHttpContext(
        provider_type="audit-report", provider_instance="default", provider_config={},
        path_params={}, user_id="user", tenant_id="tenant", is_admin=False,
        visual_runtime=runtime, host=SimpleNamespace(agent=HostAgentMustNotRun()),
    )
    result = await context.run_text(
        prompt="Return one synthetic report stage.", system_prompt="single stage only", timeout_seconds=10,
    )

    assert result == "text result"
    assert text_agent.calls == ["Return one synthetic report stage."]
    assert captured["model_settings"] == {**model_settings, "timeout": None}
    assert "retries" not in captured


@pytest.mark.asyncio
async def test_provider_bridge_resolves_bound_skill_and_uses_fresh_selected_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, text_agent, captured, _settings = _text_runtime(monkeypatch)
    credential = TokenEntry(
        token_id="bound-token", provider="openai", model="bound-model",
        base_url="https://example.invalid/v1", api_key="fresh-secret",
    )
    selected_model = object()
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice"),
        permissions={
            "skills": {"allow_all": False, "skill_permissions": []},
            "providers": {
                "allow_all": False,
                "provider_permissions": [{
                    "provider_type": "audit-report", "instance_name": "default", "allowed": True,
                }],
            },
        },
    )
    targets = []

    class Resolver:
        async def resolve(self, target, *, authz=None, required_capabilities=None, admission=None):
            targets.append((target, authz, required_capabilities))
            return SimpleNamespace(token=credential)

    class Pool:
        async def get_or_create(self, agent_id, token, factory):
            return SimpleNamespace(
                agent=SimpleNamespace(model=selected_model, model_settings={"temperature": 0.2}),
                concurrency_sem=asyncio.Semaphore(1),
            )

    runner = SimpleNamespace(
        agent=SimpleNamespace(model=object(), model_settings={"temperature": 0.1}),
        agent_id="main", agent_pool=Pool(), agent_factory=lambda *_args: None,
    )
    registry = SimpleNamespace(md_snapshot=lambda: [{
        "name": "audit-report", "qualified_name": "audit-report:audit-report",
        "provider": "audit-report", "metadata": {"provider_type": "audit-report"},
    }])
    runtime = ProviderVisualRuntime(
        runner,
        binding_resolver=Resolver(),
        authz=authz,
        provider_type="audit-report",
        provider_instance="default",
        user_id="alice",
        skill_registry=registry,
    )
    result = await runtime.run_text(
        prompt="review", system_prompt="system", skill_id="audit-report:audit-report",
    )

    assert result == "text result"
    assert captured["model"] is selected_model
    assert targets[0][0].kind == "provider_skill"
    assert targets[0][0].provider_type == "audit-report"
    assert targets[0][0].instance_name == "default"
    assert targets[0][0].skill_id == "audit-report:audit-report"
    assert targets[0][1] is authz
    assert targets[0][2] == {}
    assert text_agent.calls == ["review"]


@pytest.mark.asyncio
async def test_provider_visual_binding_requests_vision_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, visual_agent, captured = _runtime(monkeypatch, _AgentRunResult("complete", "stop"))
    token = TokenEntry(
        token_id="vision-token", provider="openai", model="vision-model",
        base_url="https://example.invalid/v1", api_key="fresh-secret",
    )
    selected_model = object()
    targets = []
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice"),
        permissions={
            "skills": {"allow_all": False, "skill_permissions": []},
            "providers": {
                "allow_all": False,
                "provider_permissions": [{
                    "provider_type": "audit-report", "instance_name": "default", "allowed": True,
                }],
            },
        },
    )

    class Resolver:
        async def resolve(self, target, *, authz=None, required_capabilities=None, admission=None):
            targets.append(required_capabilities)
            return SimpleNamespace(token=token)

    class Pool:
        async def get_or_create(self, agent_id, token, factory):
            return SimpleNamespace(
                agent=SimpleNamespace(model=selected_model, model_settings={}),
                concurrency_sem=asyncio.Semaphore(1),
            )

    runtime = ProviderVisualRuntime(
        SimpleNamespace(
            agent=SimpleNamespace(model=object(), model_settings={}),
            agent_id="main", agent_pool=Pool(), agent_factory=lambda *_args: None,
        ),
        binding_resolver=Resolver(),
        authz=authz,
        provider_type="audit-report",
        provider_instance="default",
        user_id="alice",
        skill_registry=SimpleNamespace(md_snapshot=lambda: [{
            "name": "audit-report", "qualified_name": "audit-report:audit-report",
            "provider": "audit-report", "metadata": {"provider_type": "audit-report"},
        }]),
    )
    await runtime.run_visual(
        prompt="review", system_prompt="system", timeout_seconds=5,
        data=b"page", media_type="image/png", skill_id="audit-report:audit-report",
    )

    assert targets == [{"vision": True}]
    assert captured["model"] is selected_model


@pytest.mark.asyncio
async def test_run_text_with_no_timeout_awaits_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, agent, captured, _settings = _text_runtime(monkeypatch)
    result = await runtime.run_text(prompt="stage", system_prompt="system", timeout_seconds=None)

    assert result == "text result"
    assert agent.calls == ["stage"]
    assert captured["model_settings"]["timeout"] is None
    assert "retries" not in captured


@pytest.mark.asyncio
async def test_real_openai_sdk_request_has_no_added_caps_and_all_transport_timeouts_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import httpx2
    from app.atlasclaw.bootstrap import startup_helpers
    from app.atlasclaw.core.config_schema import ModelConfig
    from app.atlasclaw.core.token_pool import TokenEntry
    from app.atlasclaw.main import _agent_model_settings

    captured: list[dict] = []
    timeout_extensions: list[dict] = []

    async def respond(request):
        captured.append(json.loads(request.content))
        timeout_extensions.append(request.extensions.get("timeout", {}))
        return httpx2.Response(200, json={
            "id": "mock", "object": "chat.completion", "created": 0,
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                         "finish_reason": "stop"}],
        })

    monkeypatch.setattr(
        startup_helpers, "create_traced_http_client",
        lambda _provider: httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    )
    model = startup_helpers.create_pydantic_model(TokenEntry(
        token_id="test-dummy", provider="openrouter", model="deepseek/deepseek-v4.1-flash-1017",
        base_url="https://openrouter.ai/api/v1", api_key="dummy-not-a-credential", api_type="openai",
    ))
    configured_settings = _agent_model_settings(ModelConfig())
    runtime = ProviderVisualRuntime(SimpleNamespace(
        agent=SimpleNamespace(model=model, model_settings=configured_settings)
    ))

    try:
        result = await runtime.run_text(prompt="Return OK.", system_prompt="Diagnostic.", timeout_seconds=None)
    finally:
        await model.client.close()

    assert result == "OK"
    assert len(captured) == 1
    assert "max_tokens" not in captured[0]
    assert "max_completion_tokens" not in captured[0]
    assert "reasoning" not in captured[0]
    assert "thinking" not in captured[0]
    assert timeout_extensions == [{"connect": None, "read": None, "write": None, "pool": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error"])
async def test_run_text_rejects_incomplete_response(
    monkeypatch: pytest.MonkeyPatch, finish_reason: str,
) -> None:
    runtime, _agent, _captured, _settings = _text_runtime(
        monkeypatch, output="partial synthetic response", finish_reason=finish_reason,
    )

    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=10)

    assert exc.value.code == "text_model_incomplete"
    assert "partial synthetic response" not in str(exc.value)


@pytest.mark.asyncio
async def test_run_text_maps_output_token_limit_to_incomplete_without_leaking_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_limit_type = type(
        "OutputTokenLimitExceeded", (RuntimeError,), {"__module__": "pydantic_ai.exceptions"}
    )
    monkeypatch.setattr(pydantic_ai_exceptions, "OutputTokenLimitExceeded", token_limit_type, raising=False)
    runtime, agent, _captured, _settings = _text_runtime(monkeypatch)
    source_error = token_limit_type("synthetic private request detail")

    async def raise_limit(prompt):
        agent.calls.append(prompt)
        raise source_error

    agent.run = raise_limit
    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=10)

    assert exc.value.code == "text_model_incomplete"
    assert exc.value.status_code == 502
    assert exc.value.__cause__ is source_error
    assert "synthetic private request detail" not in str(exc.value)


@pytest.mark.asyncio
async def test_run_text_keeps_unrelated_model_errors_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_limit_type = type(
        "OutputTokenLimitExceeded", (RuntimeError,), {"__module__": "pydantic_ai.exceptions"}
    )
    monkeypatch.setattr(pydantic_ai_exceptions, "OutputTokenLimitExceeded", token_limit_type, raising=False)
    runtime, agent, _captured, _settings = _text_runtime(monkeypatch)

    async def raise_unrelated(prompt):
        agent.calls.append(prompt)
        raise RuntimeError("synthetic unrelated failure")

    agent.run = raise_unrelated
    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=10)

    assert exc.value.code == "text_model_failed"
    assert exc.value.status_code == 502
    assert isinstance(exc.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_run_text_rejects_empty_prompt_without_creating_or_running_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, agent, captured, _settings = _text_runtime(monkeypatch)

    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_text(prompt="  ", system_prompt="single stage", timeout_seconds=10)

    assert exc.value.status_code == 422
    assert exc.value.code == "text_input_invalid"
    assert agent.calls == []
    assert captured == {}


@pytest.mark.asyncio
async def test_run_text_rejects_missing_model_and_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    no_model = ProviderVisualRuntime(SimpleNamespace(agent=SimpleNamespace(model=None)))
    with pytest.raises(ProviderHttpRuntimeError) as missing:
        await no_model.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=10)
    assert missing.value.code == "text_model_unavailable"

    runtime, _agent, _captured, _settings = _text_runtime(monkeypatch, output="")
    with pytest.raises(ProviderHttpRuntimeError) as empty:
        await runtime.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=10)
    assert empty.value.code == "text_model_empty"


@pytest.mark.asyncio
async def test_run_text_enforces_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, agent, captured, _settings = _text_runtime(monkeypatch)
    original_wait_for = asyncio.wait_for
    supplied_timeouts = []

    async def capture_wait_for(awaitable, *, timeout):
        supplied_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(visual_runtime_module.asyncio, "wait_for", capture_wait_for)

    async def wait_forever(prompt):
        agent.calls.append(prompt)
        await asyncio.sleep(5)

    agent.run = wait_forever
    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await runtime.run_text(prompt="stage", system_prompt="single stage", timeout_seconds=0.01)

    assert exc.value.status_code == 504
    assert exc.value.code == "text_model_timeout"
    assert len(agent.calls) == 1
    assert "retries" not in captured
    assert supplied_timeouts == [0.01]
