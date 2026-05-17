# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.atlasclaw.hooks.runtime_models import HookWriteMemoryRequest, PendingHookItem
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore


@pytest.mark.asyncio
async def test_memory_sink_writes_long_term_memory_file(tmp_path):
    sink = MemorySink(workspace_path=str(tmp_path))

    result = await sink.write_confirmed(
        HookWriteMemoryRequest(
            module_name="audit",
            user_id="user-a",
            title="Promoted Lesson",
            body="Remember this confirmed lesson.",
            source_event_ids=["evt-1", "evt-2"],
            metadata={"kind": "lesson"},
        )
    )

    assert result.path.exists()
    assert result.path == tmp_path / "users" / "user-a" / "memory" / "MEMORY.md"
    content = result.path.read_text(encoding="utf-8")
    assert "Promoted Lesson" in content
    assert "source_event_ids: evt-1, evt-2" in content
    assert "kind: lesson" in content


@pytest.mark.asyncio
async def test_context_sink_exposes_confirmed_items_only(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    now = datetime.now(timezone.utc)
    await store.append_pending(
        "audit",
        PendingHookItem(
            id="pending-1",
            module_name="audit",
            user_id="user-a",
            source_event_ids=["evt-1"],
            summary="Confirmed item",
            payload={"kind": "lesson"},
            status="confirmed",
            created_at=now,
            updated_at=now,
        ),
    )
    await store.append_pending(
        "audit",
        PendingHookItem(
            id="pending-2",
            module_name="audit",
            user_id="user-a",
            source_event_ids=["evt-2"],
            summary="Still pending",
            payload={"kind": "candidate"},
            status="pending",
            created_at=now,
            updated_at=now,
        ),
    )

    sink = ContextSink(store)
    confirmed = await sink.list_confirmed("audit", "user-a")

    assert len(confirmed) == 1
    assert confirmed[0].summary == "Confirmed item"
    assert confirmed[0].payload["kind"] == "lesson"


@pytest.mark.asyncio
async def test_context_sink_includes_explicit_injections(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    sink = ContextSink(store)

    await sink.add_injection(
        module_name="audit",
        user_id="user-a",
        summary="Recent preference",
        payload={"body": "Prefer concise summaries"},
        source_event_ids=["evt-3"],
    )

    confirmed = await sink.list_confirmed("audit", "user-a")

    assert len(confirmed) == 1
    assert confirmed[0].summary == "Recent preference"
    assert confirmed[0].payload["body"] == "Prefer concise summaries"
