# -*- coding: utf-8 -*-
"""E2E: local auth redirect and login flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _create_local_auth_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_path: str = "",
) -> tuple[FastAPI, object, object]:
    db_path = tmp_path / "local-auth-redirect-e2e.db"
    config_path = tmp_path / "atlasclaw.local-auth.e2e.json"

    config = {
        "workspace": {
            "path": str((tmp_path / ".atlasclaw-e2e").resolve()),
        },
        "base_path": base_path,
        "providers_root": "./app/atlasclaw/providers",
        "skills_root": "./app/atlasclaw/skills",
        "channels_root": "./app/atlasclaw/channels",
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
def test_root_redirects_to_login_when_local_auth_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(tmp_path, monkeypatch)

    try:
        with TestClient(app) as client:
            resp = client.get("/", follow_redirects=False, headers={"Accept": "text/html"})

            assert resp.status_code == 302
            location = resp.headers.get("location", "")
            assert location.startswith("/login.html?redirect=")
            assert "redirect=%2F" in location
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_root_redirects_to_base_path_login_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(
        tmp_path,
        monkeypatch,
        base_path="/atlasclaw",
    )

    try:
        with TestClient(app) as client:
            resp = client.get("/", follow_redirects=False, headers={"Accept": "text/html"})

            assert resp.status_code == 302
            location = resp.headers.get("location", "")
            assert location.startswith("/atlasclaw/login.html?redirect=")
            assert "redirect=%2Fatlasclaw%2F" in location
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_config_json_exposes_base_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(
        tmp_path,
        monkeypatch,
        base_path="/atlasclaw",
    )

    try:
        with TestClient(app) as client:
            resp = client.get("/config.json")

            assert resp.status_code == 200
            body = resp.json()
            assert body["basePath"] == "/atlasclaw"
            assert body["apiBaseUrl"] == "/atlasclaw"
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_prefixed_login_page_is_served_without_redirect_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(
        tmp_path,
        monkeypatch,
        base_path="/atlasclaw",
    )

    try:
        with TestClient(app) as client:
            resp = client.get("/atlasclaw/login.html", headers={"Accept": "text/html"})

            assert resp.status_code == 200
            assert "window.__atlasclawBasePath = \"/atlasclaw\"" in resp.text
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_prefixed_static_assets_are_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(
        tmp_path,
        monkeypatch,
        base_path="/atlasclaw",
    )

    try:
        with TestClient(app) as client:
            css_resp = client.get("/atlasclaw/styles/login.css")
            auth_resp = client.get("/atlasclaw/scripts/auth.js")
            i18n_resp = client.get("/atlasclaw/scripts/i18n.js")

            assert css_resp.status_code == 200
            assert "text/css" in css_resp.headers.get("content-type", "")
            assert auth_resp.status_code == 200
            assert "javascript" in auth_resp.headers.get("content-type", "")
            assert i18n_resp.status_code == 200
            assert "javascript" in i18n_resp.headers.get("content-type", "")
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_prefixed_local_login_api_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(
        tmp_path,
        monkeypatch,
        base_path="/atlasclaw",
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/atlasclaw/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )

            assert resp.status_code == 200
            assert resp.json().get("success") is True
    finally:
        config_module._config_manager = old_config_manager


@pytest.mark.e2e
def test_login_then_access_root_and_auth_me(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_local_auth_app(tmp_path, monkeypatch)

    try:
        with TestClient(app) as client:
            login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_resp.status_code == 200
            login_body = login_resp.json()
            assert login_body.get("success") is True
            assert login_body.get("user", {}).get("username") == "admin"
            token = login_body.get("token", "")
            assert token

            root_resp = client.get(
                "/",
                follow_redirects=False,
                headers={
                    "Accept": "text/html",
                    "AtlasClaw-Authenticate": token,
                },
            )
            location = root_resp.headers.get("location", "")
            assert not (root_resp.status_code == 302 and location.startswith("/login.html"))

            me_resp = client.get(
                "/api/auth/me",
                headers={"AtlasClaw-Authenticate": token},
            )
            assert me_resp.status_code == 200
            assert me_resp.json().get("user_id") == "admin"
    finally:
        config_module._config_manager = old_config_manager
