# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel management for AtlasClaw."""

from __future__ import annotations

from .handler import ChannelHandler
from .manager import ChannelManager
from .models import (
    ChannelConnection,
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    MessageAcknowledgementResult,
    OutboundMessage,
    SendResult,
)
from .registry import ChannelRegistry

__all__ = [
    "ChannelHandler",
    "ChannelManager",
    "ChannelRegistry",
    "ChannelConnection",
    "ChannelMode",
    "ChannelValidationResult",
    "ConnectionStatus",
    "InboundMessage",
    "MessageAcknowledgementResult",
    "OutboundMessage",
    "SendResult",
]
