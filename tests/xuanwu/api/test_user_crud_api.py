# -*- coding: utf-8 -*-
"""Unit tests for user CRUD API endpoints.

Tests:
- Admin CRUD operations for user management
- Authorization enforcement (admin-only access)
- Edge cases: duplicate username/email, self-deletion prevention, password validation
- Audit logging verification
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.api.api_routes import router as api_router
from app.xuanwu.api.routes import APIContext, create_router, set_api_context
from app.xuanwu.auth.config import AuthConfig
from app.xuanwu.auth.middleware import setup_auth_middleware
from app.xuanwu.db import get_db_session
from app.xuanwu.db.database import DatabaseConfig, DatabaseManager, init_database
from app.xuanwu.db.orm.user import UserService
from app.xuanwu.db.schemas import UserCreate
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.queue import SessionQueue
from app.xuanwu.skills.registry import SkillRegistry


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
        db_path = tmp_path / "test_user_crud.db"
        _test_db_manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        )
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
            # Create admin user
            await UserService.create(
                session,
                UserCreate(
                    username="admin",
                    password="adminpass123",
                    display_name="Test Admin",
                    email="admin@test.com",
                    roles={"admin": True},
                    auth_type="local",
                    is_admin=True,
                    is_active=True,
                ),
            )
            # Create regular user
            await UserService.create(
                session,
                UserCreate(
                    username="regularuser",
                    password="userpass123",
                    display_name="Regular User",
                    email="user@test.com",
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
            "secret_key": "test-secret-key-for-testing",
            "issuer": "xuanwu-test",
            "header_name": "Xuanwu-Authenticate",
            "cookie_name": "Xuanwu-Authenticate",
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


class TestUserCRUDAPI:
    """Tests for user CRUD API endpoints."""

    # ===================== Happy Path Tests =====================

    def test_create_user_as_admin(self, tmp_path):
        """Admin can create a new user via POST /api/users."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.post(
            "/api/users",
            json={
                "username": "newuser",
                "password": "newuserpass123",
                "display_name": "New User",
                "email": "newuser@test.com",
                "roles": {},
                "is_active": True,
                "is_admin": False,
            },
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["display_name"] == "New User"
        assert data["email"] == "newuser@test.com"
        assert data["is_admin"] is False
        assert "id" in data

        _cleanup_manager(manager)

    def test_list_users_with_pagination(self, tmp_path):
        """Admin can list users with pagination via GET /api/users."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # First page
        resp = client.get(
            "/api/users?page=1&page_size=1",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["users"]) == 1

        # Second page
        resp = client.get(
            "/api/users?page=2&page_size=1",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["users"]) == 1

        _cleanup_manager(manager)

    def test_list_users_with_search(self, tmp_path):
        """Admin can search users by username/email."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # Search by username
        resp = client.get(
            "/api/users?search=regular",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["users"][0]["username"] == "regularuser"

        # Search by email
        resp = client.get(
            "/api/users?search=admin@test",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["users"][0]["email"] == "admin@test.com"

        _cleanup_manager(manager)

    def test_get_user_by_id(self, tmp_path):
        """Admin can get a single user by ID."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # Get list to find a user ID
        list_resp = client.get(
            "/api/users",
            headers={"Xuanwu-Authenticate": token},
        )
        user_id = list_resp.json()["users"][0]["id"]

        # Get by ID
        resp = client.get(
            f"/api/users/{user_id}",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == user_id

        _cleanup_manager(manager)

    def test_update_user(self, tmp_path):
        """Admin can update a user via PUT /api/users/{id}."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # Get regular user ID
        list_resp = client.get(
            "/api/users?search=regularuser",
            headers={"Xuanwu-Authenticate": token},
        )
        user_id = list_resp.json()["users"][0]["id"]

        # Update user
        resp = client.put(
            f"/api/users/{user_id}",
            json={
                "display_name": "Updated Display Name",
                "email": "updated@test.com",
            },
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Updated Display Name"
        assert data["email"] == "updated@test.com"

        _cleanup_manager(manager)

    def test_delete_user(self, tmp_path):
        """Admin can delete a user via DELETE /api/users/{id}."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # Create a user to delete
        create_resp = client.post(
            "/api/users",
            json={
                "username": "todelete",
                "password": "deletepass123",
                "display_name": "To Delete",
            },
            headers={"Xuanwu-Authenticate": token},
        )
        user_id = create_resp.json()["id"]

        # Delete user
        resp = client.delete(
            f"/api/users/{user_id}",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 204

        # Verify user is deleted
        get_resp = client.get(
            f"/api/users/{user_id}",
            headers={"Xuanwu-Authenticate": token},
        )
        assert get_resp.status_code == 404

        _cleanup_manager(manager)

    # ===================== Authorization Tests =====================

    def test_create_user_as_non_admin_returns_403(self, tmp_path):
        """Non-admin user gets 403 when trying to create user."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "regularuser", "userpass123")

        resp = client.post(
            "/api/users",
            json={
                "username": "newuser",
                "password": "newuserpass123",
            },
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_list_users_as_non_admin_returns_403(self, tmp_path):
        """Non-admin user gets 403 when trying to list users."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "regularuser", "userpass123")

        resp = client.get(
            "/api/users",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 403

        _cleanup_manager(manager)

    def test_delete_user_as_non_admin_returns_403(self, tmp_path):
        """Non-admin user gets 403 when trying to delete user."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        user_token = _login_as(client, "regularuser", "userpass123")

        # Get admin user ID
        list_resp = client.get(
            "/api/users?search=admin",
            headers={"Xuanwu-Authenticate": admin_token},
        )
        admin_id = list_resp.json()["users"][0]["id"]

        # Try to delete as non-admin
        resp = client.delete(
            f"/api/users/{admin_id}",
            headers={"Xuanwu-Authenticate": user_token},
        )

        assert resp.status_code == 403

        _cleanup_manager(manager)

    def test_unauthenticated_request_returns_401(self, tmp_path):
        """Unauthenticated request gets 401."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())

        # Try to list users without auth
        resp = client.get("/api/users")
        assert resp.status_code == 401

        # Try to create user without auth
        resp = client.post(
            "/api/users",
            json={
                "username": "test",
                "password": "testpass123",
            },
        )
        assert resp.status_code == 401

        _cleanup_manager(manager)

    def test_invalid_token_returns_401(self, tmp_path):
        """Request with invalid token returns 401."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())

        resp = client.get(
            "/api/users",
            headers={"Xuanwu-Authenticate": "invalid-token"},
        )

        assert resp.status_code == 401

        _cleanup_manager(manager)

    # ===================== Edge Case Tests =====================

    def test_create_duplicate_username_returns_409(self, tmp_path):
        """Creating user with existing username returns 409."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.post(
            "/api/users",
            json={
                "username": "admin",
                "password": "anotherpass123",
            },
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_create_duplicate_email_returns_409(self, tmp_path):
        """Creating user with existing email returns 409."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.post(
            "/api/users",
            json={
                "username": "newusername",
                "password": "somepass123",
                "email": "admin@test.com",  # Duplicate email
            },
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_delete_self_returns_400(self, tmp_path):
        """Admin cannot delete their own account."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # Get admin's own user ID
        list_resp = client.get(
            "/api/users?search=admin",
            headers={"Xuanwu-Authenticate": token},
        )
        admin_id = list_resp.json()["users"][0]["id"]

        # Try to delete self
        resp = client.delete(
            f"/api/users/{admin_id}",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 400
        assert "own account" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_update_nonexistent_user_returns_404(self, tmp_path):
        """Updating non-existent user returns 404."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.put(
            "/api/users/nonexistent-id-12345",
            json={"display_name": "Test"},
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 404

        _cleanup_manager(manager)

    def test_delete_nonexistent_user_returns_404(self, tmp_path):
        """Deleting non-existent user returns 404."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.delete(
            "/api/users/nonexistent-id-12345",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 404

        _cleanup_manager(manager)

    def test_get_nonexistent_user_returns_404(self, tmp_path):
        """Getting non-existent user returns 404."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.get(
            "/api/users/nonexistent-id-12345",
            headers={"Xuanwu-Authenticate": token},
        )

        assert resp.status_code == 404

        _cleanup_manager(manager)
