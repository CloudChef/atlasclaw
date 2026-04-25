# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""E2E: role management lifecycle with local auth enabled."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _create_role_management_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "role-management-e2e.db"
    workspace_path = tmp_path / ".atlasclaw-role-management-e2e"
    config_path = tmp_path / "atlasclaw.role-management.e2e.json"

    config = {
        "workspace": {
            "path": str(workspace_path.resolve()),
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
                "secret_key": "role-management-e2e-secret",
                "issuer": "atlasclaw-role-management-e2e",
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


def _auth_headers(token: str) -> dict[str, str]:
    return {"AtlasClaw-Authenticate": token}


@pytest.mark.e2e
def test_role_management_full_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_module, old_config_manager = _create_role_management_app(tmp_path, monkeypatch)

    try:
        with TestClient(app) as client:
            unauthenticated_roles = client.get("/api/roles?page=1&page_size=100")
            assert unauthenticated_roles.status_code == 401

            login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            assert login_resp.status_code == 200
            admin_token = login_resp.json()["token"]
            admin_headers = _auth_headers(admin_token)

            skills_resp = client.get("/api/skills", headers=admin_headers)
            assert skills_resp.status_code == 200
            skills_payload = skills_resp.json()
            assert len(skills_payload["skills"]) > 0
            first_skill = skills_payload["skills"][0]["name"]

            builtin_roles_resp = client.get("/api/roles?page=1&page_size=100", headers=admin_headers)
            assert builtin_roles_resp.status_code == 200
            builtin_roles = builtin_roles_resp.json()["roles"]
            builtin_identifiers = {role["identifier"] for role in builtin_roles}
            assert {"admin", "user", "viewer"}.issubset(builtin_identifiers)
            admin_role = next(role for role in builtin_roles if role["identifier"] == "admin")
            assert admin_role["permissions"]["roles"]["manage_permissions"] is True
            assert admin_role["permissions"]["users"]["assign_roles"] is True
            assert admin_role["permissions"]["channels"]["manage_permissions"] is True

            create_user_resp = client.post(
                "/api/users",
                json={
                    "username": "role-user",
                    "password": "User@123",
                    "display_name": "Role User",
                    "email": "role-user@test.com",
                    "roles": {},
                    "is_active": True,
                    "is_admin": False,
                },
                headers=admin_headers,
            )
            assert create_user_resp.status_code == 201
            user_id = create_user_resp.json()["id"]

            create_target_user_resp = client.post(
                "/api/users",
                json={
                    "username": "target-user",
                    "password": "Target@123",
                    "display_name": "Target User",
                    "email": "target-user@test.com",
                    "roles": {},
                    "is_active": True,
                    "is_admin": False,
                },
                headers=admin_headers,
            )
            assert create_target_user_resp.status_code == 201
            target_user_id = create_target_user_resp.json()["id"]

            create_role_resp = client.post(
                "/api/roles",
                json={
                    "name": "Operations Manager",
                    "identifier": "ops_manager",
                    "description": "Manage operational skills and channels.",
                    "permissions": {
                        "skills": {
                            "module_permissions": {
                                "view": True,
                                "enable_disable": True,
                                "manage_permissions": True,
                            },
                            "skill_permissions": [
                                {
                                    "skill_id": first_skill,
                                    "skill_name": first_skill,
                                    "description": "Dynamic skill from /api/skills",
                                    "authorized": True,
                                    "enabled": True,
                                }
                            ],
                        },
                        "channels": {
                            "view": True,
                            "create": False,
                            "edit": True,
                            "delete": False,
                            "manage_permissions": True,
                        },
                        "tokens": {
                            "view": False,
                            "create": False,
                            "edit": False,
                            "delete": False,
                            "manage_permissions": False,
                        },
                        "users": {
                            "view": True,
                            "create": False,
                            "edit": False,
                            "delete": False,
                            "assign_roles": True,
                            "manage_permissions": True,
                        },
                        "roles": {
                            "view": True,
                            "create": False,
                            "edit": False,
                            "delete": False,
                            "manage_permissions": False,
                        },
                    },
                    "is_active": True,
                },
                headers=admin_headers,
            )
            assert create_role_resp.status_code == 201
            created_role = create_role_resp.json()
            role_id = created_role["id"]
            assert created_role["permissions"]["channels"]["manage_permissions"] is True
            assert created_role["permissions"]["skills"]["skill_permissions"][0]["skill_id"] == first_skill
            assert created_role["permissions"]["skills"]["skill_permissions"][0]["enabled"] is True

            update_role_resp = client.put(
                f"/api/roles/{role_id}",
                json={
                    "description": "Updated operations manager role.",
                    "is_active": True,
                    "permissions": {
                        "skills": {
                            "module_permissions": {
                                "view": True,
                                "enable_disable": False,
                                "manage_permissions": True,
                            },
                            "skill_permissions": [
                                {
                                    "skill_id": first_skill,
                                    "skill_name": first_skill,
                                    "description": "Dynamic skill from /api/skills",
                                    "authorized": True,
                                    "enabled": False,
                                }
                            ],
                        },
                        "channels": {
                            "view": True,
                            "create": False,
                            "edit": True,
                            "delete": False,
                            "manage_permissions": False,
                        },
                        "tokens": {
                            "view": False,
                            "create": False,
                            "edit": False,
                            "delete": False,
                            "manage_permissions": False,
                        },
                        "users": {
                            "view": True,
                            "create": False,
                            "edit": False,
                            "delete": False,
                            "assign_roles": True,
                            "manage_permissions": False,
                        },
                        "roles": {
                            "view": True,
                            "create": False,
                            "edit": True,
                            "delete": False,
                            "manage_permissions": True,
                        },
                    },
                },
                headers=admin_headers,
            )
            assert update_role_resp.status_code == 200
            updated_role = update_role_resp.json()
            assert updated_role["description"] == "Updated operations manager role."
            assert updated_role["is_active"] is True
            assert updated_role["permissions"]["roles"]["manage_permissions"] is True
            assert updated_role["permissions"]["skills"]["skill_permissions"][0]["enabled"] is False
            assert updated_role["permissions"]["roles"]["edit"] is True

            get_role_resp = client.get(f"/api/roles/{role_id}", headers=admin_headers)
            assert get_role_resp.status_code == 200
            assert get_role_resp.json()["permissions"]["users"]["assign_roles"] is True
            assert get_role_resp.json()["permissions"]["skills"]["skill_permissions"][0]["enabled"] is False

            assign_role_resp = client.put(
                f"/api/users/{user_id}",
                json={"roles": {"ops_manager": True}},
                headers=admin_headers,
            )
            assert assign_role_resp.status_code == 200
            assert assign_role_resp.json()["roles"]["ops_manager"] is True

            blocked_delete_resp = client.delete(f"/api/roles/{role_id}", headers=admin_headers)
            assert blocked_delete_resp.status_code == 400
            assert "assigned" in blocked_delete_resp.json()["detail"].lower()

            regular_login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "role-user", "password": "User@123"},
            )
            assert regular_login_resp.status_code == 200
            regular_headers = _auth_headers(regular_login_resp.json()["token"])

            regular_roles_resp = client.get("/api/roles?page=1&page_size=100", headers=regular_headers)
            assert regular_roles_resp.status_code == 200
            assert any(role["identifier"] == "ops_manager" for role in regular_roles_resp.json()["roles"])

            regular_users_resp = client.get("/api/users?search=target-user", headers=regular_headers)
            assert regular_users_resp.status_code == 200
            assert regular_users_resp.json()["users"][0]["username"] == "target-user"

            regular_assign_resp = client.put(
                f"/api/users/{target_user_id}",
                json={"roles": {"viewer": True}},
                headers=regular_headers,
            )
            assert regular_assign_resp.status_code == 200
            assert regular_assign_resp.json()["roles"]["viewer"] is True

            regular_create_role_resp = client.post(
                "/api/roles",
                json={
                    "name": "Blocked Role",
                    "identifier": "blocked_role",
                    "description": "Should not be creatable by role-user.",
                    "permissions": {},
                    "is_active": True,
                },
                headers=regular_headers,
            )
            assert regular_create_role_resp.status_code == 403

            regular_delete_role_resp = client.delete(f"/api/roles/{role_id}", headers=regular_headers)
            assert regular_delete_role_resp.status_code == 403

            unassign_role_resp = client.put(
                f"/api/users/{user_id}",
                json={"roles": {}},
                headers=admin_headers,
            )
            assert unassign_role_resp.status_code == 200
            assert unassign_role_resp.json()["roles"] == {}

            delete_role_resp = client.delete(f"/api/roles/{role_id}", headers=admin_headers)
            assert delete_role_resp.status_code == 204

            deleted_role_resp = client.get(f"/api/roles/{role_id}", headers=admin_headers)
            assert deleted_role_resp.status_code == 404
    finally:
        from app.atlasclaw.db.database import get_db_manager

        asyncio.run(get_db_manager().close())
        config_module._config_manager = old_config_manager
