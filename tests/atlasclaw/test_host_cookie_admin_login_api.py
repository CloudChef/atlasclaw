# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _build_host_cookie_local_admin_config(tmp_path: Path, db_path: Path) -> dict:
    project_root = Path(__file__).resolve().parents[2]
    providers_root = str((project_root.parent / "atlasclaw-providers" / "providers").resolve())
    skills_root = str((project_root.parent / "atlasclaw-providers" / "skills").resolve())
    return {
        "workspace": {
            "path": str((tmp_path / ".atlasclaw-host-cookie-admin").resolve()),
        },
        "providers_root": providers_root,
        "skills_root": skills_root,
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": str(db_path.resolve()),
            },
        },
        "auth": {
            "enabled": True,
            "provider": "host_cookie",
            "jwt": {
                "secret_key": "host-cookie-admin-secret",
                "issuer": "atlasclaw-host-cookie-admin-test",
                "header_name": "AtlasClaw-Authenticate",
                "cookie_name": "AtlasClaw-Authenticate",
                "expires_minutes": 60,
            },
            "local": {
                "enabled": True,
                "default_admin_username": "admin",
                "default_admin_password": "Admin@123",
            },
            "host_cookie": {
                "header_name": "Host-Authenticate",
                "cookie_name": "Host-Authenticate",
                "subject_cookie_name": "hostUser",
                "display_name_cookie_name": "hostDisplayName",
                "user_id_cookie_name": "hostUserId",
                "tenant_id_cookie_name": "hostTenantId",
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
                    "model": "gpt-test",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "test-key",
                    "api_type": "openai",
                    "priority": 100,
                    "weight": 100,
                }
            ],
        },
        "service_providers": {},
    }


def _create_host_cookie_app(tmp_path: Path, monkeypatch) -> tuple[object, object, object]:
    db_path = tmp_path / "host-cookie-admin-login.db"
    config_path = tmp_path / "atlasclaw.host-cookie-admin-login.json"
    config_path.write_text(
        json.dumps(
            _build_host_cookie_local_admin_config(tmp_path, db_path),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path.resolve()))

    import app.atlasclaw.core.config as config_module
    from app.atlasclaw.main import create_app

    old_config_manager = config_module._config_manager
    config_module._config_manager = config_module.ConfigManager(
        config_path=str(config_path.resolve()),
    )
    app = create_app()
    return app, config_module, old_config_manager


def test_host_cookie_mode_allows_local_admin_login(tmp_path: Path, monkeypatch) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["success"] is True
            assert body["user"]["auth_type"] == "local"
            assert body["user"]["is_admin"] is True
            assert body["token"]
    finally:
        config_module._config_manager = old_manager


def test_host_cookie_mode_auth_me_accepts_local_admin_jwt(tmp_path: Path, monkeypatch) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_response.status_code == 200
            token = login_response.json()["token"]

            me_response = client.get(
                "/api/auth/me",
                headers={"AtlasClaw-Authenticate": token},
            )
            assert me_response.status_code == 200
            body = me_response.json()
            assert body["user_id"] == "admin"
            assert body["auth_type"] == "local"
            assert body["is_admin"] is True
    finally:
        config_module._config_manager = old_manager


def test_host_cookie_mode_auth_me_accepts_configured_host_cookies(tmp_path: Path, monkeypatch) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            me_response = client.get(
                "/api/auth/me",
                cookies={
                    "Host-Authenticate": "host-token",
                    "hostUser": "host-admin",
                    "hostDisplayName": "Host%20Admin",
                    "hostTenantId": "tenant-a",
                },
            )
            assert me_response.status_code == 200
            body = me_response.json()
            assert body["user_id"]
            assert body["display_name"] == "Host Admin"
            assert body["provider"] == "host_cookie"
            assert body["auth_type"] == "cookie"
            assert body["tenant_id"] == "tenant-a"
            assert body["roles"] == ["user"]
            assert body["permissions"]["channels"]["view"] is True
            assert body["permissions"]["channels"]["create"] is True

            login_response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_response.status_code == 200
            users_response = client.get(
                "/api/users",
                headers={"AtlasClaw-Authenticate": login_response.json()["token"]},
            )
            assert users_response.status_code == 200
            assert any(
                user["username"] == "host-admin" and user["roles"] == {"user": True}
                for user in users_response.json()["users"]
            )
    finally:
        config_module._config_manager = old_manager


def test_host_cookie_mode_admin_api_accepts_local_admin_jwt(tmp_path: Path, monkeypatch) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_response.status_code == 200
            token = login_response.json()["token"]

            users_response = client.get(
                "/api/users",
                headers={"AtlasClaw-Authenticate": token},
            )
            assert users_response.status_code == 200
            body = users_response.json()
            assert body["total"] >= 1
            assert any(user["username"] == "admin" for user in body["users"])
    finally:
        config_module._config_manager = old_manager


def test_host_cookie_mode_rejects_inactive_cookie_user(tmp_path: Path, monkeypatch) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            first_me_response = client.get(
                "/api/auth/me",
                cookies={
                    "Host-Authenticate": "host-token",
                    "hostUser": "host-disabled",
                    "hostDisplayName": "Host%20Disabled",
                    "hostTenantId": "tenant-a",
                },
            )
            assert first_me_response.status_code == 200

            login_response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_response.status_code == 200
            token = login_response.json()["token"]

            users_response = client.get(
                "/api/users",
                headers={"AtlasClaw-Authenticate": token},
            )
            assert users_response.status_code == 200
            host_user = next(
                user
                for user in users_response.json()["users"]
                if user["username"] == "host-disabled"
            )

            update_response = client.put(
                f"/api/users/{host_user['id']}",
                json={"is_active": False},
                headers={"AtlasClaw-Authenticate": token},
            )
            assert update_response.status_code == 200

            client.cookies.clear()
            inactive_me_response = client.get(
                "/api/auth/me",
                cookies={
                    "Host-Authenticate": "host-token",
                    "hostUser": "host-disabled",
                    "hostDisplayName": "Host%20Disabled",
                    "hostTenantId": "tenant-a",
                },
            )
            assert inactive_me_response.status_code == 401
    finally:
        config_module._config_manager = old_manager


def test_host_cookie_mode_admin_page_redirects_to_login_when_unauthenticated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app, config_module, old_manager = _create_host_cookie_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin/users",
                headers={"accept": "text/html"},
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert "/login.html" in response.headers["location"]
    finally:
        config_module._config_manager = old_manager
