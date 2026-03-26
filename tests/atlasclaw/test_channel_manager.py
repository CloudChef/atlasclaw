# -*- coding: utf-8 -*-
"""Tests for ChannelManager."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.atlasclaw.channels import ChannelConnection, ChannelRegistry
from app.atlasclaw.channels.handlers import WebSocketHandler
from app.atlasclaw.channels.manager import ChannelManager


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
             patch("app.atlasclaw.channels.manager.ChannelConfigService") as mock_service:
            
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
            
            # Note: enable_connection calls initialize_connection which fails
            # because base connect() returns False
            result = await self.manager.enable_connection("user-123", "websocket", "conn-123")
            
            # Result is False because initialization fails
            assert result is False

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
