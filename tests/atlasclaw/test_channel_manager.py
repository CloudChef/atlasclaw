# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for ChannelManager."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.atlasclaw.api.deps_context import APIContext
from app.atlasclaw.api.service_provider_schemas import (
    clear_provider_schema_definitions,
    register_provider_schema_definition,
)
from app.atlasclaw.channels import ChannelConnection, ChannelRegistry
from app.atlasclaw.channels.handlers import WebSocketHandler
from app.atlasclaw.channels.models import ConnectionStatus, InboundMessage, SendResult
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from tests.atlasclaw.provider_schema_fixtures import managed_provider_definition


class TestChannelManager:
    """Test ChannelManager functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.manager = ChannelManager(self.temp_dir)
        
        # Clear registry
        ChannelRegistry._handlers.clear()
        ChannelRegistry._instances.clear()
        ChannelRegistry._connections.clear()
        
        # Register test handler
        ChannelRegistry.register("websocket", WebSocketHandler)

    @pytest.mark.asyncio
    async def test_initialize_connection(self):
        """Test initializing a connection."""
        # Mock the database service
        mock_channel = MagicMock()
        mock_channel.id = "conn-123"
        mock_channel.name = "Test Connection"
        mock_channel.type = "websocket"
        mock_channel.config = {"path": "/ws"}
        mock_channel.is_active = True
        mock_channel.is_default = False
        mock_channel.user_id = "user-123"
        
        with patch("app.atlasclaw.db.get_db_manager") as mock_db_manager, \
             patch("app.atlasclaw.channels.manager.ChannelConfigService") as mock_service:
            
            # Setup async context manager
            mock_session_instance = AsyncMock()
            mock_db_manager.return_value.get_session.return_value.__aenter__.return_value = mock_session_instance
            # get_by_id is an async static method, need to use AsyncMock
            mock_service.get_by_id = AsyncMock(return_value=mock_channel)
            mock_service.to_channel_config.return_value = {
                "id": "conn-123",
                "name": "Test Connection",
                "channel_type": "websocket",
                "config": {"path": "/ws"},
                "enabled": True,
            }
            
            # Initialize connection
            # Note: WebSocketHandler base connect() returns False
            # In production, Feishu/Slack handlers would override connect() to return True
            result = await self.manager.initialize_connection("user-123", "websocket", "conn-123")
            
            # Base WebSocketHandler.connect() returns False, so initialization fails
            # This is expected - real implementations would override connect()
            assert result is False
            assert self.manager.get_connection_runtime_status("conn-123") == "error"

    @pytest.mark.asyncio
    async def test_initialize_connection_not_found(self):
        """Test initializing a non-existent connection."""
        with patch("app.atlasclaw.db.get_db_manager") as mock_db_manager, \
             patch("app.atlasclaw.channels.manager.ChannelConfigService") as mock_service:
            
            mock_session_instance = AsyncMock()
            mock_db_manager.return_value.get_session.return_value.__aenter__.return_value = mock_session_instance
            mock_service.get_by_id = AsyncMock(return_value=None)
            
            result = await self.manager.initialize_connection("user-123", "websocket", "nonexistent")
            
            assert result is False

    @pytest.mark.asyncio
    async def test_stop_connection(self):
        """Test stopping a connection."""
        # Manually add a handler to test stop_connection
        handler = WebSocketHandler({})
        instance_key = "user-123:websocket:conn-123"
        self.manager._active_connections[instance_key] = handler
        
        # Stop connection
        result = await self.manager.stop_connection("user-123", "websocket", "conn-123")
        
        assert result is True
        
        # Check that instance was removed
        assert instance_key not in self.manager._active_connections

    @pytest.mark.asyncio
    async def test_stop_connection_not_active(self):
        """Test stopping a connection that is not active."""
        result = await self.manager.stop_connection("user-123", "websocket", "nonexistent")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_route_inbound_message(self):
        """Test routing inbound message."""
        # Manually create and register handler
        handler = WebSocketHandler({})
        instance_key = "user-123:websocket:conn-123"
        ChannelRegistry.create_instance(instance_key, "websocket", {})
        self.manager._active_connections[instance_key] = handler
        
        # Route message
        request = {
            "message_id": "msg-123",
            "sender_id": "user-456",
            "sender_name": "Test User",
            "chat_id": "chat-789",
            "content": "Hello",
        }
        
        inbound = await self.manager.route_inbound_message("websocket", "conn-123", request)
        
        assert inbound is not None
        assert inbound.message_id == "msg-123"
        assert inbound.content == "Hello"

    @pytest.mark.asyncio
    async def test_route_inbound_message_no_handler(self):
        """Test routing when handler not found."""
        inbound = await self.manager.route_inbound_message("websocket", "nonexistent", {})
        
        assert inbound is None

    def test_get_user_connections(self):
        """Test getting user connections (sync version)."""
        # Manually add handlers to active connections
        handler = WebSocketHandler({})
        self.manager._active_connections["user-123:websocket:conn-1"] = handler
        self.manager._active_connections["user-123:websocket:conn-2"] = handler
        
        # Get connections
        connections = self.manager.get_user_connections("user-123")
        
        assert len(connections) == 2

    def test_get_user_connections_with_filter(self):
        """Test getting user connections with channel type filter."""
        handler = WebSocketHandler({})
        self.manager._active_connections["user-123:websocket:conn-1"] = handler
        
        connections = self.manager.get_user_connections("user-123", "websocket")
        
        assert len(connections) == 1
        assert connections[0]["channel_type"] == "websocket"

    @pytest.mark.asyncio
    async def test_probe_connection_reports_health_and_status(self):
        handler = WebSocketHandler({})
        handler._status = ConnectionStatus.CONNECTED
        handler.health_check = AsyncMock(return_value=True)
        instance_key = "user-123:websocket:conn-123"
        self.manager._active_connections[instance_key] = handler

        result = await self.manager.probe_connection("user-123", "websocket", "conn-123")

        assert result["healthy"] is True
        assert result["status"] == "connected"

    @pytest.mark.asyncio
    async def test_reconnect_connection_delegates_to_handler(self):
        handler = WebSocketHandler({})
        handler._status = ConnectionStatus.ERROR
        handler.reconnect = AsyncMock(return_value=True)
        instance_key = "user-123:websocket:conn-123"
        self.manager._active_connections[instance_key] = handler

        result = await self.manager.reconnect_connection("user-123", "websocket", "conn-123")

        assert result is True
        handler.reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_background_initialize_retries_transient_failure(self):
        """Background startup should retry transient connection failures."""
        with patch.object(
            self.manager,
            "initialize_connection",
            AsyncMock(side_effect=[False, True]),
        ) as mock_initialize, patch(
            "app.atlasclaw.channels.manager.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            await self.manager._background_initialize("user-123", "websocket", "conn-123")

        assert mock_initialize.await_count == 2
        mock_sleep.assert_awaited_once_with(2.0)
        assert self.manager.get_connection_runtime_status("conn-123") == "connecting"

    def test_list_active_connection_descriptors(self):
        handler = WebSocketHandler({})
        self.manager._active_connections["user-123:websocket:conn-1"] = handler
        self.manager._active_connections["user-123:websocket:conn-2"] = handler

        items = self.manager.list_active_connection_descriptors()

        assert len(items) == 2
        assert items[0]["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_enable_connection(self):
        """Test enabling a connection."""
        mock_channel = MagicMock()
        mock_channel.id = "conn-123"
        mock_channel.user_id = "user-123"
        mock_channel.type = "websocket"
        mock_channel.name = "Test"
        mock_channel.config = {}
        mock_channel.is_active = True
        mock_channel.is_default = False
        
        with patch("app.atlasclaw.db.get_db_manager") as mock_db_manager, \
             patch("app.atlasclaw.channels.manager.ChannelConfigService") as mock_service, \
             patch("app.atlasclaw.channels.manager.asyncio.create_task") as mock_create_task:
            
            mock_session_instance = AsyncMock()
            mock_db_manager.return_value.get_session.return_value.__aenter__.return_value = mock_session_instance
            # update_status is an async static method, need to use AsyncMock
            mock_service.update_status = AsyncMock(return_value=mock_channel)
            mock_service.get_by_id = AsyncMock(return_value=mock_channel)
            mock_service.to_channel_config.return_value = {
                "id": "conn-123",
                "name": "Test",
                "channel_type": "websocket",
                "config": {},
                "enabled": True,
            }
            scheduled_coroutines = []

            def _capture_task(coroutine):
                scheduled_coroutines.append(coroutine)
                coroutine.close()
                return MagicMock()

            mock_create_task.side_effect = _capture_task
            
            # enable_connection now returns once DB state flips and
            # background initialization has been scheduled.
            result = await self.manager.enable_connection("user-123", "websocket", "conn-123")
            
            assert result is True
            mock_create_task.assert_called_once()
            assert len(scheduled_coroutines) == 1
            assert self.manager.get_connection_runtime_status("conn-123") == "connecting"

    @pytest.mark.asyncio
    async def test_disable_connection(self):
        """Test disabling a connection."""
        # Manually add handler since initialize_connection fails
        handler = WebSocketHandler({})
        instance_key = "user-123:websocket:conn-123"
        self.manager._active_connections[instance_key] = handler
        
        mock_channel = MagicMock()
        
        with patch("app.atlasclaw.db.get_db_manager") as mock_db_manager, \
             patch("app.atlasclaw.channels.manager.ChannelConfigService") as mock_service:
            
            mock_session_instance = AsyncMock()
            mock_db_manager.return_value.get_session.return_value.__aenter__.return_value = mock_session_instance
            # update_status is an async static method, need to use AsyncMock
            mock_service.update_status = AsyncMock(return_value=mock_channel)
            
            # Disable
            result = await self.manager.disable_connection("user-123", "websocket", "conn-123")
            
            assert result is True
            assert self.manager.get_connection_runtime_status("conn-123") == "disconnected"

    def test_get_connection_runtime_status_uses_cached_state_when_handler_missing(self):
        self.manager._set_connection_runtime_status("conn-123", ConnectionStatus.CONNECTING)

        assert self.manager.get_connection_runtime_status("conn-123") == "connecting"

    def test_build_channel_session_key_uses_sender_for_direct_messages(self):
        message = InboundMessage(
            message_id="msg-1",
            sender_id="ext-user-1",
            sender_name="External User",
            chat_id="dm-chat-1",
            channel_type="feishu",
            content="hello",
            metadata={"chat_type": "p2p"},
        )

        session_key = self.manager._build_channel_session_key(
            owner_user_id="owner-1",
            channel_type="feishu",
            connection_id="conn-1",
            message=message,
        )

        assert session_key == "agent:main:user:owner-1:feishu:conn-1:dm:ext-user-1"

    def test_build_channel_session_key_shares_group_session_by_chat_id(self):
        first = InboundMessage(
            message_id="msg-1",
            sender_id="ext-user-1",
            sender_name="User 1",
            chat_id="group-42",
            channel_type="dingtalk",
            content="hello",
            metadata={"conversation_type": "2"},
        )
        second = InboundMessage(
            message_id="msg-2",
            sender_id="ext-user-2",
            sender_name="User 2",
            chat_id="group-42",
            channel_type="dingtalk",
            content="world",
            metadata={"conversation_type": "2"},
        )

        first_key = self.manager._build_channel_session_key(
            owner_user_id="owner-1",
            channel_type="dingtalk",
            connection_id="conn-1",
            message=first,
        )
        second_key = self.manager._build_channel_session_key(
            owner_user_id="owner-1",
            channel_type="dingtalk",
            connection_id="conn-1",
            message=second,
        )

        assert first_key == second_key
        assert first_key == "agent:main:user:owner-1:dingtalk:conn-1:group:group-42"

    @pytest.mark.asyncio
    async def test_process_message_async_resolves_provider_tokens_for_connection_owner(self):
        """Channel turns should resolve provider tokens from the connection owner settings."""
        clear_provider_schema_definitions()
        register_provider_schema_definition(
            managed_provider_definition(
                provider_type="smartcmp",
                display_name="SmartCMP",
                default_base_url="https://cmp.example.test",
            )
        )
        try:
            workspace_path = Path(self.temp_dir)
            user_settings_dir = workspace_path / "users" / "user-123"
            user_settings_dir.mkdir(parents=True, exist_ok=True)
            (user_settings_dir / "user_setting.json").write_text(
                json.dumps(
                    {
                        "providers": {
                            "smartcmp": {
                                "cmp": {
                                    "configured": True,
                                    "config": {"user_token": "owner-user-token"},
                                }
                            }
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            ctx = APIContext(
                session_manager=SessionManager(str(workspace_path)),
                session_queue=SessionQueue(),
                skill_registry=SkillRegistry(),
                provider_instances={
                    "smartcmp": {
                        "cmp": {
                            "base_url": "https://cmp.example.test",
                            "auth_type": "user_token",
                        }
                    }
                },
            )
            manager = ChannelManager(self.temp_dir)
            manager.set_session_manager_router(ctx.session_manager_router)
            handler = WebSocketHandler({})
            handler.send_message = AsyncMock(return_value=SendResult(success=True))
            manager._active_connections["user-123:websocket:conn-123"] = handler

            captured = {}

            class DummyAgentRunner:
                async def run(self, **kwargs):
                    captured.update(kwargs)
                    yield SimpleNamespace(type="assistant", content="ok")

            manager._agent_runner = DummyAgentRunner()

            message = InboundMessage(
                message_id="msg-123",
                sender_id="ext-user-1",
                sender_name="External User",
                chat_id="dm-chat-1",
                channel_type="websocket",
                content="hello",
                metadata={"chat_type": "p2p"},
            )

            with patch("app.atlasclaw.api.deps_context.get_api_context", return_value=ctx):
                await manager._process_message_async(
                    "user-123",
                    "websocket",
                    "conn-123",
                    message,
                )

            deps = captured["deps"]
            provider_config = deps.extra["provider_config"]["smartcmp"]["cmp"]
            assert provider_config["user_token"] == "owner-user-token"
            assert deps.extra["provider_instances"]["smartcmp"]["cmp"] == provider_config
            assert deps.extra["channel_connection_id"] == "conn-123"
            assert deps.extra["external_sender_id"] == "ext-user-1"
            assert deps.extra["external_chat_id"] == "dm-chat-1"
            assert deps.extra["external_chat_type"] == "dm"
            assert deps.peer_id == "ext-user-1"
            assert deps.channel == "websocket"
        finally:
            clear_provider_schema_definitions()

    @pytest.mark.asyncio
    async def test_process_message_async_ignores_legacy_provider_binding(self):
        """Channel turns should not receive legacy provider binding runtime data."""
        workspace_path = Path(self.temp_dir)
        ctx = APIContext(
            session_manager=SessionManager(str(workspace_path)),
            session_queue=SessionQueue(),
            skill_registry=SkillRegistry(),
            provider_instances={},
        )
        manager = ChannelManager(self.temp_dir)
        manager.set_session_manager_router(ctx.session_manager_router)
        handler = WebSocketHandler({"provider_binding": "smartcmp/default"})
        handler.send_message = AsyncMock(return_value=SendResult(success=True))
        manager._active_connections["user-123:websocket:conn-123"] = handler

        captured = {}

        class DummyAgentRunner:
            async def run(self, **kwargs):
                captured.update(kwargs)
                yield SimpleNamespace(type="assistant", content="ok")

        manager._agent_runner = DummyAgentRunner()

        message = InboundMessage(
            message_id="msg-123",
            sender_id="ext-user-1",
            sender_name="External User",
            chat_id="dm-chat-1",
            channel_type="websocket",
            content="hello",
            metadata={"chat_type": "p2p"},
        )

        with patch("app.atlasclaw.api.deps_context.get_api_context", return_value=ctx):
            await manager._process_message_async("user-123", "websocket", "conn-123", message)

        deps = captured["deps"]
        assert "provider_type" not in deps.extra
        assert "provider_instance_name" not in deps.extra
        assert "provider_instance" not in deps.extra
        assert deps.extra["available_providers"] == {}
        assert deps.extra["provider_instances"] == {}
