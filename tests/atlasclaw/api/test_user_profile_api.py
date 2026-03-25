# -*- coding: utf-8 -*-
"""Unit tests for user profile API endpoints.

Tests:
- User self-service profile management (GET/PUT /users/me/profile)
- Password change functionality (PUT /users/me/password)
- Authentication requirements
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.middleware import setup_auth_middleware
from app.atlasclaw.db import get_db_session
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate
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
                    is_admin=False,
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
                    is_admin=False,
                    is_active=True,
                ),
            )
        return _test_db_manager

    return asyncio.run(_init())


def _cleanup_manager(manager):
    """Clean up database manager."""
    asyncio.run(manager.close())


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
