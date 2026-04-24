# Copyright 2021  Qianyun, Inc. All rights reserved.


"""API package for AtlasClaw transport integrations.

This package exposes the REST router plus the WebSocket and SSE managers used
by the AtlasClaw runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .routes import APIContext, create_router
    from .sse import SSEEvent, SSEManager
    from .websocket import ConnectionInfo, WebSocketManager

__all__ = [
    "create_router",
    "APIContext",
    "WebSocketManager",
    "ConnectionInfo",
    "SSEManager",
    "SSEEvent",
]


def __getattr__(name: str) -> Any:
    if name in {"create_router", "APIContext"}:
        from .routes import APIContext, create_router

        return {
            "create_router": create_router,
            "APIContext": APIContext,
        }[name]
    if name in {"WebSocketManager", "ConnectionInfo"}:
        from .websocket import ConnectionInfo, WebSocketManager

        return {
            "WebSocketManager": WebSocketManager,
            "ConnectionInfo": ConnectionInfo,
        }[name]
    if name in {"SSEManager", "SSEEvent"}:
        from .sse import SSEEvent, SSEManager

        return {
            "SSEManager": SSEManager,
            "SSEEvent": SSEEvent,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
