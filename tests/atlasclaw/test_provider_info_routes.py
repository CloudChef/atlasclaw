# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.atlasclaw.core.config as config_module
from app.atlasclaw.api.provider_info_routes import router as provider_info_router
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.schemas import ServiceProviderConfigCreate


@pytest.fixture(autouse=True)
def reset_config_manager():
    config_module._config_manager = None
    yield
    config_module._config_manager = None


def test_available_instances_exposes_auth_type_and_safe_config_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {
    "smartcmp": {
      "default": {
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "credential",
        "username": "cmp-robot",
        "password": "secret-pass",
        "ACCESS_TOKEN": "access-token",
        "refreshToken": "refresh-token",
        "provider-token": "provider-token",
        "clientSecret": "client-secret",
        "APIKey": "api-key",
        "sessionCookie": "session-cookie",
        "credentialAlias": "credential-alias",
        "region": "cn-north-1"
      }
    },
    "dingtalk": {
      "default": {
        "base_url": "https://oapi.dingtalk.com",
        "app_key": "ding-key",
        "app_secret": "ding-secret",
        "agent_id": "1000001"
      }
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/service-providers/available-instances")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2

    providers = {
        (item["provider_type"], item["instance_name"]): item
        for item in payload["providers"]
    }

    assert providers[("smartcmp", "default")] == {
        "provider_type": "smartcmp",
        "display_name": "SmartCMP",
        "instance_name": "default",
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "credential",
        "config_keys": ["region", "username"],
    }
    assert providers[("dingtalk", "default")] == {
        "provider_type": "dingtalk",
        "display_name": "DingTalk",
        "instance_name": "default",
        "base_url": "https://oapi.dingtalk.com",
        "auth_type": "app_credentials",
        "config_keys": ["agent_id", "app_key"],
    }


def test_available_instances_fall_back_to_schema_defaults_when_base_url_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {
    "smartcmp": {
      "default": {
        "username": "cmp-robot"
      }
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/service-providers/available-instances")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["providers"][0] == {
        "provider_type": "smartcmp",
        "display_name": "SmartCMP",
        "instance_name": "default",
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "user_token",
        "config_keys": ["username"],
    }


def test_provider_definitions_expose_backend_managed_form_schema():
    config_module._config_manager = None
    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/service-providers/definitions")

    assert response.status_code == 200
    payload = response.json()
    providers = {
        item["provider_type"]: item
        for item in payload["providers"]
    }

    smartcmp = providers["smartcmp"]
    fields = {
        field["name"]: field
        for field in smartcmp["schema"]["fields"]
    }

    assert smartcmp["name_i18n_key"] == "provider.catalog.smartcmp.name"
    assert fields["base_url"]["default"] == "https://console.smartcmp.cloud"
    assert fields["auth_type"]["type"] == "hidden"
    assert fields["auth_type"]["default"] == "user_token"
    assert fields["user_token"]["type"] == "password"
    assert fields["user_token"]["sensitive"] is True
    assert fields["user_token"]["auth_types"] == ["user_token"]
    assert fields["username"]["auth_types"] == ["credential"]


def test_provider_definitions_fill_base_url_default_from_config(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {
    "smartcmp": {
      "default": {
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "user_token"
      }
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/service-providers/definitions")

    assert response.status_code == 200
    payload = response.json()
    providers = {
        item["provider_type"]: item
        for item in payload["providers"]
    }
    smartcmp_fields = {
        field["name"]: field
        for field in providers["smartcmp"]["schema"]["fields"]
    }

    assert smartcmp_fields["base_url"]["default"] == "https://console.smartcmp.cloud"


def test_available_instances_expose_ordered_auth_chain_when_template_uses_multi_auth(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {
    "smartcmp": {
      "default": {
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": ["cookie", "user_token"],
        "username": "cmp-robot"
      }
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/service-providers/available-instances")

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"][0]["auth_type"] == ["cookie", "user_token"]


def test_available_instances_include_db_managed_provider_configs(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {}
}
        """.strip(),
        encoding="utf-8",
    )

    async def _init_db():
        manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "providers.db"))
        )
        await manager.create_tables()
        async with manager.get_session() as session:
            await ServiceProviderConfigService.create(
                session,
                ServiceProviderConfigCreate(
                    provider_type="smartcmp",
                    instance_name="db-managed",
                    config={
                        "base_url": "https://db.smartcmp.cloud",
                        "auth_type": ["provider_token", "user_token"],
                        "provider_token": "shared-provider-token",
                    },
                    is_active=True,
                ),
            )
        return manager

    manager = asyncio.run(_init_db())

    try:
        app = FastAPI()
        app.include_router(provider_info_router, prefix="/api")
        client = TestClient(app)

        instances_response = client.get("/api/service-providers/available-instances")
        definitions_response = client.get("/api/service-providers/definitions")

        assert instances_response.status_code == 200
        instances_payload = instances_response.json()
        assert instances_payload["providers"] == [
            {
                "provider_type": "smartcmp",
                "display_name": "SmartCMP",
                "instance_name": "db-managed",
                "base_url": "https://db.smartcmp.cloud",
                "auth_type": ["provider_token", "user_token"],
                "config_keys": [],
            }
        ]

        assert definitions_response.status_code == 200
        smartcmp_fields = {
            field["name"]: field
            for provider in definitions_response.json()["providers"]
            if provider["provider_type"] == "smartcmp"
            for field in provider["schema"]["fields"]
        }
        assert smartcmp_fields["base_url"]["default"] == "https://db.smartcmp.cloud"
        assert smartcmp_fields["auth_type"]["default"] == ["provider_token", "user_token"]
    finally:
        asyncio.run(manager.close())


def test_available_instances_skips_unknown_auth_type_and_logs_error(
    tmp_path,
    monkeypatch,
    caplog,
):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        """
{
  "workspace": { "path": ".atlasclaw" },
  "service_providers": {
    "smartcmp": {
      "legacy": {
        "base_url": "https://legacy.smartcmp.cloud",
        "auth_type": "cmp"
      },
      "default": {
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "user_token"
      }
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(provider_info_router, prefix="/api")
    client = TestClient(app)

    with caplog.at_level(logging.ERROR):
        response = client.get("/api/service-providers/available-instances")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["providers"][0]["instance_name"] == "default"
    assert "Unsupported auth_type: cmp" in caplog.text
    assert "Skipping provider instance smartcmp.legacy" in caplog.text
