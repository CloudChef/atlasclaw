# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""DingTalk channel handler with Stream mode support."""

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
from urllib.parse import urlparse

import aiohttp

from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)
from app.atlasclaw.channels.qr_provisioning import (
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


def _run_dingtalk_sdk_process(
    client_id: str,
    client_secret: str,
    message_queue: multiprocessing.Queue,
    control_queue: multiprocessing.Queue,
):
    """Run DingTalk SDK in a separate process to avoid event loop conflicts.
    
    This function runs in a subprocess with its own event loop,
    completely isolated from FastAPI's asyncio loop.
    """
    import os
    import logging
    import threading
    import dingtalk_stream
    from dingtalk_stream import AckMessage
    
    # Flag to track if connection signal has been sent
    connection_signaled = False
    
    # 默认支持环境代理（HTTP_PROXY/HTTPS_PROXY）。
    # 如需强制绕过 WebSocket 代理，可设置: ATLASCLAW_BYPASS_WS_PROXY=true
    bypass_ws_proxy = os.getenv("ATLASCLAW_BYPASS_WS_PROXY", "").lower() in {"1", "true", "yes", "on"}
    if bypass_ws_proxy:
        proxy_vars = [
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
            "WS_PROXY", "ws_proxy", "WSS_PROXY", "wss_proxy",
        ]
        for key in proxy_vars:
            os.environ.pop(key, None)

        try:
            import websockets.asyncio.client as ws_client
            ws_client.get_proxy = lambda uri: None
            print("[DingTalk SDK Process] WebSocket proxy bypass enabled")
        except Exception as e:
            print(f"[DingTalk SDK Process] Warning: Could not patch websockets: {e}")
    else:
        print("[DingTalk SDK Process] Using environment proxy settings for outbound network access")

    
    # Setup logging for subprocess
    logging.basicConfig(
        level=logging.INFO,
        format='[DingTalk SDK Process] %(message)s'
    )
    proc_logger = logging.getLogger("dingtalk_sdk_process")
    
    print(f"[DingTalk SDK Process] Starting with client_id: {client_id}")
    
    def send_connected_signal():
        """Send connected signal after delay if process is still running."""
        nonlocal connection_signaled
        if not connection_signaled:
            connection_signaled = True
            print("[DingTalk SDK Process] Connection established, sending signal")
            control_queue.put({"type": "connected"})
    
    class MessageHandler(dingtalk_stream.ChatbotHandler):
        """Handler for incoming DingTalk messages."""
        
        def __init__(self, msg_queue: multiprocessing.Queue, logger: logging.Logger = None):
            super().__init__()  # Properly initialize ChatbotHandler base class
            self._msg_queue = msg_queue
            if logger:
                self.logger = logger
        
        async def process(self, callback: dingtalk_stream.CallbackMessage):
            """Convert a DingTalk SDK callback into AtlasClaw message data."""
            try:
                incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                
                # Extract message content
                content = incoming_message.text.content.strip() if incoming_message.text else ""
                
                msg_data = {
                    "message_id": callback.headers.message_id or "",
                    "sender_id": incoming_message.sender_staff_id or incoming_message.sender_id or "",
                    "sender_name": incoming_message.sender_nick or "Anonymous",
                    "chat_id": incoming_message.conversation_id or "",
                    "content": content,
                    "content_type": "text",
                    "conversation_type": incoming_message.conversation_type,
                    "chatbot_user_id": incoming_message.chatbot_user_id,
                    "session_webhook": incoming_message.session_webhook,
                    "session_webhook_expired_time": incoming_message.session_webhook_expired_time,
                    "raw_data": callback.data,
                }
                
                print(f"[DingTalk SDK Process] Message received: {content[:30]}...")
                self._msg_queue.put(msg_data)
                
                return AckMessage.STATUS_OK, 'OK'
            except Exception as e:
                print(f"[DingTalk SDK Process] Error processing message: {e}")
                return AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)
    
    try:
        print(f"[DingTalk SDK Process] Connecting to DingTalk Stream...")
        
        credential = dingtalk_stream.Credential(client_id, client_secret)
        client = dingtalk_stream.DingTalkStreamClient(credential)
        client.register_callback_handler(
            dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
            MessageHandler(message_queue, proc_logger)
        )
        
        # Use timer to send connected signal after delay
        # If start_forever() throws exception before timer fires, the signal won't be sent
        timer = threading.Timer(5.0, send_connected_signal)
        timer.daemon = True
        timer.start()
        
        # start_forever runs the event loop
        client.start_forever()
        
    except KeyboardInterrupt:
        print(f"[DingTalk SDK Process] Interrupted")
    except Exception as e:
        print(f"[DingTalk SDK Process] Connection error: {e}")
        proc_logger.exception("DingTalk SDK connection error")
        if not connection_signaled:
            control_queue.put({"type": "error", "error": str(e)})


class DingTalkHandler(ChannelHandler):
    """DingTalk channel handler with Stream mode support.
    
    Uses multiprocessing to run the DingTalk SDK in a separate process,
    avoiding event loop conflicts with FastAPI.
    
    Supports:
    - Stream mode (bidirectional, long connection)
    - Webhook mode (outbound only)
    """
    
    channel_type = "dingtalk"
    channel_name = "DingTalk"
    channel_icon = "dingtalk"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = True
    supports_provisioning = True
    provisioning_default_mode = "qr"
    provisioning_manual_config_available = True
    provisioning_instructions_i18n_key = "channel.provisioning.dingtalk.instructions"
    
    # DingTalk API endpoints
    DINGTALK_API_BASE = "https://api.dingtalk.com"
    OAPI_BASE = "https://oapi.dingtalk.com"
    REGISTRATION_BASE_URL = "https://oapi.dingtalk.com"
    REGISTRATION_SOURCE = "DING_DWS_CLAW"
    REGISTRATION_TIMEOUT_SECONDS = 8
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._process: Optional[multiprocessing.Process] = None
        self._message_queue: Optional[multiprocessing.Queue] = None
        self._control_queue: Optional[multiprocessing.Queue] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_message_callback: Optional[Callable] = None
        self._access_token: Optional[str] = None
        self._token_expires: int = 0

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        """Create DingTalk OpenClaw registration QR details."""
        del request
        base_url = self._registration_base_url()
        nonce = await self._init_registration(base_url)
        begin = await self._begin_registration(base_url, nonce=nonce)

        device_code = str(begin.get("device_code") or "").strip()
        user_code = str(begin.get("user_code") or "").strip()
        verification_uri = str(begin.get("verification_uri_complete") or "").strip()
        if not device_code:
            raise ValueError("DingTalk registration response missing device_code")
        if not user_code:
            raise ValueError("DingTalk registration response missing user_code")
        if not verification_uri:
            raise ValueError("DingTalk registration response missing verification_uri_complete")

        interval = parse_positive_int(begin.get("interval"), default=5)
        expire_in = parse_positive_int(begin.get("expires_in"), default=7200)
        return ChannelProvisioningStart(
            qr_url=verification_uri,
            qr_image_url=build_qr_image_data_url(verification_uri),
            user_code=user_code,
            platform_state={
                "device_code": device_code,
                "base_url": base_url,
                "interval": interval,
                "expire_in": expire_in,
            },
            expires_at=utcnow() + timedelta(seconds=expire_in),
            refresh_after_seconds=interval,
            instructions_i18n_key=self.provisioning_instructions_i18n_key,
        )

    @classmethod
    def _registration_base_url(cls) -> str:
        """Return the DingTalk registration API base URL."""
        return (
            os.getenv("ATLASCLAW_DINGTALK_REGISTRATION_BASE_URL")
            or cls.REGISTRATION_BASE_URL
        ).rstrip("/")

    @classmethod
    def _registration_source(cls) -> str:
        """Return the DingTalk OpenClaw registration source identifier."""
        return (
            os.getenv("ATLASCLAW_DINGTALK_REGISTRATION_SOURCE")
            or cls.REGISTRATION_SOURCE
        ).strip()

    async def _post_registration(
        self,
        base_url: str,
        path: str,
        body: Dict[str, Any],
    ) -> Dict[str, Any]:
        """POST a DingTalk registration request and return the JSON body."""
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                f"{base_url}{path}",
                json=body,
                timeout=aiohttp.ClientTimeout(total=self.REGISTRATION_TIMEOUT_SECONDS),
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception as exc:
                    text = await response.text()
                    raise ValueError(f"Invalid DingTalk registration response: {text[:200]}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Invalid DingTalk registration response")
        errcode = payload.get("errcode")
        if errcode not in {0, "0", None}:
            errmsg = payload.get("errmsg") or "unknown error"
            raise ValueError(f"DingTalk registration failed: {errmsg} (errcode={errcode})")
        return payload

    async def _init_registration(self, base_url: str) -> str:
        """Initialize DingTalk registration and return its nonce."""
        payload = await self._post_registration(
            base_url,
            "/app/registration/init",
            {"source": self._registration_source()},
        )
        nonce = str(payload.get("nonce") or "").strip()
        if not nonce:
            raise ValueError("DingTalk registration response missing nonce")
        return nonce

    async def _begin_registration(
        self,
        base_url: str,
        *,
        nonce: str,
    ) -> Dict[str, Any]:
        """Begin DingTalk registration and return device-code details."""
        return await self._post_registration(
            base_url,
            "/app/registration/begin",
            {"nonce": nonce},
        )

    async def _poll_registration(self, base_url: str, *, device_code: str) -> Dict[str, Any]:
        """Poll DingTalk registration once."""
        return await self._post_registration(
            base_url,
            "/app/registration/poll",
            {"device_code": device_code},
        )

    async def poll_provisioning_connection(
        self,
        session: ChannelProvisioningSession,
    ) -> Optional[ChannelProvisioningConnection]:
        """Poll DingTalk registration and return stream credentials when ready."""
        state = dict(session.platform_state or {})
        device_code = str(state.get("device_code") or "").strip()
        if not device_code:
            raise ValueError("DingTalk registration device_code is missing")

        if mark_provisioning_poll_attempt(session, default_interval_seconds=5) is None:
            return None
        state = dict(session.platform_state or {})

        try:
            response = await self._poll_registration(
                str(state.get("base_url") or self._registration_base_url()).rstrip("/"),
                device_code=device_code,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Transient DingTalk registration poll failure: %s", exc)
            return None

        status = str(response.get("status") or "").strip().upper()
        if status == "SUCCESS":
            client_id = response.get("client_id")
            client_secret = response.get("client_secret")
            if not client_id:
                raise ValueError("client_id is required")
            if not client_secret:
                raise ValueError("client_secret is required")
            return ChannelProvisioningConnection(
                name="DingTalk Bot",
                config={
                    "connection_mode": "stream",
                    "client_id": str(client_id),
                    "client_secret": str(client_secret),
                },
            )
        if status == "WAITING":
            session.status = "authorizing"
            session.error = None
            session.updated_at = utcnow()
            return None
        if status == "EXPIRED":
            session.expires_at = utcnow()
            session.error = "DingTalk registration expired"
            session.updated_at = utcnow()
            return None
        if status == "FAIL":
            session.status = "failed"
            session.error = str(response.get("fail_reason") or "DingTalk registration failed")
            session.updated_at = utcnow()
            return None
        if status:
            session.status = "failed"
            session.error = f"DingTalk registration returned unknown status: {status}"
            session.updated_at = utcnow()
        return None
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize DingTalk handler with configuration."""
        try:
            self.config.update(connection_config)
            return True
        except Exception as e:
            logger.error(f"[DingTalk] Setup failed: {e}")
            return False
    
    async def start(self, context: Any) -> bool:
        """Start DingTalk handler."""
        try:
            self._status = ConnectionStatus.CONNECTING
            logger.info("[DingTalk] Handler started")
            return True
        except Exception as e:
            logger.error(f"[DingTalk] Start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    @staticmethod
    def _looks_like_http_url(url: str) -> bool:
        """Check whether the webhook URL is a valid HTTP(S) address."""
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _verify_credentials(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Verify DingTalk credentials by calling the gettoken API.
        
        This validates the client_id/client_secret before starting the SDK subprocess.
        Returns True if credentials are valid, False otherwise.
        """
        verify_config = config or self.config
        client_id = verify_config.get("client_id") or verify_config.get("app_key")
        client_secret = verify_config.get("client_secret") or verify_config.get("app_secret")
        
        if not client_id or not client_secret:
            logger.error("[DingTalk] Missing client_id or client_secret")
            return False
        
        try:
            url = f"{self.OAPI_BASE}/gettoken?appkey={client_id}&appsecret={client_secret}"
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS),
                ) as resp:
                    data = await resp.json()
                    if data.get("errcode") == 0 and data.get("access_token"):
                        logger.info("[DingTalk] Credentials verified successfully")
                        return True
                    else:
                        logger.error(f"[DingTalk] Credential verification failed: {data.get('errmsg', 'unknown error')}")
                        return False
        except Exception as e:
            logger.error(f"[DingTalk] Credential verification error: {e}")
            return False

    async def _verify_webhook_endpoint(self, webhook_url: str, secret: Optional[str] = None) -> Optional[str]:
        """Perform static validation for the DingTalk webhook URL."""
        if not self._looks_like_http_url(webhook_url):
            return "webhook_url must be a valid HTTP/HTTPS URL"
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https":
            return "webhook_url must use HTTPS"
        if parsed.username or parsed.password:
            return "webhook_url must not include credentials"
        return None

    async def _verify_credentials_for_connect(self) -> bool:
        """Verify credentials with short retry before opening the stream connection."""
        verify_attempts = 3
        retry_delay_seconds = 0.8
        for attempt in range(1, verify_attempts + 1):
            if await self._verify_credentials():
                return True
            if attempt < verify_attempts:
                logger.warning(
                    "[DingTalk] Credential verification failed during connect "
                    "(attempt %s/%s), retrying",
                    attempt,
                    verify_attempts,
                )
                await asyncio.sleep(retry_delay_seconds * attempt)
        return False

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
        """Connect to DingTalk using Stream mode or prepare for webhook mode."""
        try:
            client_id = self.config.get("client_id") or self.config.get("app_key")
            client_secret = self.config.get("client_secret") or self.config.get("app_secret")
            webhook_url = self.config.get("webhook_url")
            
            # If only webhook URL is provided, use webhook mode (no long connection)
            if webhook_url and not client_id:
                logger.info("[DingTalk] Using webhook mode (outbound only)")
                self._status = ConnectionStatus.CONNECTED
                return True
            
            # Use Stream mode with multiprocessing
            if client_id and client_secret:
                # Pre-verify credentials before starting SDK subprocess
                if not await self._verify_credentials_for_connect():
                    self._status = ConnectionStatus.ERROR
                    return False
                logger.info(f"[DingTalk] Connecting with client_id: {client_id}")
                
                self._message_queue = multiprocessing.Queue()
                self._control_queue = multiprocessing.Queue()
                
                self._process = multiprocessing.Process(
                    target=_run_dingtalk_sdk_process,
                    args=(client_id, client_secret, self._message_queue, self._control_queue),
                    daemon=True,
                )
                self._process.start()
                logger.info(f"[DingTalk] SDK process started (PID: {self._process.pid})")
                
                # Start message listener thread
                self._running = True
                self._listener_thread = threading.Thread(
                    target=self._listen_for_messages,
                    daemon=True,
                )
                self._listener_thread.start()
                logger.info("[DingTalk] Message listener started")
                
                # Wait for connection result from subprocess via control_queue
                # Timeout: 15 seconds (subprocess sends signal after 5 seconds if successful)
                connection_timeout = 15.0
                start_time = time.time()
                
                while time.time() - start_time < connection_timeout:
                    # Check if process died
                    if not self._process.is_alive():
                        logger.error("[DingTalk] SDK process died unexpectedly")
                        self._status = ConnectionStatus.ERROR
                        await self._cleanup_connect_failure()
                        return False
                    
                    # Try to get message from control queue (non-blocking)
                    try:
                        msg = self._control_queue.get_nowait()
                        if msg.get("type") == "connected":
                            self._status = ConnectionStatus.CONNECTED
                            logger.info("[DingTalk] Connected via Stream mode")
                            return True
                        elif msg.get("type") == "error":
                            error = msg.get("error", "Unknown error")
                            logger.error(f"[DingTalk] Connection failed: {error}")
                            self._status = ConnectionStatus.ERROR
                            await self._cleanup_connect_failure()
                            return False
                    except queue.Empty:
                        await asyncio.sleep(0.5)
                        continue
                
                # Timeout reached
                logger.error("[DingTalk] Connection timeout")
                self._status = ConnectionStatus.ERROR
                await self._cleanup_connect_failure()
                return False
            
            logger.error("[DingTalk] No valid configuration (need client_id/client_secret or webhook_url)")
            return False
            
        except Exception as e:
            logger.error(f"[DingTalk] Connect failed: {e}")
            self._status = ConnectionStatus.ERROR
            await self._cleanup_connect_failure()
            return False
    
    def _listen_for_messages(self):
        """Listen for messages from the SDK subprocess."""
        import queue as queue_module
        
        while self._running:
            try:
                if not self._message_queue:
                    time.sleep(0.1)
                    continue
                
                try:
                    msg_data = self._message_queue.get(timeout=0.5)
                except queue_module.Empty:
                    continue
                
                self._handle_incoming_message(msg_data)
                
            except Exception as e:
                if self._running:
                    logger.error(f"[DingTalk] Listener error: {e}")
                time.sleep(0.5)
    
    def _handle_incoming_message(self, msg_data: Dict[str, Any]):
        """Handle incoming message from SDK subprocess."""
        try:
            message = InboundMessage(
                message_id=msg_data.get("message_id", ""),
                sender_id=msg_data.get("sender_id", ""),
                sender_name=msg_data.get("sender_name", "Anonymous"),
                chat_id=msg_data.get("chat_id", ""),
                channel_type=self.channel_type,
                content=msg_data.get("content", ""),
                content_type=msg_data.get("content_type", "text"),
                metadata={
                    "conversation_type": msg_data.get("conversation_type"),
                    "chatbot_user_id": msg_data.get("chatbot_user_id"),
                    "session_webhook": msg_data.get("session_webhook"),
                    "session_webhook_expired_time": msg_data.get("session_webhook_expired_time"),
                    "raw_data": msg_data.get("raw_data"),
                },
            )
            
            if self._on_message_callback:
                self._on_message_callback(message)
                
        except Exception as e:
            logger.error(f"[DingTalk] Error handling message: {e}")
    
    def set_message_callback(self, callback: Callable[[InboundMessage], None]):
        """Set callback for incoming messages."""
        self._on_message_callback = callback
    
    async def disconnect(self) -> bool:
        """Disconnect from DingTalk."""
        self._running = False
        
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            logger.info("[DingTalk] SDK process terminated")
        
        self._status = ConnectionStatus.DISCONNECTED
        return True
    
    async def stop(self) -> bool:
        """Stop DingTalk handler."""
        await self.disconnect()
        return True
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming DingTalk message (webhook callback mode)."""
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request
            
            msg_type = data.get("msgtype", "")
            
            if msg_type == "text":
                content = data.get("text", {}).get("content", "")
            else:
                content = json.dumps(data.get(msg_type, {}))
            
            sender_info = data.get("senderStaffId", "") or data.get("senderId", "")
            
            return InboundMessage(
                message_id=data.get("msgId", ""),
                sender_id=sender_info,
                sender_name=data.get("senderNick", "Anonymous"),
                chat_id=data.get("conversationId", ""),
                channel_type=self.channel_type,
                content=content,
                content_type="text",
                metadata={
                    "msgtype": msg_type,
                    "chatbotUserId": data.get("chatbotUserId"),
                    "conversationType": data.get("conversationType"),
                },
            )
        except Exception as e:
            logger.error(f"[DingTalk] Failed to handle message: {e}")
            return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message to DingTalk.
        
        Uses session webhook from the incoming message metadata if available,
        otherwise falls back to webhook URL from config.
        """
        try:
            # Try session webhook first (from Stream mode message)
            session_webhook = outbound.metadata.get("session_webhook") if outbound.metadata else None
            webhook_url = self.config.get("webhook_url")
            
            if session_webhook:
                return await self._send_via_session_webhook(outbound, session_webhook)
            elif webhook_url:
                return await self._send_via_webhook(outbound, webhook_url)
            else:
                # Try to get access token and send via API
                client_id = self.config.get("client_id") or self.config.get("app_key")
                client_secret = self.config.get("client_secret") or self.config.get("app_secret")
                
                if client_id and client_secret:
                    return await self._send_via_api(outbound)
                
                return SendResult(success=False, error="No valid send method available")
                
        except Exception as e:
            logger.error(f"[DingTalk] Failed to send message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _send_via_session_webhook(self, outbound: OutboundMessage, webhook_url: str) -> SendResult:
        """Send message via session webhook (from Stream mode)."""
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
                            error=f"DingTalk error: {data.get('errmsg')}"
                        )
                else:
                    return SendResult(success=False, error=f"HTTP {response.status}")
    
    async def _send_via_webhook(self, outbound: OutboundMessage, webhook_url: str) -> SendResult:
        """Send message via custom webhook."""
        import hashlib
        import hmac
        import base64
        from urllib.parse import quote_plus
        
        secret = self.config.get("secret")
        
        # Add signature if secret is configured
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
            sign = quote_plus(base64.b64encode(hmac_code).decode("utf-8"))
            webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
        
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
                            error=f"DingTalk error: {data.get('errmsg')}"
                        )
                else:
                    return SendResult(success=False, error=f"HTTP {response.status}")
    
    async def _send_via_api(self, outbound: OutboundMessage) -> SendResult:
        """Send message via DingTalk API (requires access token)."""
        # For now, return not implemented
        # Full implementation would need robot_code and user_ids
        return SendResult(success=False, error="API send not implemented, use session webhook")
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate DingTalk configuration."""
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        connection_mode = config.get("connection_mode")
        if not connection_mode:
            connection_mode = "stream" if (config.get("client_id") or config.get("app_key")) else "webhook"
        
        if connection_mode == "webhook":
            if not config.get("webhook_url"):
                errors.append("webhook_url is required for Webhook mode")
            if not errors:
                webhook_error = await self._verify_webhook_endpoint(
                    str(config.get("webhook_url", "")),
                    config.get("secret"),
                )
                if webhook_error:
                    errors.append(webhook_error)
        elif connection_mode == "stream":
            client_id = config.get("client_id") or config.get("app_key")
            client_secret = config.get("client_secret") or config.get("app_secret")
            if not client_id:
                errors.append("client_id/app_key is required for Stream mode")
            if not client_secret:
                errors.append("client_secret/app_secret is required for Stream mode")
            if not errors and not await self._verify_credentials(config):
                errors.append("Failed to verify DingTalk stream credentials")
        else:
            errors.append(f"Unsupported connection_mode: {connection_mode}")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return DingTalk configuration schema."""
        return {
            "type": "object",
            "title": "DingTalk",
            "description": "DingTalk bot configuration",
            "properties": {
                "connection_mode": {
                    "type": "string",
                    "title": "Connection Mode",
                    "description": "Select connection mode",
                    "enum": ["stream", "webhook"],
                    "enumLabels": {
                        "stream": "Stream Mode (Enterprise Bot)",
                        "webhook": "Webhook Robot"
                    },
                    "default": "stream",
                },
                "client_id": {
                    "type": "string",
                    "title": "Client ID (AppKey)",
                    "description": "Application AppKey for Stream mode",
                    "placeholder": "dingxxxxxxxxxx",
                    "showWhen": {"connection_mode": "stream"},
                },
                "client_secret": {
                    "type": "string",
                    "title": "Client Secret (AppSecret)",
                    "description": "Application AppSecret",
                    "placeholder": "Application secret",
                    "showWhen": {"connection_mode": "stream"},
                },
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "Custom bot Webhook address",
                    "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
                    "showWhen": {"connection_mode": "webhook"},
                },
                "secret": {
                    "type": "string",
                    "title": "Signing Secret",
                    "description": "Webhook signing secret (optional)",
                    "placeholder": "SEC...",
                    "showWhen": {"connection_mode": "webhook"},
                },
            },
            "required_by_mode": {
                "stream": ["client_id", "client_secret"],
                "webhook": ["webhook_url"]
            },
        }
