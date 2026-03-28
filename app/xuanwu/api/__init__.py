"""API package for Xuanwu transport integrations.

This package exposes the REST router plus the WebSocket and SSE managers used
by the Xuanwu runtime.
"""

from .routes import create_router, APIContext
from .websocket import WebSocketManager, ConnectionInfo
from .sse import SSEManager, SSEEvent

__all__ = [
    "create_router",
    "APIContext",
    "WebSocketManager",
    "ConnectionInfo",
    "SSEManager",
    "SSEEvent",
]
