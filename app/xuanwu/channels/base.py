"""Base protocol and helpers for channel adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class MessageType(Enum):
    """Supported outbound or inbound message types."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACT = "contact"
    TYPING = "typing"


class DeliveryStatus(Enum):
    """Delivery lifecycle states reported by a channel."""
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


@dataclass
class ChannelConfig:
    """Static configuration for a channel adapter.

    Attributes:
        channel_id: Unique channel identifier.
        channel_type: Provider or transport type.
        text_chunk_limit: Maximum characters allowed in one outbound message.
        supports_markdown: Whether the channel accepts Markdown directly.
        supports_html: Whether the channel accepts HTML directly.
        supports_typing: Whether typing indicators are supported.
        supports_reactions: Whether reactions are supported.
        rate_limit_per_second: Advisory per-channel send rate.
    """
    channel_id: str
    channel_type: str
    text_chunk_limit: int = 4096
    supports_markdown: bool = True
    supports_html: bool = False
    supports_typing: bool = True
    supports_reactions: bool = False
    rate_limit_per_second: float = 10.0
    
    # Content format conversion flags.
    markdown_to_html: bool = False
    html_to_markdown: bool = False

    # File upload limits.
    max_file_size_mb: int = 50
    allowed_file_types: list[str] = field(default_factory=list)

    # Provider-specific extension fields.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelMessage:
    """Normalized message representation exchanged with channel adapters.

    Attributes:
        message_id: Provider-side message identifier.
        channel_id: Channel identifier.
        chat_id: Chat, room, or conversation identifier.
        content: Primary text content.
        message_type: Structured message type.
        reply_to_id: Optional parent message identifier.
        attachments: Structured attachment payloads.
        metadata: Provider-specific metadata.
    """
    message_id: str
    channel_id: str
    chat_id: str
    content: str
    message_type: MessageType = MessageType.TEXT
    reply_to_id: Optional[str] = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MessageChunk:
    """A partial outbound message chunk."""
    content: str
    chunk_index: int
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TypingIndicator:
    """Typing indicator payload."""
    chat_id: str
    is_typing: bool = True
    duration_seconds: float = 5.0


@dataclass
class SendResult:
    """Result of a send, edit, or chunk operation."""
    success: bool
    message_id: Optional[str] = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChannelAdapter(Protocol):
    """Protocol implemented by concrete channel adapters."""
    
    @property
    def channel_id(self) -> str:
        """Return the channel identifier."""
        ...
    
    @property
    def config(self) -> ChannelConfig:
        """Channel configuration"""
        ...
    
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
        ...
    
    async def send_typing_indicator(
        self,
        chat_id: str,
        *,
        duration_seconds: float = 5.0,
    ) -> bool:
        """Send a typing indicator"""
        ...
    
    async def send_chunk(
        self,
        chat_id: str,
        chunk: MessageChunk,
    ) -> SendResult:
        """Send a message chunk"""
        ...
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """Edit a message"""
        ...
    
    async def delete_message(
        self,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Delete a message"""
        ...


class BaseChannelAdapter(ABC):
    """Shared helper implementation for concrete channel adapters."""
    
    def __init__(self, config: ChannelConfig) -> None:
        """Initialize the adapter with channel configuration."""
        self._config = config
        self._message_buffer: dict[str, list[str]] = {}
    
    @property
    def channel_id(self) -> str:
        return self._config.channel_id
    
    @property
    def config(self) -> ChannelConfig:
        return self._config
    
    def format_content(self, content: str) -> str:
        """Format content according to channel conversion settings."""
        if self._config.markdown_to_html:
            return self._markdown_to_html(content)
        if self._config.html_to_markdown:
            return self._html_to_markdown(content)
        return content
    
    def split_content(self, content: str) -> list[str]:
        """Split text into channel-sized chunks."""
        limit = self._config.text_chunk_limit
        if len(content) <= limit:
            return [content]
        
        chunks = []
        while content:
            if len(content) <= limit:
                chunks.append(content)
                break
            
            # Prefer natural breakpoints over hard cuts.
            break_pos = self._find_break_point(content, limit)
            chunks.append(content[:break_pos])
            content = content[break_pos:].lstrip()
        
        return chunks
    
    def _find_break_point(self, text: str, max_pos: int) -> int:
        """Find a natural split position before `max_pos`."""
        # Prefer paragraph, sentence, or word boundaries.
        for sep in ["\n\n", "\n", "。", ".", " "]:
            pos = text.rfind(sep, 0, max_pos)
            if pos > max_pos // 2:
                return pos + len(sep)
        return max_pos
    
    def _markdown_to_html(self, content: str) -> str:
        """Convert a small Markdown subset to HTML."""
        import re
        
        # Headings
        content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=re.MULTILINE)
        content = re.sub(r'^## (.+)$', r'<h2>\1</h2>', content, flags=re.MULTILINE)
        content = re.sub(r'^# (.+)$', r'<h1>\1</h1>', content, flags=re.MULTILINE)
        
        # Bold and italic
        content = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', content)
        content = re.sub(r'\*(.+?)\*', r'<i>\1</i>', content)
        
        # Inline code
        content = re.sub(r'`(.+?)`', r'<code>\1</code>', content)
        
        # Links
        content = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', content)
        
        return content
    
    def _html_to_markdown(self, content: str) -> str:
        """Convert a small HTML subset to Markdown."""
        import re
        
        # Headings
        content = re.sub(r'<h1>(.+?)</h1>', r'# \1', content)
        content = re.sub(r'<h2>(.+?)</h2>', r'## \1', content)
        content = re.sub(r'<h3>(.+?)</h3>', r'### \1', content)
        
        # Bold and italic
        content = re.sub(r'<b>(.+?)</b>', r'**\1**', content)
        content = re.sub(r'<strong>(.+?)</strong>', r'**\1**', content)
        content = re.sub(r'<i>(.+?)</i>', r'*\1*', content)
        content = re.sub(r'<em>(.+?)</em>', r'*\1*', content)
        
        # Inline code
        content = re.sub(r'<code>(.+?)</code>', r'`\1`', content)
        
        # Links
        content = re.sub(r'<a href="(.+?)">(.+?)</a>', r'[\2](\1)', content)
        
        # Remove any remaining tags.
        content = re.sub(r'<[^>]+>', '', content)
        
        return content
    
    @abstractmethod
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
        ...
    
    async def send_typing_indicator(
        self,
        chat_id: str,
        *,
        duration_seconds: float = 5.0,
    ) -> bool:
        """Send a typing indicator using the default no-op implementation."""
        return self._config.supports_typing
    
    async def send_chunk(
        self,
        chat_id: str,
        chunk: MessageChunk,
    ) -> SendResult:
        """Buffer message chunks until the final chunk arrives."""
        # Lazily create the chunk buffer for the chat.
        if chat_id not in self._message_buffer:
            self._message_buffer[chat_id] = []
        
        self._message_buffer[chat_id].append(chunk.content)
        
        # Flush the buffered content when the final chunk arrives.
        if chunk.is_final:
            full_content = "".join(self._message_buffer[chat_id])
            del self._message_buffer[chat_id]
            return await self.send_message(chat_id, full_content)
        
        return SendResult(success=True, status=DeliveryStatus.PENDING)
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """Return a standard unsupported result for message edits."""
        return SendResult(
            success=False,
            error="Edit not supported by this channel",
        )
    
    async def delete_message(
        self,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Return `False` because deletion is unsupported by default."""
        return False
