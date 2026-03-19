# -*- coding: utf-8 -*-
"""Tests for enterprise channel handlers (DingTalk, WeCom)."""

from __future__ import annotations

import pytest

from app.atlasclaw.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
)
from providers.dingtalk.channels.dingtalk import DingTalkHandler
from providers.wecom.channels.wecom import WeComHandler


class TestDingTalkHandler:
    """Test DingTalkHandler functionality."""

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert DingTalkHandler.channel_type == "dingtalk"
        assert DingTalkHandler.channel_name == "DingTalk"
        assert DingTalkHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert DingTalkHandler.supports_long_connection is False
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
    async def test_setup_with_app_key(self):
        """Test handler setup with app key."""
        handler = DingTalkHandler()
        result = await handler.setup({
            "app_key": "dingxxxxxxxx",
            "app_secret": "secret123"
        })
        
        assert result is True
        assert handler.config["app_key"] == "dingxxxxxxxx"

    @pytest.mark.asyncio
    async def test_setup_without_credentials(self):
        """Test handler setup fails without credentials."""
        handler = DingTalkHandler()
        result = await handler.setup({})
        
        assert result is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = DingTalkHandler()
        await handler.setup({"webhook_url": "https://example.com"})
        
        start_result = await handler.start(None)
        assert start_result is True
        assert handler.get_status() == ConnectionStatus.CONNECTED
        
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
    async def test_validate_config_app_key(self):
        """Test configuration validation for app key mode."""
        handler = DingTalkHandler()
        
        result = await handler.validate_config({
            "app_key": "dingxxxxxxxx",
            "app_secret": "secret123"
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
        assert "app_key" in schema["properties"]
        assert "app_secret" in schema["properties"]

    @pytest.mark.asyncio
    async def test_handle_inbound_json(self):
        """Test handling inbound DingTalk callback."""
        handler = DingTalkHandler()
        await handler.setup({"app_key": "test", "app_secret": "secret"})
        
        # DingTalk callback format
        request = {
            "msgId": "msg-123",
            "createAt": 1234567890000,
            "conversationType": "1",
            "conversationId": "chat-456",
            "senderId": "user-789",
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
        assert WeComHandler.supports_long_connection is False
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
    async def test_setup_with_corpid(self):
        """Test handler setup with corp ID."""
        handler = WeComHandler()
        result = await handler.setup({
            "corpid": "wwxxxxxxxx",
            "corpsecret": "secret123",
            "agentid": 1000001
        })
        
        assert result is True
        assert handler.config["corpid"] == "wwxxxxxxxx"

    @pytest.mark.asyncio
    async def test_setup_without_credentials(self):
        """Test handler setup fails without credentials."""
        handler = WeComHandler()
        result = await handler.setup({})
        
        assert result is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test handler start and stop."""
        handler = WeComHandler()
        await handler.setup({"webhook_url": "https://example.com"})
        
        start_result = await handler.start(None)
        assert start_result is True
        assert handler.get_status() == ConnectionStatus.CONNECTED
        
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
    async def test_validate_config_corpid(self):
        """Test configuration validation for corp ID mode."""
        handler = WeComHandler()
        
        result = await handler.validate_config({
            "corpid": "wwxxxxxxxx",
            "corpsecret": "secret123",
            "agentid": 1000001
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
        assert "corpid" in schema["properties"]
        assert "corpsecret" in schema["properties"]
        assert "agentid" in schema["properties"]

    @pytest.mark.asyncio
    async def test_handle_inbound_xml(self):
        """Test handling inbound WeCom callback."""
        handler = WeComHandler()
        await handler.setup({"corpid": "test", "corpsecret": "secret", "agentid": 1000001})
        
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
