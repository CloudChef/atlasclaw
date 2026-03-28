"""WebSocket adapter for outbound channel delivery."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional, Protocol

from .base import (
    BaseChannelAdapter,
    ChannelConfig,
    DeliveryStatus,
    MessageChunk,
    SendResult,
)


class WebSocketConnection(Protocol):
    """Minimal protocol required from a WebSocket connection object."""
    
    async def send_text(self, data: str) -> None:
        """Send a text frame."""
        ...
    
    async def send_json(self, data: dict) -> None:
        """Send a JSON frame."""
        ...
    
    @property
    def closed(self) -> bool:
        """Return whether the connection is closed."""
        ...


class WebSocketAdapter(BaseChannelAdapter):
    """Send outbound channel messages through WebSocket connections."""
    
    def __init__(
        self,
        config: ChannelConfig,
        connection: Optional[WebSocketConnection] = None,
    ) -> None:
        """Initialize the WebSocket adapter."""
        super().__init__(config)
        self._connection = connection
        self._connections: dict[str, WebSocketConnection] = {}
        self._event_seq: dict[str, int] = {}
    
    def set_connection(self, connection: WebSocketConnection) -> None:
        """Set the default connection used when no chat-specific one exists."""
        self._connection = connection
    
    def add_connection(self, chat_id: str, connection: WebSocketConnection) -> None:
        """Register a chat-specific connection."""
        self._connections[chat_id] = connection
        self._event_seq[chat_id] = 0
    
    def remove_connection(self, chat_id: str) -> None:
        """Remove a chat-specific connection."""
        if chat_id in self._connections:
            del self._connections[chat_id]
        if chat_id in self._event_seq:
            del self._event_seq[chat_id]
    
    def get_connection(self, chat_id: str) -> Optional[WebSocketConnection]:
        """Return the connection for a chat, or the default connection."""
        return self._connections.get(chat_id) or self._connection
    
    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to_id: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        """Send a message"""
        conn = self.get_connection(chat_id)
        
        if not conn:
            return SendResult(
                success=False,
                error="No WebSocket connection",
                status=DeliveryStatus.FAILED,
            )
        
        if conn.closed:
            return SendResult(
                success=False,
                error="WebSocket connection closed",
                status=DeliveryStatus.FAILED,
            )
        
        try:
            message_id = str(uuid.uuid4())
            
            # Format the content for the target channel.
            formatted_content = self.format_content(content)
            
            # split long messages
            chunks = self.split_content(formatted_content)
            
            for i, chunk in enumerate(chunks):
                # build message
                frame = {
                    "type": "event",
                    "event": "message",
                    "payload": {
                        "message_id": f"{message_id}_{i}" if len(chunks) > 1 else message_id,
                        "chat_id": chat_id,
                        "content": chunk,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "is_final": i == len(chunks) - 1,
                    },
                    "seq": self._next_seq(chat_id),
                }
                
                if reply_to_id:
                    frame["payload"]["reply_to_id"] = reply_to_id
                
                if attachments and i == len(chunks) - 1:
                    frame["payload"]["attachments"] = attachments
                
                if metadata:
                    frame["payload"]["metadata"] = metadata
                
                await conn.send_json(frame)
            
            return SendResult(
                success=True,
                message_id=message_id,
                status=DeliveryStatus.SENT,
            )
            
        except Exception as e:
            return SendResult(
                success=False,
                error=str(e),
                status=DeliveryStatus.FAILED,
            )
    
    async def send_typing_indicator(
        self,
        chat_id: str,
        *,
        duration_seconds: float = 5.0,
    ) -> bool:
        """Send a typing indicator"""
        if not self._config.supports_typing:
            return False
        
        conn = self.get_connection(chat_id)
        if not conn or conn.closed:
            return False
        
        try:
            frame = {
                "type": "event",
                "event": "typing",
                "payload": {
                    "chat_id": chat_id,
                    "is_typing": True,
                    "duration_seconds": duration_seconds,
                },
                "seq": self._next_seq(chat_id),
            }
            
            await conn.send_json(frame)
            return True
            
        except Exception:
            return False
    
    async def send_chunk(
        self,
        chat_id: str,
        chunk: MessageChunk,
    ) -> SendResult:
        """Send a message chunk"""
        conn = self.get_connection(chat_id)
        
        if not conn:
            return SendResult(
                success=False,
                error="No WebSocket connection",
                status=DeliveryStatus.FAILED,
            )
        
        if conn.closed:
            return SendResult(
                success=False,
                error="WebSocket connection closed",
                status=DeliveryStatus.FAILED,
            )
        
        try:
            frame = {
                "type": "event",
                "event": "stream",
                "payload": {
                    "chat_id": chat_id,
                    "content": chunk.content,
                    "chunk_index": chunk.chunk_index,
                    "is_final": chunk.is_final,
                    "metadata": chunk.metadata,
                },
                "seq": self._next_seq(chat_id),
            }
            
            await conn.send_json(frame)
            
            return SendResult(
                success=True,
                status=DeliveryStatus.SENT if chunk.is_final else DeliveryStatus.PENDING,
            )
            
        except Exception as e:
            return SendResult(
                success=False,
                error=str(e),
                status=DeliveryStatus.FAILED,
            )
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """Edit a message"""
        conn = self.get_connection(chat_id)
        
        if not conn or conn.closed:
            return SendResult(
                success=False,
                error="No WebSocket connection",
                status=DeliveryStatus.FAILED,
            )
        
        try:
            frame = {
                "type": "event",
                "event": "message_edit",
                "payload": {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "content": self.format_content(content),
                },
                "seq": self._next_seq(chat_id),
            }
            
            await conn.send_json(frame)
            
            return SendResult(
                success=True,
                message_id=message_id,
                status=DeliveryStatus.SENT,
            )
            
        except Exception as e:
            return SendResult(
                success=False,
                error=str(e),
                status=DeliveryStatus.FAILED,
            )
    
    async def delete_message(
        self,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Delete a message"""
        conn = self.get_connection(chat_id)
        
        if not conn or conn.closed:
            return False
        
        try:
            frame = {
                "type": "event",
                "event": "message_delete",
                "payload": {
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
                "seq": self._next_seq(chat_id),
            }
            
            await conn.send_json(frame)
            return True
            
        except Exception:
            return False
    
    def _next_seq(self, chat_id: str) -> int:
        """get"""
        seq = self._event_seq.get(chat_id, 0) + 1
        self._event_seq[chat_id] = seq
        return seq
