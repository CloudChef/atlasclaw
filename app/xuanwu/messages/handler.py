"""Inbound and outbound message handling utilities.

This module normalizes inbound messages, applies command parsing, duplicate
detection, debounce logic, and group-history enrichment, and then shapes
outbound responses for delivery.
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Callable, Coroutine

from .command import CommandParser, ParsedCommand


class MessageType(Enum):
    """Message type"""
    TEXT = "text"
    MEDIA = "media"
    ATTACHMENT = "attachment"
    COMMAND = "command"
    SYSTEM = "system"


class ChatType(Enum):
    """Conversation type for an inbound message."""
    DM = "dm"
    GROUP = "group"
    THREAD = "thread"


@dataclass
class InboundMessage:
    """
    Canonical representation of an inbound message.

    Attributes:
        message_id: Provider-specific message identifier.
        channel: Channel name, such as `telegram` or `slack`.
        account_id: Account identifier within the channel provider.
        peer_id: Peer identifier, such as a user, room, or thread target.
        chat_type: Conversation type for the message.
        body: Normalized message body used by the agent.
        raw_body: Raw message content before preprocessing.
        command: Parsed command metadata, if the message contains a command.
        sender_name: Optional human-readable sender name.
        media_path: Local file path for attached media, if present.
        media_type: Media type for the attached content, if present.
        reply_to_id: ID of the message being replied to, if present.
        timestamp: Message timestamp as a Unix epoch value.
        metadata: Additional provider-specific metadata.
    """
    message_id: str
    channel: str
    account_id: str
    peer_id: str
    chat_type: ChatType = ChatType.DM
    body: str = ""
    raw_body: str = ""
    command: Optional[ParsedCommand] = None
    sender_name: str = ""
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    reply_to_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_group_chat(self) -> bool:
        """Return whether the message belongs to a group-style conversation."""
        return self.chat_type in (ChatType.GROUP, ChatType.THREAD)
        
    @property
    def is_command(self) -> bool:
        """Return whether the inbound message contains a parsed command."""
        return self.command is not None
        
    @property
    def has_media(self) -> bool:
        """Return whether the inbound message includes media content."""
        return self.media_path is not None
        

@dataclass
class OutboundMessage:
    """
    Canonical representation of an outbound message.

    Attributes:
        body: Text content to deliver.
        channel: Target channel name.
        account_id: Target account identifier.
        peer_id: Target peer identifier.
        reply_to_id: ID of the message being replied to, if present.
        is_chunk: Whether this message is a chunk of a larger response.
        chunk_index: Zero-based chunk index.
        is_final: Whether this is the final chunk in a chunked response.
        metadata: Additional delivery metadata.
    """
    body: str
    channel: str
    account_id: str
    peer_id: str
    reply_to_id: Optional[str] = None
    is_chunk: bool = False
    chunk_index: int = 0
    is_final: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass 
class DedupeEntry:
    """Cached duplicate-detection entry."""
    message_id: str
    timestamp: float


class MessageHandler:
    """
    Process inbound messages and shape outbound responses.

    The handler is responsible for:

    1. Normalizing inbound message structures.
    2. Detecting duplicates.
    3. Parsing commands and applying debounce rules.
    4. Enriching group messages with buffered history.
    5. Shaping outbound responses with prefixes, reply metadata, and chunking.
    """
    
    def __init__(
        self,
        *,
        dedupe_ttl_seconds: float = 300.0,
        debounce_ms: int = 500,
        group_history_limit: int = 10,
        response_prefix: str = "",
        command_parser: Optional[CommandParser] = None,
    ) -> None:
        """
        Initialize the message handler.

        Args:
            dedupe_ttl_seconds: TTL for duplicate detection entries.
            debounce_ms: Debounce delay for inbound text messages.
            group_history_limit: Maximum buffered group-history entries.
            response_prefix: Optional prefix for outbound responses.
            command_parser: Optional command parser override.
        """
        self._dedupe_ttl = dedupe_ttl_seconds
        self._debounce_ms = debounce_ms
        self._group_history_limit = group_history_limit
        self._response_prefix = response_prefix
        self._command_parser = command_parser or CommandParser()
        
        # Duplicate cache keyed by message fingerprint.
        self._dedupe_cache: dict[str, DedupeEntry] = {}
        
        # Debounce buffer keyed by session key.
        self._debounce_buffer: dict[str, list[InboundMessage]] = {}
        self._debounce_tasks: dict[str, asyncio.Task[None]] = {}
        
        # Buffered group-history messages keyed by session key.
        self._group_history: dict[str, list[InboundMessage]] = {}
        
        # Callback invoked once a message is ready for downstream processing.
        self._on_message_ready: Optional[Callable[[InboundMessage], Coroutine[Any, Any, None]]] = None
        
    def set_message_callback(
        self,
        callback: Callable[[InboundMessage], Coroutine[Any, Any, None]]
    ) -> None:
        """Register the callback invoked for ready-to-process messages."""
        self._on_message_ready = callback
        
    async def process_inbound(
        self,
        message: InboundMessage,
        *,
        session_key: str,
        bypass_debounce: bool = False,
    ) -> Optional[InboundMessage]:
        """
        Process an inbound message.

        Args:
            message: Inbound message to process.
            session_key: Session key used for dedupe and debounce state.
            bypass_debounce: Whether to skip debounce handling.

        Returns:
            The processed message, or `None` if it was dropped due to duplicate
            detection or debounce buffering.
        """
        # 1. Duplicate check
        if self._is_duplicate(message):
            return None
            
        # 2. Command parsing
        message = self._parse_command(message)
        
        # 3. Command-specific debounce bypass
        if message.command:
            if self._command_parser.should_bypass_debounce(message.command):
                bypass_debounce = True
                
        # 4. message()
        if message.has_media:
            bypass_debounce = True
            
        # 5. apply or handle
        if bypass_debounce:
            return await self._process_ready_message(message, session_key)
        else:
            await self._debounce_message(message, session_key)
            return None
            
    def _generate_dedupe_key(self, message: InboundMessage) -> str:
        """"""
        key_parts = [
            message.channel,
            message.account_id,
            message.peer_id,
            message.message_id,
        ]
        key_str = ":".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
        
    def _is_duplicate(self, message: InboundMessage) -> bool:
        """check message"""
        # entry
        self._cleanup_dedupe_cache()
        
        # 
        dedupe_key = self._generate_dedupe_key(message)
        
        # check at
        if dedupe_key in self._dedupe_cache:
            return True
            
        # to
        self._dedupe_cache[dedupe_key] = DedupeEntry(
            message_id=message.message_id,
            timestamp=time.time()
        )
        return False
        
    def _cleanup_dedupe_cache(self) -> None:
        """entry"""
        now = time.time()
        expired_keys = [
            key for key, entry in self._dedupe_cache.items()
            if now - entry.timestamp > self._dedupe_ttl
        ]
        for key in expired_keys:
            del self._dedupe_cache[key]
            
    def _parse_command(self, message: InboundMessage) -> InboundMessage:
        """parsemessagein"""
        if not message.body:
            return message
            
        parsed = self._command_parser.parse(message.body)
        if parsed:
            message.command = parsed
            # such as frommessagein
            if self._command_parser.should_strip_from_message(parsed):
                message.body = parsed.remaining_text
        return message
        
    async def _debounce_message(
        self,
        message: InboundMessage,
        session_key: str
    ) -> None:
        """apply to message"""
        # to
        if session_key not in self._debounce_buffer:
            self._debounce_buffer[session_key] = []
        self._debounce_buffer[session_key].append(message)
        
        # 
        if session_key in self._debounce_tasks:
            self._debounce_tasks[session_key].cancel()
            
        # create
        async def debounce_flush() -> None:
            await asyncio.sleep(self._debounce_ms / 1000.0)
            await self._flush_debounce_buffer(session_key)
            
        self._debounce_tasks[session_key] = asyncio.create_task(debounce_flush())
        
    async def _flush_debounce_buffer(self, session_key: str) -> None:
        """"""
        if session_key not in self._debounce_buffer:
            return
            
        messages = self._debounce_buffer.pop(session_key, [])
        self._debounce_tasks.pop(session_key, None)
        
        if not messages:
            return
            
        # message
        merged = self._merge_messages(messages)
        await self._process_ready_message(merged, session_key)
        
    def _merge_messages(self, messages: list[InboundMessage]) -> InboundMessage:
        """multiitemmessage item"""
        if len(messages) == 1:
            return messages[0]
            
        # use itemmessage
        base = messages[0]
        
        # 
        bodies = [m.body for m in messages if m.body]
        base.body = "\n".join(bodies)
        base.raw_body = "\n".join(m.raw_body or m.body for m in messages)
        
        # itemtimestamp
        base.timestamp = messages[-1].timestamp
        
        # metadata
        for m in messages[1:]:
            base.metadata.update(m.metadata)
            
        return base
        
    async def _process_ready_message(
        self,
        message: InboundMessage,
        session_key: str
    ) -> InboundMessage:
        """handle message"""
        # message handling:context
        if message.is_group_chat:
            message = self._inject_group_history(message, session_key)
            
        # (in)
        if message.is_group_chat and message.sender_name:
            if not message.body.startswith(f"[{message.sender_name}]"):
                message.body = f"[{message.sender_name}] {message.body}"
                
        # 
        if self._on_message_ready:
            await self._on_message_ready(message)
            
        return message
        
    def _inject_group_history(
        self,
        message: InboundMessage,
        session_key: str
    ) -> InboundMessage:
        """inject context"""
        history = self._group_history.get(session_key, [])
        
        if not history:
            return message
            
        # build context
        history_lines = []
        for h in history[-self._group_history_limit:]:
            sender = h.sender_name or h.peer_id
            history_lines.append(f"[{sender}] {h.body}")
            
        if history_lines:
            history_text = "\n".join(history_lines)
            wrapped_body = (
                "[Chat messages since your last reply - for context]\n"
                f"{history_text}\n\n"
                "[Current message - respond to this]\n"
                f"{message.body}"
            )
            message.body = wrapped_body
            
        # 
        self._group_history[session_key] = []
        
        return message
        
    def add_to_group_history(
        self,
        message: InboundMessage,
        session_key: str
    ) -> None:
        """Add a message to buffered group history for later processing."""
        if session_key not in self._group_history:
            self._group_history[session_key] = []
            
        history = self._group_history[session_key]
        history.append(message)
        
        # 
        if len(history) > self._group_history_limit * 2:
            self._group_history[session_key] = history[-self._group_history_limit:]
            
    def shape_response(
        self,
        response: str,
        *,
        channel: str,
        account_id: str,
        peer_id: str,
        reply_to_id: Optional[str] = None,
        text_chunk_limit: Optional[int] = None,
    ) -> list[OutboundMessage]:
        """


        
        applyprefix, NO_REPLY filter, etc.handle.
        
        Args:
            response:
            channel:channel
            account_id:account
            peer_id:etc.
            reply_to_id:message being replied to ID
            text_chunk_limit:
            
        Returns:
            message list
        
"""
        # 1. NO_REPLY check
        if self._is_no_reply(response):
            return []
            
        # 2. applyprefix
        if self._response_prefix:
            response = f"{self._response_prefix}{response}"
            
        # 3. handle(such as)
        if text_chunk_limit and len(response) > text_chunk_limit:
            chunks = self._split_response(response, text_chunk_limit)
        else:
            chunks = [response]
            
        # 4. build message
        messages = []
        for i, chunk in enumerate(chunks):
            messages.append(OutboundMessage(
                body=chunk,
                channel=channel,
                account_id=account_id,
                peer_id=peer_id,
                reply_to_id=reply_to_id if i == 0 else None,
                is_chunk=len(chunks) > 1,
                chunk_index=i,
                is_final=i == len(chunks) - 1
            ))
            
        return messages
        
    def _is_no_reply(self, response: str) -> bool:
        """check contains NO_REPLY"""
        no_reply_tokens = ["NO_REPLY", "[NO_REPLY]", "[[NO_REPLY]]"]
        response_stripped = response.strip()
        return response_stripped in no_reply_tokens
        
    def _split_response(self, response: str, limit: int) -> list[str]:
        """

split
        
        at paragraph,, sub split.
        
"""
        if len(response) <= limit:
            return [response]
            
        chunks = []
        remaining = response
        
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
                
            # to split
            split_pos = self._find_split_position(remaining, limit)
            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()
            
        return chunks
        
    def _find_split_position(self, text: str, limit: int) -> int:
        """to split"""
        # :paragraph > > sub > > split
        
        # paragraph
        pos = text.rfind('\n\n', 0, limit)
        if pos > limit // 2:
            return pos + 2
            
        # 
        pos = text.rfind('\n', 0, limit)
        if pos > limit // 2:
            return pos + 1
            
        # sub
        for punct in ['. ', '。', '! ', '? ', '！', '？']:
            pos = text.rfind(punct, 0, limit)
            if pos > limit // 2:
                return pos + len(punct)
                
        # 
        pos = text.rfind(' ', 0, limit)
        if pos > limit // 2:
            return pos + 1
            
        # split
        return limit
        
    def clear_session_buffers(self, session_key: str) -> None:
        """

session
        
        at session.
        
        Args:
            session_key:session key
        
"""
        self._debounce_buffer.pop(session_key, None)
        self._group_history.pop(session_key, None)
        
        if session_key in self._debounce_tasks:
            self._debounce_tasks[session_key].cancel()
            self._debounce_tasks.pop(session_key, None)
