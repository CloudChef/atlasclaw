# -*- coding: utf-8 -*-
"""E2E: provider config should merge JSON + DB with DB override on same instance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.xuanwu.api.routes import get_api_context
from app.xuanwu.db.database import DatabaseConfig, init_database
from app.xuanwu.db.orm.service_provider_config import ServiceProviderConfigService
from app.xuanwu.db.schemas import ServiceProviderConfigCreate


def _seed_db_provider_config(db_path: Path) -> None:
    async def _seed() -> None:
        manager = await init_database(DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path)))
        await manager.create_tables()

        async with manager.get_session() as session:
            await ServiceProviderConfigService.create(
                session,
                ServiceProviderConfigCreate(
                    provider_type="jira",
                    instance_name="dev",
                    config={
                        "base_url": "https://db.example",
                        "username": "db-user",
                        "password": "db-password",
                        "api_version": "3",
                    },
                    is_active=True,
                ),
            )
            await session.commit()

        await manager.close()

    asyncio.run(_seed())


@pytest.mark.e2e
def test_provider_config_db_overrides_json_on_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "provider-merge-e2e.db"
    config_path = tmp_path / "xuanwu.e2e.json"

    config = {
        "workspace": {
            "path": str((tmp_path / ".xuanwu-e2e").resolve()),
        },
        "providers_root": "./app/xuanwu/providers",
        "skills_root": "./app/xuanwu/skills",
        "channels_root": "./app/xuanwu/channels",
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": str(db_path.resolve()),
            },
        },
        "auth": {"enabled": False},
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
        "service_providers": {
            "jira": {
                "dev": {
                    "base_url": "https://json.example",
                    "username": "json-user",
                    "password": "json-password",
                    "api_version": "2",
                }
            }
        },
    }

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    _seed_db_provider_config(db_path)

    monkeypatch.setenv("XUANWU_CONFIG", str(config_path.resolve()))

    import app.xuanwu.core.config as config_module
    from app.xuanwu.main import create_app

    old_config_manager = config_module._config_manager
    config_module._config_manager = config_module.ConfigManager(config_path=str(config_path.resolve()))

    try:
        app = create_app()
        with TestClient(app):
            ctx = get_api_context()
            cfg = ctx.provider_instances["jira"]["dev"]

            assert cfg["base_url"] == "https://db.example"
            assert cfg["username"] == "db-user"
            assert cfg["password"] == "db-password"
            assert cfg["api_version"] == "3"
    finally:
        config_module._config_manager = old_config_manager
