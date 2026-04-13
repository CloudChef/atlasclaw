"""Thinking-stream emission helpers for model response parts."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from pydantic_ai.messages import ThinkingPart

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.core.trace import get_current_trace_context, resolve_trace_context, sanitize_log_value


logger = logging.getLogger(__name__)


def split_thinking_chunks(text: str, target_size: int = 5) -> list[str]:
    """Split thinking content into chunks for streaming display."""
    if len(text) <= target_size:
        return [text]

    if target_size <= 10:
        return [text[i : i + target_size] for i in range(0, len(text), target_size)]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= target_size:
            chunks.append(remaining)
            break

        best_pos = target_size
        for delim in ["\n", ".", "!", "?", ",", " "]:
            pos = remaining.rfind(delim, int(target_size * 0.6), target_size + 1)
            if pos > 0:
                best_pos = pos + len(delim)
                break

        chunks.append(remaining[:best_pos])
        remaining = remaining[best_pos:]

    return chunks


class ThinkingStreamEmitter:
    """Stateful emitter that converts model parts into thinking/assistant stream events."""

    def __init__(self, *, chunk_delay_seconds: float = 0.015, chunk_size: int = 5) -> None:
        self.chunk_delay_seconds = chunk_delay_seconds
        self.chunk_size = chunk_size
        self.thinking_started = False
        self.thinking_start_time: float | None = None
        self.thinking_chunk_count = 0
        self.assistant_emitted = False
        self.current_cycle_had_thinking = False

    async def emit_from_model_response(
        self,
        *,
        model_response: Any,
        hooks: Any,
        session_key: str,
    ) -> AsyncIterator[StreamEvent]:
        """Emit stream events from one model response object."""
        if not hasattr(model_response, "parts"):
            return

        for part in model_response.parts:
            part_kind = getattr(part, "part_kind", None)
            is_thinking = part_kind == "thinking" or isinstance(part, ThinkingPart)

            if is_thinking:
                async for event in self._emit_thinking_delta(part):
                    yield event
                continue

            text_content = getattr(part, "content", None) or getattr(part, "text", None)
            if text_content:
                async for event in self._emit_assistant_text(
                    content=str(text_content),
                    hooks=hooks,
                    session_key=session_key,
                ):
                    yield event

    async def emit_plain_content(
        self,
        *,
        content: str,
        hooks: Any,
        session_key: str,
    ) -> AsyncIterator[StreamEvent]:
        """Emit assistant output for plain node.content."""
        async for event in self._emit_assistant_text(content=content, hooks=hooks, session_key=session_key):
            yield event

    async def close_if_active(self) -> AsyncIterator[StreamEvent]:
        """Emit thinking_end when the current thinking phase is still open."""
        if self.thinking_started:
            thinking_elapsed = self._calc_thinking_elapsed()
            yield StreamEvent.thinking_end(elapsed=thinking_elapsed)
            self.thinking_started = False

    async def _emit_thinking_delta(self, part: Any) -> AsyncIterator[StreamEvent]:
        if not self.thinking_started:
            yield StreamEvent.thinking_start()
            self.thinking_started = True
            self.thinking_start_time = time.time()
            self.thinking_chunk_count = 0
        self.current_cycle_had_thinking = True

        thinking_content = getattr(part, "content", "")
        if not thinking_content:
            return

        content_str = str(thinking_content)
        chunks = split_thinking_chunks(content_str, target_size=self.chunk_size)
        for chunk in chunks:
            yield StreamEvent.thinking_delta(chunk)
            self.thinking_chunk_count += 1
            await asyncio.sleep(self.chunk_delay_seconds)

    async def _emit_assistant_text(
        self,
        *,
        content: str,
        hooks: Any,
        session_key: str,
    ) -> AsyncIterator[StreamEvent]:
        if self.thinking_started:
            thinking_elapsed = self._calc_thinking_elapsed()
            yield StreamEvent.thinking_end(elapsed=thinking_elapsed)
            self.thinking_started = False

        self.assistant_emitted = True
        trace_context = get_current_trace_context() or resolve_trace_context(session_key)
        hook_payload = {
            "session_key": session_key,
            "content": content,
            "trace_id": trace_context.trace_id,
            "thread_id": trace_context.thread_id,
            "run_id": trace_context.run_id,
        }
        logger.info(
            "llm_trace %s",
            {
                "event": "llm_output",
                **trace_context.as_log_fields(),
                "content": sanitize_log_value(content),
            },
        )
        if hooks:
            await hooks.trigger(
                "llm_output",
                hook_payload,
            )
        yield StreamEvent.assistant_delta(content)

    def _calc_thinking_elapsed(self) -> float:
        wall_elapsed = time.time() - self.thinking_start_time if self.thinking_start_time else 0
        simulated_elapsed = self.thinking_chunk_count * self.chunk_delay_seconds
        return round(max(wall_elapsed, simulated_elapsed), 1)

    def reset_cycle_flags(self) -> None:
        """Reset per-model-cycle flags used by the runner."""
        self.current_cycle_had_thinking = False
