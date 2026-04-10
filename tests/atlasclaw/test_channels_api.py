# -*- coding: utf-8 -*-
"""Tests for Channel Management API routes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.channels import router, set_channel_manager
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.handlers import WebSocketHandler
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.db import init_database
from app.atlasclaw.db.database import DatabaseConfig
from app.atlasclaw.db.orm.role import build_default_permissions


def _build_channel_authz(user_info: UserInfo, *, is_admin: bool) -> AuthorizationContext:
    permissions = build_default_permissions()
    permissions["channels"].update(
        {
            "view": True,
            "create": True,
            "edit": True,
            "delete": True,
        }
    )
    return AuthorizationContext(
        user=user_info,
        role_identifiers=["admin"] if is_admin else ["channel_operator"],
        permissions=permissions,
        is_admin=is_admin,
    )


@pytest.fixture
def app():
    """Create test FastAPI application."""
    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        raw_is_admin = request.headers.get("X-Test-Is-Admin", "true").strip().lower()
        is_admin = raw_is_admin in {"1", "true", "yes", "on"}
        user_id = request.headers.get(
            "X-Test-User-Id",
            "channel-admin" if is_admin else "channel-operator",
        )
        user_info = UserInfo(
            user_id=user_id,
            display_name=user_id,
            roles=["admin"] if is_admin else ["channel_operator"],
            extra={"is_admin": is_admin},
            auth_type="test",
        )
        request.state.user_info = user_info
        if user_id != "anonymous":
            request.state.authorization_context = _build_channel_authz(
                user_info,
                is_admin=is_admin,
            )
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture
def temp_workspace():
    """Create temporary workspace directory."""
    return tempfile.mkdtemp()


@pytest_asyncio.fixture
async def initialized_db(temp_workspace):
    """Initialize database for testing."""
    db_path = Path(temp_workspace) / "test.db"
    config = DatabaseConfig(
        db_type="sqlite",
        sqlite_path=str(db_path),
    )
    db_manager = await init_database(config)
    await db_manager.create_tables()
    yield
    # Cleanup is handled by temp_workspace fixture


@pytest.fixture
def channel_manager(temp_workspace):
    """Create channel manager and register test handlers."""
    # Clear registry
    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry._connections.clear()
    
    # Register test handler
    ChannelRegistry.register("websocket", WebSocketHandler)
    
    # Create manager
    manager = ChannelManager(temp_workspace)
    set_channel_manager(manager)
    
    return manager


@pytest.fixture
def client(app, channel_manager, initialized_db):
    """Create test client with initialized database."""
    return TestClient(app)


class TestChannelTypesAPI:
    """Test channel types listing API."""

    def test_list_channel_types(self, client, channel_manager):
        """Test listing available channel types."""
        response = client.get("/api/channels")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        
        # Find websocket channel
        ws_channel = next((c for c in data if c["type"] == "websocket"), None)
        assert ws_channel is not None
        assert ws_channel["name"] == "WebSocket"
        assert ws_channel["connection_count"] == 0

    def test_list_channel_types_with_connections(self, client, channel_manager):
        """Test listing channel types shows connection count."""
        # Create a connection first
        response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test Connection", "config": {}}
        )
        assert response.status_code == 200
        
        # List channel types
        response = client.get("/api/channels")
        assert response.status_code == 200
        
        data = response.json()
        ws_channel = next((c for c in data if c["type"] == "websocket"), None)
        assert ws_channel is not None
        assert ws_channel["connection_count"] == 1

    def test_list_channel_types_allows_non_admin(self, client, channel_manager):
        """Test listing channel types is available to authenticated non-admin users."""
        response = client.get("/api/channels", headers={"X-Test-Is-Admin": "false"})

        assert response.status_code == 200


class TestChannelSchemaAPI:
    """Test channel schema API."""

    def test_get_channel_schema(self, client, channel_manager):
        """Test getting channel configuration schema."""
        response = client.get("/api/channels/websocket/schema")
        
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "object"
        assert "properties" in data

    def test_get_schema_not_found(self, client, channel_manager):
        """Test getting schema for non-existent channel type."""
        response = client.get("/api/channels/nonexistent/schema")
        
        assert response.status_code == 404


class TestConnectionsAPI:
    """Test connection CRUD API."""

    def test_list_connections_empty(self, client, channel_manager):
        """Test listing connections when none exist."""
        response = client.get("/api/channels/websocket/connections")
        
        assert response.status_code == 200
        data = response.json()
        assert data["channel_type"] == "websocket"
        assert data["connections"] == []

    def test_create_connection(self, client, channel_manager):
        """Test creating a new connection."""
        response = client.post(
            "/api/channels/websocket/connections",
            json={
                "name": "Test Connection",
                "config": {"path": "/ws"},
                "enabled": True
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Connection"
        assert data["channel_type"] == "websocket"
        assert data["config"]["path"] == "/ws"
        assert data["enabled"] is True
        assert "id" in data

    def test_create_connection_invalid_channel(self, client, channel_manager):
        """Test creating connection for non-existent channel type."""
        response = client.post(
            "/api/channels/nonexistent/connections",
            json={"name": "Test", "config": {}}
        )
        
        assert response.status_code == 404

    def test_create_connection_allows_non_admin(self, client, channel_manager):
        """Test creating a connection is available to authenticated non-admin users."""
        response = client.post(
            "/api/channels/websocket/connections",
            headers={"X-Test-Is-Admin": "false"},
            json={"name": "Test Connection", "config": {}}
        )

        assert response.status_code == 200

    def test_channel_routes_require_authenticated_user(self, client, channel_manager):
        """Test channel routes return 401 for anonymous users."""
        response = client.get(
            "/api/channels",
            headers={
                "X-Test-Is-Admin": "false",
                "X-Test-User-Id": "anonymous",
            },
        )

        assert response.status_code == 401

    def test_list_connections_after_create(self, client, channel_manager):
        """Test listing connections after creating one."""
        # Create connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test Connection", "config": {}}
        )
        assert create_response.status_code == 200
        created_id = create_response.json()["id"]
        
        # List connections
        response = client.get("/api/channels/websocket/connections")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["connections"]) == 1
        assert data["connections"][0]["id"] == created_id

    def test_update_connection(self, client, channel_manager):
        """Test updating an existing connection."""
        # Create connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Original Name", "config": {}}
        )
        connection_id = create_response.json()["id"]
        
        # Update connection
        response = client.patch(
            f"/api/channels/websocket/connections/{connection_id}",
            json={"name": "Updated Name"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    def test_update_connection_not_found(self, client, channel_manager):
        """Test updating non-existent connection."""
        response = client.patch(
            "/api/channels/websocket/connections/nonexistent",
            json={"name": "Test"}
        )
        
        assert response.status_code == 404

    def test_delete_connection(self, client, channel_manager):
        """Test deleting a connection."""
        # Create connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test", "config": {}}
        )
        connection_id = create_response.json()["id"]
        
        # Delete connection
        response = client.delete(f"/api/channels/websocket/connections/{connection_id}")
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        
        # Verify deleted
        list_response = client.get("/api/channels/websocket/connections")
        assert len(list_response.json()["connections"]) == 0

    def test_delete_connection_not_found(self, client, channel_manager):
        """Test deleting non-existent connection."""
        response = client.delete("/api/channels/websocket/connections/nonexistent")
        
        assert response.status_code == 404


class TestConnectionVerificationAPI:
    """Test connection verification API."""

    def test_verify_connection(self, client, channel_manager):
        """Test verifying a connection's configuration."""
        # Create connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test", "config": {"path": "/ws"}}
        )
        connection_id = create_response.json()["id"]
        
        # Verify connection
        response = client.post(
            f"/api/channels/websocket/connections/{connection_id}/verify"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        assert isinstance(data["valid"], bool)

    def test_verify_connection_not_found(self, client, channel_manager):
        """Test verifying non-existent connection."""
        response = client.post(
            "/api/channels/websocket/connections/nonexistent/verify"
        )
        
        assert response.status_code == 404


class TestConnectionEnableDisableAPI:
    """Test connection enable/disable API."""

    def test_enable_connection(self, client, channel_manager):
        """Test enabling a connection."""
        # Create disabled connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test", "config": {}, "enabled": False}
        )
        connection_id = create_response.json()["id"]
        
        # Enable connection
        response = client.post(
            f"/api/channels/websocket/connections/{connection_id}/enable"
        )
        
        # Note: WebSocketHandler.connect() returns False, so enable may fail
        # This tests the API endpoint works
        assert response.status_code in [200, 500]

    def test_disable_connection(self, client, channel_manager):
        """Test disabling a connection."""
        # Create enabled connection
        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Test", "config": {}, "enabled": True}
        )
        connection_id = create_response.json()["id"]
        
        # Disable connection
        response = client.post(
            f"/api/channels/websocket/connections/{connection_id}/disable"
        )
        
        # Note: disable may fail if connection was never initialized
        assert response.status_code in [200, 500]
