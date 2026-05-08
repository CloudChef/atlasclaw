# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Session key and session metadata primitives.

This module defines hierarchical session-key formats, identity-link helpers,
and the dataclasses used to persist session and transcript state.

Key formats:
| SessionScope | Format | Example |
|--------------|--------|---------|
| MAIN | `agent:<agentId>:user:<userId>:main` | `agent:main:user:u-a1:main` |
| PER_PEER | `agent:<agentId>:user:<userId>:<chatType>:<peerId>` | `agent:main:user:u-a1:dm:user_42` |
| PER_CHANNEL_PEER | `agent:<agentId>:user:<userId>:<channel>:<chatType>:<peerId>` | `agent:main:user:u-a1:telegram:dm:user_42` |
| PER_ACCOUNT_CHANNEL_PEER | `agent:<agentId>:user:<userId>:<channel>:<accountId>:<chatType>:<peerId>` | `agent:main:user:u-a1:telegram:acc1:dm:user_42` |

Legacy formats (without user: segment) fill `user_id="default"` on parse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionScope(Enum):
    """
    Session isolation strategy.

    - `MAIN`: a single shared session for the agent
    - `PER_PEER`: one session per peer across channels
    - `PER_CHANNEL_PEER`: one session per channel and peer
    - `PER_ACCOUNT_CHANNEL_PEER`: one session per account, channel, and peer
    """
    MAIN = "main"
    PER_PEER = "per-peer"
    PER_CHANNEL_PEER = "per-channel-peer"
    PER_ACCOUNT_CHANNEL_PEER = "per-account-channel-peer"


class ChatType(Enum):
    """Conversation type used in session-key construction."""
    DM = "dm"           # Direct message
    GROUP = "group"     # group
    CHANNEL = "channel" # Channel-style conversation, such as Slack
    THREAD = "thread"   # Topic or threaded conversation


def _encode_key_segment(value: str) -> str:
    """Escape delimiters inside one session-key segment."""
    return str(value or "").replace("%", "%25").replace(":", "%3A")


def _decode_key_segment(value: str) -> str:
    """Decode a session-key segment escaped by `_encode_key_segment`."""
    return (
        str(value or "")
        .replace("%3A", ":")
        .replace("%3a", ":")
        .replace("%25", "%")
    )


@dataclass
class SessionKey:
    """
    Hierarchical session-key definition.

    Attributes:
        agent_id: Agent identifier. Defaults to `main`.
        user_id: Authenticated user identifier. Defaults to `default` (legacy compat).
        channel: Channel name. Defaults to `main`.
        account_id: Account identifier for multi-account deployments.
        chat_type: Conversation type.
        peer_id: Peer identifier, such as a user or group ID.
        thread_id: Optional topic or thread identifier.
    """
    agent_id: str = "main"
    user_id: str = "default"
    channel: str = "main"
    account_id: str = "default"
    chat_type: ChatType = ChatType.DM
    peer_id: str = "default"
    thread_id: Optional[str] = None
    
    def to_string(self, scope: SessionScope = SessionScope.PER_CHANNEL_PEER) -> str:
        """Render the session key using the requested isolation strategy."""
        agent_id = _encode_key_segment(self.agent_id)
        user_id = _encode_key_segment(self.user_id)
        channel = _encode_key_segment(self.channel)
        account_id = _encode_key_segment(self.account_id)
        peer_id = _encode_key_segment(self.peer_id)
        thread_id = _encode_key_segment(self.thread_id) if self.thread_id else None
        base = f"agent:{agent_id}:user:{user_id}"
        match scope:
            case SessionScope.MAIN:
                return f"{base}:main"
            case SessionScope.PER_PEER:
                return f"{base}:{self.chat_type.value}:{peer_id}"
            case SessionScope.PER_CHANNEL_PEER:
                rest = f"{channel}:{self.chat_type.value}:{peer_id}"
                return f"{base}:{rest}:topic:{thread_id}" if thread_id else f"{base}:{rest}"
            case SessionScope.PER_ACCOUNT_CHANNEL_PEER:
                rest = f"{channel}:{account_id}:{self.chat_type.value}:{peer_id}"
                return f"{base}:{rest}:topic:{thread_id}" if thread_id else f"{base}:{rest}"
            case _:
                return f"{base}:main"
    
    @classmethod
    def from_string(cls, key: str) -> "SessionKey":
        """Parse a session-key string into a `SessionKey` instance.
        
        Supports both new format (`agent:<id>:user:<userId>:<rest>`) and
        legacy format (no user segment, fills `user_id="default"`).
        """
        parts = key.split(":")
        
        # The smallest valid format is `agent:<agentId>:...`.
        if len(parts) < 3 or parts[0] != "agent":
            return cls()
        
        agent_id = _decode_key_segment(parts[1])
        
        # Detect new format: `agent:<id>:user:<userId>:<rest>`
        user_id = "default"
        rest_start = 2
        if len(parts) > 3 and parts[2] == "user":
            user_id = _decode_key_segment(parts[3])
            rest_start = 4
        
        rest = parts[rest_start:]
        
        if not rest:
            return cls(agent_id=agent_id, user_id=user_id)
        
        # `...:main` -> MAIN
        if len(rest) == 1 and rest[0] == "main":
            return cls(agent_id=agent_id, user_id=user_id)
        
        # `...:dm:user_42` -> PER_PEER
        if len(rest) == 2:
            chat_type_str = rest[0]
            peer_id = _decode_key_segment(rest[1])
            chat_type = ChatType(chat_type_str) if chat_type_str in [e.value for e in ChatType] else ChatType.DM
            return cls(agent_id=agent_id, user_id=user_id, chat_type=chat_type, peer_id=peer_id)
        
        # `...:telegram:dm:user_42` or `...:telegram:acc1:dm:user_42`
        if len(rest) >= 3:
            channel = _decode_key_segment(rest[0])
            
            # Detect PER_ACCOUNT_CHANNEL_PEER: channel, account_id, chat_type, peer_id
            if len(rest) >= 4 and rest[2] in [e.value for e in ChatType]:
                account_id = _decode_key_segment(rest[1])
                chat_type_str = rest[2]
                peer_id = _decode_key_segment(rest[3])
                chat_type = ChatType(chat_type_str)
                thread_id = (
                    _decode_key_segment(rest[5])
                    if len(rest) >= 6 and rest[4] == "topic"
                    else None
                )
                return cls(
                    agent_id=agent_id,
                    user_id=user_id,
                    channel=channel,
                    account_id=account_id,
                    chat_type=chat_type,
                    peer_id=peer_id,
                    thread_id=thread_id,
                )
            
            # PER_CHANNEL_PEER: channel, chat_type, peer_id
            chat_type_str = rest[1]
            peer_id = _decode_key_segment(rest[2])
            chat_type = ChatType(chat_type_str) if chat_type_str in [e.value for e in ChatType] else ChatType.DM
            thread_id = (
                _decode_key_segment(rest[4])
                if len(rest) >= 5 and rest[3] == "topic"
                else None
            )
            return cls(
                agent_id=agent_id,
                user_id=user_id,
                channel=channel,
                chat_type=chat_type,
                peer_id=peer_id,
                thread_id=thread_id,
            )
        
        return cls(agent_id=agent_id, user_id=user_id)


@dataclass
class IdentityLinks:
    """
    Cross-channel identity mapping.

    Identity links map provider-specific peer IDs to a canonical identity.

    Example:
        `{"alice": ["telegram:123", "slack:U456"]}`

    In that case, both `telegram:123` and `slack:U456` resolve to `alice`.
    """
    mappings: dict[str, list[str]] = field(default_factory=dict)
    
    def resolve(self, provider_peer_id: str) -> str:
        """Resolve a provider-specific peer ID to its canonical identity."""
        for canonical_id, links in self.mappings.items():
            if provider_peer_id in links:
                return canonical_id
        return provider_peer_id  # Fall back to the raw provider-specific value.
    
    def add_mapping(self, canonical_id: str, provider_peer_id: str) -> None:
        """Add a new canonical-to-provider identity mapping."""
        if canonical_id not in self.mappings:
            self.mappings[canonical_id] = []
        if provider_peer_id not in self.mappings[canonical_id]:
            self.mappings[canonical_id].append(provider_peer_id)


class SessionKeyFactory:
    """
    Factory for creating `SessionKey` instances from runtime context.
    """
    
    def __init__(self, identity_links: Optional[IdentityLinks] = None):
        """Initialize the factory with optional cross-channel identity links."""
        self.identity_links = identity_links or IdentityLinks()
    
    def create(
        self,
        scope: SessionScope,
        *,
        agent_id: str = "main",
        user_id: str = "default",
        channel: str = "main",
        account_id: str = "default",
        chat_type: ChatType = ChatType.DM,
        peer_id: str = "default",
        thread_id: Optional[str] = None,
    ) -> SessionKey:
        """
        Create a session key from runtime routing context.

        Args:
            scope: Session isolation strategy.
            agent_id: Agent identifier.
            user_id: Authenticated user identifier.
            channel: Channel name.
            account_id: Account identifier.
            chat_type: Conversation type.
            peer_id: Peer identifier.
            thread_id:topic ID
            
        Returns:
            SessionKey instance
        
"""
        # use IdentityLinks parsecanonical identity
        provider_peer_id = f"{channel}:{peer_id}"
        canonical_peer_id = self.identity_links.resolve(provider_peer_id)
        
        # such as parse provider:peer for mat, peer
        if ":" in canonical_peer_id and canonical_peer_id != provider_peer_id:
            # canonical identity parse, use
            pass
        elif ":" in canonical_peer_id:
            # , raw peer_id
            canonical_peer_id = peer_id
        
        return SessionKey(
            agent_id=agent_id,
            user_id=user_id,
            channel=channel,
            account_id=account_id,
            chat_type=chat_type,
            peer_id=canonical_peer_id,
            thread_id=thread_id,
        )


@dataclass
class SessionOrigin:
    """
    Source metadata attached to a session.

    Attributes:
        label: Human-readable source label.
        provider: Provider or channel identifier.
        from_id: Provider-side sender identifier.
        to_id: Provider-side receiver identifier.
        account_id: Account identifier within the provider.
        thread_id: Optional thread or topic identifier.
    """
    label: str = ""
    provider: str = ""
    from_id: Optional[str] = None
    to_id: Optional[str] = None
    account_id: Optional[str] = None
    thread_id: Optional[str] = None


@dataclass
class SessionMetadata:
    """
    Session metadata stored in JSON.

    Attributes:
        session_id: Stable session identifier.
        session_key: Serialized session key string.
        created_at: Session creation time.
        updated_at: Last update time.
        agent_id: Agent identifier.
        channel: Channel name.
        account_id: Account identifier.
        peer_id: Peer identifier.
        display_name: Optional display name shown to humans.
        origin: Optional source metadata.
        input_tokens: Total input token count.
        output_tokens: Total output token count.
        total_tokens: Aggregate token count.
        context_tokens: Current context token count.
        compaction_count: Number of compaction cycles applied.
        last_compacted_at: Time of the last compaction.
        memory_flushed_this_cycle: Whether memory has been flushed this cycle.
        extra: Additional persisted metadata.
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_key: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    agent_id: str = "main"
    channel: str = "main"
    account_id: str = "default"
    peer_id: str = "default"
    title: str = ""
    title_status: str = "empty"
    display_name: Optional[str] = None
    origin: Optional[SessionOrigin] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_tokens: int = 0
    compaction_count: int = 0
    last_compacted_at: Optional[datetime] = None
    memory_flushed_this_cycle: bool = False
    extra: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Serialize the metadata to a JSON-friendly dictionary."""
        return {
            "session_id": self.session_id,
            "session_key": self.session_key,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "agent_id": self.agent_id,
            "channel": self.channel,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "title": self.title,
            "title_status": self.title_status,
            "display_name": self.display_name,
            "origin": {
                "label": self.origin.label,
                "provider": self.origin.provider,
                "from_id": self.origin.from_id,
                "to_id": self.origin.to_id,
                "account_id": self.origin.account_id,
                "thread_id": self.origin.thread_id,
            } if self.origin else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "context_tokens": self.context_tokens,
            "compaction_count": self.compaction_count,
            "last_compacted_at": self.last_compacted_at.isoformat() if self.last_compacted_at else None,
            "memory_flushed_this_cycle": self.memory_flushed_this_cycle,
            "extra": self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetadata":
        """Deserialize metadata from a persisted dictionary."""
        origin_data = data.get("origin")
        origin = SessionOrigin(**origin_data) if origin_data else None
        
        return cls(
            session_id=data.get("session_id", str(uuid.uuid4())),
            session_key=data.get("session_key", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            agent_id=data.get("agent_id", "main"),
            channel=data.get("channel", "main"),
            account_id=data.get("account_id", "default"),
            peer_id=data.get("peer_id", "default"),
            title=data.get("title", ""),
            title_status=data.get("title_status", "empty"),
            display_name=data.get("display_name"),
            origin=origin,
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            context_tokens=data.get("context_tokens", 0),
            compaction_count=data.get("compaction_count", 0),
            last_compacted_at=datetime.fromisoformat(data["last_compacted_at"]) if data.get("last_compacted_at") else None,
            memory_flushed_this_cycle=data.get("memory_flushed_this_cycle", False),
            extra=data.get("extra", {}),
        )


@dataclass
class TranscriptEntry:
    """
    Transcript entry stored in JSONL.

    Attributes:
        timestamp: Entry timestamp.
        role: Message role such as `user`, `assistant`, `system`, or `tool`.
        content: Entry text content.
        tool_calls: Serialized tool-call payloads.
        tool_results: Serialized tool-result payloads.
        metadata: Additional transcript metadata.
    """
    timestamp: datetime = field(default_factory=datetime.now)
    role: str = "user"  # user | assistant | system | tool
    content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Serialize the transcript entry to a JSONL-friendly dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptEntry":
        """Deserialize a transcript entry from persisted data."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(),
            role=data.get("role", "user"),
            content=data.get("content", ""),
            tool_name=data.get("tool_name", ""),
            tool_call_id=data.get("tool_call_id", ""),
            tool_calls=data.get("tool_calls", []),
            tool_results=data.get("tool_results", []),
            metadata=data.get("metadata", {}),
        )
