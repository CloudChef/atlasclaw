"""Server-Sent Events adapter for outbound channel delivery."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Optional

from .base import (
    BaseChannelAdapter,
    ChannelConfig,
    DeliveryStatus,
    MessageChunk,
    SendResult,
)


class SSEAdapter(BaseChannelAdapter):
    """Send outbound channel messages through Server-Sent Events."""
    
    def __init__(self, config: ChannelConfig) -> None:
        """Initialize the SSE adapter."""
        super().__init__(config)
        self._senders: dict[str, Callable[[str, dict], Any]] = {}
        self._event_ids: dict[str, int] = {}
    
    def register_sender(
        self,
        chat_id: str,
        sender: Callable[[str, dict], Any],
    ) -> None:
        """Register an SSE sender callback for a chat ID."""
        self._senders[chat_id] = sender
        self._event_ids[chat_id] = 0
    
    def unregister_sender(self, chat_id: str) -> None:
        """Remove the sender callback associated with a chat ID."""
        if chat_id in self._senders:
            del self._senders[chat_id]
        if chat_id in self._event_ids:
            del self._event_ids[chat_id]
    
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
        sender = self._senders.get(chat_id)
        
        if not sender:
            return SendResult(
                success=False,
                error="No SSE sender registered",
                status=DeliveryStatus.FAILED,
            )
        
        try:
            message_id = str(uuid.uuid4())
            
            # Format the content for the target channel.
            formatted_content = self.format_content(content)
            
            # Split long payloads into channel-sized chunks.
            chunks = self.split_content(formatted_content)
            
            for i, chunk in enumerate(chunks):
                event_data = {
                    "message_id": f"{message_id}_{i}" if len(chunks) > 1 else message_id,
                    "chat_id": chat_id,
                    "content": chunk,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "is_final": i == len(chunks) - 1,
                    "id": self._next_event_id(chat_id),
                }
                
                if reply_to_id:
                    event_data["reply_to_id"] = reply_to_id
                
                if attachments and i == len(chunks) - 1:
                    event_data["attachments"] = attachments
                
                if metadata:
                    event_data["metadata"] = metadata
                
                result = sender("message", event_data)
                if asyncio.iscoroutine(result):
                    await result
            
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
        
        sender = self._senders.get(chat_id)
        if not sender:
            return False
        
        try:
            event_data = {
                "chat_id": chat_id,
                "is_typing": True,
                "duration_seconds": duration_seconds,
                "id": self._next_event_id(chat_id),
            }
            
            result = sender("typing", event_data)
            if asyncio.iscoroutine(result):
                await result
            return True
            
        except Exception:
            return False
    
    async def send_chunk(
        self,
        chat_id: str,
        chunk: MessageChunk,
    ) -> SendResult:
        """Send a message chunk"""
        sender = self._senders.get(chat_id)
        
        if not sender:
            return SendResult(
                success=False,
                error="No SSE sender registered",
                status=DeliveryStatus.FAILED,
            )
        
        try:
            event_data = {
                "chat_id": chat_id,
                "content": chunk.content,
                "chunk_index": chunk.chunk_index,
                "is_final": chunk.is_final,
                "metadata": chunk.metadata,
                "id": self._next_event_id(chat_id),
            }
            
            # based on event type
            event_type = "assistant" if not chunk.is_final else "message"
            
            result = sender(event_type, event_data)
            if asyncio.iscoroutine(result):
                await result
            
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
    
    def _next_event_id(self, chat_id: str) -> int:
        """get ID"""
        event_id = self._event_ids.get(chat_id, 0) + 1
        self._event_ids[chat_id] = event_id
        return event_id
    
    @staticmethod
    def format_sse_event(event_type: str, data: dict, event_id: Optional[int] = None) -> str:
        """

for mat SSE event
        
        Args:
            event_type:event type
            data:event payload
            event_id:ID
            
        Returns:
            SSE for matcharacters
        
"""
        lines = []
        
        if event_id is not None:
            lines.append(f"id: {event_id}")
        
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
        lines.append("")  # 
        
        return "\n".join(lines)
