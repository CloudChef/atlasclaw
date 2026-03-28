"""REST callback adapter for outbound channel delivery."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from .base import (
    BaseChannelAdapter,
    ChannelConfig,
    DeliveryStatus,
    MessageChunk,
    SendResult,
)


class RESTCallbackAdapter(BaseChannelAdapter):
    """Send outbound channel messages to a REST callback endpoint."""
    
    def __init__(
        self,
        config: ChannelConfig,
        callback_url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        auth_token: Optional[str] = None,
        timeout_seconds: float = 30.0,
        retry_count: int = 3,
    ) -> None:
        """Initialize the REST callback adapter."""
        super().__init__(config)
        self._callback_url = callback_url
        self._headers = headers or {}
        self._auth_token = auth_token
        self._timeout = timeout_seconds
        self._retry_count = retry_count
        
        # Populate the bearer token header when provided.
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
    
    @property
    def callback_url(self) -> str:
        return self._callback_url
    
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
        import httpx
        
        message_id = str(uuid.uuid4())
        
        # Format the content for the target channel.
        formatted_content = self.format_content(content)
        
        # Split long payloads into channel-sized chunks.
        chunks = self.split_content(formatted_content)
        
        last_result = None
        for i, chunk in enumerate(chunks):
            payload = {
                "event": "message",
                "message_id": f"{message_id}_{i}" if len(chunks) > 1 else message_id,
                "chat_id": chat_id,
                "content": chunk,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "is_final": i == len(chunks) - 1,
            }
            
            if reply_to_id:
                payload["reply_to_id"] = reply_to_id
            
            if attachments and i == len(chunks) - 1:
                payload["attachments"] = attachments
            
            if metadata:
                payload["metadata"] = metadata
            
            last_result = await self._send_request(payload)
            if not last_result.success:
                return last_result
        
        return last_result or SendResult(
            success=True,
            message_id=message_id,
            status=DeliveryStatus.SENT,
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
        
        payload = {
            "event": "typing",
            "chat_id": chat_id,
            "is_typing": True,
            "duration_seconds": duration_seconds,
        }
        
        result = await self._send_request(payload)
        return result.success
    
    async def send_chunk(
        self,
        chat_id: str,
        chunk: MessageChunk,
    ) -> SendResult:
        """Send a message chunk"""
        payload = {
            "event": "stream",
            "chat_id": chat_id,
            "content": chunk.content,
            "chunk_index": chunk.chunk_index,
            "is_final": chunk.is_final,
            "metadata": chunk.metadata,
        }
        
        return await self._send_request(payload)
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """Edit a message"""
        payload = {
            "event": "message_edit",
            "chat_id": chat_id,
            "message_id": message_id,
            "content": self.format_content(content),
        }
        
        return await self._send_request(payload)
    
    async def delete_message(
        self,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Delete a message"""
        payload = {
            "event": "message_delete",
            "chat_id": chat_id,
            "message_id": message_id,
        }
        
        result = await self._send_request(payload)
        return result.success
    
    async def _send_request(self, payload: dict) -> SendResult:
        """

HTTP
        
        Args:
            payload:
            
        Returns:
            Delivery result
        
"""
        import httpx
        
        headers = {
            "Content-Type": "application/json",
            **self._headers,
        }
        
        last_error = None
        
        for attempt in range(self._retry_count):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, trust_env=True) as client:

                    response = await client.post(
                        self._callback_url,
                        headers=headers,
                        json=payload,
                    )
                    
                    if response.status_code >= 200 and response.status_code < 300:
                        return SendResult(
                            success=True,
                            message_id=payload.get("message_id"),
                            status=DeliveryStatus.DELIVERED,
                            metadata={"status_code": response.status_code},
                        )
                    
                    # 4xx
                    if response.status_code >= 400 and response.status_code < 500:
                        return SendResult(
                            success=False,
                            error=f"HTTP {response.status_code}: {response.text}",
                            status=DeliveryStatus.FAILED,
                        )
                    
                    # 5xx
                    last_error = f"HTTP {response.status_code}"
                    
            except httpx.TimeoutException:
                last_error = "Request timeout"
            except Exception as e:
                last_error = str(e)
            
            # etc.
            if attempt < self._retry_count - 1:
                await asyncio.sleep(2 ** attempt)  # exponential backoff
        
        return SendResult(
            success=False,
            error=last_error or "Unknown error",
            status=DeliveryStatus.FAILED,
        )
