# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.bootstrap.startup_helpers import create_pydantic_model
from app.atlasclaw.models.openai_chat_compat import (
    QwenVllmOpenAIChatModel,
    normalize_openai_chat_system_messages,
    requires_single_leading_system_message,
)
from app.atlasclaw.models.providers import ModelFactory, ProviderConfig, ProviderRegistry


def test_normalize_openai_chat_system_messages_merges_system_messages_at_front() -> None:
    messages = [
        {"role": "system", "content": "base prompt"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "runtime prompt"},
        {"role": "assistant", "content": "hello"},
    ]

    normalized = normalize_openai_chat_system_messages(messages)

    assert normalized == [
        {"role": "system", "content": "base prompt\n\nruntime prompt"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert messages[2]["role"] == "system"


@pytest.mark.asyncio
async def test_qwen_vllm_model_maps_base_prompt_and_instructions_to_one_system_message() -> None:
    provider = OpenAIProvider(api_key="test-key", base_url="http://localhost:28100/v1")
    model = QwenVllmOpenAIChatModel("Qwen3.5-27B", provider=provider)
    model_request = ModelRequest(
        parts=[
            SystemPromptPart(content="base prompt"),
            UserPromptPart(content="hi"),
        ],
        instructions="runtime prompt",
    )

    mapped = await model._map_messages(
        [model_request],
        ModelRequestParameters(),
        model_settings={"thinking": False},
    )

    assert [message.get("role") for message in mapped] == ["system", "user"]
    assert mapped[0]["content"] == "base prompt\n\nruntime prompt"
    assert sum(1 for message in mapped if message.get("role") == "system") == 1


def test_requires_single_leading_system_message_detects_compatible_models() -> None:
    assert requires_single_leading_system_message(provider="vllm-local", model="Qwen3.5-27B")
    assert requires_single_leading_system_message(provider="vllm", model="Qwen2.5-72B")
    assert requires_single_leading_system_message(
        provider="openai",
        model="minimax/minimax-m3:free",
    )
    assert not requires_single_leading_system_message(provider="openrouter", model="qwen/qwen3")


def test_create_pydantic_model_uses_vllm_qwen_compat_model() -> None:
    token = TokenEntry(
        token_id="model-1",
        provider="vllm-local",
        model="Qwen3.5-27B",
        base_url="http://localhost:28100/v1",
        api_key="test-key",
        api_type="openai",
    )

    model = create_pydantic_model(token)

    assert isinstance(model, QwenVllmOpenAIChatModel)
    assert isinstance(model, OpenAIChatModel)


def test_model_factory_uses_vllm_qwen_compat_model() -> None:
    registry = ProviderRegistry()
    registry.register(
        "vllm-local",
        ProviderConfig(
            base_url="http://localhost:28100/v1",
            api_key="test-key",
            api_type="openai",
        ),
    )
    factory = ModelFactory(registry)

    model = factory.create_model("vllm-local/Qwen3.5-27B")

    assert isinstance(model, QwenVllmOpenAIChatModel)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type, model_name", [("openai", "example"), ("anthropic", "claude-example")])
@pytest.mark.parametrize("reasoning", [False, True])
async def test_model_factory_creates_current_sdk_models(api_type, model_name, reasoning) -> None:
    from app.atlasclaw.models.providers import ModelDefinition

    registry = ProviderRegistry()
    registry.register("example", ProviderConfig(
        api_key="test-key", api_type=api_type,
        models=[ModelDefinition(id=model_name, reasoning=reasoning)],
    ))
    model = ModelFactory(registry).create_model(f"example/{model_name}")
    try:
        assert model.model_name == model_name
    finally:
        await model.client.close()


@pytest.mark.asyncio
async def test_create_pydantic_model_initializes_anthropic() -> None:
    model = create_pydantic_model(TokenEntry(
        token_id="model-1", provider="anthropic", model="claude-example",
        api_key="test-key", api_type="anthropic", base_url="https://example.invalid",
    ))
    try:
        assert model.model_name == "claude-example"
    finally:
        await model.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("thinking", [False, True])
async def test_deepseek_preserves_thinking_toggle_in_sdk_request(stream, thinking) -> None:
    import json
    import httpx2
    from pydantic_ai.models.openai import OpenAIChatModelSettings
    from app.atlasclaw.models.openai_chat_compat import DeepSeekOpenAIChatModel

    captured = []

    async def respond(request):
        captured.append(json.loads(request.content))
        if stream:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: [DONE]\n\n")
        return httpx2.Response(200, json={
            "id": "example", "object": "chat.completion", "created": 0, "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = DeepSeekOpenAIChatModel("deepseek-chat", provider=OpenAIProvider(api_key="test-key", http_client=client))
        result = await model._completions_create(
            [ModelRequest(parts=[UserPromptPart(content="hello")])], stream,
            OpenAIChatModelSettings(extra_body={"custom": "preserved"}),
            ModelRequestParameters(thinking=thinking),
        )
        if stream:
            await result.close()
    assert captured[0]["thinking"] == {"type": "enabled" if thinking else "disabled"}
    assert captured[0]["custom"] == "preserved"
    assert captured[0]["stream"] is stream
