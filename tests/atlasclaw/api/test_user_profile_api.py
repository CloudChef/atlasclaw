# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Unit tests for user profile API endpoints.

Tests:
- User self-service profile management (GET/PUT /users/me/profile)
- Password change functionality (PUT /users/me/password)
- Authentication requirements
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.guards import get_current_user
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.middleware import setup_auth_middleware
from app.atlasclaw.db import get_db_session
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import ServiceProviderConfigCreate, UserCreate
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


# Global reference to db manager for tests
_test_db_manager: DatabaseManager = None


async def _test_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Test-compatible db session dependency (plain async generator, not @asynccontextmanager)."""
    global _test_db_manager
    async with _test_db_manager.get_session() as session:
        yield session


def _build_client(tmp_path: Path, auth_config: AuthConfig) -> TestClient:
    """Build a test FastAPI client with all required routers and auth middleware."""
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()
    app.state.config = SimpleNamespace(auth=auth_config)
    
    # Setup auth middleware first (must be done before including routers)
    setup_auth_middleware(app, auth_config)
    
    app.include_router(create_router())
    
    # Override the db session dependency for api_router
    app.include_router(api_router)
    app.dependency_overrides[get_db_session] = _test_get_db_session
    
    return TestClient(app)


def _init_database_sync(tmp_path: Path):
    """Initialize database synchronously with test users."""
    global _test_db_manager

    async def _init():
        global _test_db_manager
        db_path = tmp_path / "test_user_profile.db"
        _test_db_manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        )
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
            # Create regular user with full profile
            await UserService.create(
                session,
                UserCreate(
                    username="testuser",
                    password="testpass123",
                    display_name="Test User",
                    email="testuser@test.com",
                    roles={"user": True},
                    auth_type="local",
                    is_active=True,
                ),
            )
            # Create another user for email conflict tests
            await UserService.create(
                session,
                UserCreate(
                    username="otheruser",
                    password="otherpass123",
                    display_name="Other User",
                    email="other@test.com",
                    roles={},
                    auth_type="local",
                    is_active=True,
                ),
            )
        return _test_db_manager

    return asyncio.run(_init())


def _cleanup_manager(manager):
    """Clean up database manager."""
    asyncio.run(manager.close())


def _create_provider_config_sync(
    manager: DatabaseManager,
    *,
    provider_type: str,
    instance_name: str,
    config: dict,
) -> None:
    """Create a DB-backed provider config for tests."""

    async def _create() -> None:
        async with manager.get_session() as session:
            await ServiceProviderConfigService.create(
                session,
                ServiceProviderConfigCreate(
                    provider_type=provider_type,
                    instance_name=instance_name,
                    config=config,
                    is_active=True,
                ),
            )

    asyncio.run(_create())


@contextmanager
def _patch_runtime_config(
    workspace_path: Path,
    service_providers: dict | None = None,
):
    """Patch config lookups used by user settings and merged provider catalog."""
    config = SimpleNamespace(
        workspace=SimpleNamespace(path=str(workspace_path)),
        service_providers=service_providers or {},
    )
    with patch("app.atlasclaw.api.api_routes.get_config", return_value=config), patch(
        "app.atlasclaw.core.provider_catalog.get_config",
        return_value=config,
    ):
        yield


def _get_auth_config() -> AuthConfig:
    """Get test auth configuration."""
    return AuthConfig(
        provider="local",
        jwt={
            "secret_key": "test-secret-key-for-profile-tests",
            "issuer": "atlasclaw-test",
            "header_name": "AtlasClaw-Authenticate",
            "cookie_name": "AtlasClaw-Authenticate",
            "expires_minutes": 60,
        },
    )


def _login_as(client: TestClient, username: str, password: str) -> str:
    """Login and return JWT token."""
    resp = client.post(
        "/api/auth/local/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    return resp.json()["token"]


class TestUserProfileAPI:
    """Tests for user profile self-service endpoints."""

    def test_get_own_profile(self, tmp_path):
        """Authenticated user can get their own profile."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.get(
            "/api/users/me/profile",
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["display_name"] == "Test User"
        assert data["email"] == "testuser@test.com"
        assert "id" in data

        _cleanup_manager(manager)

    def test_update_own_profile(self, tmp_path):
        """Authenticated user can update display_name, email, avatar_url."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.put(
            "/api/users/me/profile",
            json={
                "display_name": "Updated Name",
                "email": "updated@test.com",
                "avatar_url": "https://example.com/avatar.png",
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Updated Name"
        assert data["email"] == "updated@test.com"
        assert data["avatar_url"] == "https://example.com/avatar.png"

        # Verify changes persist
        get_resp = client.get(
            "/api/users/me/profile",
            headers={"AtlasClaw-Authenticate": token},
        )
        assert get_resp.json()["display_name"] == "Updated Name"

        _cleanup_manager(manager)

    def test_update_profile_partial(self, tmp_path):
        """User can update only some profile fields."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        # Update only display_name
        resp = client.put(
            "/api/users/me/profile",
            json={"display_name": "Only Name Updated"},
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Only Name Updated"
        # Email should remain unchanged
        assert data["email"] == "testuser@test.com"

        _cleanup_manager(manager)

    def test_update_profile_email_conflict(self, tmp_path):
        """Updating email to an existing email returns 409."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.put(
            "/api/users/me/profile",
            json={"email": "other@test.com"},  # Email belongs to otheruser
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 409
        assert "email" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_update_profile_empty_returns_400(self, tmp_path):
        """Updating profile with no fields returns 400."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.put(
            "/api/users/me/profile",
            json={},
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 400
        assert "no fields" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_change_password_success(self, tmp_path):
        """User can change password with correct current password."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.put(
            "/api/users/me/password",
            json={
                "current_password": "testpass123",
                "new_password": "newpassword456",
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify new password works
        new_token = _login_as(client, "testuser", "newpassword456")
        assert new_token is not None

        _cleanup_manager(manager)

    def test_change_password_wrong_current(self, tmp_path):
        """Wrong current password returns 400."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")

        resp = client.put(
            "/api/users/me/password",
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword456",
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 400
        assert "incorrect" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_upload_avatar_success(self, tmp_path):
        """User can upload avatar image and receives stored avatar URL."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.post(
                "/api/users/me/avatar",
                files={"avatar": ("avatar.png", b"fake-png-data", "image/png")},
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["avatar_url"].startswith("/user-content/avatars/testuser-")

        avatar_files = list((workspace_path / "public" / "avatars").glob("testuser-*.png"))
        assert len(avatar_files) == 1

        _cleanup_manager(manager)

    def test_upload_avatar_rejects_invalid_file_type(self, tmp_path):
        """Avatar upload rejects non-image files."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.post(
                "/api/users/me/avatar",
                files={"avatar": ("avatar.txt", b"not-an-image", "text/plain")},
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 400
        assert "supported" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_get_db_external_user_profile_by_id(self, tmp_path):
        """External users load their profile from the DB user table."""
        manager = _init_database_sync(tmp_path)
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        async def _create_external_user():
            async with _test_db_manager.get_session() as session:
                return await UserService.create(
                    session,
                    UserCreate(
                        username="sso-user@example.com",
                        password=None,
                        display_name="SSO User",
                        roles={"user": True},
                        auth_type="oidc:test",
                        is_active=True,
                    ),
                )

        db_user = asyncio.run(_create_external_user())

        app = FastAPI()
        app.include_router(api_router)
        app.dependency_overrides[get_current_user] = lambda: UserInfo(
            user_id=db_user.id,
            display_name="SSO User",
            auth_type="oidc:test",
            roles=["user"],
        )

        client = TestClient(app)

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.get("/api/users/me/profile")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == db_user.id
        assert data["username"] == "sso-user@example.com"
        assert data["display_name"] == "SSO User"
        assert data["auth_type"] == "oidc:test"

        _cleanup_manager(manager)

    def test_get_federated_profile_prefers_db_user_by_external_subject(self, tmp_path):
        """Federated accounts should resolve DB-managed profiles via the external subject."""
        manager = _init_database_sync(tmp_path)
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        async def _create_federated_user():
            async with _test_db_manager.get_session() as session:
                await UserService.create(
                    session,
                    UserCreate(
                        username="sso-user@example.com",
                        password=None,
                        display_name="Workspace SSO User",
                        email="sso-user@example.com",
                        roles={"viewer": True},
                        auth_type="oidc:test",
                        is_active=True,
                    ),
                )

        asyncio.run(_create_federated_user())

        app = FastAPI()
        app.include_router(api_router)
        app.dependency_overrides[get_current_user] = lambda: UserInfo(
            user_id="shadow-user-1",
            display_name="SSO User",
            auth_type="oidc:test",
            roles=["user"],
            extra={"external_subject": "sso-user@example.com"},
        )

        client = TestClient(app)

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.get("/api/users/me/profile")

        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "sso-user@example.com"
        assert data["display_name"] == "Workspace SSO User"
        assert data["email"] == "sso-user@example.com"
        assert data["roles"]["viewer"] is True

        _cleanup_manager(manager)

    def test_update_profile_unavailable_for_federated_account(self, tmp_path):
        """Federated accounts cannot use local-only profile mutation endpoints."""
        manager = _init_database_sync(tmp_path)
        app = FastAPI()
        app.include_router(api_router)
        app.dependency_overrides[get_current_user] = lambda: UserInfo(
            user_id="shadow-user-1",
            display_name="SSO User",
            auth_type="oidc:test",
            roles=["user"],
        )
        app.dependency_overrides[get_db_session] = _test_get_db_session

        client = TestClient(app)
        resp = client.put("/api/users/me/profile", json={"display_name": "Updated"})

        assert resp.status_code == 400
        assert "federated" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_get_my_provider_settings_redacts_sensitive_values(self, tmp_path):
        """Authenticated users should not receive saved sensitive provider values back."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        user_dir = workspace_path / "users" / "testuser"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "user_setting.json").write_text(
            json.dumps(
                {
                    "channels": {},
                    "providers": {
                        "smartcmp": {
                            "default": {
                                "configured": True,
                                "config": {
                                    "auth_type": "user_token",
                                    "user_token": "secret-token",
                                },
                                "updated_at": "2026-04-13T10:00:00Z",
                            }
                        }
                    },
                    "preferences": {},
                }
            ),
            encoding="utf-8",
        )

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.get(
                "/api/users/me/provider-settings",
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"]["smartcmp"]["default"]["configured"] is True
        assert data["providers"]["smartcmp"]["default"]["config"]["auth_type"] == "user_token"
        assert "user_token" not in data["providers"]["smartcmp"]["default"]["config"]

        _cleanup_manager(manager)

    def test_get_my_provider_settings_redacts_unknown_provider_sensitive_values(self, tmp_path):
        """Sensitive config keys are redacted even when a provider has no built-in schema."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        user_dir = workspace_path / "users" / "testuser"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "user_setting.json").write_text(
            json.dumps(
                {
                    "channels": {},
                    "providers": {
                        "external": {
                            "default": {
                                "configured": True,
                                "config": {
                                    "auth_type": "user_token",
                                    "user_token": "secret-user-token",
                                    "provider_token": "secret-provider-token",
                                    "accessToken": "secret-access-token",
                                    "api-key": "secret-api-key",
                                    "password": "secret-password",
                                    "clientSecret": "secret-client-secret",
                                    "session_cookie": "secret-cookie",
                                    "region": "cn-north-1",
                                },
                                "updated_at": "2026-04-13T10:00:00Z",
                            }
                        }
                    },
                    "preferences": {},
                }
            ),
            encoding="utf-8",
        )

        with patch(
            "app.atlasclaw.api.api_routes.get_config",
            return_value=SimpleNamespace(workspace=SimpleNamespace(path=str(workspace_path))),
        ):
            resp = client.get(
                "/api/users/me/provider-settings",
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        config = resp.json()["providers"]["external"]["default"]["config"]
        assert config == {
            "auth_type": "user_token",
            "region": "cn-north-1",
        }

        _cleanup_manager(manager)

    def test_put_my_provider_settings_persists_user_token_without_mutating_template_url(self, tmp_path):
        """Authenticated users can save personal provider credentials without storing base_url."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps(
                {
                    "workspace": {"path": str(workspace_path)},
                    "service_providers": {
                        "smartcmp": {
                            "default": {
                                "base_url": "https://console.smartcmp.cloud",
                                "auth_type": "user_token",
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": "user_token",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "auth_type": "user_token",
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads((workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8"))
        assert saved["providers"]["smartcmp"]["default"]["config"]["user_token"] == "secret-token"
        assert "base_url" not in saved["providers"]["smartcmp"]["default"]["config"]

        _cleanup_manager(manager)

    def test_put_my_provider_settings_uses_db_managed_provider_template(self, tmp_path):
        """User-owned provider settings use the merged DB/config provider catalog."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)
        _create_provider_config_sync(
            manager,
            provider_type="smartcmp",
            instance_name="db-managed",
            config={
                "base_url": "https://db.smartcmp.cloud",
                "auth_type": ["provider_token", "user_token"],
                "provider_token": "shared-provider-token",
            },
        )

        with _patch_runtime_config(workspace_path, service_providers={}):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "db-managed",
                    "config": {
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        persisted = saved["providers"]["smartcmp"]["db-managed"]["config"]
        assert persisted["auth_type"] == ["provider_token", "user_token"]
        assert persisted["user_token"] == "secret-token"
        assert "provider_token" not in persisted
        assert "base_url" not in persisted

        _cleanup_manager(manager)

    def test_put_my_provider_settings_preserves_existing_sensitive_values_when_omitted(self, tmp_path):
        """Omitted sensitive fields should be preserved so redacted GET payloads do not erase them."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        user_dir = workspace_path / "users" / "testuser"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "user_setting.json").write_text(
            json.dumps(
                {
                    "channels": {},
                    "providers": {
                        "smartcmp": {
                            "default": {
                                "configured": True,
                                "config": {
                                    "auth_type": "user_token",
                                    "user_token": "secret-token",
                                },
                                "updated_at": "2026-04-13T10:00:00Z",
                            }
                        }
                    },
                    "preferences": {},
                }
            ),
            encoding="utf-8",
        )

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": "user_token",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "auth_type": "user_token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        assert saved["providers"]["smartcmp"]["default"]["config"]["auth_type"] == "user_token"
        assert saved["providers"]["smartcmp"]["default"]["config"]["user_token"] == "secret-token"

        _cleanup_manager(manager)

    def test_put_my_provider_settings_uses_template_auth_chain_for_multi_auth_templates(self, tmp_path):
        """Multi-auth templates keep template auth chain authoritative over user-submitted auth_type."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": ["cookie", "user_token"],
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "auth_type": "user_token",
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        assert saved["providers"]["smartcmp"]["default"]["config"]["auth_type"] == [
            "cookie",
            "user_token",
        ]
        assert saved["providers"]["smartcmp"]["default"]["config"]["user_token"] == "secret-token"

        _cleanup_manager(manager)

    def test_put_my_provider_settings_does_not_persist_template_secrets_for_multi_auth_templates(self, tmp_path):
        """User settings must not copy template-owned auth fields into the persisted user config."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": ["cookie", "user_token"],
                        "cookie": "AtlasClaw-Host-Authenticate=template-cookie",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        persisted = saved["providers"]["smartcmp"]["default"]["config"]
        assert persisted["auth_type"] == ["cookie", "user_token"]
        assert persisted["user_token"] == "secret-token"
        assert "cookie" not in persisted

        _cleanup_manager(manager)

    def test_put_my_provider_settings_does_not_allow_user_provider_token_override(self, tmp_path):
        """Shared provider tokens are template-owned and cannot be persisted from user settings."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": ["provider_token", "user_token"],
                        "provider_token": "template-provider-token",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "provider_token": "user-provider-token",
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 200
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        persisted = saved["providers"]["smartcmp"]["default"]["config"]
        assert persisted["auth_type"] == ["provider_token", "user_token"]
        assert persisted["user_token"] == "secret-token"
        assert "provider_token" not in persisted

        _cleanup_manager(manager)

    def test_put_my_provider_settings_rejects_template_without_user_token(self, tmp_path):
        """Users can only save settings for provider templates that include user_token."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": ["cookie", "provider_token"],
                        "provider_token": "template-provider-token",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {
                        "user_token": "secret-token",
                    },
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 422
        assert "does not support user-owned user_token" in resp.json()["detail"]
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        assert saved["providers"] == {}

        _cleanup_manager(manager)

    def test_put_my_provider_settings_requires_initial_user_token(self, tmp_path):
        """A first-time personal provider setting must include a user_token."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "testuser", "testpass123")
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        with _patch_runtime_config(
            workspace_path,
            service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://console.smartcmp.cloud",
                        "auth_type": ["provider_token", "user_token"],
                        "provider_token": "template-provider-token",
                    }
                }
            },
        ):
            resp = client.put(
                "/api/users/me/provider-settings",
                json={
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "config": {},
                },
                headers={"AtlasClaw-Authenticate": token},
            )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "user_token is required for user-owned provider settings"
        saved = json.loads(
            (workspace_path / "users" / "testuser" / "user_setting.json").read_text(encoding="utf-8")
        )
        assert saved["providers"] == {}

        _cleanup_manager(manager)

    def test_profile_not_accessible_when_unauthenticated(self, tmp_path):
        """Unauthenticated request to profile returns 401."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())

        # GET profile without auth
        resp = client.get("/api/users/me/profile")
        assert resp.status_code == 401

        # PUT profile without auth
        resp = client.put(
            "/api/users/me/profile",
            json={"display_name": "Test"},
        )
        assert resp.status_code == 401

        # Change password without auth
        resp = client.put(
            "/api/users/me/password",
            json={
                "current_password": "test",
                "new_password": "newpass123",
            },
        )
        assert resp.status_code == 401

        _cleanup_manager(manager)

    def test_profile_with_invalid_token_returns_401(self, tmp_path):
        """Request with invalid token returns 401."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())

        resp = client.get(
            "/api/users/me/profile",
            headers={"AtlasClaw-Authenticate": "invalid-token-xyz"},
        )

        assert resp.status_code == 401

        _cleanup_manager(manager)
