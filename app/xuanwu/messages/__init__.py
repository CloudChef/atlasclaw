"""Message processing package.

This package exposes inbound and outbound message models together with command
parsing and response-shaping helpers.
"""

from .handler import MessageHandler, InboundMessage, OutboundMessage
from .command import CommandParser, CommandDefinition, ParsedCommand

__all__ = [
    "MessageHandler",
    "InboundMessage",
    "OutboundMessage",
    "CommandParser",
    "CommandDefinition",
    "ParsedCommand",
]
