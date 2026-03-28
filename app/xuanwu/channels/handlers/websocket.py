# -*- coding: utf-8 -*-
"""WebSocket channel handler."""

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


class WebSocketHandler(ChannelHandler):
    """WebSocket channel handler for bidirectional communication."""
    
    channel_type = "websocket"
    channel_name = "WebSocket"
    channel_icon = "🔌"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True  # WebSocket is inherently long connection
    supports_webhook = False
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._connections: Dict[str, Any] = {}  # session_id -> websocket
    
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize WebSocket handler.
        
        Args:
            connection_config: Configuration with host, port, path
            
        Returns:
            True if setup successful
        """
        try:
            self.config.update(connection_config)
            return True
        except Exception as e:
            logger.error(f"WebSocket setup failed: {e}")
            return False
    
    async def start(self, context: Any) -> bool:
        """Start WebSocket server.
        
        Args:
            context: Application context with FastAPI app
            
        Returns:
            True if started successfully
        """
        try:
            self._status = ConnectionStatus.CONNECTED
            logger.info("WebSocket channel started")
            return True
        except Exception as e:
            logger.error(f"WebSocket start failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False
    
    async def stop(self) -> bool:
        """Stop WebSocket server and close connections.
        
        Returns:
            True if stopped successfully
        """
        try:
            # Close all active connections
            for session_id, ws in self._connections.items():
                try:
                    await ws.close()
                except Exception:
                    pass
            self._connections.clear()
            self._status = ConnectionStatus.DISCONNECTED
            return True
        except Exception as e:
            logger.error(f"WebSocket stop failed: {e}")
            return False
    
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming WebSocket message.
        
        Args:
            request: WebSocket message data
            
        Returns:
            Standardized InboundMessage
        """
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request
            
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
            logger.error(f"Failed to handle WebSocket message: {e}")
            return None
    
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message via WebSocket.
        
        Args:
            outbound: Outbound message
            
        Returns:
            SendResult with success status
        """
        try:
            # Find connection by chat_id (which is session_id for WebSocket)
            ws = self._connections.get(outbound.chat_id)
            if not ws:
                return SendResult(
                    success=False,
                    error=f"No WebSocket connection for session: {outbound.chat_id}"
                )
            
            message = {
                "chat_id": outbound.chat_id,
                "content": outbound.content,
                "content_type": outbound.content_type,
                "thread_id": outbound.thread_id,
                "reply_to": outbound.reply_to,
                "metadata": outbound.metadata,
            }
            
            await ws.send_text(json.dumps(message))
            return SendResult(success=True, message_id=outbound.chat_id)
            
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {e}")
            return SendResult(success=False, error=str(e))
    
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate WebSocket configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            Validation result
        """
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Config must be a dictionary")
            return ChannelValidationResult(valid=False, errors=errors)
        
        # WebSocket config is optional, no required fields
        return ChannelValidationResult(valid=True, errors=errors)
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return WebSocket configuration schema.
        
        Returns:
            JSON Schema
        """
        return {
            "type": "object",
            "title": "WebSocket Configuration",
            "description": "WebSocket channel configuration",
            "properties": {
                "path": {
                    "type": "string",
                    "title": "WebSocket Path",
                    "description": "WebSocket endpoint path",
                    "default": "/ws"
                }
            }
        }
    
    def register_connection(self, session_id: str, websocket: Any) -> None:
        """Register a WebSocket connection.
        
        Args:
            session_id: Session identifier
            websocket: WebSocket object
        """
        self._connections[session_id] = websocket
    
    def unregister_connection(self, session_id: str) -> None:
        """Unregister a WebSocket connection.
        
        Args:
            session_id: Session identifier
        """
        self._connections.pop(session_id, None)
