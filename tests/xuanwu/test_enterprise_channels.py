# -*- coding: utf-8 -*-
"""Tests for enterprise channel handlers (DingTalk, WeCom)."""

from __future__ import annotations

import pytest

from app.xuanwu.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
)
from app.xuanwu.channels.handlers.dingtalk import DingTalkHandler
from app.xuanwu.channels.handlers.wecom import WeComHandler


class TestDingTalkHandler:
    """Test DingTalkHandler functionality."""

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert DingTalkHandler.channel_type == "dingtalk"
        assert DingTalkHandler.channel_name == "DingTalk"
        assert DingTalkHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert DingTalkHandler.supports_long_connection is True
        assert DingTalkHandler.supports_webhook is True

    @pytest.mark.asyncio
    async def test_setup_with_webhook(self):
        """Test handler setup with webhook URL."""
        handler = DingTalkHandler()
        result = await handler.setup({
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        })
        
        assert result is True
        assert handler.config["webhook_url"] == "https://oapi.dingtalk.com/robot/send?access_token=xxx"

    @pytest.mark.asyncio
    async def test_setup_with_client_id(self):
        """Test handler setup with client_id (app_key)."""
        handler = DingTalkHandler()
        result = await handler.setup({
            "client_id": "dingxxxxxxxx",
            "client_secret": "secret123"
        })
        
        assert result is True
        assert handler.config["client_id"] == "dingxxxxxxxx"

    @pytest.mark.asyncio
    async def test_setup_without_credentials(self):
        """Test handler setup works without credentials (just returns True)."""
        handler = DingTalkHandler()
        result = await handler.setup({})
        
        # setup() now returns True even without credentials
        assert result is True

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = DingTalkHandler()
        await handler.setup({"webhook_url": "https://example.com"})
        
        start_result = await handler.start(None)
        assert start_result is True
        assert handler.get_status() == ConnectionStatus.CONNECTING
        
        stop_result = await handler.stop()
        assert stop_result is True
        assert handler.get_status() == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_validate_config_webhook(self):
        """Test configuration validation for webhook mode."""
        handler = DingTalkHandler()
        
        result = await handler.validate_config({
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        })
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_validate_config_client_id(self):
        """Test configuration validation for client_id (app_key) mode."""
        handler = DingTalkHandler()
        
        result = await handler.validate_config({
            "connection_mode": "stream",
            "client_id": "dingxxxxxxxx",
            "client_secret": "secret123"
        })
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_validate_config_empty(self):
        """Test configuration validation fails with empty config."""
        handler = DingTalkHandler()
        
        result = await handler.validate_config({})
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is False
        assert len(result.errors) > 0

    def test_describe_schema(self):
        """Test schema description."""
        handler = DingTalkHandler()
        
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "webhook_url" in schema["properties"]
        assert "client_id" in schema["properties"]
        assert "client_secret" in schema["properties"]

    @pytest.mark.asyncio
    async def test_handle_inbound_json(self):
        """Test handling inbound DingTalk callback."""
        handler = DingTalkHandler()
        await handler.setup({"client_id": "test", "client_secret": "secret"})
        
        # DingTalk callback format uses msgtype/text format
        request = {
            "msgId": "msg-123",
            "msgtype": "text",
            "conversationId": "chat-456",
            "senderStaffId": "user-789",
            "senderNick": "Test User",
            "text": {
                "content": "Hello DingTalk"
            }
        }
        
        inbound = await handler.handle_inbound(request)
        
        assert inbound is not None
        assert inbound.message_id == "msg-123"
        assert inbound.sender_id == "user-789"
        assert inbound.content == "Hello DingTalk"
        assert inbound.channel_type == "dingtalk"


class TestWeComHandler:
    """Test WeComHandler functionality."""

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert WeComHandler.channel_type == "wecom"
        assert WeComHandler.channel_name == "WeCom"
        assert WeComHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert WeComHandler.supports_long_connection is True
        assert WeComHandler.supports_webhook is True

    @pytest.mark.asyncio
    async def test_setup_with_webhook(self):
        """Test handler setup with webhook URL."""
        handler = WeComHandler()
        result = await handler.setup({
            "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
        })
        
        assert result is True
        assert handler.config["webhook_url"] == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

    @pytest.mark.asyncio
    async def test_setup_with_bot_id(self):
        """Test handler setup with bot_id (WeChat Work intelligent robot)."""
        handler = WeComHandler()
        result = await handler.setup({
            "connection_mode": "websocket",
            "bot_id": "aibxxxxxxxx",
            "bot_secret": "secret123"
        })
        
        assert result is True
        assert handler.config["bot_id"] == "aibxxxxxxxx"

    @pytest.mark.asyncio
    async def test_setup_without_credentials(self):
        """Test handler setup fails without credentials."""
        handler = WeComHandler()
        result = await handler.setup({})
        
        # setup() now returns False without valid credentials
        assert result is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = WeComHandler()
        await handler.setup({"webhook_url": "https://example.com"})
        
        start_result = await handler.start(None)
        assert start_result is True
        # start() sets status to CONNECTING, actual connection happens in connect()
        assert handler.get_status() in [ConnectionStatus.CONNECTING, ConnectionStatus.CONNECTED]
        
        stop_result = await handler.stop()
        assert stop_result is True
        assert handler.get_status() == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_validate_config_webhook(self):
        """Test configuration validation for webhook mode."""
        handler = WeComHandler()
        
        result = await handler.validate_config({
            "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
        })
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_validate_config_websocket(self):
        """Test configuration validation for WebSocket (bot) mode."""
        handler = WeComHandler()
        
        result = await handler.validate_config({
            "connection_mode": "websocket",
            "bot_id": "aibxxxxxxxx",
            "bot_secret": "secret123"
        })
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_validate_config_empty(self):
        """Test configuration validation fails with empty config."""
        handler = WeComHandler()
        
        result = await handler.validate_config({})
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is False
        assert len(result.errors) > 0

    def test_describe_schema(self):
        """Test schema description."""
        handler = WeComHandler()
        
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "webhook_url" in schema["properties"]
        assert "bot_id" in schema["properties"]
        assert "bot_secret" in schema["properties"]
        assert "connection_mode" in schema["properties"]

    @pytest.mark.asyncio
    async def test_handle_inbound_xml(self):
        """Test handling inbound WeCom callback."""
        handler = WeComHandler()
        await handler.setup({"bot_id": "test", "bot_secret": "secret"})
        
        # WeCom callback format (parsed from XML)
        request = {
            "MsgId": "msg-123",
            "CreateTime": 1234567890,
            "MsgType": "text",
            "Content": "Hello WeCom",
            "FromUserName": "user-789",
            "ToUserName": "corp-123",
            "AgentID": 1000001
        }
        
        inbound = await handler.handle_inbound(request)
        
        assert inbound is not None
        assert inbound.message_id == "msg-123"
        assert inbound.sender_id == "user-789"
        assert inbound.content == "Hello WeCom"
        assert inbound.channel_type == "wecom"
