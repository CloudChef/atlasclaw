# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

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

from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.jwt_token import issue_atlas_token
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
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["display_name"] == "New User"
        assert data["email"] == "newuser@test.com"
        assert data["is_admin"] is False
        assert "id" in data

        _cleanup_manager(manager)

    def test_create_user_with_unknown_role_returns_400(self, tmp_path):
        """User creation rejects role assignments that reference unknown identifiers."""
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
                "roles": {"future_admin_like": True},
                "is_active": True,
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 400
        assert "unknown role" in resp.json()["detail"].lower()
        assert "future_admin_like" in resp.json()["detail"]

        _cleanup_manager(manager)

    def test_list_users_with_pagination(self, tmp_path):
        """Admin can list users with pagination via GET /api/users."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        # First page
        resp = client.get(
            "/api/users?page=1&page_size=1",
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["users"]) == 1

        # Second page
        resp = client.get(
            "/api/users?page=2&page_size=1",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["users"][0]["username"] == "regularuser"

        # Search by email
        resp = client.get(
            "/api/users?search=admin@test",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )
        user_id = list_resp.json()["users"][0]["id"]

        # Get by ID
        resp = client.get(
            f"/api/users/{user_id}",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )
        user_id = list_resp.json()["users"][0]["id"]

        # Update user
        resp = client.put(
            f"/api/users/{user_id}",
            json={
                "display_name": "Updated Display Name",
                "email": "updated@test.com",
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Updated Display Name"
        assert data["email"] == "updated@test.com"

        _cleanup_manager(manager)

    def test_update_user_duplicate_email_returns_409(self, tmp_path):
        """Admin update returns 409 when the target email is already in use."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        list_resp = client.get(
            "/api/users?search=regularuser",
            headers={"AtlasClaw-Authenticate": token},
        )
        user_id = list_resp.json()["users"][0]["id"]

        resp = client.put(
            f"/api/users/{user_id}",
            json={"email": "admin@test.com"},
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 409
        assert "email already in use" in resp.json()["detail"].lower()

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
            headers={"AtlasClaw-Authenticate": token},
        )
        user_id = create_resp.json()["id"]

        # Delete user
        resp = client.delete(
            f"/api/users/{user_id}",
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 204

        # Verify user is deleted
        get_resp = client.get(
            f"/api/users/{user_id}",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 403
        assert "users.create" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_list_users_as_non_admin_returns_403(self, tmp_path):
        """Non-admin user gets 403 when trying to list users."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "regularuser", "userpass123")

        resp = client.get(
            "/api/users",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": admin_token},
        )
        admin_id = list_resp.json()["users"][0]["id"]

        # Try to delete as non-admin
        resp = client.delete(
            f"/api/users/{admin_id}",
            headers={"AtlasClaw-Authenticate": user_token},
        )

        assert resp.status_code == 403

        _cleanup_manager(manager)

    def test_user_with_assign_roles_can_manage_role_assignments_only(self, tmp_path):
        """Role assigners can update roles without broader user-edit privileges."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        admin_headers = {"AtlasClaw-Authenticate": admin_token}

        create_assigner_resp = client.post(
            "/api/users",
            json={
                "username": "assigner",
                "password": "assignerpass123",
                "display_name": "Assigner",
                "email": "assigner@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_resp.status_code == 201
        assigner_id = create_assigner_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Role Assigner",
                "identifier": "role_assigner",
                "description": "Can view users and assign roles.",
                "permissions": {
                    "users": {
                        "view": True,
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        assigner_token = _login_as(client, "assigner", "assignerpass123")
        assigner_headers = {"AtlasClaw-Authenticate": assigner_token}

        role_catalog_resp = client.get("/api/roles?page=1&page_size=100", headers=assigner_headers)
        assert role_catalog_resp.status_code == 200
        role_catalog = role_catalog_resp.json()["roles"]
        assert [role["identifier"] for role in role_catalog] == ["role_assigner"]
        assert role_catalog[0]["permissions"] == {}

        own_role_resp = client.get(f"/api/roles/{create_role_resp.json()['id']}", headers=assigner_headers)
        assert own_role_resp.status_code == 200
        assert own_role_resp.json()["identifier"] == "role_assigner"
        assert own_role_resp.json()["permissions"] == {}

        admin_role = next(
            role
            for role in client.get("/api/roles?page=1&page_size=100", headers=admin_headers).json()["roles"]
            if role["identifier"] == "admin"
        )
        other_role_resp = client.get(f"/api/roles/{admin_role['id']}", headers=assigner_headers)
        assert other_role_resp.status_code == 403

        list_users_resp = client.get("/api/users?search=regularuser", headers=assigner_headers)
        assert list_users_resp.status_code == 200
        regular_user_id = list_users_resp.json()["users"][0]["id"]

        assign_viewer_resp = client.put(
            f"/api/users/{regular_user_id}",
            json={"roles": {"viewer": True}},
            headers=assigner_headers,
        )
        assert assign_viewer_resp.status_code == 200
        assert assign_viewer_resp.json()["roles"]["viewer"] is True

        edit_profile_resp = client.put(
            f"/api/users/{regular_user_id}",
            json={"display_name": "Should Not Work"},
            headers=assigner_headers,
        )
        assert edit_profile_resp.status_code == 403
        assert "users.edit" in edit_profile_resp.json()["detail"].lower()

        reset_password_resp = client.put(
            f"/api/users/{regular_user_id}",
            json={"password": "shouldnotwork123"},
            headers=assigner_headers,
        )
        assert reset_password_resp.status_code == 403
        assert "users.edit" in reset_password_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_user_with_edit_permission_can_reset_another_user_password(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_headers = {"AtlasClaw-Authenticate": _login_as(client, "admin", "adminpass123")}

        create_editor_resp = client.post(
            "/api/users",
            json={
                "username": "usereditor",
                "password": "editorpass123",
                "display_name": "User Editor",
                "email": "usereditor@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_editor_resp.status_code == 201
        editor_id = create_editor_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "User Editor",
                "identifier": "user_editor",
                "description": "Can edit users.",
                "permissions": {
                    "users": {
                        "view": True,
                        "edit": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{editor_id}",
            json={"roles": {"user_editor": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        list_users_resp = client.get("/api/users?search=regularuser", headers=admin_headers)
        assert list_users_resp.status_code == 200
        regular_user_id = list_users_resp.json()["users"][0]["id"]

        editor_headers = {"AtlasClaw-Authenticate": _login_as(client, "usereditor", "editorpass123")}
        reset_password_resp = client.put(
            f"/api/users/{regular_user_id}",
            json={"password": "newregularpass123"},
            headers=editor_headers,
        )
        assert reset_password_resp.status_code == 200

        assert _login_as(client, "regularuser", "newregularpass123")

        _cleanup_manager(manager)

    def test_user_with_assign_roles_cannot_self_assign_admin_role(self, tmp_path):
        """Role assigners cannot promote themselves to the built-in admin role."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        admin_headers = {"AtlasClaw-Authenticate": admin_token}

        create_assigner_resp = client.post(
            "/api/users",
            json={
                "username": "assigner",
                "password": "assignerpass123",
                "display_name": "Assigner",
                "email": "assigner@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_resp.status_code == 201
        assigner_id = create_assigner_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Role Assigner",
                "identifier": "role_assigner",
                "description": "Can view users and assign roles.",
                "permissions": {
                    "users": {
                        "view": True,
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        assigner_token = _login_as(client, "assigner", "assignerpass123")
        assigner_headers = {"AtlasClaw-Authenticate": assigner_token}

        elevate_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True, "admin": True}},
            headers=assigner_headers,
        )
        assert elevate_resp.status_code == 403
        assert "administrator" in elevate_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_user_with_assign_roles_cannot_create_admin_user(self, tmp_path):
        """Role assigners cannot grant the built-in admin role during user creation."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        admin_headers = {"AtlasClaw-Authenticate": admin_token}

        create_assigner_resp = client.post(
            "/api/users",
            json={
                "username": "assigner",
                "password": "assignerpass123",
                "display_name": "Assigner",
                "email": "assigner@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_resp.status_code == 201
        assigner_id = create_assigner_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Role Assigner",
                "identifier": "role_assigner",
                "description": "Can view users and assign roles.",
                "permissions": {
                    "users": {
                        "view": True,
                        "create": True,
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        assigner_token = _login_as(client, "assigner", "assignerpass123")
        assigner_headers = {"AtlasClaw-Authenticate": assigner_token}

        create_admin_resp = client.post(
            "/api/users",
            json={
                "username": "shadowadmin",
                "password": "shadowadminpass123",
                "display_name": "Shadow Admin",
                "email": "shadowadmin@test.com",
                "roles": {"admin": True},
                "is_active": True,
            },
            headers=assigner_headers,
        )
        assert create_admin_resp.status_code == 403
        assert "administrator" in create_admin_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_user_with_assign_roles_cannot_self_assign_unknown_role(self, tmp_path):
        """Role assigners cannot store future role identifiers on their own account."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        admin_headers = {"AtlasClaw-Authenticate": admin_token}

        create_assigner_resp = client.post(
            "/api/users",
            json={
                "username": "assigner",
                "password": "assignerpass123",
                "display_name": "Assigner",
                "email": "assigner@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_resp.status_code == 201
        assigner_id = create_assigner_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Role Assigner",
                "identifier": "role_assigner",
                "description": "Can view users and assign roles.",
                "permissions": {
                    "users": {
                        "view": True,
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        assigner_token = _login_as(client, "assigner", "assignerpass123")
        assigner_headers = {"AtlasClaw-Authenticate": assigner_token}

        elevate_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True, "future_admin_like": True}},
            headers=assigner_headers,
        )
        assert elevate_resp.status_code == 400
        assert "unknown role" in elevate_resp.json()["detail"].lower()
        assert "future_admin_like" in elevate_resp.json()["detail"]

        _cleanup_manager(manager)

    def test_user_with_assign_roles_cannot_grant_protected_custom_role(self, tmp_path):
        """Role assigners cannot grant custom roles with high-risk permissions."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, "admin", "adminpass123")
        admin_headers = {"AtlasClaw-Authenticate": admin_token}

        create_assigner_resp = client.post(
            "/api/users",
            json={
                "username": "assigner",
                "password": "assignerpass123",
                "display_name": "Assigner",
                "email": "assigner@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_resp.status_code == 201
        assigner_id = create_assigner_resp.json()["id"]

        create_assigner_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Role Assigner",
                "identifier": "role_assigner",
                "description": "Can view users and assign low-risk roles.",
                "permissions": {
                    "users": {
                        "view": True,
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_assigner_role_resp.status_code == 201

        create_protected_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Catalog Manager",
                "identifier": "catalog_manager",
                "description": "Can edit provider configurations.",
                "permissions": {
                    "provider_configs": {
                        "view": True,
                        "edit": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_protected_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        assigner_token = _login_as(client, "assigner", "assignerpass123")
        assigner_headers = {"AtlasClaw-Authenticate": assigner_token}

        elevate_resp = client.put(
            f"/api/users/{assigner_id}",
            json={"roles": {"role_assigner": True, "catalog_manager": True}},
            headers=assigner_headers,
        )
        assert elevate_resp.status_code == 403
        assert "protected role" in elevate_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_federated_admin_cannot_delete_own_workspace_user(self, tmp_path):
        """Federated admins are protected from deleting their own workspace user record."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())

        async def _create_federated_user():
            async with _test_db_manager.get_session() as session:
                user = await UserService.create(
                    session,
                    UserCreate(
                        username="sso-admin@example.com",
                        password=None,
                        display_name="SSO Admin",
                        email="sso-admin@example.com",
                        roles={"admin": True},
                        auth_type="oidc:test",
                        is_active=True,
                    ),
                )
                return user.id

        federated_user_id = asyncio.run(_create_federated_user())
        token = issue_atlas_token(
            subject="shadow-user-1",
            is_admin=False,
            roles=[],
            auth_type="oidc:test",
            secret_key="test-secret-key-for-testing",
            expires_minutes=60,
            issuer="atlasclaw-test",
            additional_claims={
                "external_subject": "sso-admin@example.com",
                "provider_subject": "oidc:sso-admin@example.com",
            },
        )

        delete_resp = client.delete(
            f"/api/users/{federated_user_id}",
            headers={"AtlasClaw-Authenticate": token},
        )

        assert delete_resp.status_code == 400
        assert "cannot delete your own account" in delete_resp.json()["detail"].lower()

        async def _fetch_user():
            async with _test_db_manager.get_session() as session:
                return await UserService.get_by_id(session, federated_user_id)

        assert asyncio.run(_fetch_user()) is not None

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
            headers={"AtlasClaw-Authenticate": "invalid-token"},
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
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_create_same_username_different_auth_type_succeeds(self, tmp_path):
        """Creating same username under a different auth type is allowed."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        resp = client.post(
            "/api/users",
            json={
                "username": "admin",
                "password": "cookiepass123",
                "display_name": "Cookie Admin",
                "auth_type": "cookie",
                "roles": {"user": True},
            },
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 201
        assert resp.json()["username"] == "admin"
        assert resp.json()["auth_type"] == "cookie"

        users_resp = client.get(
            "/api/users?search=admin",
            headers={"AtlasClaw-Authenticate": token},
        )
        assert users_resp.status_code == 200
        admin_users = [
            user
            for user in users_resp.json()["users"]
            if user["username"] == "admin"
        ]
        assert {user["auth_type"] for user in admin_users} == {"local", "cookie"}

        _cleanup_manager(manager)

    def test_update_user_auth_type_returns_400(self, tmp_path):
        """Existing users cannot be moved between auth types."""
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, "admin", "adminpass123")

        users_resp = client.get(
            "/api/users?search=regularuser",
            headers={"AtlasClaw-Authenticate": token},
        )
        assert users_resp.status_code == 200
        regular_user_id = users_resp.json()["users"][0]["id"]

        resp = client.put(
            f"/api/users/{regular_user_id}",
            json={"auth_type": "cookie"},
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 400
        assert "auth type cannot be changed" in resp.json()["detail"].lower()

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
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )
        admin_id = list_resp.json()["users"][0]["id"]

        # Try to delete self
        resp = client.delete(
            f"/api/users/{admin_id}",
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
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
            headers={"AtlasClaw-Authenticate": token},
        )

        assert resp.status_code == 404

        _cleanup_manager(manager)
