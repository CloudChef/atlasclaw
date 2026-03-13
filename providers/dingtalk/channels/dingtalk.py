# -*- coding: utf-8 -*-
"""DingTalk channel handler."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)

logger = logging.getLogger(__name__)


class DingTalkHandler(ChannelHandler):
    """DingTalk channel handler for enterprise bot integration.
    
    Supports both custom webhook robot and enterprise internal bot.
    
    Webhook Robot:
    - Simple outbound-only messaging via webhook URL
    - Optional signature verification
    
    Enterprise Internal Bot:
    - Full bidirectional messaging
    - Requires App Key and App Secret
    - Supports message callback
    """
    
    channel_type = "dingtalk"
    channel_name = "DingTalk"
    channel_icon = "dingtalk"  # Brand identifier
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = False
    supports_webhook = True
    
    # DingTalk API endpoints
    API_BASE = "https://oapi.dingtalk.com"
    TOKEN_URL = f"{API_BASE}/gettoken"
    SEND_MSG_URL = f"{API_BASE}/robot/send"
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._access_token: Optional[str] = None
        self._token_expires: int = 0
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize DingTalk handler with configuration.
        
        Args:
            connection_config: Configuration with webhook_url or app_key/app_secret
            
        Returns:
            True if setup successful
        """
        try:
            self.config.update(connection_config)
            
            # Check if using webhook mode or enterprise bot mode
            webhook_url = self.config.get("webhook_url")
            app_key = self.config.get("app_key")
            
            if not webhook_url and not app_key:
                logger.error("DingTalk requires either webhook_url or app_key")
                return False
            
            return True
        except Exception as e:
            logger.error(f"DingTalk setup failed: {e}")
            return False
    
    async def start(self, context: Any) -> bool:
        """Start DingTalk handler."""
        try:
            self._status = ConnectionStatus.CONNECTED
            logger.info("DingTalk handler started")
            return True
        except Exception as e:
            logger.error(f"DingTalk start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def connect(self) -> bool:
        """Connect to DingTalk (get access token for enterprise bot)."""
        try:
            app_key = self.config.get("app_key")
            app_secret = self.config.get("app_secret")
            
            if app_key and app_secret:
                if not await self._get_access_token():
                    logger.error("Failed to get DingTalk access token")
                    return False
            
            self._status = ConnectionStatus.CONNECTED
            return True
        except Exception as e:
            logger.error(f"DingTalk connect failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def disconnect(self) -> bool:
        """Disconnect from DingTalk."""
        self._access_token = None
        self._status = ConnectionStatus.DISCONNECTED
        return True
    
    async def stop(self) -> bool:
        """Stop DingTalk handler."""
        await self.disconnect()
        return True
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming DingTalk message callback.
        
        Args:
            request: DingTalk callback data
            
        Returns:
            Standardized InboundMessage
        """
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request
            
            msg_type = data.get("msgtype", "")
            
            # Handle text message
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
            logger.error(f"Failed to handle DingTalk message: {e}")
            return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message to DingTalk.
        
        Supports both webhook and enterprise bot modes.
        """
        try:
            import aiohttp
            
            webhook_url = self.config.get("webhook_url")
            
            if webhook_url:
                # Webhook mode - simple robot
                return await self._send_webhook_message(outbound)
            else:
                # Enterprise bot mode
                return await self._send_enterprise_message(outbound)
                
        except Exception as e:
            logger.error(f"Failed to send DingTalk message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def _send_webhook_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via webhook."""
        import aiohttp
        
        webhook_url = self.config.get("webhook_url")
        secret = self.config.get("secret")
        
        # Add signature if secret is configured
        if secret:
            timestamp = str(round(time.time() * 1000))
            sign = self._generate_sign(timestamp, secret)
            webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
        
        payload = {
            "msgtype": "text",
            "text": {"content": outbound.content}
        }
        
        async with aiohttp.ClientSession() as session:
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
    
    async def _send_enterprise_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via enterprise bot API."""
        # Enterprise bot message sending would be implemented here
        return SendResult(success=False, error="Enterprise bot not implemented yet")
    
    def _generate_sign(self, timestamp: str, secret: str) -> str:
        """Generate signature for webhook."""
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        return quote_plus(base64.b64encode(hmac_code).decode("utf-8"))
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate DingTalk configuration."""
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        webhook_url = config.get("webhook_url")
        app_key = config.get("app_key")
        
        if not webhook_url and not app_key:
            errors.append("Either webhook_url or app_key is required")
        
        if app_key and not config.get("app_secret"):
            errors.append("app_secret is required when using app_key")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return DingTalk configuration schema."""
        return {
            "type": "object",
            "title": "DingTalk",
            "description": "DingTalk bot configuration (Webhook URL or App Key required)",
            "oneOf_hint": "webhook_url or app_key",
            "properties": {
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "Custom bot Webhook URL (or use App Key)",
                    "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
                },
                "secret": {
                    "type": "string",
                    "title": "Secret",
                    "description": "Signing secret for security verification (optional)",
                    "placeholder": "SEC...",
                },
                "app_key": {
                    "type": "string",
                    "title": "AppKey",
                    "description": "Enterprise bot AppKey (or use Webhook)",
                    "placeholder": "dingxxxxxxxxxx",
                },
                "app_secret": {
                    "type": "string",
                    "title": "AppSecret",
                    "description": "Enterprise bot AppSecret (required with AppKey)",
                    "placeholder": "App secret",
                },
            },
        }
    
    async def _get_access_token(self) -> bool:
        """Get DingTalk access token for enterprise bot."""
        try:
            import aiohttp
            
            if self._access_token and time.time() < self._token_expires:
                return True
            
            params = {
                "appkey": self.config.get("app_key"),
                "appsecret": self.config.get("app_secret"),
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.TOKEN_URL, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("errcode") == 0:
                            self._access_token = data.get("access_token")
                            self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                            return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to get DingTalk access token: {e}")
            return False
