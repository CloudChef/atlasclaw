# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Compatibility helpers for OpenAI-compatible chat backends."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel


def requires_single_leading_system_message(
    *,
    provider: str,
    model: str,
    base_url: str = "",
) -> bool:
    """Return whether the backend rejects multiple or non-leading system messages."""
    provider_name = str(provider or "").strip().lower().replace("_", "-")
    model_name = str(model or "").strip().lower()
    if provider_name == "vllm" or provider_name.startswith("vllm-"):
        return "qwen" in model_name
    if "minimax" in model_name:
        return True
    return False


def normalize_openai_chat_system_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge all system messages into one leading system message.

    Qwen chat templates served by vLLM may reject any system message that is not
    exactly the first message. PydanticAI can emit a base system prompt plus
    per-run instructions as separate system messages, so normalize them at the
    model-adapter boundary while preserving non-system message order.
    """
    system_template: Optional[dict[str, Any]] = None
    system_blocks: list[str] = []
    non_system_messages: list[dict[str, Any]] = []

    for message in messages:
        copied = dict(message)
        if str(copied.get("role", "") or "").strip().lower() != "system":
            non_system_messages.append(copied)
            continue

        if system_template is None:
            system_template = copied
        system_text = _stringify_system_content(copied.get("content")).strip()
        if system_text:
            system_blocks.append(system_text)

    if not system_blocks:
        return non_system_messages

    system_message = dict(system_template or {})
    system_message["role"] = "system"
    system_message["content"] = "\n\n".join(system_blocks)
    return [system_message, *non_system_messages]


def _stringify_system_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text", item.get("content", ""))
                parts.append(str(text) if text is not None else "")
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


class QwenVllmOpenAIChatModel(OpenAIChatModel):
    """OpenAI chat model variant that normalizes system messages for vLLM Qwen."""

    async def _map_messages(
        self,
        messages: Sequence[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> list[Any]:
        mapped_messages = await super()._map_messages(messages, model_request_parameters)
        return normalize_openai_chat_system_messages(mapped_messages)
