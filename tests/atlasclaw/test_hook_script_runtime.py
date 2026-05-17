# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.atlasclaw.hooks.runtime import HookRuntime, HookRuntimeContext
from app.atlasclaw.hooks.runtime_models import HookEventEnvelope, HookEventType
from app.atlasclaw.hooks.runtime_script import (
    HookScriptExecutionError,
    HookScriptHandlerDefinition,
    HookScriptRunner,
)
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore


def _event() -> HookEventEnvelope:
    return HookEventEnvelope(
        id="evt-script-1",
        event_type=HookEventType.RUN_CONTEXT_READY,
        user_id="user-a",
        session_key="agent:main:user:user-a:web:dm:user-a:topic:test",
        run_id="run-script-1",
        channel="web",
        agent_id="main",
        created_at=datetime.now(timezone.utc),
        payload={
            "user_message": "hello",
            "message_history": [],
            "assistant_message": "world",
            "run_status": "completed",
        },
    )


def _write_script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_hook_script_runner_parses_structured_actions(tmp_path: Path) -> None:
    script_path = tmp_path / "hook_script.py"
    _write_script(
        script_path,
        """
import json
import sys

event = json.load(sys.stdin)
print(json.dumps({
    "actions": [
        {"type": "create_pending", "summary": event["payload"]["user_message"], "body": "body"},
        {"type": "write_memory", "title": "Lesson", "body": "Remember this"},
        {"type": "add_context", "summary": "Context", "body": "Useful context"},
    ]
}, ensure_ascii=False))
""".strip(),
    )

    runner = HookScriptRunner()
    batch = await runner.run(
        HookScriptHandlerDefinition(
            module_name="script-audit",
            event_types={HookEventType.RUN_CONTEXT_READY},
            command=[sys.executable, str(script_path)],
            timeout_seconds=5,
            enabled=True,
        ),
        _event(),
    )

    assert [action.action_type.value for action in batch.actions] == [
        "create_pending",
        "write_memory",
        "add_context",
    ]


@pytest.mark.asyncio
async def test_hook_script_runner_rejects_invalid_stdout_json(tmp_path: Path) -> None:
    script_path = tmp_path / "hook_invalid.py"
    _write_script(script_path, "print('not-json')")

    runner = HookScriptRunner()
    with pytest.raises(HookScriptExecutionError):
        await runner.run(
            HookScriptHandlerDefinition(
                module_name="script-audit",
                event_types={HookEventType.RUN_CONTEXT_READY},
                command=[sys.executable, str(script_path)],
                timeout_seconds=5,
                enabled=True,
            ),
            _event(),
        )


@pytest.mark.asyncio
async def test_hook_runtime_script_handler_applies_actions(tmp_path: Path) -> None:
    script_path = tmp_path / "hook_apply.py"
    _write_script(
        script_path,
        """
import json
import sys

event = json.load(sys.stdin)
print(json.dumps({
    "actions": [
        {
            "type": "create_pending",
            "summary": "Need review",
            "body": event["payload"]["assistant_message"],
            "metadata": {"kind": "review"}
        },
        {
            "type": "write_memory",
            "title": "Confirmed note",
            "body": "Persisted from script",
            "metadata": {"source": "script"}
        },
        {
            "type": "add_context",
            "summary": "Recent note",
            "body": "Expose this in context",
            "metadata": {"kind": "context"}
        }
    ]
}, ensure_ascii=False))
""".strip(),
    )

    store = HookStateStore(workspace_path=str(tmp_path))
    runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=str(tmp_path),
            hook_state_store=store,
            memory_sink=MemorySink(str(tmp_path)),
            context_sink=ContextSink(store),
        )
    )
    runtime.register_script_handler(
        HookScriptHandlerDefinition(
            module_name="script-audit",
            event_types={HookEventType.RUN_CONTEXT_READY},
            command=[sys.executable, str(script_path)],
            timeout_seconds=5,
            enabled=True,
        )
    )

    await runtime.dispatch(_event())

    stored_events = await store.list_events("script-audit", "user-a")
    pending = await store.list_pending("script-audit", "user-a")
    context_items = await runtime.context_sink.list_confirmed("script-audit", "user-a")
    memory_file = tmp_path / "users" / "user-a" / "memory" / "MEMORY.md"

    assert len(stored_events) == 1
    assert len(pending) == 1
    assert pending[0].payload["body"] == "world"
    assert len(context_items) == 1
    assert context_items[0].summary == "Recent note"
    assert memory_file.exists()
    assert "Persisted from script" in memory_file.read_text(encoding="utf-8")
