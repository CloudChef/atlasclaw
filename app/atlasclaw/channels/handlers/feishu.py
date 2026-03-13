# -*- coding: utf-8 -*-
"""Feishu (Lark) channel handler with multiprocessing for SDK isolation."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

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

logger = logging.getLogger(__name__)


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
    
    try:
        print(f"[Feishu SDK Process] Starting with app_id: {app_id}")
        
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
        
        # Start the client (blocking)
        print("[Feishu SDK Process] Connecting...")
        client.start()
        
    except Exception as e:
        print(f"[Feishu SDK Process] Error: {e}")
        control_queue.put({"type": "error", "error": str(e)})


class FeishuHandler(ChannelHandler):
    """Feishu channel handler using multiprocessing for SDK isolation."""
    
    channel_type = "feishu"
    channel_name = "Feishu"
    channel_icon = "feishu"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = False
    
    # Feishu API endpoints
    FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
    AUTH_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    
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
    
    async def connect(self) -> bool:
        """Establish connection using multiprocessing."""
        try:
            app_id = self.config.get("app_id")
            app_secret = self.config.get("app_secret")
            
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
            
            # Wait a bit for connection
            await asyncio.sleep(3)
            
            if self._process.is_alive():
                self._status = ConnectionStatus.CONNECTED
                logger.info("Feishu connected via multiprocessing")
                return True
            else:
                logger.error("Feishu SDK process died")
                self._status = ConnectionStatus.ERROR
                return False
                
        except Exception as e:
            logger.error(f"Feishu connect failed: {e}")
            self._status = ConnectionStatus.ERROR
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
            # Get fresh access token
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
            
            async with aiohttp.ClientSession() as session:
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
            
            async with aiohttp.ClientSession() as session:
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
        
        if not config.get("app_id"):
            errors.append("app_id is required")
        if not config.get("app_secret"):
            errors.append("app_secret is required")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return configuration schema."""
        return {
            "type": "object",
            "title": "Feishu",
            "description": "Feishu bot configuration",
            "required": ["app_id", "app_secret"],
            "properties": {
                "app_id": {
                    "type": "string",
                    "title": "App ID",
                    "description": "Feishu application App ID",
                    "placeholder": "cli_xxxxxxxxxx",
                },
                "app_secret": {
                    "type": "string",
                    "title": "App Secret",
                    "description": "Feishu application App Secret",
                    "placeholder": "Your app secret",
                },
            },
        }
