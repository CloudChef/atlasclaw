# -*- coding: utf-8 -*-
"""REST channel handler."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

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


class RESTHandler(ChannelHandler):
    """REST channel handler for HTTP-based communication."""
    
    channel_type = "rest"
    channel_name = "REST API"
    channel_icon = "🌐"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = False  # REST is request/response
    supports_webhook = True
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._webhook_url: Optional[str] = None
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize REST handler.
        
        Args:
            connection_config: Configuration with webhook_url, headers
            
        Returns:
            True if setup successful
        """
        try:
            self.config.update(connection_config)
            self._webhook_url = connection_config.get("webhook_url")
            return True
        except Exception as e:
            logger.error(f"REST setup failed: {e}")
            return False
    
    async def start(self, context: Any) -> bool:
        """Start REST channel.
        
        Args:
            context: Application context
            
        Returns:
            True if started successfully
        """
        try:
            self._status = ConnectionStatus.CONNECTED
            logger.info("REST channel started")
            return True
        except Exception as e:
            logger.error(f"REST start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def stop(self) -> bool:
        """Stop REST channel.
        
        Returns:
            True if stopped successfully
        """
        try:
            self._status = ConnectionStatus.DISCONNECTED
            return True
        except Exception as e:
            logger.error(f"REST stop failed: {e}")
            return False
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming REST request.
        
        Args:
            request: Request data (dict with headers, body, etc.)
            
        Returns:
            Standardized InboundMessage
        """
        try:
            if isinstance(request, dict):
                data = request.get("body", request)
            else:
                data = json.loads(request)
            
            return InboundMessage(
                message_id=data.get("message_id", ""),
                sender_id=data.get("sender_id", ""),
                sender_name=data.get("sender_name", "Anonymous"),
                chat_id=data.get("chat_id", data.get("sender_id", "")),
                channel_type=self.channel_type,
                content=data.get("content", ""),
                content_type=data.get("content_type", "text"),
                thread_id=data.get("thread_id"),
                reply_to=data.get("reply_to"),
                metadata=data.get("metadata", {}),
            )
        except Exception as e:
            logger.error(f"Failed to handle REST message: {e}")
            return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via REST webhook.
        
        Args:
            outbound: Outbound message
            
        Returns:
            SendResult with success status
        """
        try:
            import aiohttp
            
            if not self._webhook_url:
                return SendResult(
                    success=False,
                    error="Webhook URL not configured"
                )
            
            payload = {
                "chat_id": outbound.chat_id,
                "content": outbound.content,
                "content_type": outbound.content_type,
                "thread_id": outbound.thread_id,
                "reply_to": outbound.reply_to,
                "metadata": outbound.metadata,
            }
            
            headers = self.config.get("headers", {})
            
            async with aiohttp.ClientSession(trust_env=True) as session:

                async with session.post(
                    self._webhook_url,
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        return SendResult(success=True)
                    else:
                        return SendResult(
                            success=False,
                            error=f"HTTP {response.status}"
                        )
                        
        except Exception as e:
            logger.error(f"Failed to send REST message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate REST configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            Validation result
        """
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        webhook_url = config.get("webhook_url")
        if webhook_url and not isinstance(webhook_url, str):
            errors.append("webhook_url must be a string")
        
        return ChannelValidationResult(valid=len(errors) == 0, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return REST configuration schema.
        
        Returns:
            JSON Schema
        """
        return {
            "type": "object",
            "title": "REST Configuration",
            "description": "REST API channel configuration",
            "properties": {
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "URL to send outgoing messages",
                    "format": "uri"
                },
                "headers": {
                    "type": "object",
                    "title": "HTTP Headers",
                    "description": "Additional HTTP headers",
                    "additionalProperties": {"type": "string"}
                }
            }
        }
