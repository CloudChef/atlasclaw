# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""E2E: config-driven script hook handlers with aggregated context events."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.atlasclaw.api.deps_context import get_api_context
from app.atlasclaw.hooks.runtime_models import HookEventType


def _create_script_hook_e2e_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script_path = tmp_path / "hook_consumer.py"
    script_path.write_text(
        """
import json
import sys

event = json.load(sys.stdin)
print(json.dumps({
    "actions": [
        {
            "type": "create_pending",
            "summary": "Review aggregated context",
            "body": event["payload"]["assistant_message"],
            "metadata": {"source": "script-e2e"}
        },
        {
            "type": "write_memory",
            "title": "Aggregated lesson",
            "body": "Remember aggregated context output",
            "metadata": {"source": "script-e2e"}
        },
        {
            "type": "add_context",
            "summary": "Recent aggregated context",
            "body": event["payload"]["user_message"],
            "metadata": {"source": "script-e2e"}
        }
    ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "hook-script-runtime-e2e.db"
    config_path = tmp_path / "atlasclaw.hook-script-runtime.e2e.json"
    config = {
        "workspace": {
            "path": str((tmp_path / ".atlasclaw-script-e2e").resolve()),
        },
        "providers_root": "./app/atlasclaw/providers",
        "skills_root": "./app/atlasclaw/skills",
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": str(db_path.resolve()),
            },
        },
        "auth": {
            "enabled": True,
            "provider": "local",
            "jwt": {
                "secret_key": "script-hook-e2e-secret",
                "issuer": "atlasclaw-script-hook-e2e",
                "header_name": "AtlasClaw-Authenticate",
                "cookie_name": "AtlasClaw-Authenticate",
                "expires_minutes": 60,
            },
            "local": {
                "enabled": True,
                "default_admin_username": "admin",
                "default_admin_password": "Admin@123",
            },
        },
        "model": {
            "primary": "test-token",
            "temperature": 0.2,
            "selection_strategy": "health",
            "tokens": [
                {
                    "id": "test-token",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "test-key",
                    "api_type": "openai",
                    "priority": 100,
                    "weight": 100,
                }
            ],
        },
        "hooks_runtime": {
            "script_handlers": [
                {
                    "module": "script-e2e",
                    "events": ["run.context_ready"],
                    "command": [sys.executable, str(script_path)],
                    "timeout_seconds": 5,
                    "enabled": True,
                    "cwd": str(tmp_path),
                }
            ]
        },
    }

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path.resolve()))

    import app.atlasclaw.core.config as config_module
    from app.atlasclaw.main import create_app

    old_config_manager = config_module._config_manager
    config_module._config_manager = config_module.ConfigManager(config_path=str(config_path.resolve()))
    app = create_app()
    return app, config_module, old_config_manager


@pytest.mark.e2e
def test_script_hook_handler_consumes_aggregated_context_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_script_hook_e2e_app(tmp_path, monkeypatch)

    try:
        with TestClient(app) as client:
            login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_resp.status_code == 200
            token = login_resp.json()["token"]
            headers = {"AtlasClaw-Authenticate": token}

            ctx = get_api_context()
            assert ctx.hook_runtime is not None

            import asyncio

            async def _seed():
                await ctx.hook_runtime.emit(
                    event_type=HookEventType.RUN_CONTEXT_READY,
                    user_id="admin",
                    session_key="agent:main:user:admin:web:dm:admin:topic:e2e",
                    run_id="run-context-e2e",
                    channel="web",
                    agent_id="main",
                    payload={
                        "user_message": "hello",
                        "message_history": [],
                        "system_prompt": "prompt",
                        "assistant_message": "world",
                        "tool_calls": [],
                        "run_status": "completed",
                        "error": "",
                        "session_title": "hello",
                    },
                )

            asyncio.run(_seed())

            events_resp = client.get("/api/hooks/script-e2e/events", headers=headers)
            pending_resp = client.get("/api/hooks/script-e2e/pending", headers=headers)

            assert events_resp.status_code == 200
            assert len(events_resp.json()) == 1
            assert pending_resp.status_code == 200
            assert len(pending_resp.json()) == 1
            assert pending_resp.json()[0]["payload"]["body"] == "world"

            memory_file = (
                tmp_path / ".atlasclaw-script-e2e" / "users" / "admin" / "memory" / "MEMORY.md"
            )
            assert memory_file.exists()
            assert "Remember aggregated context output" in memory_file.read_text(encoding="utf-8")

            import asyncio as _asyncio

            context_items = _asyncio.run(ctx.context_sink.list_confirmed("script-e2e", "admin"))
            assert len(context_items) == 1
            assert context_items[0].payload["body"] == "hello"
    finally:
        config_module._config_manager = old_config_manager
