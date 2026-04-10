# -*- coding: utf-8 -*-
"""Permission enforcement tests for workspace management configuration APIs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.channels import router as channels_router
from app.atlasclaw.api.channels import set_channel_manager
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.middleware import setup_auth_middleware
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.handlers import WebSocketHandler
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.db import get_db_session
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


_test_db_manager: DatabaseManager = None


async def _test_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    global _test_db_manager
    async with _test_db_manager.get_session() as session:
        yield session


def _get_auth_config() -> AuthConfig:
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


def _build_client(tmp_path: Path, auth_config: AuthConfig) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry._connections.clear()
    ChannelRegistry.register("websocket", WebSocketHandler)
    set_channel_manager(ChannelManager(tmp_path))

    app = FastAPI()
    app.state.config = SimpleNamespace(auth=auth_config)
    setup_auth_middleware(app, auth_config)
    app.include_router(create_router())
    app.include_router(api_router)
    app.include_router(channels_router)
    app.dependency_overrides[get_db_session] = _test_get_db_session
    return TestClient(app)


def _init_database_sync(tmp_path: Path):
    global _test_db_manager

    async def _init():
        global _test_db_manager
        db_path = tmp_path / "test_management_permissions.db"
        _test_db_manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        )
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
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
    asyncio.run(manager.close())


def _login_as(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/local/login", json={"username": username, "password": password})
    assert response.status_code == 200, f"Login failed: {response.json()}"
    return response.json()["token"]


class TestManagementConfigPermissionsAPI:
    """Permission tests for token/provider/model/channel management APIs."""

    def test_agent_permissions_gate_agent_config_crud(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_headers = {"AtlasClaw-Authenticate": _login_as(client, "admin", "adminpass123")}

        create_user_resp = client.post(
            "/api/users",
            json={
                "username": "agentmanager",
                "password": "agentpass123",
                "display_name": "Agent Manager",
                "email": "agentmanager@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_user_resp.status_code == 201
        agent_manager_id = create_user_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Agent Manager",
                "identifier": "agent_manager",
                "description": "Manages reusable agent templates.",
                "permissions": {
                    "agent_configs": {
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": True,
                    },
                    "users": {
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{agent_manager_id}",
            json={"roles": {"agent_manager": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        agent_headers = {"AtlasClaw-Authenticate": _login_as(client, "agentmanager", "agentpass123")}

        create_agent_resp = client.post(
            "/api/agent-configs",
            json={
                "name": "enterprise-assistant",
                "display_name": "Enterprise Assistant",
                "identity": {"role": "assistant"},
                "user": {"tone": "helpful"},
                "soul": {"values": ["reliable"]},
                "memory": {"enabled": True},
                "is_active": True,
            },
            headers=agent_headers,
        )
        assert create_agent_resp.status_code == 201
        agent_id = create_agent_resp.json()["id"]

        list_agent_resp = client.get("/api/agent-configs?page=1&page_size=20", headers=agent_headers)
        assert list_agent_resp.status_code == 200
        assert list_agent_resp.json()["total"] == 1

        update_agent_resp = client.put(
            f"/api/agent-configs/{agent_id}",
            json={"display_name": "Enterprise Assistant v2"},
            headers=agent_headers,
        )
        assert update_agent_resp.status_code == 200
        assert update_agent_resp.json()["display_name"] == "Enterprise Assistant v2"

        delete_agent_resp = client.delete(f"/api/agent-configs/{agent_id}", headers=agent_headers)
        assert delete_agent_resp.status_code == 204

        regular_headers = {"AtlasClaw-Authenticate": _login_as(client, "regularuser", "userpass123")}
        blocked_agent_resp = client.get("/api/agent-configs", headers=regular_headers)
        assert blocked_agent_resp.status_code == 403
        assert "agent_configs.view" in blocked_agent_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_token_permissions_gate_token_config_crud(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_headers = {"AtlasClaw-Authenticate": _login_as(client, "admin", "adminpass123")}

        create_user_resp = client.post(
            "/api/users",
            json={
                "username": "tokenmanager",
                "password": "tokenpass123",
                "display_name": "Token Manager",
                "email": "tokenmanager@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_user_resp.status_code == 201
        token_manager_id = create_user_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Token Manager",
                "identifier": "token_manager",
                "description": "Manages token configuration records.",
                "permissions": {
                    "tokens": {
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": True,
                    },
                    "users": {
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{token_manager_id}",
            json={"roles": {"token_manager": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        token_manager_headers = {"AtlasClaw-Authenticate": _login_as(client, "tokenmanager", "tokenpass123")}

        create_token_resp = client.post(
            "/api/token-configs",
            json={
                "name": "ops-token",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-token-value",
                "is_active": True,
            },
            headers=token_manager_headers,
        )
        assert create_token_resp.status_code == 201
        token_id = create_token_resp.json()["id"]

        list_token_resp = client.get("/api/token-configs?page=1&page_size=20", headers=token_manager_headers)
        assert list_token_resp.status_code == 200
        assert list_token_resp.json()["total"] == 1

        update_token_resp = client.put(
            f"/api/token-configs/{token_id}",
            json={"weight": 200},
            headers=token_manager_headers,
        )
        assert update_token_resp.status_code == 200
        assert update_token_resp.json()["weight"] == 200

        delete_token_resp = client.delete(f"/api/token-configs/{token_id}", headers=token_manager_headers)
        assert delete_token_resp.status_code == 204

        regular_headers = {"AtlasClaw-Authenticate": _login_as(client, "regularuser", "userpass123")}
        blocked_list_resp = client.get("/api/token-configs", headers=regular_headers)
        assert blocked_list_resp.status_code == 403
        assert "tokens.view" in blocked_list_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_provider_and_model_permissions_gate_catalog_crud(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_headers = {"AtlasClaw-Authenticate": _login_as(client, "admin", "adminpass123")}

        create_user_resp = client.post(
            "/api/users",
            json={
                "username": "catalogmanager",
                "password": "catalogpass123",
                "display_name": "Catalog Manager",
                "email": "catalogmanager@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_user_resp.status_code == 201
        catalog_manager_id = create_user_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Catalog Manager",
                "identifier": "catalog_manager",
                "description": "Manages provider and model catalogs.",
                "permissions": {
                    "provider_configs": {
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": True,
                    },
                    "model_configs": {
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": True,
                    },
                    "users": {
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{catalog_manager_id}",
            json={"roles": {"catalog_manager": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        catalog_headers = {"AtlasClaw-Authenticate": _login_as(client, "catalogmanager", "catalogpass123")}

        create_provider_resp = client.post(
            "/api/provider-configs",
            json={
                "provider_type": "demo",
                "instance_name": "demo-prod",
                "config": {"endpoint": "https://example.test"},
                "is_active": True,
            },
            headers=catalog_headers,
        )
        assert create_provider_resp.status_code == 201
        provider_id = create_provider_resp.json()["id"]

        list_provider_resp = client.get("/api/provider-configs?page=1&page_size=20", headers=catalog_headers)
        assert list_provider_resp.status_code == 200
        assert list_provider_resp.json()["total"] == 1

        update_provider_resp = client.put(
            f"/api/provider-configs/{provider_id}",
            json={"is_active": False},
            headers=catalog_headers,
        )
        assert update_provider_resp.status_code == 200
        assert update_provider_resp.json()["is_active"] is False

        create_model_resp = client.post(
            "/api/model-configs",
            json={
                "name": "demo-model",
                "display_name": "Demo Model",
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "is_active": True,
            },
            headers=catalog_headers,
        )
        assert create_model_resp.status_code == 201
        model_id = create_model_resp.json()["id"]

        list_model_resp = client.get("/api/model-configs?page=1&page_size=20", headers=catalog_headers)
        assert list_model_resp.status_code == 200
        assert list_model_resp.json()["total"] == 1

        update_model_resp = client.put(
            f"/api/model-configs/{model_id}",
            json={"temperature": 0.3},
            headers=catalog_headers,
        )
        assert update_model_resp.status_code == 200
        assert update_model_resp.json()["temperature"] == 0.3

        delete_model_resp = client.delete(f"/api/model-configs/{model_id}", headers=catalog_headers)
        assert delete_model_resp.status_code == 204

        delete_provider_resp = client.delete(f"/api/provider-configs/{provider_id}", headers=catalog_headers)
        assert delete_provider_resp.status_code == 204

        regular_headers = {"AtlasClaw-Authenticate": _login_as(client, "regularuser", "userpass123")}
        blocked_provider_resp = client.get("/api/provider-configs", headers=regular_headers)
        assert blocked_provider_resp.status_code == 403
        assert "provider_configs.view" in blocked_provider_resp.json()["detail"].lower()

        blocked_model_resp = client.get("/api/model-configs", headers=regular_headers)
        assert blocked_model_resp.status_code == 403
        assert "model_configs.view" in blocked_model_resp.json()["detail"].lower()

        _cleanup_manager(manager)

    def test_channel_permissions_gate_connection_lifecycle(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_headers = {"AtlasClaw-Authenticate": _login_as(client, "admin", "adminpass123")}

        create_user_resp = client.post(
            "/api/users",
            json={
                "username": "channelmanager",
                "password": "channelpass123",
                "display_name": "Channel Manager",
                "email": "channelmanager@test.com",
                "roles": {},
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_user_resp.status_code == 201
        channel_manager_id = create_user_resp.json()["id"]

        create_role_resp = client.post(
            "/api/roles",
            json={
                "name": "Channel Manager",
                "identifier": "channel_manager",
                "description": "Manages channel connections.",
                "permissions": {
                    "channels": {
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": True,
                    },
                    "users": {
                        "assign_roles": True,
                    },
                },
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{channel_manager_id}",
            json={"roles": {"channel_manager": True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        channel_headers = {"AtlasClaw-Authenticate": _login_as(client, "channelmanager", "channelpass123")}

        list_types_resp = client.get("/api/channels", headers=channel_headers)
        assert list_types_resp.status_code == 200
        assert any(item["type"] == "websocket" for item in list_types_resp.json())

        create_connection_resp = client.post(
            "/api/channels/websocket/connections",
            json={"name": "Ops Channel", "config": {"path": "/ops"}, "enabled": True},
            headers=channel_headers,
        )
        assert create_connection_resp.status_code == 200
        connection_id = create_connection_resp.json()["id"]

        list_connections_resp = client.get("/api/channels/websocket/connections", headers=channel_headers)
        assert list_connections_resp.status_code == 200
        assert len(list_connections_resp.json()["connections"]) == 1

        update_connection_resp = client.patch(
            f"/api/channels/websocket/connections/{connection_id}",
            json={"name": "Ops Channel Updated"},
            headers=channel_headers,
        )
        assert update_connection_resp.status_code == 200
        assert update_connection_resp.json()["name"] == "Ops Channel Updated"

        verify_connection_resp = client.post(
            f"/api/channels/websocket/connections/{connection_id}/verify",
            headers=channel_headers,
        )
        assert verify_connection_resp.status_code == 200

        delete_connection_resp = client.delete(
            f"/api/channels/websocket/connections/{connection_id}",
            headers=channel_headers,
        )
        assert delete_connection_resp.status_code == 200

        regular_headers = {"AtlasClaw-Authenticate": _login_as(client, "regularuser", "userpass123")}
        blocked_channel_resp = client.get("/api/channels", headers=regular_headers)
        assert blocked_channel_resp.status_code == 403
        assert "channels.view" in blocked_channel_resp.json()["detail"].lower()

        _cleanup_manager(manager)
