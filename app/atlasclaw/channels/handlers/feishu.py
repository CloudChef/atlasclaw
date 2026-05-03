# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Feishu (Lark) channel handler with multiprocessing for SDK isolation."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import os
import queue
import threading
import time
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    parse_positive_int,
    utcnow,
)

logger = logging.getLogger(__name__)
VERIFY_TIMEOUT_SECONDS = 2.6


def _run_feishu_sdk_process(
    app_id: str,
    app_secret: str,
    message_queue: multiprocessing.Queue,
    control_queue: multiprocessing.Queue,
):
    """
    Run Feishu SDK in a separate process to avoid event loop conflicts.
    
    Args:
        app_id: Feishu app ID
        app_secret: Feishu app secret
        message_queue: Queue to send received messages to main process
        control_queue: Queue to receive control commands from main process
    """
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    
    # Flag to track if connection signal has been sent
    connection_signaled = False
    
    def handle_message(data: P2ImMessageReceiveV1):
        """Handle received message and send to queue."""
        try:
            message = data.event.message
            sender = data.event.sender
            
            # Parse content
            content = message.content
            if message.message_type == "text":
                try:
                    content_obj = json.loads(content)
                    text = content_obj.get("text", "")
                except:
                    text = content
            else:
                text = content
            
            # Send to queue as dict
            msg_data = {
                "message_id": message.message_id,
                "sender_id": sender.sender_id.open_id,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "content": text,
                "content_type": message.message_type,
                "create_time": message.create_time,
                "tenant_key": sender.tenant_key,
            }
            message_queue.put(msg_data)
            print(f"[Feishu SDK Process] Message received: {text[:50]}...")
            
        except Exception as e:
            print(f"[Feishu SDK Process] Error handling message: {e}")
    
    def send_connected_signal():
        """Send connected signal after delay if process is still running."""
        nonlocal connection_signaled
        if not connection_signaled:
            connection_signaled = True
            print("[Feishu SDK Process] Connection established, sending signal", flush=True)
            try:
                control_queue.put({"type": "connected"}, timeout=5)
            except Exception as exc:
                print(f"[Feishu SDK Process] Failed to put connected signal: {exc}", flush=True)
    
    try:
        print(f"[Feishu SDK Process] Starting with app_id: {app_id}", flush=True)
        
        # Create event handler
        event_handler = lark.EventDispatcherHandler.builder(
            "", ""
        ).register_p2_im_message_receive_v1(
            handle_message
        ).build()
        
        # Create WebSocket client
        client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        
        # Use timer to send connected signal after delay
        # If client.start() throws exception before timer fires, the signal won't be sent
        timer = threading.Timer(3.0, send_connected_signal)
        timer.daemon = True
        timer.start()
        
        # Start the client (blocking)
        print("[Feishu SDK Process] Connecting...", flush=True)
        client.start()
        
    except Exception as e:
        print(f"[Feishu SDK Process] Error: {e}", flush=True)
        if not connection_signaled:
            try:
                control_queue.put({"type": "error", "error": str(e)}, timeout=5)
            except Exception:
                pass


class FeishuHandler(ChannelHandler):
    """Feishu channel handler using multiprocessing for SDK isolation."""
    
    channel_type = "feishu"
    channel_name = "Feishu"
    channel_icon = "feishu"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = False
    supports_provisioning = True
    provisioning_default_mode = "qr"
    provisioning_manual_config_available = True
    provisioning_instructions_i18n_key = "channel.provisioning.feishu.instructions"
    provisioning_user_code_groups = 2
    
    # Feishu API endpoints
    FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
    AUTH_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    FEISHU_ACCOUNTS_URL = "https://accounts.feishu.cn"
    LARK_ACCOUNTS_URL = "https://accounts.larksuite.com"
    REGISTRATION_PATH = "/oauth/v1/app/registration"
    REGISTRATION_TIMEOUT_SECONDS = 10
    REGISTRATION_QR_FROM = "oc_onboard"
    REGISTRATION_QR_TP = "ob_cli_app"
    REGISTRATION_POLL_TP = "ob_app"
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._process: Optional[multiprocessing.Process] = None
        self._message_queue: Optional[multiprocessing.Queue] = None
        self._control_queue: Optional[multiprocessing.Queue] = None
        self._message_callback: Optional[Callable[[InboundMessage], None]] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._running = False

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        """Create a Feishu app-registration QR session."""
        del request
        domain = self._registration_domain()
        await self._init_app_registration(domain)
        begin = await self._begin_app_registration(domain)

        device_code = str(begin.get("device_code") or "").strip()
        user_code = str(begin.get("user_code") or "").strip()
        verification_uri = str(begin.get("verification_uri_complete") or "").strip()
        if not device_code:
            raise ValueError("Feishu registration response missing device_code")
        if not user_code:
            raise ValueError("Feishu registration response missing user_code")
        if not verification_uri:
            raise ValueError("Feishu registration response missing verification_uri_complete")

        interval = parse_positive_int(begin.get("interval"), default=5)
        expire_in = parse_positive_int(begin.get("expire_in"), default=600)
        qr_url = self._with_registration_qr_params(verification_uri)
        return ChannelProvisioningStart(
            qr_url=qr_url,
            qr_image_url=build_qr_image_data_url(qr_url),
            user_code=user_code,
            platform_state={
                "device_code": device_code,
                "domain": domain,
                "interval": interval,
                "expire_in": expire_in,
                "tp": self.REGISTRATION_POLL_TP,
            },
            expires_at=utcnow() + timedelta(seconds=expire_in),
            refresh_after_seconds=interval,
            instructions_i18n_key=self.provisioning_instructions_i18n_key,
        )

    @classmethod
    def _registration_domain(cls) -> str:
        """Return the Feishu accounts domain used for app registration."""
        domain = os.getenv("ATLASCLAW_FEISHU_REGISTRATION_DOMAIN", "feishu").strip().lower()
        return "lark" if domain == "lark" else "feishu"

    @classmethod
    def _registration_base_url(cls, domain: str) -> str:
        """Return the registration API base URL for a Feishu/Lark domain."""
        override = os.getenv("ATLASCLAW_FEISHU_REGISTRATION_BASE_URL")
        if override:
            return override.rstrip("/")
        return cls.LARK_ACCOUNTS_URL if domain == "lark" else cls.FEISHU_ACCOUNTS_URL

    @classmethod
    def _registration_url(cls, domain: str) -> str:
        """Return the app-registration endpoint URL."""
        return f"{cls._registration_base_url(domain)}{cls.REGISTRATION_PATH}"

    @classmethod
    def _with_registration_qr_params(cls, url: str) -> str:
        """Append OpenClaw-compatible registration source parameters to the QR URL."""
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["from"] = os.getenv(
            "ATLASCLAW_FEISHU_REGISTRATION_QR_FROM",
            cls.REGISTRATION_QR_FROM,
        )
        query["tp"] = os.getenv(
            "ATLASCLAW_FEISHU_REGISTRATION_QR_TP",
            cls.REGISTRATION_QR_TP,
        )
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def _post_app_registration(
        self,
        domain: str,
        body: Dict[str, str],
    ) -> Dict[str, Any]:
        """POST a form-encoded Feishu app-registration request."""
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                self._registration_url(domain),
                data=urlencode(body),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=self.REGISTRATION_TIMEOUT_SECONDS),
            ) as response:
                try:
                    payload = await response.json()
                except Exception as exc:
                    text = await response.text()
                    raise ValueError(f"Invalid Feishu registration response: {text[:200]}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Invalid Feishu registration response")
        return payload

    async def _init_app_registration(self, domain: str) -> None:
        """Verify Feishu app registration supports client_secret auth."""
        response = await self._post_app_registration(domain, {"action": "init"})
        methods = response.get("supported_auth_methods") or []
        if not isinstance(methods, list) or "client_secret" not in methods:
            raise ValueError("Feishu app registration does not support client_secret auth")

    async def _begin_app_registration(self, domain: str) -> Dict[str, Any]:
        """Begin Feishu app registration and return device-code details."""
        return await self._post_app_registration(
            domain,
            {
                "action": "begin",
                "archetype": "PersonalAgent",
                "auth_method": "client_secret",
                "request_user_info": "open_id",
            },
        )

    async def _poll_app_registration(
        self,
        domain: str,
        *,
        device_code: str,
        tp: str,
    ) -> Dict[str, Any]:
        """Poll Feishu app registration once."""
        return await self._post_app_registration(
            domain,
            {
                "action": "poll",
                "device_code": device_code,
                "tp": tp,
            },
        )

    async def poll_provisioning_connection(
        self,
        session: ChannelProvisioningSession,
    ) -> Optional[ChannelProvisioningConnection]:
        """Poll Feishu registration and return app credentials when scan completes."""
        state = dict(session.platform_state or {})
        device_code = str(state.get("device_code") or "")
        if not device_code:
            raise ValueError("Feishu registration device_code is missing")

        interval = mark_provisioning_poll_attempt(session, default_interval_seconds=5)
        if interval is None:
            return None
        state = dict(session.platform_state or {})

        domain = str(state.get("domain") or self._registration_domain())
        if domain not in {"feishu", "lark"}:
            domain = "feishu"
        tp = str(state.get("tp") or self.REGISTRATION_POLL_TP)

        try:
            response = await self._poll_app_registration(
                domain,
                device_code=device_code,
                tp=tp,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Transient Feishu registration poll failure: %s", exc)
            return None

        tenant_brand = (response.get("user_info") or {}).get("tenant_brand")
        if tenant_brand == "lark" and domain != "lark" and not state.get("domain_switched"):
            domain = "lark"
            state["domain"] = domain
            state["domain_switched"] = True
            session.platform_state = state
            try:
                response = await self._poll_app_registration(
                    domain,
                    device_code=device_code,
                    tp=tp,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("Transient Lark registration poll failure: %s", exc)
                return None

        app_id = response.get("client_id")
        app_secret = response.get("client_secret")
        if app_id and app_secret:
            return ChannelProvisioningConnection(
                name="Feishu Bot",
                config={
                    "connection_mode": "longconnection",
                    "app_id": str(app_id),
                    "app_secret": str(app_secret),
                },
            )

        error = response.get("error")
        if error == "authorization_pending":
            session.status = "authorizing"
            session.error = None
            session.updated_at = utcnow()
            return None
        if error == "slow_down":
            state["interval"] = interval + 5
            session.platform_state = state
            session.status = "authorizing"
            session.error = None
            session.updated_at = utcnow()
            return None
        if error == "access_denied":
            session.status = "failed"
            session.error = "Feishu app registration was denied"
            session.updated_at = utcnow()
            return None
        if error == "expired_token":
            session.expires_at = utcnow()
            session.error = "Feishu app registration expired"
            session.updated_at = utcnow()
            return None
        if error:
            description = response.get("error_description") or "unknown error"
            session.status = "failed"
            session.error = f"{error}: {description}"
            session.updated_at = utcnow()
        return None

    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize handler with configuration."""
        try:
            self.config.update(connection_config)
            
            if not self.config.get("app_id"):
                logger.error("Feishu app_id is required")
                return False
            if not self.config.get("app_secret"):
                logger.error("Feishu app_secret is required")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Feishu setup failed: {e}")
            return False
    
    def set_message_callback(self, callback: Callable[[InboundMessage], None]) -> None:
        """Set callback for incoming messages."""
        self._message_callback = callback
    
    async def start(self, context: Any) -> bool:
        """Start handler."""
        try:
            self._status = ConnectionStatus.CONNECTING
            logger.info("Feishu handler starting...")
            return True
        except Exception as e:
            logger.error(f"Feishu start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    @staticmethod
    def _looks_like_http_url(url: str) -> bool:
        """Check whether the webhook URL is a valid HTTP(S) address."""
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _verify_credentials(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Verify Feishu credentials by getting tenant_access_token.
        
        This validates the app_id/app_secret before starting the SDK subprocess.
        Returns True if credentials are valid, False otherwise.
        """
        verify_config = config or self.config
        app_id = verify_config.get("app_id")
        app_secret = verify_config.get("app_secret")
        
        if not app_id or not app_secret:
            logger.error("[Feishu] Missing app_id or app_secret")
            return False
        
        try:
            url = f"{self.FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
            payload = {"app_id": app_id, "app_secret": app_secret}
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS),
                ) as resp:
                    data = await resp.json()
                    if data.get("code") == 0 and data.get("tenant_access_token"):
                        logger.info("[Feishu] Credentials verified successfully")
                        return True
                    else:
                        logger.error(f"[Feishu] Credential verification failed: {data.get('msg', 'unknown error')}")
                        return False
        except Exception as e:
            logger.error(f"[Feishu] Credential verification error: {e}")
            return False

    async def _verify_webhook_endpoint(self, webhook_url: str) -> Optional[str]:
        """Perform static validation for the Feishu webhook URL."""
        if not self._looks_like_http_url(webhook_url):
            return "webhook_url must be a valid HTTP/HTTPS URL"
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https":
            return "webhook_url must use HTTPS"
        if parsed.username or parsed.password:
            return "webhook_url must not include credentials"
        return None

    async def _cleanup_connect_failure(self) -> None:
        """Cleanup resources after connect failure."""
        self._running = False
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
        self._process = None
        self._message_queue = None
        self._control_queue = None

    async def connect(self) -> bool:
        """Establish connection using multiprocessing."""
        try:
            app_id = self.config.get("app_id")
            app_secret = self.config.get("app_secret")
            
            # Pre-verify credentials before starting SDK subprocess
            if not await self._verify_credentials():
                self._status = ConnectionStatus.ERROR
                return False
            
            print(f"[Feishu] Connecting with app_id: {app_id}")
            
            # Create queues for IPC
            self._message_queue = multiprocessing.Queue()
            self._control_queue = multiprocessing.Queue()
            
            # Start SDK process
            self._process = multiprocessing.Process(
                target=_run_feishu_sdk_process,
                args=(app_id, app_secret, self._message_queue, self._control_queue),
                daemon=True,
            )
            self._process.start()
            print(f"[Feishu] SDK process started (PID: {self._process.pid})")
            
            # Start message listener thread
            self._running = True
            self._listener_thread = threading.Thread(
                target=self._listen_for_messages,
                daemon=True,
            )
            self._listener_thread.start()
            print("[Feishu] Message listener started")
            
            # Wait for connection result from subprocess via control_queue
            # Timeout: 20 seconds (subprocess sends signal after 3 seconds if successful)
            connection_timeout = 20.0
            start_time = time.time()
            
            while time.time() - start_time < connection_timeout:
                # Check if process died
                if not self._process.is_alive():
                    logger.error("Feishu SDK process died unexpectedly")
                    self._status = ConnectionStatus.ERROR
                    await self._cleanup_connect_failure()
                    return False
                
                # Try to get message from control queue (non-blocking)
                try:
                    msg = self._control_queue.get_nowait()
                    if msg.get("type") == "connected":
                        self._status = ConnectionStatus.CONNECTED
                        logger.info("Feishu connected via multiprocessing")
                        return True
                    elif msg.get("type") == "error":
                        error = msg.get("error", "Unknown error")
                        logger.error(f"Feishu connection failed: {error}")
                        self._status = ConnectionStatus.ERROR
                        await self._cleanup_connect_failure()
                        return False
                except queue.Empty:
                    # Fallback: if process is still alive after 10 seconds,
                    # consider it connected (Queue signal may not arrive on Windows)
                    elapsed = time.time() - start_time
                    if elapsed > 10.0 and self._process.is_alive():
                        logger.info("Feishu process alive after 10s, assuming connected (Queue fallback)")
                        self._status = ConnectionStatus.CONNECTED
                        return True
                    await asyncio.sleep(0.5)
                    continue
            
            # Timeout reached
            logger.error("Feishu connection timeout")
            self._status = ConnectionStatus.ERROR
            await self._cleanup_connect_failure()
            return False
                
        except Exception as e:
            logger.error(f"Feishu connect failed: {e}")
            self._status = ConnectionStatus.ERROR
            await self._cleanup_connect_failure()
            return False
    
    def _listen_for_messages(self):
        """Listen for messages from SDK process."""
        while self._running:
            try:
                # Non-blocking check with timeout
                try:
                    msg_data = self._message_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Convert to InboundMessage
                inbound = InboundMessage(
                    message_id=msg_data["message_id"],
                    sender_id=msg_data["sender_id"],
                    sender_name="",
                    chat_id=msg_data["chat_id"],
                    channel_type=self.channel_type,
                    content=msg_data["content"],
                    content_type=msg_data["content_type"],
                    thread_id=None,
                    metadata={
                        "chat_type": msg_data["chat_type"],
                        "create_time": msg_data["create_time"],
                        "tenant_key": msg_data["tenant_key"],
                    },
                )
                
                logger.info(f"Feishu message received: {inbound.message_id}")
                
                # Call callback
                if self._message_callback:
                    self._message_callback(inbound)
                else:
                    logger.warning("No message callback set for Feishu handler")
                    
            except Exception as e:
                logger.error(f"Error in message listener: {e}")
    
    async def disconnect(self) -> bool:
        """Disconnect and cleanup."""
        try:
            self._running = False
            
            if self._process and self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=5)
                print("[Feishu] SDK process terminated")
            
            self._process = None
            self._message_queue = None
            self._control_queue = None
            self._status = ConnectionStatus.DISCONNECTED
            logger.info("Feishu disconnected")
            return True
        except Exception as e:
            logger.error(f"Feishu disconnect failed: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop handler."""
        await self.disconnect()
        return True
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message to Feishu."""
        try:
            # Check if using Webhook mode
            webhook_url = self.config.get("webhook_url")
            if webhook_url:
                return await self._send_via_webhook(outbound, webhook_url)
            
            # Long connection mode - use API
            if not await self._refresh_access_token():
                return SendResult(success=False, error="Failed to get access token")
            
            url = f"{self.FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id"
            
            payload = {
                "receive_id": outbound.chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": outbound.content}),
            }
            
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
            
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(url, json=payload, headers=headers) as response:

                    data = await response.json()
                    if data.get("code") == 0:
                        logger.info(f"Feishu message sent to {outbound.chat_id}")
                        return SendResult(
                            success=True,
                            message_id=data.get("data", {}).get("message_id")
                        )
                    else:
                        return SendResult(
                            success=False,
                            error=f"Feishu API error: {data.get('msg')}"
                        )
                        
        except Exception as e:
            logger.error(f"Failed to send Feishu message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _send_via_webhook(self, outbound: OutboundMessage, webhook_url: str) -> SendResult:
        """Send message via Webhook (custom bot)."""
        try:
            payload = {
                "msg_type": "text",
                "content": {"text": outbound.content}
            }
            
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(webhook_url, json=payload) as response:

                    if response.status == 200:
                        data = await response.json()
                        if data.get("code") == 0 or data.get("StatusCode") == 0:
                            logger.info("Feishu webhook message sent")
                            return SendResult(success=True)
                        else:
                            return SendResult(
                                success=False,
                                error=f"Feishu webhook error: {data.get('msg', data.get('StatusMessage'))}"
                            )
                    else:
                        return SendResult(success=False, error=f"HTTP {response.status}")
        except Exception as e:
            logger.error(f"Failed to send Feishu webhook message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _refresh_access_token(self) -> bool:
        """Refresh Feishu access token."""
        try:
            # Check if token still valid
            if self._access_token and time.time() < self._token_expires_at - 60:
                return True
            
            payload = {
                "app_id": self.config.get("app_id"),
                "app_secret": self.config.get("app_secret"),
            }
            
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(self.AUTH_URL, json=payload) as response:

                    data = await response.json()
                    if data.get("code") == 0:
                        self._access_token = data.get("tenant_access_token")
                        expire = data.get("expire", 7200)
                        self._token_expires_at = time.time() + expire
                        return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to refresh Feishu access token: {e}")
            return False
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming message (for webhook fallback)."""
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request
            
            event_type = data.get("header", {}).get("event_type", "")
            if event_type != "im.message.receive_v1":
                return None
            
            event_data = data.get("event", {})
            message = event_data.get("message", {})
            sender = event_data.get("sender", {})
            
            content = message.get("content", "")
            if isinstance(content, str):
                try:
                    content_obj = json.loads(content)
                    text = content_obj.get("text", "")
                except:
                    text = content
            else:
                text = str(content)
            
            return InboundMessage(
                message_id=message.get("message_id", ""),
                sender_id=sender.get("sender_id", {}).get("open_id", ""),
                sender_name="",
                chat_id=message.get("chat_id", ""),
                channel_type=self.channel_type,
                content=text,
                content_type="text",
                thread_id=None,
                metadata={
                    "chat_type": message.get("chat_type"),
                    "create_time": message.get("create_time"),
                },
            )
        except Exception as e:
            logger.error(f"Failed to handle Feishu inbound: {e}")
            return None
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate configuration."""
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        connection_mode = config.get("connection_mode")
        if not connection_mode:
            connection_mode = "webhook" if config.get("webhook_url") and not config.get("app_id") else "longconnection"
        
        if connection_mode == "longconnection":
            if not config.get("app_id"):
                errors.append("app_id is required for Long Connection mode")
            if not config.get("app_secret"):
                errors.append("app_secret is required for Long Connection mode")
            if not errors and not await self._verify_credentials(config):
                errors.append("Failed to verify Feishu app credentials")
        elif connection_mode == "webhook":
            if not config.get("webhook_url"):
                errors.append("webhook_url is required for Webhook mode")
            if not errors:
                webhook_error = await self._verify_webhook_endpoint(str(config.get("webhook_url", "")))
                if webhook_error:
                    errors.append(webhook_error)
        else:
            errors.append(f"Unsupported connection_mode: {connection_mode}")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return configuration schema."""
        return {
            "type": "object",
            "title": "Feishu",
            "description": "Feishu bot configuration",
            "properties": {
                "connection_mode": {
                    "type": "string",
                    "title": "Connection Mode",
                    "description": "Select connection mode",
                    "enum": ["longconnection", "webhook"],
                    "enumLabels": {
                        "longconnection": "Long Connection (Enterprise App)",
                        "webhook": "Webhook (Custom Bot)"
                    },
                    "default": "longconnection",
                },
                "app_id": {
                    "type": "string",
                    "title": "App ID",
                    "description": "Feishu application App ID",
                    "placeholder": "cli_xxxxxxxxxx",
                    "showWhen": {"connection_mode": "longconnection"},
                },
                "app_secret": {
                    "type": "string",
                    "title": "App Secret",
                    "description": "Feishu application App Secret",
                    "placeholder": "Your app secret",
                    "showWhen": {"connection_mode": "longconnection"},
                },
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "Custom bot Webhook address",
                    "placeholder": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
                    "showWhen": {"connection_mode": "webhook"},
                },
            },
            "required_by_mode": {
                "longconnection": ["app_id", "app_secret"],
                "webhook": ["webhook_url"]
            },
        }
