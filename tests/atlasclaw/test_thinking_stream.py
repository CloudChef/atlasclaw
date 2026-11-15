# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Regression tests for completed thinking output."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter

@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['', 'x', 'x' * 1000, 'x' * 3500, '你好🙂\n' * 900])
async def test_completed_thinking_is_batched_without_sleep(text, monkeypatch):
    sleep = AsyncMock(side_effect=AssertionError('artificial delay'))
    monkeypatch.setattr('asyncio.sleep', sleep)
    emitter = ThinkingStreamEmitter()
    events = [e async for e in emitter._emit_thinking_delta(SimpleNamespace(content=text))]
    chunks = [e.content for e in events if e.phase == 'delta']
    assert ''.join(chunks) == text
    assert len(chunks) <= max(1, (len(text) + 299) // 300)
    sleep.assert_not_awaited()

@pytest.mark.asyncio
async def test_legacy_delay_argument_does_not_delay_output(monkeypatch):
    sleep = AsyncMock(side_effect=AssertionError('artificial delay'))
    monkeypatch.setattr('asyncio.sleep', sleep)
    emitter = ThinkingStreamEmitter(chunk_delay_seconds=100, chunk_size=512)
    events = [e async for e in emitter._emit_thinking_delta(SimpleNamespace(content='reason'))]
    assert ''.join(e.content for e in events if e.phase == 'delta') == 'reason'
    sleep.assert_not_awaited()

@pytest.mark.asyncio
async def test_completed_thinking_does_not_report_playback_as_model_time():
    emitter = ThinkingStreamEmitter(chunk_delay_seconds=0)
    events = [e async for e in emitter._emit_thinking_delta(SimpleNamespace(content='reason'))]
    assert events[0].phase == 'start'
    end = [e async for e in emitter.close_if_active()]
    assert end[0].metadata['elapsed'] == 0
    assert [e async for e in emitter.close_if_active()] == []
