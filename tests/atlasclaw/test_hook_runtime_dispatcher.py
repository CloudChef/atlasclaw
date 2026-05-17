# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.atlasclaw.hooks.runtime import (
    HookHandlerDefinition,
    HookRuntime,
    HookRuntimeContext,
)
from app.atlasclaw.hooks.runtime_models import HookDecision, HookEventEnvelope, HookEventType
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore


@dataclass
class _Recorder:
    seen: list[str] = field(default_factory=list)

    async def __call__(self, event, runtime_context):
        self.seen.append(event.id)
        await runtime_context.hook_state_store.append_event("audit", event)


@pytest.mark.asyncio
async def test_hook_runtime_dispatches_registered_handlers(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=str(tmp_path),
            hook_state_store=store,
            memory_sink=MemorySink(str(tmp_path)),
            context_sink=ContextSink(store),
        )
    )
    recorder = _Recorder()
    runtime.register(
        HookHandlerDefinition(
            module_name="audit",
            event_types={HookEventType.RUN_STARTED},
            handler=recorder,
        )
    )

    event = HookEventEnvelope(
        id="evt-1",
        event_type=HookEventType.RUN_STARTED,
        user_id="user-a",
        session_key="agent:main:user:user-a:main",
        run_id="run-1",
        channel="web",
        agent_id="main",
        created_at=datetime.now(timezone.utc),
        payload={"message": "hello"},
    )

    await runtime.dispatch(event)

    assert recorder.seen == ["evt-1"]
    stored = await store.list_events("audit", "user-a")
    assert [item.id for item in stored] == ["evt-1"]


@pytest.mark.asyncio
async def test_hook_runtime_confirm_promotes_memory(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=str(tmp_path),
            hook_state_store=store,
            memory_sink=MemorySink(str(tmp_path)),
            context_sink=ContextSink(store),
        )
    )
    event = HookEventEnvelope(
        id="evt-2",
        event_type=HookEventType.RUN_FAILED,
        user_id="user-a",
        session_key="agent:main:user:user-a:main",
        run_id="run-2",
        channel="web",
        agent_id="main",
        created_at=datetime.now(timezone.utc),
        payload={"error": "failure"},
    )

    pending = await runtime.create_pending_item(
        module_name="audit",
        user_id="user-a",
        source_event_ids=[event.id],
        summary="Review failure",
        payload={"error": "failure"},
    )
    resolved = await runtime.resolve_pending(
        module_name="audit",
        user_id="user-a",
        pending_id=pending.id,
        decision=HookDecision.CONFIRM.value,
        decided_by="user-a",
        note="approved",
    )

    assert resolved.status.value == "confirmed"
    memory_file = tmp_path / "users" / "user-a" / "memory" / "MEMORY.md"
    assert memory_file.exists()
    content = memory_file.read_text(encoding="utf-8")
    assert "Review failure" in content
    confirmed_items = await runtime.context_sink.list_confirmed("audit", "user-a")
    assert len(confirmed_items) == 1
