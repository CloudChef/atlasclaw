# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for enterprise channel handlers (DingTalk, WeCom)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sys
from types import ModuleType
from urllib.parse import parse_qs, urlparse

import pytest
from unittest.mock import AsyncMock, patch

from app.atlasclaw.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
)
from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from app.atlasclaw.channels.handlers.wecom import WeComHandler
from app.atlasclaw.channels.qr_provisioning import (
    ChannelProvisioningRequest,
    ChannelProvisioningSession,
)


class TestDingTalkHandler:
    """Test DingTalkHandler functionality."""

    def test_class_attributes(self):
        """Test handler class attributes."""
        assert DingTalkHandler.channel_type == "dingtalk"
        assert DingTalkHandler.channel_name == "DingTalk"
        assert DingTalkHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert DingTalkHandler.supports_long_connection is True
        assert DingTalkHandler.supports_webhook is True
        assert DingTalkHandler.supports_provisioning is True

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
        
        config = {
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        }
        with patch.object(handler, "_verify_webhook_endpoint", AsyncMock(return_value=None)) as mock_verify:
            result = await handler.validate_config(config)
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(config["webhook_url"], None)

    @pytest.mark.asyncio
    async def test_validate_config_client_id(self):
        """Test configuration validation for client_id (app_key) mode."""
        handler = DingTalkHandler()
        
        config = {
            "connection_mode": "stream",
            "client_id": "dingxxxxxxxx",
            "client_secret": "secret123"
        }
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(config)
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(config)

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
        assert WeComHandler.supports_provisioning is True
        assert WeComHandler.provisioning_manual_config_available is True

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
    async def test_poll_provisioning_connection_maps_websocket_config(self):
        """Test QR polling maps completed WeCom WebSocket bot credentials."""
        handler = WeComHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="wecom",
            state_token="state-token",
            user_code="WECM-CODE",
            platform_state={"scode": "scode", "interval": 1},
        )

        with patch.object(
            handler,
            "_query_openclaw_qr_result",
            AsyncMock(return_value={
                "data": {
                    "status": "success",
                    "bot_info": {"botid": "aib_test", "secret": "secret"},
                },
            }),
        ):
            result = await handler.poll_provisioning_connection(session)

        assert result is not None
        assert result.name == "WeCom Bot"
        assert result.config == {
            "connection_mode": "websocket",
            "bot_id": "aib_test",
            "bot_secret": "secret",
        }

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_rejects_missing_bot_id(self):
        """Test QR polling completion requires WeCom bot credentials."""
        handler = WeComHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="wecom",
            state_token="state-token",
            user_code="WECM-CODE",
            platform_state={"scode": "scode", "interval": 1},
        )

        with pytest.raises(ValueError, match="bot_id is required"):
            with patch.object(
                handler,
                "_query_openclaw_qr_result",
                AsyncMock(return_value={
                    "data": {
                        "status": "success",
                        "bot_info": {"corpid": "corp_test", "secret": "secret"},
                    },
                }),
            ):
                await handler.poll_provisioning_connection(session)

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_rejects_noncanonical_bot_id_field(self):
        """Test WeCom QR provisioning only accepts broker-issued botid."""
        handler = WeComHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="wecom",
            state_token="state-token",
            user_code="WECM-CODE",
            platform_state={"scode": "scode", "interval": 1},
        )

        with pytest.raises(ValueError, match="bot_id is required"):
            with patch.object(
                handler,
                "_query_openclaw_qr_result",
                AsyncMock(return_value={
                    "data": {
                        "status": "success",
                        "bot_info": {"bot_id": "aib_test", "secret": "secret"},
                    },
                }),
            ):
                await handler.poll_provisioning_connection(session)

    @pytest.mark.asyncio
    async def test_provisioned_config_is_compatible_with_manual_websocket_path(self):
        """Test poll-generated config is accepted by the manual WebSocket path."""
        handler = WeComHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="wecom",
            state_token="state-token",
            user_code="WECM-CODE",
            platform_state={"scode": "scode", "interval": 1},
        )
        with patch.object(
            handler,
            "_query_openclaw_qr_result",
            AsyncMock(return_value={
                "data": {
                    "status": "success",
                    "bot_info": {"botid": "aib_test", "secret": "secret"},
                },
            }),
        ):
            provisioned = await handler.poll_provisioning_connection(session)

        assert provisioned is not None
        assert await handler.setup(provisioned.config) is True
        with patch.object(handler, "_verify_websocket_credentials", AsyncMock(return_value=True)):
            result = await handler.validate_config(provisioned.config)

        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_create_provisioning_session_uses_openclaw_qr_url(self):
        """Test WeCom QR uses the official OpenClaw CLI broker flow."""
        pytest.importorskip("qrcode")
        handler = WeComHandler()

        with patch.object(
            handler,
            "_fetch_openclaw_qr",
            AsyncMock(return_value={
                "scode": "scode",
                "auth_url": "https://work.weixin.qq.com/ai/qc/auth?scode=scode",
            }),
        ) as mock_fetch:
            result = await handler.create_provisioning_session(ChannelProvisioningRequest(
                user_id="user-1",
                channel_type="wecom",
                session_id="session-1",
                state_token="state-token",
                user_code="WECM-2026-TEST",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))

        parsed = urlparse(result.qr_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "work.weixin.qq.com"
        assert parsed.path == "/ai/qc/auth"
        assert query == {"scode": ["scode"]}
        assert result.platform_state["scode"] == "scode"
        assert result.refresh_after_seconds == 3
        assert result.qr_image_url is not None
        assert result.qr_image_url.startswith("data:image/png;base64,")
        mock_fetch.assert_awaited_once()

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
        
        config = {
            "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
        }
        with patch.object(handler, "_verify_webhook_endpoint", AsyncMock(return_value=None)) as mock_verify:
            result = await handler.validate_config(config)
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(config["webhook_url"])

    @pytest.mark.asyncio
    async def test_validate_config_websocket(self):
        """Test configuration validation for WebSocket (bot) mode."""
        handler = WeComHandler()
        
        config = {
            "connection_mode": "websocket",
            "bot_id": "aibxxxxxxxx",
            "bot_secret": "secret123"
        }
        with patch.object(handler, "_verify_websocket_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(config)
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    async def test_validate_config_empty(self):
        """Test configuration validation fails with empty config."""
        handler = WeComHandler()
        
        result = await handler.validate_config({})
        
        assert isinstance(result, ChannelValidationResult)
        assert result.valid is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_verify_webhook_endpoint_rejects_insecure_url(self):
        """Test webhook validation rejects non-HTTPS URLs."""
        handler = WeComHandler()

        result = await handler._verify_webhook_endpoint(
            "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
        )

        assert result == "webhook_url must use HTTPS"

    @pytest.mark.asyncio
    async def test_connect_websocket_waits_for_async_handshake(self):
        """Test WebSocket connect tolerates async SDK handshake delay."""

        class SlowConnectWSClient:
            def __init__(self, bot_id, secret):
                self.bot_id = bot_id
                self.secret = secret
                self.is_connected = False

            def on(self, event_name, callback):
                return None

            async def connect(self):
                async def mark_connected():
                    await asyncio.sleep(1.3)
                    self.is_connected = True

                asyncio.create_task(mark_connected())

            async def disconnect(self):
                self.is_connected = False

        fake_sdk = ModuleType("wecom_aibot_sdk")
        fake_sdk.WSClient = SlowConnectWSClient

        handler = WeComHandler({
            "connection_mode": "websocket",
            "bot_id": "aibxxxxxxxx",
            "bot_secret": "secret123",
        })

        with patch.dict(sys.modules, {"wecom_aibot_sdk": fake_sdk}):
            result = await handler._connect_websocket()

        assert result is True
        assert handler.get_status() == ConnectionStatus.CONNECTED
        await handler.stop()

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
