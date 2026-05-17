# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""E2E: hook runtime API with local auth and full app startup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.atlasclaw.api.deps_context import get_api_context
from app.atlasclaw.hooks.runtime_builtin import RUNTIME_AUDIT_MODULE
from app.atlasclaw.hooks.runtime_models import HookEventType


def _create_hooks_e2e_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "hook-runtime-e2e.db"
    config_path = tmp_path / "atlasclaw.hook-runtime.e2e.json"

    config = {
        "workspace": {
            "path": str((tmp_path / ".atlasclaw-e2e").resolve()),
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
                "secret_key": "e2e-secret",
                "issuer": "atlasclaw-e2e",
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
            "fallbacks": [],
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
def test_hook_runtime_routes_support_events_and_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_hooks_e2e_app(tmp_path, monkeypatch)

    try:
        with TestClient(app) as client:
            login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_resp.status_code == 200
            token = login_resp.json()["token"]

            ctx = get_api_context()
            assert ctx.hook_runtime is not None

            import asyncio

            async def _seed():
                await ctx.hook_runtime.emit(
                    event_type=HookEventType.RUN_FAILED,
                    user_id="admin",
                    session_key="agent:main:user:admin:web:dm:admin:topic:e2e",
                    run_id="run-e2e-1",
                    channel="web",
                    agent_id="main",
                    payload={"error": "e2e runtime failure"},
                )
                await ctx.hook_runtime.emit(
                    event_type=HookEventType.RUN_FAILED,
                    user_id="admin",
                    session_key="agent:main:user:admin:web:dm:admin:topic:e2e-2",
                    run_id="run-e2e-2",
                    channel="web",
                    agent_id="main",
                    payload={"error": "reject this event"},
                )

            asyncio.run(_seed())

            headers = {"AtlasClaw-Authenticate": token}
            events_resp = client.get(f"/api/hooks/{RUNTIME_AUDIT_MODULE}/events", headers=headers)
            pending_resp = client.get(f"/api/hooks/{RUNTIME_AUDIT_MODULE}/pending", headers=headers)

            assert events_resp.status_code == 200
            assert len(events_resp.json()) == 2
            assert pending_resp.status_code == 200
            assert len(pending_resp.json()) == 2

            confirm_id = pending_resp.json()[0]["id"]
            reject_id = pending_resp.json()[1]["id"]

            confirm_resp = client.post(
                f"/api/hooks/{RUNTIME_AUDIT_MODULE}/pending/{confirm_id}/confirm",
                headers=headers,
                json={"note": "promote to memory"},
            )
            reject_resp = client.post(
                f"/api/hooks/{RUNTIME_AUDIT_MODULE}/pending/{reject_id}/reject",
                headers=headers,
                json={"note": "skip"},
            )

            assert confirm_resp.status_code == 200
            assert reject_resp.status_code == 200

            pending_after = client.get(f"/api/hooks/{RUNTIME_AUDIT_MODULE}/pending", headers=headers)
            assert pending_after.status_code == 200
            assert pending_after.json() == []

            memory_file = tmp_path / ".atlasclaw-e2e" / "users" / "admin" / "memory" / "MEMORY.md"
            assert memory_file.exists()
    finally:
        config_module._config_manager = old_config_manager
