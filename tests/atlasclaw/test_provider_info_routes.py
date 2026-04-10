# -*- coding: utf-8 -*-

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.atlasclaw.core.config as config_module
from app.atlasclaw.api.provider_info_routes import router as provider_info_router


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
        "password": "secret-pass"
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
        "instance_name": "default",
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "credential",
        "config_keys": ["username"],
    }
    assert providers[("dingtalk", "default")] == {
        "provider_type": "dingtalk",
        "instance_name": "default",
        "base_url": "https://oapi.dingtalk.com",
        "auth_type": "",
        "config_keys": ["agent_id", "app_key"],
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
