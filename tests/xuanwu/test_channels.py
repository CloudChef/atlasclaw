# -*- coding: utf-8 -*-
"""Tests for channel registry and handlers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.xuanwu.channels import (
    ChannelConnection,
    ChannelMode,
    ChannelRegistry,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)
from app.xuanwu.channels.handler import ChannelHandler
from app.xuanwu.channels.handlers import RESTHandler, SSEHandler, WebSocketHandler


class TestChannelModels:
    """Test channel data models."""

    def test_channel_mode_enum(self):
        """Test ChannelMode enum values."""
        assert ChannelMode.INBOUND.value == "inbound"
        assert ChannelMode.OUTBOUND.value == "outbound"
        assert ChannelMode.BIDIRECTIONAL.value == "bidirectional"

    def test_connection_status_enum(self):
        """Test ConnectionStatus enum."""
        assert ConnectionStatus.DISCONNECTED.name == "DISCONNECTED"
        assert ConnectionStatus.CONNECTED.name == "CONNECTED"

    def test_inbound_message_creation(self):
        """Test InboundMessage dataclass."""
        msg = InboundMessage(
            message_id="msg-123",
            sender_id="user-456",
            sender_name="Test User",
            chat_id="chat-789",
            channel_type="test",
            content="Hello",
        )
        assert msg.message_id == "msg-123"
        assert msg.content == "Hello"
        assert msg.content_type == "text"  # default value

    def test_outbound_message_creation(self):
        """Test OutboundMessage dataclass."""
        msg = OutboundMessage(
            chat_id="chat-789",
            content="Reply",
            content_type="markdown",
        )
        assert msg.chat_id == "chat-789"
        assert msg.content == "Reply"
        assert msg.content_type == "markdown"

    def test_send_result(self):
        """Test SendResult dataclass."""
        result = SendResult(success=True, message_id="msg-123")
        assert result.success is True
        assert result.message_id == "msg-123"

    def test_channel_connection(self):
        """Test ChannelConnection dataclass."""
        conn = ChannelConnection(
            id="conn-123",
            name="Test Connection",
            channel_type="websocket",
            config={"host": "localhost"},
            enabled=True,
        )
        assert conn.id == "conn-123"
        assert conn.channel_type == "websocket"
        assert conn.config["host"] == "localhost"


class TestChannelRegistry:
    """Test ChannelRegistry functionality."""

    def setup_method(self):
        """Clear registry before each test."""
        ChannelRegistry._handlers.clear()
        ChannelRegistry._instances.clear()
        ChannelRegistry._connections.clear()

    def test_register_handler(self):
        """Test registering a channel handler."""
        ChannelRegistry.register("websocket", WebSocketHandler)
        
        assert "websocket" in ChannelRegistry._handlers
        assert ChannelRegistry._handlers["websocket"] == WebSocketHandler

    def test_get_handler(self):
        """Test getting a registered handler."""
        ChannelRegistry.register("websocket", WebSocketHandler)
        
        handler_class = ChannelRegistry.get("websocket")
        assert handler_class == WebSocketHandler

    def test_get_nonexistent_handler(self):
        """Test getting a non-existent handler."""
        handler_class = ChannelRegistry.get("nonexistent")
        assert handler_class is None

    def test_list_channels(self):
        """Test listing registered channels."""
        ChannelRegistry.register("websocket", WebSocketHandler)
        ChannelRegistry.register("sse", SSEHandler)
        
        channels = ChannelRegistry.list_channels()
        assert len(channels) == 2
        
        types = [c["type"] for c in channels]
        assert "websocket" in types
        assert "sse" in types

    def test_create_instance(self):
        """Test creating handler instance."""
        ChannelRegistry.register("websocket", WebSocketHandler)
        
        instance = ChannelRegistry.create_instance(
            "instance-1",
            "websocket",
            {"path": "/ws"}
        )
        
        assert instance is not None
        assert isinstance(instance, WebSocketHandler)
        assert instance.config["path"] == "/ws"

    def test_get_instance(self):
        """Test getting cached instance."""
        ChannelRegistry.register("websocket", WebSocketHandler)
        ChannelRegistry.create_instance("instance-1", "websocket", {})
        
        instance = ChannelRegistry.get_instance("instance-1")
        assert instance is not None
        assert isinstance(instance, WebSocketHandler)

    def test_register_connection(self):
        """Test registering a channel connection."""
        conn = ChannelConnection(
            id="conn-123",
            name="Test",
            channel_type="websocket",
        )
        
        ChannelRegistry.register_connection(conn)
        
        retrieved = ChannelRegistry.get_connection("conn-123")
        assert retrieved == conn


class TestWebSocketHandler:
    """Test WebSocketHandler functionality."""

    def test_long_connection_support(self):
        """Test that WebSocketHandler supports long connection."""
        assert WebSocketHandler.supports_long_connection is True
        assert WebSocketHandler.supports_webhook is False

    @pytest.mark.asyncio
    async def test_setup(self):
        """Test handler setup."""
        handler = WebSocketHandler()
        result = await handler.setup({"path": "/ws"})
        
        assert result is True
        assert handler.config["path"] == "/ws"

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = WebSocketHandler()
        
        start_result = await handler.start(None)
        assert start_result is True
        assert handler.get_status() == ConnectionStatus.CONNECTED
        
        stop_result = await handler.stop()
        assert stop_result is True
        assert handler.get_status() == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_handle_inbound_json(self):
        """Test handling inbound JSON message."""
        handler = WebSocketHandler()
        
        json_data = json.dumps({
            "message_id": "msg-123",
            "sender_id": "user-456",
            "sender_name": "Test User",
            "chat_id": "chat-789",
            "content": "Hello",
        })
        
        inbound = await handler.handle_inbound(json_data)
        
        assert inbound is not None
        assert inbound.message_id == "msg-123"
        assert inbound.content == "Hello"
        assert inbound.channel_type == "websocket"

    @pytest.mark.asyncio
    async def test_handle_inbound_dict(self):
        """Test handling inbound dict message."""
        handler = WebSocketHandler()
        
        data = {
            "message_id": "msg-123",
            "sender_id": "user-456",
            "sender_name": "Test User",
            "chat_id": "chat-789",
            "content": "Hello",
        }
        
        inbound = await handler.handle_inbound(data)
        
        assert inbound is not None
        assert inbound.message_id == "msg-123"

    @pytest.mark.asyncio
    async def test_validate_config(self):
        """Test configuration validation."""
        handler = WebSocketHandler()
        
        result = await handler.validate_config({"path": "/ws"})
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    def test_describe_schema(self):
        """Test schema description."""
        handler = WebSocketHandler()
        
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert WebSocketHandler.channel_type == "websocket"
        assert WebSocketHandler.channel_name == "WebSocket"
        assert WebSocketHandler.channel_mode == ChannelMode.BIDIRECTIONAL

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        """Test long connection methods."""
        handler = WebSocketHandler()
        
        # WebSocketHandler supports long connection
        assert handler.supports_long_connection is True
        
        # Base implementation returns False (subclasses should override)
        # For WebSocketHandler, connect() is called in start()
        result = await handler.connect()
        # Base class returns False, subclasses should return True
        assert result is False  # Base implementation
        
        # disconnect() should return True (base implementation)
        result = await handler.disconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_reconnect(self):
        """Test reconnect method."""
        handler = WebSocketHandler()
        
        # reconnect() calls disconnect() then connect()
        result = await handler.reconnect()
        # Base implementation returns False after connect() fails
        assert result is False

    def test_message_callback(self):
        """Test message callback functionality."""
        handler = WebSocketHandler()
        
        messages = []
        def callback(msg):
            messages.append(msg)
        
        handler.set_message_callback(callback)
        
        # Simulate message received
        from app.xuanwu.channels.models import InboundMessage
        msg = InboundMessage(
            message_id="test-123",
            sender_id="user-456",
            sender_name="Test",
            chat_id="chat-789",
            channel_type="websocket",
            content="Hello"
        )
        handler._on_message_received(msg)
        
        assert len(messages) == 1
        assert messages[0].message_id == "test-123"


class TestSSEHandler:
    """Test SSEHandler functionality."""

    def test_long_connection_support(self):
        """Test that SSEHandler supports long connection."""
        assert SSEHandler.supports_long_connection is True
        assert SSEHandler.supports_webhook is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = SSEHandler()
        
        start_result = await handler.start(None)
        assert start_result is True
        
        stop_result = await handler.stop()
        assert stop_result is True

    @pytest.mark.asyncio
    async def test_handle_inbound_returns_none(self):
        """Test that SSE handler returns None for inbound (outbound-only)."""
        handler = SSEHandler()
        
        result = await handler.handle_inbound({})
        
        assert result is None

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert SSEHandler.channel_type == "sse"
        assert SSEHandler.channel_mode == ChannelMode.OUTBOUND


class TestRESTHandler:
    """Test RESTHandler functionality."""

    def test_webhook_support(self):
        """Test that RESTHandler supports webhook mode."""
        assert RESTHandler.supports_long_connection is False
        assert RESTHandler.supports_webhook is True

    @pytest.mark.asyncio
    async def test_setup(self):
        """Test handler setup."""
        handler = RESTHandler()
        result = await handler.setup({"webhook_url": "http://example.com/webhook"})
        
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_config(self):
        """Test configuration validation."""
        handler = RESTHandler()
        
        result = await handler.validate_config({"webhook_url": "http://example.com"})
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert RESTHandler.channel_type == "rest"
        assert RESTHandler.channel_mode == ChannelMode.BIDIRECTIONAL
