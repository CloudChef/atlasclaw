# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for Feishu channel handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlparse

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
from app.atlasclaw.channels.qr_provisioning import ChannelProvisioningRequest
from app.atlasclaw.channels.qr_provisioning import ChannelProvisioningSession, utcnow


class TestFeishuHandler:
    """Tests for FeishuHandler class."""

    def test_handler_class_attributes(self):
        """Test handler class has correct attributes."""
        assert FeishuHandler.channel_type == "feishu"
        assert FeishuHandler.channel_name == "Feishu"
        assert FeishuHandler.channel_mode == ChannelMode.BIDIRECTIONAL
        assert FeishuHandler.supports_long_connection is True
        assert FeishuHandler.supports_webhook is False
        assert FeishuHandler.supports_provisioning is True
        assert FeishuHandler.provisioning_default_mode == "qr"
        assert FeishuHandler.provisioning_manual_config_available is True
        assert FeishuHandler.provisioning_user_code_groups == 2

    def test_handler_init(self):
        """Test handler initialization."""
        handler = FeishuHandler()
        assert handler.config == {}
        assert handler._status == ConnectionStatus.DISCONNECTED
        assert handler._access_token is None

    @pytest.mark.asyncio
    async def test_provisioned_config_is_compatible_with_manual_longconnection_path(self):
        """Test poll-generated config is accepted by the manual long-connection path."""
        handler = FeishuHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="feishu",
            state_token="state-token",
            user_code="QFXB-8X3X",
            platform_state={
                "device_code": "device-code",
                "domain": "feishu",
                "interval": 1,
                "tp": "ob_app",
            },
        )
        with patch.object(
            handler,
            "_poll_app_registration",
            AsyncMock(return_value={"client_id": "cli_test", "client_secret": "secret"}),
        ):
            provisioned = await handler.poll_provisioning_connection(session)

        assert provisioned is not None
        assert await handler.setup(provisioned.config) is True
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)):
            result = await handler.validate_config(provisioned.config)

        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_create_provisioning_session_returns_qr_image(self):
        """Test provisioning session returns a scannable QR image data URL."""
        pytest.importorskip("qrcode")
        handler = FeishuHandler()

        with (
            patch.object(handler, "_init_app_registration", AsyncMock()) as mock_init,
            patch.object(
                handler,
                "_begin_app_registration",
                AsyncMock(return_value={
                    "device_code": "device-code",
                    "user_code": "QFXB-8X3X",
                    "verification_uri_complete": (
                        "https://accounts.feishu.cn/oauth/v1/device/verify"
                        "?user_code=QFXB-8X3X"
                    ),
                    "interval": 5,
                    "expire_in": 600,
                }),
            ) as mock_begin,
        ):
            result = await handler.create_provisioning_session(ChannelProvisioningRequest(
                user_id="user-1",
                channel_type="feishu",
                session_id="session-1",
                state_token="state-token",
                user_code="LOCAL-CODE",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))

        parsed = urlparse(result.qr_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "accounts.feishu.cn"
        assert parsed.path == "/oauth/v1/device/verify"
        assert query == {
            "user_code": ["QFXB-8X3X"],
            "from": ["oc_onboard"],
            "tp": ["ob_cli_app"],
        }
        assert result.user_code == "QFXB-8X3X"
        assert result.platform_state["device_code"] == "device-code"
        assert result.platform_state["interval"] == 5
        assert result.platform_state["tp"] == "ob_app"
        assert result.refresh_after_seconds == 5
        assert result.qr_image_url is not None
        assert result.qr_image_url.startswith("data:image/png;base64,")
        mock_init.assert_awaited_once_with("feishu")
        mock_begin.assert_awaited_once_with("feishu")

    @pytest.mark.asyncio
    async def test_create_provisioning_session_rejects_noncanonical_qr_url_field(self):
        """Test QR provisioning does not fall back to legacy verification_uri."""
        handler = FeishuHandler()

        with (
            patch.object(handler, "_init_app_registration", AsyncMock()),
            patch.object(
                handler,
                "_begin_app_registration",
                AsyncMock(return_value={
                    "device_code": "device-code",
                    "user_code": "QFXB-8X3X",
                    "verification_uri": "https://accounts.feishu.cn/legacy",
                }),
            ),
        ):
            with pytest.raises(ValueError, match="verification_uri"):
                await handler.create_provisioning_session(ChannelProvisioningRequest(
                    user_id="user-1",
                    channel_type="feishu",
                    session_id="session-1",
                    state_token="state-token",
                    user_code="LOCAL-CODE",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ))

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_returns_credentials(self):
        """Test Feishu registration polling maps completed credentials."""
        handler = FeishuHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="feishu",
            state_token="state-token",
            user_code="QFXB-8X3X",
            platform_state={
                "device_code": "device-code",
                "domain": "feishu",
                "interval": 1,
                "tp": "ob_app",
            },
        )

        with patch.object(
            handler,
            "_poll_app_registration",
            AsyncMock(return_value={"client_id": "cli_test", "client_secret": "secret"}),
        ) as mock_poll:
            result = await handler.poll_provisioning_connection(session)

        assert result is not None
        assert result.name == "Feishu Bot"
        assert result.config == {
            "connection_mode": "longconnection",
            "app_id": "cli_test",
            "app_secret": "secret",
        }
        mock_poll.assert_awaited_once_with(
            "feishu",
            device_code="device-code",
            tp="ob_app",
        )

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_respects_interval(self):
        """Test Feishu registration polling does not exceed platform interval."""
        handler = FeishuHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="feishu",
            state_token="state-token",
            user_code="QFXB-8X3X",
            platform_state={
                "device_code": "device-code",
                "domain": "feishu",
                "interval": 60,
                "last_poll_at": utcnow().isoformat(),
            },
        )

        with patch.object(handler, "_poll_app_registration", AsyncMock()) as mock_poll:
            result = await handler.poll_provisioning_connection(session)

        assert result is None
        mock_poll.assert_not_awaited()

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
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(config)

        assert result.valid is True
        assert len(result.errors) == 0
        mock_verify.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    async def test_connect_credential_verification_retries_transient_failure(self):
        """Connect-time credential verification should tolerate short platform delays."""
        handler = FeishuHandler({"app_id": "test_app_id", "app_secret": "test_app_secret"})

        with patch.object(
            handler,
            "_verify_credentials",
            AsyncMock(side_effect=[False, False, True]),
        ) as mock_verify, patch(
            "app.atlasclaw.channels.handlers.feishu.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            result = await handler._verify_credentials_for_connect()

        assert result is True
        assert mock_verify.await_count == 3
        assert mock_sleep.await_count == 2

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

    @pytest.mark.asyncio
    async def test_verify_webhook_endpoint_rejects_insecure_url(self):
        """Test webhook validation rejects non-HTTPS URLs."""
        handler = FeishuHandler()

        result = await handler._verify_webhook_endpoint(
            "http://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        )

        assert result == "webhook_url must use HTTPS"

    def test_describe_schema(self):
        """Test schema description returns valid structure."""
        handler = FeishuHandler()
        schema = handler.describe_schema()
        
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "connection_mode" in schema["properties"]
        assert "app_id" in schema["properties"]
        assert "app_secret" in schema["properties"]
        assert "webhook_url" in schema["properties"]
        assert "required_by_mode" in schema

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


class TestFeishuConnectionMode:
    """Tests for Feishu connection_mode feature."""

    def test_schema_has_connection_mode(self):
        """Test schema includes connection_mode field."""
        handler = FeishuHandler()
        schema = handler.describe_schema()
        
        assert "connection_mode" in schema["properties"]
        cm = schema["properties"]["connection_mode"]
        assert cm["type"] == "string"
        assert cm["enum"] == ["longconnection", "webhook"]
        assert cm["default"] == "longconnection"
        assert "enumLabels" in cm

    def test_schema_has_required_by_mode(self):
        """Test schema includes required_by_mode."""
        handler = FeishuHandler()
        schema = handler.describe_schema()
        
        assert "required_by_mode" in schema
        rbm = schema["required_by_mode"]
        assert "longconnection" in rbm
        assert "webhook" in rbm
        assert "app_id" in rbm["longconnection"]
        assert "app_secret" in rbm["longconnection"]
        assert "webhook_url" in rbm["webhook"]

    def test_schema_fields_have_show_when(self):
        """Test fields have showWhen conditions."""
        handler = FeishuHandler()
        schema = handler.describe_schema()
        props = schema["properties"]
        
        # Long connection mode fields
        assert props["app_id"]["showWhen"] == {"connection_mode": "longconnection"}
        assert props["app_secret"]["showWhen"] == {"connection_mode": "longconnection"}
        
        # Webhook mode fields
        assert props["webhook_url"]["showWhen"] == {"connection_mode": "webhook"}

    @pytest.mark.asyncio
    async def test_validate_config_longconnection_mode(self):
        """Test validation for long connection mode."""
        handler = FeishuHandler()
        
        # Valid long connection config
        valid_config = {
            "connection_mode": "longconnection",
            "app_id": "cli_test",
            "app_secret": "test_secret"
        }
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(valid_config)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(valid_config)
        
        # Invalid long connection config (missing app_secret)
        result = await handler.validate_config({
            "connection_mode": "longconnection",
            "app_id": "cli_test"
        })
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_validate_config_webhook_mode(self):
        """Test validation for webhook mode."""
        handler = FeishuHandler()
        
        # Valid webhook config
        valid_config = {
            "connection_mode": "webhook",
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        }
        with patch.object(handler, "_verify_webhook_endpoint", AsyncMock(return_value=None)) as mock_verify:
            result = await handler.validate_config(valid_config)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(valid_config["webhook_url"])
        
        # Invalid webhook config (missing webhook_url)
        result = await handler.validate_config({
            "connection_mode": "webhook"
        })
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_send_via_webhook(self):
        """Test sending message via webhook."""
        handler = FeishuHandler()
        handler.config = {
            "connection_mode": "webhook",
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        }
        
        outbound = OutboundMessage(
            chat_id="test_chat",
            content="Hello via webhook",
            content_type="text",
        )
        
        with patch("app.atlasclaw.channels.handlers.feishu.aiohttp") as mock_aiohttp:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"code": 0})
            
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
            # Verify webhook URL was called
            mock_session.post.assert_called_once()
            call_args = mock_session.post.call_args
            assert "webhook_url" in handler.config
