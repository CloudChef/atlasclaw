# -*- coding: utf-8 -*-
"""Tests for Feishu channel handler."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Dict, Any

from app.atlasclaw.channels.handlers.feishu import FeishuHandler
from app.atlasclaw.channels.models import (
    ChannelMode,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)


class TestFeishuHandler:
    """Tests for FeishuHandler class."""

    def test_handler_class_attributes(self):
        """Test handler class has correct attributes."""
        assert FeishuHandler.channel_type == "feishu"
        assert FeishuHandler.channel_name == "Feishu"
        assert FeishuHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert FeishuHandler.supports_long_connection is True
        assert FeishuHandler.supports_webhook is False

    def test_handler_init(self):
        """Test handler initialization."""
        handler = FeishuHandler()
        assert handler.config == {}
        assert handler._status == ConnectionStatus.DISCONNECTED
        assert handler._access_token is None

    @pytest.mark.asyncio
    async def test_setup_with_valid_config(self):
        """Test setup with valid app_id and app_secret."""
        handler = FeishuHandler()
        config = {
            "app_id": "test_app_id",
            "app_secret": "test_app_secret",
        }
        result = await handler.setup(config)
        assert result is True
        assert handler.config["app_id"] == "test_app_id"
        assert handler.config["app_secret"] == "test_app_secret"

    @pytest.mark.asyncio
    async def test_setup_missing_app_id(self):
        """Test setup fails when app_id is missing."""
        handler = FeishuHandler()
        config = {
            "app_secret": "test_app_secret",
        }
        result = await handler.setup(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_setup_missing_app_secret(self):
        """Test setup fails when app_secret is missing."""
        handler = FeishuHandler()
        config = {
            "app_id": "test_app_id",
        }
        result = await handler.setup(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_config_valid(self):
        """Test config validation with valid config."""
        handler = FeishuHandler()
        config = {
            "app_id": "test_app_id",
            "app_secret": "test_app_secret",
        }
        result = await handler.validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_validate_config_missing_app_id(self):
        """Test config validation fails when app_id missing."""
        handler = FeishuHandler()
        config = {
            "app_secret": "test_app_secret",
        }
        result = await handler.validate_config(config)
        assert result.valid is False
        assert any("app_id" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_validate_config_missing_app_secret(self):
        """Test config validation fails when app_secret missing."""
        handler = FeishuHandler()
        config = {
            "app_id": "test_app_id",
        }
        result = await handler.validate_config(config)
        assert result.valid is False
        assert any("app_secret" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_validate_config_empty(self):
        """Test config validation fails with empty config."""
        handler = FeishuHandler()
        config = {}
        result = await handler.validate_config(config)
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_describe_schema(self):
        """Test schema description returns valid structure."""
        handler = FeishuHandler()
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "app_id" in schema["properties"]
        assert "app_secret" in schema["properties"]
        assert "required" in schema
        assert "app_id" in schema["required"]
        assert "app_secret" in schema["required"]

    @pytest.mark.asyncio
    async def test_start_sets_connecting_status(self):
        """Test start method sets status to CONNECTING."""
        handler = FeishuHandler()
        result = await handler.start(None)
        
        assert result is True
        assert handler._status == ConnectionStatus.CONNECTING

    @pytest.mark.asyncio
    async def test_stop_disconnects(self):
        """Test stop method disconnects handler."""
        handler = FeishuHandler()
        handler._running = True
        result = await handler.stop()
        
        assert result is True
        assert handler._status == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_handle_inbound_text_message(self):
        """Test handling inbound text message."""
        handler = FeishuHandler()
        request = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "msg_123",
                    "chat_id": "chat_456",
                    "chat_type": "p2p",
                    "content": json.dumps({"text": "Hello Feishu"}),
                    "create_time": "1234567890",
                },
                "sender": {
                    "sender_id": {"open_id": "user_789"},
                }
            }
        }
        
        message = await handler.handle_inbound(request)
        
        assert message is not None
        assert message.message_id == "msg_123"
        assert message.content == "Hello Feishu"
        assert message.sender_id == "user_789"
        assert message.chat_id == "chat_456"

    @pytest.mark.asyncio
    async def test_handle_inbound_json_string(self):
        """Test handling inbound message from JSON string."""
        handler = FeishuHandler()
        request = json.dumps({
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "msg_abc",
                    "chat_id": "chat_def",
                    "content": json.dumps({"text": "Hello from JSON"}),
                },
                "sender": {
                    "sender_id": {"open_id": "user_xyz"},
                }
            }
        })
        
        message = await handler.handle_inbound(request)
        
        assert message is not None
        assert message.content == "Hello from JSON"

    @pytest.mark.asyncio
    async def test_handle_inbound_wrong_event_type(self):
        """Test handling inbound with wrong event type returns None."""
        handler = FeishuHandler()
        request = {
            "header": {"event_type": "some.other.event"},
            "event": {}
        }
        
        message = await handler.handle_inbound(request)
        
        assert message is None

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Test sending message successfully."""
        handler = FeishuHandler({"app_id": "test_id", "app_secret": "test_secret"})
        handler._access_token = "test_token"
        handler._token_expires_at = 9999999999
        
        outbound = OutboundMessage(
            chat_id="chat_123",
            content="Test message",
            content_type="text",
        )
        
        with patch("app.atlasclaw.channels.handlers.feishu.aiohttp") as mock_aiohttp:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "code": 0,
                "data": {"message_id": "sent_msg_id"}
            })
            
            mock_post_cm = AsyncMock()
            mock_post_cm.__aenter__.return_value = mock_response
            mock_post_cm.__aexit__.return_value = None
            
            mock_session = MagicMock()
            mock_session.post.return_value = mock_post_cm
            
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session
            mock_session_cm.__aexit__.return_value = None
            
            mock_aiohttp.ClientSession.return_value = mock_session_cm
            
            result = await handler.send_message(outbound)
            
            assert result.success is True
            assert result.message_id == "sent_msg_id"

    @pytest.mark.asyncio
    async def test_send_message_api_error(self):
        """Test sending message handles API error."""
        handler = FeishuHandler({"app_id": "test_id", "app_secret": "test_secret"})
        handler._access_token = "test_token"
        handler._token_expires_at = 9999999999
        
        outbound = OutboundMessage(
            chat_id="chat_123",
            content="Test message",
            content_type="text",
        )
        
        with patch("app.atlasclaw.channels.handlers.feishu.aiohttp") as mock_aiohttp:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "code": 99999,
                "msg": "API Error"
            })
            
            mock_post_cm = AsyncMock()
            mock_post_cm.__aenter__.return_value = mock_response
            mock_post_cm.__aexit__.return_value = None
            
            mock_session = MagicMock()
            mock_session.post.return_value = mock_post_cm
            
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session
            mock_session_cm.__aexit__.return_value = None
            
            mock_aiohttp.ClientSession.return_value = mock_session_cm
            
            result = await handler.send_message(outbound)
            
            assert result.success is False
            assert "API Error" in result.error


class TestFeishuHandlerMessageCallback:
    """Tests for Feishu handler message callback functionality."""

    def test_set_message_callback(self):
        """Test setting message callback."""
        handler = FeishuHandler()
        callback = MagicMock()
        
        handler.set_message_callback(callback)
        
        assert handler._message_callback == callback
