# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for DingTalk channel handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Dict, Any

from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from app.atlasclaw.channels.models import (
    ChannelMode,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)
from app.atlasclaw.channels.qr_provisioning import (
    ChannelProvisioningRequest,
    ChannelProvisioningSession,
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
        assert DingTalkHandler.supports_provisioning is True
        assert DingTalkHandler.provisioning_default_mode == "qr"
        assert DingTalkHandler.provisioning_manual_config_available is True

    def test_handler_init(self):
        """Test handler initialization."""
        handler = DingTalkHandler()
        assert handler.config == {}
        assert handler._status == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_maps_stream_config(self):
        """Test registration polling maps completed DingTalk stream credentials."""
        handler = DingTalkHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="dingtalk",
            state_token="state-token",
            user_code="DING-CODE",
            platform_state={
                "device_code": "device-code",
                "base_url": "https://oapi.dingtalk.com",
                "interval": 1,
            },
        )

        with patch.object(
            handler,
            "_poll_registration",
            AsyncMock(return_value={
                "status": "SUCCESS",
                "client_id": "ding_test",
                "client_secret": "secret",
            }),
        ):
            result = await handler.poll_provisioning_connection(session)

        assert result is not None
        assert result.name == "DingTalk Bot"
        assert result.config == {
            "connection_mode": "stream",
            "client_id": "ding_test",
            "client_secret": "secret",
        }

    @pytest.mark.asyncio
    async def test_poll_provisioning_connection_rejects_missing_credentials(self):
        """Test polling completion requires DingTalk's canonical client credentials."""
        handler = DingTalkHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="dingtalk",
            state_token="state-token",
            user_code="DING-CODE",
            platform_state={
                "device_code": "device-code",
                "base_url": "https://oapi.dingtalk.com",
                "interval": 1,
            },
        )

        with pytest.raises(ValueError, match="client_id is required"):
            with patch.object(
                handler,
                "_poll_registration",
                AsyncMock(return_value={"status": "SUCCESS", "app_key": "ding_test"}),
            ):
                await handler.poll_provisioning_connection(session)

    @pytest.mark.asyncio
    async def test_provisioned_config_is_compatible_with_manual_stream_path(self):
        """Test poll-generated config is accepted by the manual stream path."""
        handler = DingTalkHandler()
        session = ChannelProvisioningSession(
            session_id="session-1",
            user_id="user-1",
            channel_type="dingtalk",
            state_token="state-token",
            user_code="DING-CODE",
            platform_state={
                "device_code": "device-code",
                "base_url": "https://oapi.dingtalk.com",
                "interval": 1,
            },
        )
        with patch.object(
            handler,
            "_poll_registration",
            AsyncMock(return_value={
                "status": "SUCCESS",
                "client_id": "ding_test",
                "client_secret": "secret",
            }),
        ):
            provisioned = await handler.poll_provisioning_connection(session)

        assert provisioned is not None
        assert await handler.setup(provisioned.config) is True
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)):
            result = await handler.validate_config(provisioned.config)

        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_create_provisioning_session_uses_openclaw_registration_url(self):
        """Test DingTalk QR points to OpenClaw registration from device-code flow."""
        pytest.importorskip("qrcode")
        handler = DingTalkHandler()

        with (
            patch.object(handler, "_init_registration", AsyncMock(return_value="nonce")) as mock_init,
            patch.object(
                handler,
                "_begin_registration",
                AsyncMock(return_value={
                    "device_code": "device-code",
                    "user_code": "VPRV-82TT",
                    "verification_uri_complete": (
                        "https://open-dev.dingtalk.com/openapp/registration/openClaw"
                        "?user_code=VPRV-82TT"
                    ),
                    "expires_in": 7200,
                    "interval": 5,
                }),
            ) as mock_begin,
        ):
            result = await handler.create_provisioning_session(ChannelProvisioningRequest(
                user_id="user-1",
                channel_type="dingtalk",
                session_id="session-1",
                state_token="state-token",
                user_code="LOCAL-CODE",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))

        parsed = urlparse(result.qr_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "open-dev.dingtalk.com"
        assert parsed.path == "/openapp/registration/openClaw"
        assert query == {"user_code": ["VPRV-82TT"]}
        assert result.user_code == "VPRV-82TT"
        assert result.platform_state["device_code"] == "device-code"
        assert result.platform_state["base_url"] == "https://oapi.dingtalk.com"
        assert result.refresh_after_seconds == 5
        assert result.qr_image_url is not None
        assert result.qr_image_url.startswith("data:image/png;base64,")
        mock_init.assert_awaited_once_with("https://oapi.dingtalk.com")
        mock_begin.assert_awaited_once_with("https://oapi.dingtalk.com", nonce="nonce")

    @pytest.mark.asyncio
    async def test_create_provisioning_session_requires_broker_user_code(self):
        """Test DingTalk QR provisioning requires broker-issued user_code."""
        handler = DingTalkHandler()

        with (
            patch.object(handler, "_init_registration", AsyncMock(return_value="nonce")),
            patch.object(
                handler,
                "_begin_registration",
                AsyncMock(return_value={
                    "device_code": "device-code",
                    "verification_uri_complete": (
                        "https://open-dev.dingtalk.com/openapp/registration/openClaw"
                    ),
                }),
            ),
        ):
            with pytest.raises(ValueError, match="user_code"):
                await handler.create_provisioning_session(ChannelProvisioningRequest(
                    user_id="user-1",
                    channel_type="dingtalk",
                    session_id="session-1",
                    state_token="state-token",
                    user_code="LOCAL-CODE",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ))

    @pytest.mark.asyncio
    async def test_create_provisioning_session_rejects_noncanonical_qr_url_field(self):
        """Test DingTalk QR provisioning does not fall back to verification_uri."""
        handler = DingTalkHandler()

        with (
            patch.object(handler, "_init_registration", AsyncMock(return_value="nonce")),
            patch.object(
                handler,
                "_begin_registration",
                AsyncMock(return_value={
                    "device_code": "device-code",
                    "user_code": "VPRV-82TT",
                    "verification_uri": "https://open-dev.dingtalk.com/legacy",
                }),
            ),
        ):
            with pytest.raises(ValueError, match="verification_uri_complete"):
                await handler.create_provisioning_session(ChannelProvisioningRequest(
                    user_id="user-1",
                    channel_type="dingtalk",
                    session_id="session-1",
                    state_token="state-token",
                    user_code="LOCAL-CODE",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ))

    def test_registration_env_ignores_legacy_variable_names(self, monkeypatch):
        """Test QR provisioning env overrides use only AtlasClaw names."""
        monkeypatch.setenv("DINGTALK_REGISTRATION_BASE_URL", "https://legacy.example.com")
        monkeypatch.setenv("DINGTALK_REGISTRATION_SOURCE", "legacy-source")
        monkeypatch.delenv("ATLASCLAW_DINGTALK_REGISTRATION_BASE_URL", raising=False)
        monkeypatch.delenv("ATLASCLAW_DINGTALK_REGISTRATION_SOURCE", raising=False)

        assert DingTalkHandler._registration_base_url() == DingTalkHandler.REGISTRATION_BASE_URL
        assert DingTalkHandler._registration_source() == DingTalkHandler.REGISTRATION_SOURCE

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
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0
        mock_verify.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    async def test_connect_credential_verification_retries_transient_failure(self):
        """Connect-time credential verification should tolerate short platform delays."""
        handler = DingTalkHandler({"client_id": "ding_test", "client_secret": "secret"})

        with patch.object(
            handler,
            "_verify_credentials",
            AsyncMock(side_effect=[False, False, True]),
        ) as mock_verify, patch(
            "app.atlasclaw.channels.handlers.dingtalk.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            result = await handler._verify_credentials_for_connect()

        assert result is True
        assert mock_verify.await_count == 3
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_validate_config_valid_webhook(self):
        """Test config validation with valid webhook_url."""
        handler = DingTalkHandler()
        config = {
            "connection_mode": "webhook",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        }
        with patch.object(handler, "_verify_webhook_endpoint", AsyncMock(return_value=None)) as mock_verify:
            result = await handler.validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0
        mock_verify.assert_awaited_once_with(config["webhook_url"], None)

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

    @pytest.mark.asyncio
    async def test_verify_webhook_endpoint_rejects_insecure_url(self):
        """Test webhook validation rejects non-HTTPS URLs."""
        handler = DingTalkHandler()

        result = await handler._verify_webhook_endpoint(
            "http://oapi.dingtalk.com/robot/send?access_token=xxx"
        )

        assert result == "webhook_url must use HTTPS"

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
        
        with patch("app.atlasclaw.channels.handlers.dingtalk.aiohttp") as mock_aiohttp:
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
        valid_config = {
            "connection_mode": "stream",
            "client_id": "test_id",
            "client_secret": "test_secret"
        }
        with patch.object(handler, "_verify_credentials", AsyncMock(return_value=True)) as mock_verify:
            result = await handler.validate_config(valid_config)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(valid_config)
        
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
        valid_config = {
            "connection_mode": "webhook",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        }
        with patch.object(handler, "_verify_webhook_endpoint", AsyncMock(return_value=None)) as mock_verify:
            result = await handler.validate_config(valid_config)
        assert result.valid is True
        mock_verify.assert_awaited_once_with(valid_config["webhook_url"], None)
        
        # Invalid webhook config (missing webhook_url)
        result = await handler.validate_config({
            "connection_mode": "webhook"
        })
        assert result.valid is False
