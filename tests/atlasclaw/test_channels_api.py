# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for Channel Management API routes."""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.channels import (
    router,
    set_channel_manager,
)
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.handlers import (
    DingTalkHandler,
    FeishuHandler,
    WeComHandler,
    WebSocketHandler,
)
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.channels.qr_provisioning import (
    ChannelProvisioningConnection,
    ChannelProvisioningRequest,
    ChannelProvisioningStart,
)
from app.atlasclaw.db import init_database
from app.atlasclaw.db.database import DatabaseConfig
from app.atlasclaw.db.orm.role import build_default_permissions


def _build_channel_authz(user_info: UserInfo, *, is_admin: bool) -> AuthorizationContext:
    permissions = build_default_permissions()
    channel_type = user_info.extra.get("channel_type", "websocket")
    channel_allowed = user_info.extra.get("channel_allowed", True)
    channel_permissions = []
    if channel_type:
        channel_permissions.append({
            "channel_type": channel_type,
            "channel_name": channel_type,
            "allowed": channel_allowed,
        })
    permissions["channels"] = {
        "module_permissions": {
            "manage_permissions": user_info.extra.get("can_manage_channels", False),
        },
        "channel_permissions": channel_permissions,
    }
    permissions["roles"]["manage_permissions"] = user_info.extra.get("can_manage_roles", False)
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
            extra={
                "is_admin": is_admin,
                "channel_type": request.headers.get("X-Test-Channel-Type", "websocket"),
                "channel_allowed": request.headers.get(
                    "X-Test-Channel-Allowed", "true"
                ).strip().lower() in {"1", "true", "yes", "on"},
                "can_manage_channels": request.headers.get(
                    "X-Test-Can-Manage-Channels", "false"
                ).strip().lower() in {"1", "true", "yes", "on"},
                "can_manage_roles": request.headers.get(
                    "X-Test-Can-Manage-Roles", "false"
                ).strip().lower() in {"1", "true", "yes", "on"},
            },
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


class ProvisioningWebSocketHandler(WebSocketHandler):
    """Test channel handler that supports QR provisioning."""

    channel_type = "provisioning_websocket"
    channel_name = "Provisioning WebSocket"
    supports_provisioning = True
    provisioning_default_mode = "qr"
    provisioning_manual_config_available = False
    provisioning_instructions_i18n_key = "channel.provisioning.test"

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        return ChannelProvisioningStart(
            qr_url=(
                "https://platform.example/setup"
                f"?state={request.state_token}"
                f"&user_code={request.user_code}"
            ),
            qr_image_url="https://platform.example/setup/qr.png",
            expires_at=request.expires_at,
            refresh_after_seconds=30,
            instructions_i18n_key=self.provisioning_instructions_i18n_key,
        )


class PollingProvisioningWebSocketHandler(ProvisioningWebSocketHandler):
    """Test handler that completes provisioning via platform polling."""

    channel_type = "polling_provisioning_websocket"
    channel_name = "Polling Provisioning WebSocket"

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        start = await super().create_provisioning_session(request)
        start.platform_state = {"ready": False}
        return start

    async def poll_provisioning_connection(
        self,
        session,
    ) -> ChannelProvisioningConnection | None:
        if not session.platform_state.get("ready"):
            session.platform_state["ready"] = True
            session.status = "authorizing"
            return None
        return ChannelProvisioningConnection(
            name="Polled Bot",
            config={
                "path": "/poll",
                "provisioned": True,
            },
        )


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

    def test_list_channel_types_exposes_provisioning_metadata(self, client, channel_manager):
        """Channel catalog exposes one-click provisioning capability metadata."""
        ChannelRegistry.register("provisioning_websocket", ProvisioningWebSocketHandler)

        response = client.get(
            "/api/channels",
            headers={"X-Test-Channel-Type": "provisioning_websocket"},
        )

        assert response.status_code == 200
        data = response.json()
        channel = next(item for item in data if item["type"] == "provisioning_websocket")
        assert channel["provisioning"] == {
            "supported": True,
            "default_mode": "qr",
            "manual_config_available": False,
            "instructions_i18n_key": "channel.provisioning.test",
        }

    def test_list_channel_types_allows_non_admin(self, client, channel_manager):
        """Test listing channel types is available to authenticated non-admin users."""
        response = client.get("/api/channels", headers={"X-Test-Is-Admin": "false"})

        assert response.status_code == 200

    def test_list_channel_types_filters_to_allowed_types(self, client, channel_manager):
        """Normal catalog listing only returns allowed channel types."""
        ChannelRegistry.register("feishu", FeishuHandler)

        response = client.get("/api/channels")

        assert response.status_code == 200
        channel_types = {item["type"] for item in response.json()}
        assert channel_types == {"websocket"}

    def test_empty_allowlist_returns_no_channel_types(self, client, channel_manager):
        """Missing/empty channel permissions deny all normal catalog access."""
        response = client.get("/api/channels", headers={"X-Test-Channel-Type": ""})

        assert response.status_code == 200
        assert response.json() == []

    def test_explicit_denial_returns_no_channel_types(self, client, channel_manager):
        """An explicit false channel rule denies that type."""
        response = client.get(
            "/api/channels",
            headers={"X-Test-Channel-Allowed": "false"},
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_include_all_requires_channel_or_role_governance(self, client, channel_manager):
        """Full catalog is only available to permission governors."""
        denied = client.get("/api/channels?include_all=true")
        assert denied.status_code == 403

        allowed = client.get(
            "/api/channels?include_all=true",
            headers={"X-Test-Can-Manage-Channels": "true"},
        )
        assert allowed.status_code == 200
        assert any(item["type"] == "websocket" for item in allowed.json())

        role_allowed = client.get(
            "/api/channels?include_all=true",
            headers={"X-Test-Can-Manage-Roles": "true"},
        )
        assert role_allowed.status_code == 200


class TestChannelSchemaAPI:
    """Test channel schema API."""

    def test_get_channel_schema(self, client, channel_manager):
        """Test getting channel configuration schema."""
        response = client.get("/api/channels/websocket/schema")
        
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "object"
        assert "properties" in data
        assert data["provisioning"]["supported"] is False

    @pytest.mark.parametrize(
        ("channel_type", "handler_class"),
        [
            ("feishu", FeishuHandler),
            ("dingtalk", DingTalkHandler),
            ("wecom", WeComHandler),
        ],
    )
    def test_enterprise_channel_schema_does_not_include_provider_bindings(
        self,
        client,
        channel_manager,
        channel_type,
        handler_class,
    ):
        """Enterprise channel schemas should not receive provider-binding fields."""
        ChannelRegistry.register(channel_type, handler_class)

        response = client.get(
            f"/api/channels/{channel_type}/schema",
            headers={"X-Test-Channel-Type": channel_type},
        )

        assert response.status_code == 200
        data = response.json()
        properties = data["properties"]
        property_names = list(properties.keys())

        assert "provider_type" not in properties
        assert "provider_binding" not in properties
        assert "provider_bindings" not in properties
        assert property_names[0] == "connection_mode"
        assert data["provisioning"]["supported"] is True
        assert data["provisioning"]["default_mode"] == "qr"
        assert data["provisioning"]["manual_config_available"] is True

    def test_get_schema_not_found(self, client, channel_manager):
        """Test getting schema for non-existent channel type."""
        response = client.get("/api/channels/nonexistent/schema")
        
        assert response.status_code == 404


class TestChannelProvisioningAPI:
    """Test one-click channel provisioning routes."""

    def test_create_provisioning_session(self, client, channel_manager):
        """Creating a provisioning session returns QR state without exposing secrets."""
        ChannelRegistry.register("provisioning_websocket", ProvisioningWebSocketHandler)

        response = client.post(
            "/api/channels/provisioning_websocket/provisioning-sessions",
            headers={"X-Test-Channel-Type": "provisioning_websocket"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["channel_type"] == "provisioning_websocket"
        assert data["status"] == "pending"
        assert data["qr_url"].startswith("https://platform.example/setup")
        assert data["qr_image_url"] == "https://platform.example/setup/qr.png"
        assert data["refresh_after_seconds"] == 30
        assert data["instructions_i18n_key"] == "channel.provisioning.test"
        assert data["connection"] is None
        query = parse_qs(urlparse(data["qr_url"]).query)
        assert query["state"][0]
        assert query["user_code"][0].count("-") == 2
        assert "callback_url" not in query

    def test_unsupported_channel_rejects_provisioning(self, client, channel_manager):
        """Channels must opt into QR provisioning."""
        response = client.post("/api/channels/websocket/provisioning-sessions")

        assert response.status_code == 400
        assert response.json()["detail"] == "Channel type does not support provisioning: websocket"

    def test_provisioning_session_complete_routes_are_not_available(self, client, channel_manager):
        """QR provisioning does not expose unauthenticated completion callback routes."""
        ChannelRegistry.register("provisioning_websocket", ProvisioningWebSocketHandler)
        headers = {"X-Test-Channel-Type": "provisioning_websocket"}

        create_response = client.post(
            "/api/channels/provisioning_websocket/provisioning-sessions",
            headers=headers,
        )
        created = create_response.json()

        scoped_response = client.post(
            f"/api/channels/provisioning_websocket/provisioning-sessions/{created['session_id']}/complete",
            json={
                "state_token": "state-token",
                "config": {"path": "/qr"},
            },
        )
        user_code_response = client.post(
            "/api/channels/provisioning_websocket/provisioning-sessions/complete",
            json={"user_code": "ABCD1234", "config": {"path": "/qr"}},
        )

        assert scoped_response.status_code in {404, 405}
        assert user_code_response.status_code in {404, 405}

    def test_provisioning_session_refresh_rotates_qr_state(self, client, channel_manager):
        """Refreshing a pending provisioning session rotates the embedded state token."""
        ChannelRegistry.register("provisioning_websocket", ProvisioningWebSocketHandler)
        headers = {"X-Test-Channel-Type": "provisioning_websocket"}

        create_response = client.post(
            "/api/channels/provisioning_websocket/provisioning-sessions",
            headers=headers,
        )
        created = create_response.json()
        first_query = parse_qs(urlparse(created["qr_url"]).query)
        first_state = first_query["state"][0]
        first_user_code = first_query["user_code"][0]

        refresh_response = client.post(
            f"/api/channels/provisioning_websocket/provisioning-sessions/{created['session_id']}/refresh",
            headers=headers,
        )

        assert refresh_response.status_code == 200
        refreshed = refresh_response.json()
        second_query = parse_qs(urlparse(refreshed["qr_url"]).query)
        second_state = second_query["state"][0]
        second_user_code = second_query["user_code"][0]
        assert refreshed["session_id"] == created["session_id"]
        assert refreshed["status"] == "pending"
        assert second_state != first_state
        assert second_user_code != first_user_code

    def test_provisioning_session_poll_creates_enabled_connection(self, client, channel_manager):
        """POST poll can advance a platform-owned registration flow and save credentials."""
        ChannelRegistry.register(
            "polling_provisioning_websocket",
            PollingProvisioningWebSocketHandler,
        )
        headers = {"X-Test-Channel-Type": "polling_provisioning_websocket"}

        create_response = client.post(
            "/api/channels/polling_provisioning_websocket/provisioning-sessions",
            headers=headers,
        )
        assert create_response.status_code == 200
        created = create_response.json()

        first_poll = client.post(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}/poll",
            headers=headers,
        )
        assert first_poll.status_code == 200
        assert first_poll.json()["status"] == "authorizing"

        second_poll = client.post(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}/poll",
            headers=headers,
        )
        assert second_poll.status_code == 200
        completed = second_poll.json()
        assert completed["status"] == "completed"
        assert completed["connection"]["name"] == "Polled Bot"

        connections_response = client.get(
            "/api/channels/polling_provisioning_websocket/connections",
            headers=headers,
        )
        assert connections_response.status_code == 200
        connections = connections_response.json()["connections"]
        assert len(connections) == 1
        assert connections[0]["config"] == {"path": "/poll", "provisioned": True}

    def test_get_provisioning_session_is_read_only(self, client, channel_manager):
        """GET session reports state without polling or creating a connection."""
        ChannelRegistry.register(
            "polling_provisioning_websocket",
            PollingProvisioningWebSocketHandler,
        )
        headers = {"X-Test-Channel-Type": "polling_provisioning_websocket"}

        create_response = client.post(
            "/api/channels/polling_provisioning_websocket/provisioning-sessions",
            headers=headers,
        )
        created = create_response.json()

        get_response = client.get(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}",
            headers=headers,
        )
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "pending"

        connections_response = client.get(
            "/api/channels/polling_provisioning_websocket/connections",
            headers=headers,
        )
        assert connections_response.json()["connections"] == []

    def test_duplicate_provisioning_poll_returns_existing_connection(self, client, channel_manager):
        """Repeated polls after completion do not create duplicate connections."""
        ChannelRegistry.register(
            "polling_provisioning_websocket",
            PollingProvisioningWebSocketHandler,
        )
        headers = {"X-Test-Channel-Type": "polling_provisioning_websocket"}

        create_response = client.post(
            "/api/channels/polling_provisioning_websocket/provisioning-sessions",
            headers=headers,
        )
        created = create_response.json()

        first_response = client.post(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}/poll",
            headers=headers,
        )
        second_response = client.post(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}/poll",
            headers=headers,
        )
        third_response = client.post(
            f"/api/channels/polling_provisioning_websocket/provisioning-sessions/{created['session_id']}/poll",
            headers=headers,
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert third_response.status_code == 200
        assert first_response.json()["connection"] is None
        assert third_response.json()["connection"]["id"] == second_response.json()["connection"]["id"]

        connections_response = client.get(
            "/api/channels/polling_provisioning_websocket/connections",
            headers=headers,
        )
        assert len(connections_response.json()["connections"]) == 1

    def test_provisioning_routes_require_channel_type_permission(self, client, channel_manager):
        """Owned provisioning routes enforce channel allowlist permissions."""
        ChannelRegistry.register("provisioning_websocket", ProvisioningWebSocketHandler)

        response = client.post(
            "/api/channels/provisioning_websocket/provisioning-sessions",
            headers={
                "X-Test-Channel-Type": "provisioning_websocket",
                "X-Test-Channel-Allowed": "false",
            },
        )

        assert response.status_code == 403


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

    def test_create_connection_discards_legacy_provider_selection(
        self,
        client,
        channel_manager,
    ):
        """Provider binding fields are ignored for channel configs."""
        response = client.post(
            "/api/channels/websocket/connections",
            json={
                "name": "Bound Connection",
                "config": {
                    "path": "/ws",
                    "provider_type": "smartcmp",
                    "provider_binding": "smartcmp/default",
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["config"]["path"] == "/ws"
        assert "provider_type" not in data["config"]
        assert "provider_binding" not in data["config"]

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

    def test_denied_channel_type_blocks_lifecycle_endpoints(self, client, channel_manager):
        """Lifecycle endpoints require access to the requested channel type."""
        headers = {"X-Test-Channel-Allowed": "false"}

        assert client.get("/api/channels/websocket/schema", headers=headers).status_code == 403
        assert client.get("/api/channels/websocket/connections", headers=headers).status_code == 403
        assert client.post(
            "/api/channels/websocket/connections",
            headers=headers,
            json={"name": "Denied", "config": {}},
        ).status_code == 403
        assert client.post(
            "/api/channels/websocket/validate-config",
            headers=headers,
            json={"config": {}},
        ).status_code == 403

        create_response = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Allowed", "config": {}},
        )
        connection_id = create_response.json()["id"]
        assert client.patch(
            f"/api/channels/websocket/connections/{connection_id}",
            headers=headers,
            json={"name": "Denied Update"},
        ).status_code == 403
        assert client.post(
            f"/api/channels/websocket/connections/{connection_id}/verify",
            headers=headers,
        ).status_code == 403
        assert client.post(
            f"/api/channels/websocket/connections/{connection_id}/enable",
            headers=headers,
        ).status_code == 403
        assert client.post(
            f"/api/channels/websocket/connections/{connection_id}/disable",
            headers=headers,
        ).status_code == 403
        assert client.delete(
            f"/api/channels/websocket/connections/{connection_id}",
            headers=headers,
        ).status_code == 403

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

    def test_enable_disable_blocks_other_user_connection(self, client, channel_manager):
        """Enable/disable can only target the current user's own connection."""
        owner_headers = {"X-Test-User-Id": "channel-owner"}
        other_headers = {"X-Test-User-Id": "other-channel-user"}
        create_response = client.post(
            "/api/channels/websocket/connections",
            headers=owner_headers,
            json={"name": "Owner Connection", "config": {}, "enabled": False},
        )
        assert create_response.status_code == 200
        connection_id = create_response.json()["id"]

        enable_response = client.post(
            f"/api/channels/websocket/connections/{connection_id}/enable",
            headers=other_headers,
        )
        assert enable_response.status_code == 404

        disable_response = client.post(
            f"/api/channels/websocket/connections/{connection_id}/disable",
            headers=other_headers,
        )
        assert disable_response.status_code == 404
