# -*- coding: utf-8 -*-
"""SSE (Server-Sent Events) channel handler."""

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


class SSEHandler(ChannelHandler):
    """SSE channel handler for server-to-client streaming."""
    
    channel_type = "sse"
    channel_name = "Server-Sent Events"
    channel_icon = "📡"
    channel_mode = ChannelMode.OUTBOUND
    supports_long_connection = True  # SSE is inherently long connection
    supports_webhook = False
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._queues: Dict[str, Any] = {}  # session_id -> asyncio.Queue
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize SSE handler.
        
        Args:
            connection_config: Configuration with headers, retry settings
            
        Returns:
            True if setup successful
        """
        try:
            self.config.update(connection_config)
            return True
        except Exception as e:
            logger.error(f"SSE setup failed: {e}")
            return False
    
    async def start(self, context: Any) -> bool:
        """Start SSE channel.
        
        Args:
            context: Application context
            
        Returns:
            True if started successfully
        """
        try:
            self._status = ConnectionStatus.CONNECTED
            logger.info("SSE channel started")
            return True
        except Exception as e:
            logger.error(f"SSE start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def stop(self) -> bool:
        """Stop SSE channel.
        
        Returns:
            True if stopped successfully
        """
        try:
            self._queues.clear()
            self._status = ConnectionStatus.DISCONNECTED
            return True
        except Exception as e:
            logger.error(f"SSE stop failed: {e}")
            return False
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming SSE request (not applicable for outbound-only channel).
        
        Args:
            request: Request data
            
        Returns:
            None (SSE is outbound-only)
        """
        # SSE is outbound-only, does not receive messages
        logger.warning("SSE channel does not support inbound messages")
        return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via SSE.
        
        Args:
            outbound: Outbound message
            
        Returns:
            SendResult with success status
        """
        try:
            queue = self._queues.get(outbound.chat_id)
            if not queue:
                return SendResult(
                    success=False,
                    error=f"No SSE connection for session: {outbound.chat_id}"
                )
            
            event_data = {
                "chat_id": outbound.chat_id,
                "content": outbound.content,
                "content_type": outbound.content_type,
                "thread_id": outbound.thread_id,
                "reply_to": outbound.reply_to,
                "metadata": outbound.metadata,
            }
            
            await queue.put(json.dumps(event_data))
            return SendResult(success=True, message_id=outbound.chat_id)
            
        except Exception as e:
            logger.error(f"Failed to send SSE message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate SSE configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            Validation result
        """
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        return ChannelValidationResult(valid=True, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return SSE configuration schema.
        
        Returns:
            JSON Schema
        """
        return {
            "type": "object",
            "title": "SSE Configuration",
            "description": "Server-Sent Events channel configuration",
            "properties": {
                "retry": {
                    "type": "integer",
                    "title": "Retry Interval",
                    "description": "Reconnection time in milliseconds",
                    "default": 3000
                }
            }
        }
    
    def register_queue(self, session_id: str, queue: Any) -> None:
        """Register an SSE message queue.
        
        Args:
            session_id: Session identifier
            queue: asyncio.Queue for messages
        """
        self._queues[session_id] = queue
    
    def unregister_queue(self, session_id: str) -> None:
        """Unregister an SSE message queue.
        
        Args:
            session_id: Session identifier
        """
        self._queues.pop(session_id, None)
