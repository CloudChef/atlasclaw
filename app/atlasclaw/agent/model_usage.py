# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Collect usage at PydanticAI's provider-neutral model request boundary."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import math
import time
from typing import Any, Iterator
import uuid

from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import BinaryContent, FileUrl, ModelRequest, ToolReturnPart
from pydantic_ai.models.wrapper import WrapperModel

from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.models import ModelUsageModel

logger = logging.getLogger(__name__)
_action: ContextVar[str] = ContextVar("model_usage_action", default="model_response")


@contextmanager
def usage_action(action: str) -> Iterator[None]:
    """Label nested model calls without mutating shared session or run state."""
    token = _action.set(action)
    try:
        yield
    finally:
        _action.reset(token)


def usage_values(response: Any, request_context: Any) -> dict[str, Any]:
    """Use normalized provider totals, with a labeled text-only fallback estimate."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    details = getattr(usage, "details", {}) or {}
    if input_tokens or output_tokens:
        return {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens, "source": "reported",
            "cache_read_tokens": getattr(usage, "cache_read_tokens", None),
            "cache_write_tokens": getattr(usage, "cache_write_tokens", None),
            "reasoning_tokens": details.get("reasoning_tokens"),
        }
    empty = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens",
                           "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"))
    empty["source"] = "unavailable"
    if response is None or getattr(response, "state", None) == "suspended":
        return empty
    messages = request_context.messages
    for message in [*messages, response]:
        for part in message.parts:
            if _contains_media(getattr(part, "content", None)):
                return empty  # Pixel budgets are context allowances, not usage estimates.
    # Match the existing four-characters-per-token heuristic, including tool schemas.
    tools = [dict(name=tool.name, description=tool.description,
                  parameters=tool.parameters_json_schema) for tool in (
        *request_context.model_request_parameters.function_tools,
        *request_context.model_request_parameters.output_tools,
    )]
    input_chars = _message_chars(messages)
    if tools:
        input_chars += len(json.dumps(tools, ensure_ascii=False))
    output_chars = _message_chars([response])
    input_tokens, output_tokens = math.ceil(input_chars / 4), math.ceil(output_chars / 4)
    return {**empty, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens, "source": "estimated"}


def _message_chars(messages: list[Any]) -> int:
    """Count model-visible content, excluding local usage and transport metadata."""
    size = 0
    for message in messages:
        size += len(getattr(message, "instructions", None) or "")
        for part in message.parts:
            content = getattr(part, "content", getattr(part, "args", ""))
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            size += len(content) + len(getattr(part, "tool_name", ""))
    return size


def _contains_media(content: Any) -> bool:
    if isinstance(content, (BinaryContent, FileUrl)):
        return True
    if isinstance(content, (list, tuple)):
        return any(_contains_media(item) for item in content)
    if isinstance(content, dict):
        return any(_contains_media(item) for item in content.values())
    return False


def build_usage_hooks(token: TokenEntry) -> Hooks:
    """Keep one immutable usage row per model call, including failures and retries."""
    async def before_request(ctx: Any, request_context: Any) -> Any:
        snapshots: dict[str, dict[str, Any]] = {}
        if request_context.messages:
            tail = request_context.messages[-1]
            if getattr(tail, "state", None) == "suspended" and getattr(tail, "provider_response_id", None):
                snapshots[tail.provider_response_id] = usage_values(tail, request_context)

        async def record(response: Any, context: Any, started: float, status: str) -> None:
            try:
                manager = get_db_manager()
                if manager.is_initialized:
                    deps = ctx.deps
                    action = _action.get()
                    if action == "model_response" and context.messages:
                        last = context.messages[-1]
                        if isinstance(last, ModelRequest):
                            names = [part.tool_name for part in last.parts if isinstance(part, ToolReturnPart)]
                            if names:
                                action = "tool_result: " + ", ".join(dict.fromkeys(names))
                    run_id = str(deps.extra.get("run_id") or ctx.run_id)
                    values = usage_values(response, context)
                    response_id = getattr(response, "provider_response_id", None)
                    if response_id and values["source"] == "reported":
                        # Re-polling the same provider job returns cumulative usage.
                        previous = snapshots.get(response_id, {})
                        snapshot = dict(values)
                        for field in ("input_tokens", "output_tokens", "cache_read_tokens",
                                      "cache_write_tokens", "reasoning_tokens"):
                            if values[field] is not None:
                                high_water = max(values[field], previous.get(field) or 0)
                                values[field] = high_water - (previous.get(field) or 0)
                                snapshot[field] = high_water
                            else:
                                snapshot[field] = previous.get(field)
                        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
                        snapshots[response_id] = snapshot
                    record = ModelUsageModel(
                        id=str(uuid.uuid4()), user_id=deps.user_info.user_id,
                        run_id=run_id[:64], session_key=deps.session_key,
                        model_config_id=token.source_model_config_id,
                        provider=token.provider[:100], model=str(getattr(response, "model_name", None) or token.model)[:255],
                        token_id=token.token_id[:100],
                        key_label=("•••• " + token.api_key[-4:] if len(token.api_key) > 8 else "••••") if token.api_key else "—",
                        action=action[:255], status=status,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        **values,
                    )
                    async with manager.get_session() as session:
                        session.add(record)
            except Exception:
                # Metering must never change a model result or expose prompts/credentials.
                logger.warning("Model usage could not be recorded", exc_info=False)

        return replace(request_context, model=_UsageModel(request_context.model, request_context, record))
    return Hooks(before_model_request=before_request)


class _UsageModel(WrapperModel):
    """Capture each provider segment before native continuations merge responses."""

    def __init__(self, wrapped: Any, context: Any, record: Any) -> None:
        super().__init__(wrapped)
        self.context = context
        self.record = record

    async def request(self, messages: Any, model_settings: Any, model_request_parameters: Any) -> Any:
        started = time.monotonic()
        response = None
        status = "failed"
        try:
            response = await self.wrapped.request(messages, model_settings, model_request_parameters)
            status = "completed"
            return response
        finally:
            await self.record(response, replace(self.context, messages=messages), started, status)

    @asynccontextmanager
    async def request_stream(self, messages: Any, model_settings: Any,
                             model_request_parameters: Any, run_context: Any = None) -> Any:
        started = time.monotonic()
        stream = None
        status = "failed"
        try:
            async with self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
                yield stream
            status = "completed"
        finally:
            response = stream.get() if stream is not None else None
            await self.record(response, replace(self.context, messages=messages), started, status)
