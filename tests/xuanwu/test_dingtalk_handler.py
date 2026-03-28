# -*- coding: utf-8 -*-
"""Tests for DingTalk channel handler."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Dict, Any

from app.xuanwu.channels.handlers.dingtalk import DingTalkHandler
from app.xuanwu.channels.models import (
    ChannelMode,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)


class TestDingTalkHandler:
    """Tests for DingTalkHandler class."""

    def test_handler_class_attributes(self):
        """Test handler class has correct attributes."""
        assert DingTalkHandler.channel_type == "dingtalk"
        assert DingTalkHandler.channel_name == "DingTalk"
        assert DingTalkHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert DingTalkHandler.supports_long_connection is True
        assert DingTalkHandler.supports_webhook is True

    def test_handler_init(self):
        """Test handler initialization."""
        handler = DingTalkHandler()
        assert handler.config == {}
        assert handler._status == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_setup_with_client_id(self):
        """Test setup with client_id and client_secret."""
        handler = DingTalkHandler()
        config = {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        result = await handler.setup(config)
        assert result is True
        assert handler.config["client_id"] == "test_client_id"
        assert handler.config["client_secret"] == "test_client_secret"

    @pytest.mark.asyncio
    async def test_setup_with_webhook_url(self):
        """Test setup with webhook_url."""
        handler = DingTalkHandler()
        config = {
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        }
        result = await handler.setup(config)
        assert result is True
        assert handler.config["webhook_url"] == config["webhook_url"]

    @pytest.mark.asyncio
    async def test_validate_config_valid_client_id(self):
        """Test config validation with valid client_id."""
        handler = DingTalkHandler()
        config = {
            "connection_mode": "stream",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        result = await handler.validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_validate_config_valid_webhook(self):
        """Test config validation with valid webhook_url."""
        handler = DingTalkHandler()
        config = {
            "connection_mode": "webhook",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        }
        result = await handler.validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_validate_config_missing_all(self):
        """Test config validation fails when missing both webhook_url and client_id."""
        handler = DingTalkHandler()
        config = {"connection_mode": "webhook"}
        result = await handler.validate_config(config)
        assert result.valid is False
        assert len(result.errors) > 0
        assert "webhook_url is required" in result.errors[0]

    @pytest.mark.asyncio
    async def test_validate_config_missing_client_secret(self):
        """Test config validation fails when client_id provided without client_secret."""
        handler = DingTalkHandler()
        config = {
            "connection_mode": "stream",
            "client_id": "test_client_id",
        }
        result = await handler.validate_config(config)
        assert result.valid is False
        assert len(result.errors) > 0
        assert "client_secret" in result.errors[0].lower()

    def test_describe_schema(self):
        """Test schema description returns valid structure."""
        handler = DingTalkHandler()
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "client_id" in schema["properties"]
        assert "client_secret" in schema["properties"]
        assert "webhook_url" in schema["properties"]
        assert "secret" in schema["properties"]

    @pytest.mark.asyncio
    async def test_handle_inbound_text_message(self):
        """Test handling inbound text message."""
        handler = DingTalkHandler()
        request = {
            "msgId": "test_msg_id",
            "msgtype": "text",
            "text": {"content": "Hello"},
            "senderStaffId": "user_123",
            "senderNick": "Test User",
            "conversationId": "conv_123",
        }
        
        message = await handler.handle_inbound(request)
        
        assert message is not None
        assert message.message_id == "test_msg_id"
        assert message.content == "Hello"
        assert message.sender_id == "user_123"
        assert message.sender_name == "Test User"
        assert message.chat_id == "conv_123"

    @pytest.mark.asyncio
    async def test_handle_inbound_json_string(self):
        """Test handling inbound message from JSON string."""
        import json
        handler = DingTalkHandler()
        request = json.dumps({
            "msgId": "test_msg_id",
            "msgtype": "text",
            "text": {"content": "Hello from JSON"},
            "senderStaffId": "user_456",
            "senderNick": "JSON User",
            "conversationId": "conv_456",
        })
        
        message = await handler.handle_inbound(request)
        
        assert message is not None
        assert message.content == "Hello from JSON"

    @pytest.mark.asyncio
    async def test_start_sets_connected_status(self):
        """Test start method sets status to CONNECTING."""
        handler = DingTalkHandler()
        result = await handler.start(None)
        
        assert result is True
        assert handler._status == ConnectionStatus.CONNECTING

    @pytest.mark.asyncio
    async def test_stop_disconnects(self):
        """Test stop method disconnects handler."""
        handler = DingTalkHandler()
        await handler.start(None)
        result = await handler.stop()
        
        assert result is True
        assert handler._status == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_send_message_via_webhook(self):
        """Test sending message via webhook."""
        handler = DingTalkHandler({"webhook_url": "https://test.webhook.url"})
        
        outbound = OutboundMessage(
            chat_id="conv_123",
            content="Test message",
            content_type="text",
        )
        
        with patch("app.xuanwu.channels.handlers.dingtalk.aiohttp") as mock_aiohttp:
            # Create proper async mock for response
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"errcode": 0})
            
            # Create async context manager for post
            mock_post_cm = AsyncMock()
            mock_post_cm.__aenter__.return_value = mock_response
            mock_post_cm.__aexit__.return_value = None
            
            # Create async context manager for session
            mock_session = MagicMock()
            mock_session.post.return_value = mock_post_cm
            
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session
            mock_session_cm.__aexit__.return_value = None
            
            mock_aiohttp.ClientSession.return_value = mock_session_cm
            
            result = await handler.send_message(outbound)
            
            assert result.success is True

    @pytest.mark.asyncio
    async def test_send_message_no_method_available(self):
        """Test sending message fails when no valid method available."""
        handler = DingTalkHandler({})  # No webhook_url and no client_id
        
        outbound = OutboundMessage(
            chat_id="conv_123",
            content="Test message",
            content_type="text",
        )
        
        result = await handler.send_message(outbound)
        
        assert result.success is False
        assert "No valid send method" in result.error


class TestDingTalkHandlerMessageCallback:
    """Tests for DingTalk handler message callback functionality."""

    def test_set_message_callback(self):
        """Test setting message callback."""
        handler = DingTalkHandler()
        callback = MagicMock()
        
        handler.set_message_callback(callback)
        
        assert handler._on_message_callback == callback

    def test_handle_incoming_message_calls_callback(self):
        """Test _handle_incoming_message calls the registered callback."""
        handler = DingTalkHandler()
        callback = MagicMock()
        handler.set_message_callback(callback)
        
        msg_data = {
            "message_id": "msg_123",
            "sender_id": "user_123",
            "sender_name": "Test User",
            "chat_id": "conv_123",
            "content": "Hello",
            "content_type": "text",
        }
        
        handler._handle_incoming_message(msg_data)
        
        callback.assert_called_once()
        call_arg = callback.call_args[0][0]
        assert isinstance(call_arg, InboundMessage)
        assert call_arg.content == "Hello"


class TestDingTalkConnectionMode:
    """Tests for DingTalk connection_mode feature."""

    def test_schema_has_connection_mode(self):
        """Test schema includes connection_mode field."""
        handler = DingTalkHandler()
        schema = handler.describe_schema()
        
        assert "connection_mode" in schema["properties"]
        cm = schema["properties"]["connection_mode"]
        assert cm["type"] == "string"
        assert cm["enum"] == ["stream", "webhook"]
        assert cm["default"] == "stream"
        assert "enumLabels" in cm

    def test_schema_has_required_by_mode(self):
        """Test schema includes required_by_mode."""
        handler = DingTalkHandler()
        schema = handler.describe_schema()
        
        assert "required_by_mode" in schema
        rbm = schema["required_by_mode"]
        assert "stream" in rbm
        assert "webhook" in rbm
        assert "client_id" in rbm["stream"]
        assert "client_secret" in rbm["stream"]
        assert "webhook_url" in rbm["webhook"]

    def test_schema_fields_have_show_when(self):
        """Test fields have showWhen conditions."""
        handler = DingTalkHandler()
        schema = handler.describe_schema()
        props = schema["properties"]
        
        # Stream mode fields
        assert props["client_id"]["showWhen"] == {"connection_mode": "stream"}
        assert props["client_secret"]["showWhen"] == {"connection_mode": "stream"}
        
        # Webhook mode fields
        assert props["webhook_url"]["showWhen"] == {"connection_mode": "webhook"}
        assert props["secret"]["showWhen"] == {"connection_mode": "webhook"}

    @pytest.mark.asyncio
    async def test_validate_config_stream_mode(self):
        """Test validation for stream mode."""
        handler = DingTalkHandler()
        
        # Valid stream config
        result = await handler.validate_config({
            "connection_mode": "stream",
            "client_id": "test_id",
            "client_secret": "test_secret"
        })
        assert result.valid is True
        
        # Invalid stream config (missing client_secret)
        result = await handler.validate_config({
            "connection_mode": "stream",
            "client_id": "test_id"
        })
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_validate_config_webhook_mode(self):
        """Test validation for webhook mode."""
        handler = DingTalkHandler()
        
        # Valid webhook config
        result = await handler.validate_config({
            "connection_mode": "webhook",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        })
        assert result.valid is True
        
        # Invalid webhook config (missing webhook_url)
        result = await handler.validate_config({
            "connection_mode": "webhook"
        })
        assert result.valid is False
