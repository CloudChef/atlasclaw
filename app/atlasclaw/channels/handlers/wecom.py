# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WeCom channel handler with WebSocket long connection support."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from datetime import timedelta
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode, urlparse

import aiohttp

from ..handler import ChannelHandler
from ..models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)
from ..qr_provisioning import (
    ChannelProvisioningConnection,
    ChannelProvisioningRequest,
    ChannelProvisioningSession,
    ChannelProvisioningStart,
    build_qr_image_data_url,
    mark_provisioning_poll_attempt,
    utcnow,
)

logger = logging.getLogger(__name__)
VERIFY_TIMEOUT_SECONDS = 2.6
WEBSOCKET_VERIFY_WAIT_SECONDS = 2.0


class WeComHandler(ChannelHandler):
    """WeCom channel handler.
    
    Supports:
    - WebSocket long connection for intelligent robot (bidirectional, recommended)
    - Group robot webhook (outbound only)
    - Application messaging via API (bidirectional with callback URL)
    
    WebSocket Long Connection (Intelligent Robot):
    - Real-time bidirectional messaging via WebSocket
    - Requires bot_id and secret from WeCom admin console
    - Supports streaming replies, template cards, events
    
    Group Robot Webhook:
    - Simple outbound messaging via webhook URL
    - Supports text, markdown, image, news, file messages
    
    Application Messaging:
    - Full bidirectional messaging via API
    - Requires Corp ID, Agent ID, and Secret
    - Requires callback URL for receiving user messages
    """
    
    channel_type = "wecom"
    channel_name = "WeCom"
    channel_icon = "wecom"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = True
    supports_provisioning = True
    provisioning_default_mode = "qr"
    provisioning_manual_config_available = True
    provisioning_instructions_i18n_key = "channel.provisioning.wecom.instructions"
    
    # WeCom API endpoints
    API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
    TOKEN_URL = f"{API_BASE}/gettoken"
    SEND_MSG_URL = f"{API_BASE}/message/send"
    OPENCLAW_QR_GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
    OPENCLAW_QR_QUERY_URL = "https://work.weixin.qq.com/ai/qc/query_result"
    OPENCLAW_QR_SOURCE = "wecom-cli"
    OPENCLAW_QR_TIMEOUT_SECONDS = 8
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        self._ws_client = None
        self._message_callback: Optional[Callable[[InboundMessage], None]] = None
        self._connection_task: Optional[asyncio.Task] = None
        self._running = False
        # Store frame for reply lookup
        self._pending_frames: Dict[str, Any] = {}

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        """Create WeCom OpenClaw intelligent robot setup QR details."""
        del request
        qr = await self._fetch_openclaw_qr()
        scode = str(qr.get("scode") or "").strip()
        auth_url = str(qr.get("auth_url") or "").strip()
        if not scode:
            raise ValueError("WeCom QR response missing scode")
        if not auth_url:
            raise ValueError("WeCom QR response missing auth_url")

        refresh_after_seconds = 3
        return ChannelProvisioningStart(
            qr_url=auth_url,
            qr_image_url=build_qr_image_data_url(auth_url),
            platform_state={
                "scode": scode,
                "interval": refresh_after_seconds,
            },
            expires_at=utcnow() + timedelta(minutes=5),
            refresh_after_seconds=refresh_after_seconds,
            instructions_i18n_key=self.provisioning_instructions_i18n_key,
        )

    @classmethod
    def _qr_source(cls) -> str:
        """Return the WeCom QR provisioning source identifier."""
        return os.getenv("ATLASCLAW_WECOM_QR_SOURCE", cls.OPENCLAW_QR_SOURCE).strip()

    @classmethod
    def _qr_platform_code(cls) -> int:
        """Return the WeCom QR platform code used by the official CLI."""
        if sys.platform == "darwin":
            return 1
        if sys.platform == "win32":
            return 2
        if sys.platform.startswith("linux"):
            return 3
        return 0

    @classmethod
    def _qr_generate_url(cls) -> str:
        """Return the WeCom QR generation endpoint URL."""
        base_url = os.getenv("ATLASCLAW_WECOM_QR_GENERATE_URL", cls.OPENCLAW_QR_GENERATE_URL)
        query = urlencode({
            "source": cls._qr_source(),
            "plat": cls._qr_platform_code(),
        })
        return f"{base_url}?{query}"

    @classmethod
    def _qr_query_url(cls, scode: str) -> str:
        """Return the WeCom QR polling endpoint URL for a scode."""
        base_url = os.getenv("ATLASCLAW_WECOM_QR_QUERY_URL", cls.OPENCLAW_QR_QUERY_URL)
        return f"{base_url}?{urlencode({'scode': scode})}"

    async def _get_json(self, url: str) -> Dict[str, Any]:
        """GET JSON from a WeCom QR endpoint."""
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=self.OPENCLAW_QR_TIMEOUT_SECONDS),
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception as exc:
                    text = await response.text()
                    raise ValueError(f"Invalid WeCom QR response: {text[:200]}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Invalid WeCom QR response")
        return payload

    async def _fetch_openclaw_qr(self) -> Dict[str, Any]:
        """Fetch a WeCom QR auth URL and polling scode."""
        payload = await self._get_json(self._qr_generate_url())
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return dict(data or {})

    async def _query_openclaw_qr_result(self, scode: str) -> Dict[str, Any]:
        """Poll a WeCom QR registration result once."""
        return await self._get_json(self._qr_query_url(scode))

    async def poll_provisioning_connection(
        self,
        session: ChannelProvisioningSession,
    ) -> Optional[ChannelProvisioningConnection]:
        """Poll WeCom QR registration and return robot credentials when ready."""
        state = dict(session.platform_state or {})
        scode = str(state.get("scode") or "").strip()
        if not scode:
            raise ValueError("WeCom QR scode is missing")

        if mark_provisioning_poll_attempt(session, default_interval_seconds=3) is None:
            return None

        try:
            response = await self._query_openclaw_qr_result(scode)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Transient WeCom QR poll failure: %s", exc)
            return None

        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        status = str(data.get("status") or "").strip().lower()
        if status == "success":
            bot_info = data.get("bot_info") if isinstance(data.get("bot_info"), dict) else {}
            bot_id = bot_info.get("botid")
            bot_secret = bot_info.get("secret")
            if not bot_id:
                raise ValueError("bot_id is required")
            if not bot_secret:
                raise ValueError("bot_secret is required")
            return ChannelProvisioningConnection(
                name="WeCom Bot",
                config={
                    "connection_mode": "websocket",
                    "bot_id": str(bot_id),
                    "bot_secret": str(bot_secret),
                },
            )
        if status in {"", "pending", "waiting", "scanned"}:
            session.status = "authorizing" if status == "scanned" else "pending"
            session.error = None
            session.updated_at = utcnow()
            return None
        session.status = "failed"
        session.error = str(data.get("message") or data.get("errmsg") or f"WeCom QR status: {status}")
        session.updated_at = utcnow()
        return None
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize WeCom handler with configuration.
        
        Args:
            connection_config: Configuration with bot_id/secret, webhook_url, or corpid/secret
            
        Returns:
            True if setup successful
        """
        try:
            self.config.update(connection_config)
            
            bot_id = self.config.get("bot_id")
            webhook_url = self.config.get("webhook_url")
            corpid = self.config.get("corpid")
            
            if not bot_id and not webhook_url and not corpid:
                logger.error("WeCom requires bot_id, webhook_url, or corpid")
                return False
            
            return True
        except Exception as e:
            logger.error(f"WeCom setup failed: {e}")
            return False
    
    def set_message_callback(self, callback: Callable[[InboundMessage], None]) -> None:
        """Set callback for incoming messages."""
        self._message_callback = callback
    
    async def start(self, context: Any) -> bool:
        """Start WeCom handler."""
        try:
            self._status = ConnectionStatus.CONNECTING
            logger.info("WeCom handler starting...")
            return True
        except Exception as e:
            logger.error(f"WeCom start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def connect(self) -> bool:
        """Establish connection based on configuration."""
        try:
            bot_id = self.config.get("bot_id")
            secret = self.config.get("bot_secret") or self.config.get("secret")  # bot_secret is new, secret for backward compat
            
            if bot_id and secret:
                # Use WebSocket long connection mode
                return await self._connect_websocket()
            else:
                # Use API/Webhook mode
                corpid = self.config.get("corpid")
                corpsecret = self.config.get("corpsecret")
                
                if corpid and corpsecret:
                    if not await self._get_access_token():
                        logger.error("Failed to get WeCom access token")
                        return False
                
                self._status = ConnectionStatus.CONNECTED
                logger.info("WeCom connected (API mode)")
                return True
                
        except Exception as e:
            logger.error(f"WeCom connect failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False

    @staticmethod
    def _looks_like_http_url(url: str) -> bool:
        """Check whether the webhook URL is a valid HTTP(S) address."""
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _verify_webhook_endpoint(self, webhook_url: str) -> Optional[str]:
        """Perform static validation for the WeCom webhook URL."""
        if not self._looks_like_http_url(webhook_url):
            return "webhook_url must be a valid HTTP/HTTPS URL"
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https":
            return "webhook_url must use HTTPS"
        if parsed.username or parsed.password:
            return "webhook_url must not include credentials"
        return None

    async def _verify_websocket_credentials(self, config: Dict[str, Any]) -> bool:
        """Verify WeCom WebSocket credentials by connecting and cleaning up immediately."""
        probe_handler = WeComHandler(dict(config))
        started = await probe_handler.start(None)
        if not started:
            return False

        try:
            return await asyncio.wait_for(probe_handler.connect(), timeout=2.8)
        except asyncio.TimeoutError:
            logger.error("[WeCom] WebSocket credential verification timed out")
            return False
        finally:
            await probe_handler.stop()

    async def _verify_app_credentials(self, config: Dict[str, Any]) -> bool:
        """Verify WeCom application credentials by requesting an access token."""
        probe_handler = WeComHandler(dict(config))
        try:
            return await asyncio.wait_for(probe_handler._get_access_token(), timeout=2.8)
        except asyncio.TimeoutError:
            logger.error("[WeCom] Application credential verification timed out")
            return False
    
    async def _connect_websocket(self) -> bool:
        """Connect via WebSocket long connection."""
        try:
            # 默认支持环境代理（HTTP_PROXY/HTTPS_PROXY）。
            # 如需绕过 WebSocket 代理，可设置: ATLASCLAW_BYPASS_WS_PROXY=true
            bypass_ws_proxy = os.getenv("ATLASCLAW_BYPASS_WS_PROXY", "").lower() in {"1", "true", "yes", "on"}
            if bypass_ws_proxy:
                try:
                    import websockets.asyncio.client as ws_client
                    ws_client.get_proxy = lambda uri: None
                    logger.info("[WeCom] WebSocket proxy bypass enabled")
                except Exception as e:
                    logger.warning(f"[WeCom] Could not patch websockets: {e}")

            
            from wecom_aibot_sdk import WSClient
            
            bot_id = self.config.get("bot_id")
            secret = self.config.get("bot_secret") or self.config.get("secret")
            
            logger.info(f"[WeCom] Connecting WebSocket with bot_id: {bot_id}")
            
            # Create WebSocket client
            self._ws_client = WSClient(bot_id, secret)
            
            # Register event handlers
            self._ws_client.on("connected", self._on_connected)
            self._ws_client.on("authenticated", self._on_authenticated)
            self._ws_client.on("disconnected", self._on_disconnected)
            self._ws_client.on("error", self._on_error)
            self._ws_client.on("message.text", self._on_text_message)
            self._ws_client.on("message.image", self._on_image_message)
            self._ws_client.on("message.file", self._on_file_message)
            self._ws_client.on("message.voice", self._on_voice_message)
            self._ws_client.on("event.enter_chat", self._on_enter_chat)
            
            # Connect
            self._running = True
            await self._ws_client.connect()
            
            deadline = time.monotonic() + WEBSOCKET_VERIFY_WAIT_SECONDS
            while time.monotonic() < deadline:
                if self._ws_client.is_connected or self._status == ConnectionStatus.CONNECTED:
                    break
                await asyncio.sleep(0.12)
            
            if self._ws_client.is_connected:
                self._status = ConnectionStatus.CONNECTED
                logger.info("[WeCom] WebSocket connected")
                return True
            else:
                logger.error("[WeCom] WebSocket connection failed")
                self._status = ConnectionStatus.ERROR
                return False
                
        except ImportError:
            logger.error("[WeCom] wecom-aibot-sdk not installed. Install with: pip install wecom-aibot-sdk")
            self._status = ConnectionStatus.ERROR
            return False
        except Exception as e:
            logger.error(f"[WeCom] WebSocket connect failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    def _on_connected(self) -> None:
        """Handle WebSocket connected event."""
        logger.info("[WeCom] WebSocket connected")
    
    def _on_authenticated(self) -> None:
        """Handle WebSocket authenticated event."""
        logger.info("[WeCom] WebSocket authenticated")
        self._status = ConnectionStatus.CONNECTED
    
    def _on_disconnected(self, reason: str) -> None:
        """Handle WebSocket disconnected event."""
        logger.warning(f"[WeCom] WebSocket disconnected: {reason}")
        if self._running:
            self._status = ConnectionStatus.ERROR
    
    def _on_error(self, error: Exception) -> None:
        """Handle WebSocket error event."""
        logger.error(f"[WeCom] WebSocket error: {error}")
    
    async def _on_text_message(self, frame: dict) -> None:
        """Handle incoming text message."""
        await self._handle_message(frame, "text")
    
    async def _on_image_message(self, frame: dict) -> None:
        """Handle incoming image message."""
        await self._handle_message(frame, "image")
    
    async def _on_file_message(self, frame: dict) -> None:
        """Handle incoming file message."""
        await self._handle_message(frame, "file")
    
    async def _on_voice_message(self, frame: dict) -> None:
        """Handle incoming voice message."""
        await self._handle_message(frame, "voice")
    
    async def _on_enter_chat(self, frame: dict) -> None:
        """Handle user entering chat event."""
        logger.info(f"[WeCom] User entered chat: {frame}")
    
    async def _handle_message(self, frame: dict, content_type: str) -> None:
        """Handle incoming message from WebSocket."""
        try:
            # Extract message info from frame
            body = frame.get("body", {})
            headers = frame.get("headers", {})
            
            # Get content based on type
            if content_type == "text":
                content = body.get("text", {}).get("content", "")
            elif content_type == "image":
                content = body.get("image", {}).get("url", "")
            elif content_type == "file":
                content = body.get("file", {}).get("filename", "")
            elif content_type == "voice":
                content = body.get("voice", {}).get("url", "")
            else:
                content = json.dumps(body)
            
            # Store frame for reply
            req_id = headers.get("req_id", "")
            if req_id:
                self._pending_frames[req_id] = frame
            
            # Create InboundMessage
            inbound = InboundMessage(
                message_id=req_id or str(time.time()),
                sender_id=body.get("userid", "") or headers.get("userid", ""),
                sender_name=body.get("nickname", ""),
                chat_id=body.get("chatid", ""),
                channel_type=self.channel_type,
                content=content,
                content_type=content_type,
                thread_id=None,
                metadata={
                    "req_id": req_id,
                    "chat_type": body.get("chattype", ""),
                    "frame": frame,
                },
            )
            
            logger.info(f"[WeCom] Message received: {content[:50]}...")
            
            # Call callback
            if self._message_callback:
                self._message_callback(inbound)
            else:
                logger.warning("[WeCom] No message callback set")
                
        except Exception as e:
            logger.error(f"[WeCom] Error handling message: {e}")
    
    async def disconnect(self) -> bool:
        """Disconnect from WeCom."""
        try:
            self._running = False
            
            if self._ws_client:
                await self._ws_client.disconnect()
                self._ws_client = None
                logger.info("[WeCom] WebSocket disconnected")
            
            self._access_token = None
            self._status = ConnectionStatus.DISCONNECTED
            return True
        except Exception as e:
            logger.error(f"WeCom disconnect failed: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop WeCom handler."""
        await self.disconnect()
        return True
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming WeCom message callback (for API mode).
        
        Args:
            request: WeCom callback data (XML parsed to dict)
            
        Returns:
            Standardized InboundMessage
        """
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request
            
            msg_type = data.get("MsgType", "text")
            
            # Extract content based on message type
            if msg_type == "text":
                content = data.get("Content", "")
            elif msg_type == "image":
                content = data.get("PicUrl", "")
            elif msg_type == "voice":
                content = data.get("MediaId", "")
            else:
                content = json.dumps(data)
            
            return InboundMessage(
                message_id=data.get("MsgId", ""),
                sender_id=data.get("FromUserName", ""),
                sender_name=data.get("FromUserName", "Anonymous"),
                chat_id=data.get("ToUserName", ""),
                channel_type=self.channel_type,
                content=content,
                content_type=msg_type,
                metadata={
                    "AgentID": data.get("AgentID"),
                    "CreateTime": data.get("CreateTime"),
                },
            )
        except Exception as e:
            logger.error(f"Failed to handle WeCom message: {e}")
            return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message to WeCom.
        
        Supports WebSocket, webhook, and application modes.
        """
        try:
            # Try WebSocket first
            if self._ws_client and self._ws_client.is_connected:
                return await self._send_ws_message(outbound)
            
            # Try webhook
            webhook_url = self.config.get("webhook_url")
            if webhook_url:
                return await self._send_webhook_message(outbound)
            
            # Try application API
            corpid = self.config.get("corpid")
            if corpid:
                return await self._send_app_message(outbound)
            
            return SendResult(success=False, error="No send method available")
                
        except Exception as e:
            logger.error(f"Failed to send WeCom message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _send_ws_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via WebSocket."""
        try:
            chat_id = outbound.chat_id
            
            # Check if this is a reply to a pending message
            req_id = outbound.metadata.get("req_id") if outbound.metadata else None
            frame = self._pending_frames.pop(req_id, None) if req_id else None
            
            if frame:
                # Reply to the original message with stream
                from wecom_aibot_sdk.utils import generate_random_string
                stream_id = generate_random_string(16)
                
                await self._ws_client.reply_stream(
                    frame,
                    stream_id,
                    outbound.content,
                    finish=True,
                )
                logger.info(f"[WeCom] Replied via WebSocket stream")
                return SendResult(success=True)
            else:
                # Proactive message
                await self._ws_client.send_message(
                    chat_id,
                    {"msgtype": "markdown", "markdown": {"content": outbound.content}}
                )
                logger.info(f"[WeCom] Sent proactive message to {chat_id}")
                return SendResult(success=True)
                
        except Exception as e:
            logger.error(f"[WeCom] WebSocket send failed: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _send_webhook_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via group robot webhook."""
        webhook_url = self.config.get("webhook_url")
        
        # Support different message types
        msg_type = outbound.metadata.get("msgtype", "text") if outbound.metadata else "text"
        
        if msg_type == "markdown":
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": outbound.content}
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": outbound.content}
            }
        
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(webhook_url, json=payload) as response:

                if response.status == 200:
                    data = await response.json()
                    if data.get("errcode") == 0:
                        return SendResult(success=True)
                    else:
                        return SendResult(
                            success=False,
                            error=f"WeCom error: {data.get('errmsg')}"
                        )
                else:
                    return SendResult(success=False, error=f"HTTP {response.status}")
    
    async def _send_app_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via application API."""
        if not self._access_token:
            if not await self._get_access_token():
                return SendResult(success=False, error="Failed to get access token")
        
        url = f"{self.SEND_MSG_URL}?access_token={self._access_token}"
        
        payload = {
            "touser": outbound.chat_id or "@all",
            "msgtype": "text",
            "agentid": self.config.get("agentid"),
            "text": {"content": outbound.content},
        }
        
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(url, json=payload) as response:

                if response.status == 200:
                    data = await response.json()
                    if data.get("errcode") == 0:
                        return SendResult(success=True, message_id=data.get("msgid"))
                    else:
                        return SendResult(
                            success=False,
                            error=f"WeCom error: {data.get('errmsg')}"
                        )
                else:
                    return SendResult(success=False, error=f"HTTP {response.status}")
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate WeCom configuration."""
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        connection_mode = config.get("connection_mode")
        if not connection_mode:
            if config.get("bot_id"):
                connection_mode = "websocket"
            elif config.get("corpid"):
                connection_mode = "app"
            else:
                connection_mode = "webhook"
        
        if connection_mode == "webhook":
            if not config.get("webhook_url"):
                errors.append("webhook_url is required for Webhook mode")
            if not errors:
                webhook_error = await self._verify_webhook_endpoint(str(config.get("webhook_url", "")))
                if webhook_error:
                    errors.append(webhook_error)
        elif connection_mode == "websocket":
            if not config.get("bot_id"):
                errors.append("bot_id is required for WebSocket mode")
            if not (config.get("bot_secret") or config.get("secret")):
                errors.append("bot_secret is required for WebSocket mode")
            if not errors and not await self._verify_websocket_credentials(config):
                errors.append("Failed to verify WeCom WebSocket credentials")
        elif connection_mode == "app":
            if not config.get("corpid"):
                errors.append("corpid is required for Application mode")
            if not config.get("corpsecret"):
                errors.append("corpsecret is required for Application mode")
            if not config.get("agentid"):
                errors.append("agentid is required for Application mode")
            if not errors and not await self._verify_app_credentials(config):
                errors.append("Failed to verify WeCom application credentials")
        else:
            errors.append(f"Unsupported connection_mode: {connection_mode}")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return WeCom configuration schema."""
        return {
            "type": "object",
            "title": "WeCom",
            "description": "WeCom bot configuration",
            "properties": {
                "connection_mode": {
                    "type": "string",
                    "title": "Connection Mode",
                    "description": "Select connection mode",
                    "enum": ["websocket", "webhook"],
                    "enumLabels": {
                        "websocket": "Long Connection (Intelligent Robot)",
                        "webhook": "Webhook Robot"
                    },
                    "default": "websocket",
                },
                "bot_id": {
                    "type": "string",
                    "title": "Bot ID",
                    "description": "Intelligent robot Bot ID",
                    "placeholder": "aib...",
                    "showWhen": {"connection_mode": "websocket"},
                },
                "bot_secret": {
                    "type": "string",
                    "title": "Bot Secret",
                    "description": "Intelligent robot Secret",
                    "placeholder": "Bot secret",
                    "showWhen": {"connection_mode": "websocket"},
                },
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "Group bot Webhook address",
                    "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
                    "showWhen": {"connection_mode": "webhook"},
                },
            },
            "required_by_mode": {
                "websocket": ["bot_id", "bot_secret"],
                "webhook": ["webhook_url"]
            },
        }
    
    async def _get_access_token(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Get WeCom access token."""
        try:
            if self._access_token and time.time() < self._token_expires:
                return True
            
            token_config = config or self.config
            params = {
                "corpid": token_config.get("corpid"),
                "corpsecret": token_config.get("corpsecret"),
            }
            
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(
                    self.TOKEN_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS),
                ) as response:

                    if response.status == 200:
                        data = await response.json()
                        if data.get("errcode") == 0:
                            self._access_token = data.get("access_token")
                            self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                            return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to get WeCom access token: {e}")
            return False
