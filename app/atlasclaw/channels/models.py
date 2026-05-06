# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class ChannelMode(Enum):
    """Channel operation mode."""
    INBOUND = "inbound"           # Only receives messages
    OUTBOUND = "outbound"         # Only sends messages
    BIDIRECTIONAL = "bidirectional"  # Both sends and receives


class ConnectionStatus(Enum):
    """Channel connection status."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()


@dataclass
class InboundMessage:
    """Standard format for inbound messages from any channel."""
    message_id: str
    sender_id: str
    sender_name: str
    chat_id: str
    channel_type: str
    content: str
    content_type: str = "text"  # text, markdown, html
    thread_id: Optional[str] = None
    reply_to: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class OutboundMessage:
    """Standard format for outbound messages to any channel."""
    chat_id: str
    content: str
    content_type: str = "text"
    thread_id: Optional[str] = None
    reply_to: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SendResult:
    """Result of sending a message."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MessageAcknowledgementResult:
    """Result of a native inbound-message acknowledgement attempt.

    This result models platform-visible receipt or processing state, not a
    normal outbound text reply. Unsupported channels return `supported=False`
    so the manager can continue Agent processing without sending fallback text.
    """
    supported: bool
    success: bool
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelValidationResult:
    """Result of validating channel configuration."""
    valid: bool
    errors: List[str] = field(default_factory=list)


@dataclass
class ChannelConnection:
    """Channel connection configuration."""
    id: str
    name: str
    channel_type: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
