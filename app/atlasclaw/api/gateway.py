# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..auth.models import UserInfo


class FrameType(Enum):
    """Frame type"""
    CONNECT = "connect"  # Connection bootstrap frame
    HELLO_OK = "hello-ok"  # Successful connection handshake
    REQUEST = "req"  # Client request frame
    RESPONSE = "res"  # Server response frame
    EVENT = "event"  # Server-pushed event frame


class ConnectionState(Enum):
    """Lifecycle state for a gateway connection."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


@dataclass
class DeviceInfo:
    """Device information"""
    device_id: str
    platform: str = ""
    version: str = ""
    user_agent: str = ""


@dataclass
class ConnectionInfo:
    """Connection information"""
    connection_id: str
    device: DeviceInfo
    state: ConnectionState = ConnectionState.CONNECTING
    authenticated_at: Optional[datetime] = None
    user_id: str = ""
    tenant_id: str = ""
    user_info: Optional[Any] = None  # Optional[UserInfo]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class ConnectFrame(BaseModel):
    """Connection frame"""
    type: str = "connect"
    device_id: str
    auth_token: Optional[str] = None
    platform: str = ""
    version: str = ""


class HelloOkFrame(BaseModel):
    """Connection success frame"""
    type: str = "hello-ok"
    connection_id: str
    server_time: str
    presence: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)


class RequestFrame(BaseModel):
    """Request frame"""
    type: str = "req"
    id: str  # ID
    method: str  # method name
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None  # etc.


class ResponseFrame(BaseModel):
    """Response frame"""
    type: str = "res"
    id: str  # corresponds to ID
    ok: bool
    payload: Optional[Any] = None
    error: Optional[dict[str, Any]] = None


class EventFrame(BaseModel):
    """Event frame"""
    type: str = "event"
    event: str  # event name
    payload: Any
    seq: Optional[int] = None  # 
    state_version: Optional[str] = None  # 


class IdempotencyCache:
    """
    In-memory cache for idempotent request responses.
    """
    
    def __init__(self, ttl_seconds: int = 300) -> None:
        """Initialize the cache with a time-to-live in seconds."""
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Return the cached response for a key when it is still valid."""
        async with self._lock:
            if key in self._cache:
                timestamp, response = self._cache[key]
                if time.time() - timestamp < self._ttl:
                    return response
                del self._cache[key]
        return None
    
    async def set(self, key: str, response: Any) -> None:
        """Store a response for a given idempotency key."""
        async with self._lock:
            self._cache[key] = (time.time(), response)
    
    async def cleanup(self) -> int:
        """Remove expired cache entries and return the number removed."""
        now = time.time()
        expired = []
        
        async with self._lock:
            for key, (timestamp, _) in self._cache.items():
                if now - timestamp >= self._ttl:
                    expired.append(key)
            
            for key in expired:
                del self._cache[key]
        
        return len(expired)


class Gateway:
    """
    WebSocket gateway for request/response and event traffic.

    The gateway manages connection lifecycle, method registration, request
    dispatch, idempotency handling, and event fan-out.

    Example usage::
        gateway = Gateway()

        # Register a request handler.
        @gateway.method("agent.run")
        async def handle_agent_run(conn: ConnectionInfo, params: dict) -> dict:
            return {"run_id": "xxx"}

        # Handle an incoming WebSocket frame.
        response = await gateway.handle_message(conn_id, message_json)
    """
    
    def __init__(
        self,
        *,
        idempotency_ttl: int = 300,
        auth_handler: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """Initialize the gateway."""
        self._connections: dict[str, ConnectionInfo] = {}
        self._methods: dict[str, Callable] = {}
        self._idempotency_cache = IdempotencyCache(idempotency_ttl)
        self._auth_handler = auth_handler
        self._event_seq: dict[str, int] = {}  # Per-connection event sequence
        self._lock = asyncio.Lock()
    
    def method(self, name: str) -> Callable:
        """


        
        register handle.
        
        Args:
            name:method name
        
"""
        def decorator(func: Callable) -> Callable:
            self._methods[name] = func
            return func
        return decorator
    
    def register_method(self, name: str, handler: Callable) -> None:
        """register"""
        self._methods[name] = handler

    @staticmethod
    def _idempotency_cache_key(conn: ConnectionInfo, key: str) -> str:
        """Return a connection-scoped cache key for a client idempotency key."""
        return f"{conn.connection_id}:{key}"
    
    async def connect(
        self,
        connection_id: str,
        frame: ConnectFrame,
    ) -> HelloOkFrame:
        """

handleconnection
        
        Args:
            connection_id:connection ID
            frame:Connection frame
            
        Returns:
            Connection success frame
        
"""
        device = DeviceInfo(
            device_id=frame.device_id,
            platform=frame.platform,
            version=frame.version,
        )
        
        conn = ConnectionInfo(
            connection_id=connection_id,
            device=device,
            state=ConnectionState.CONNECTED,
        )
        
        # 
        if frame.auth_token and self._auth_handler:
            if asyncio.iscoroutinefunction(self._auth_handler):
                auth_user_info = await self._auth_handler(frame.auth_token)
            else:
                auth_user_info = self._auth_handler(frame.auth_token)
            if auth_user_info:
                conn.user_info = auth_user_info
                conn.user_id = getattr(auth_user_info, "user_id", "") or auth_user_info.get("user_id", "") if isinstance(auth_user_info, dict) else getattr(auth_user_info, "user_id", "")
                conn.tenant_id = getattr(auth_user_info, "tenant_id", "") or auth_user_info.get("tenant_id", "") if isinstance(auth_user_info, dict) else getattr(auth_user_info, "tenant_id", "")
                conn.state = ConnectionState.AUTHENTICATED
                conn.authenticated_at = datetime.now(timezone.utc)
        
        async with self._lock:
            self._connections[connection_id] = conn
            self._event_seq[connection_id] = 0
        
        return HelloOkFrame(
            connection_id=connection_id,
            server_time=datetime.now(timezone.utc).isoformat(),
            presence={"online": True, "user_id": conn.user_id},
            health={"status": "healthy"},
        )
    
    async def disconnect(self, connection_id: str) -> None:
        """

connection
        
        Args:
            connection_id:connection ID
        
"""
        async with self._lock:
            if connection_id in self._connections:
                self._connections[connection_id].state = ConnectionState.DISCONNECTED
                del self._connections[connection_id]
            if connection_id in self._event_seq:
                del self._event_seq[connection_id]
    
    async def get_connection(self, connection_id: str) -> Optional[ConnectionInfo]:
        """Get connection information"""
        return self._connections.get(connection_id)
    
    async def handle_request(
        self,
        connection_id: str,
        frame: RequestFrame,
    ) -> ResponseFrame:
        """
Handle a request
        
        Args:
            connection_id:connection ID
            frame:Request frame
            
        Returns:
            Response frame
        
"""
        conn = self._connections.get(connection_id)
        if not conn:
            return ResponseFrame(
                id=frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Connection not found"},
            )
        
        # 
        conn.last_activity = datetime.now(timezone.utc)
        
        # check etc.
        idempotency_cache_key = None
        if frame.idempotency_key:
            idempotency_cache_key = self._idempotency_cache_key(conn, frame.idempotency_key)
            cached = await self._idempotency_cache.get(idempotency_cache_key)
            if cached is not None:
                return ResponseFrame(
                    id=frame.id,
                    ok=True,
                    payload=cached,
                )
        
        # handle
        handler = self._methods.get(frame.method)
        if not handler:
            return ResponseFrame(
                id=frame.id,
                ok=False,
                error={"code": "METHOD_NOT_FOUND", "message": f"Unknown method: {frame.method}"},
            )
        
        # execute handle
        try:
            result = await handler(conn, frame.params)
            
            # etc.
            if idempotency_cache_key:
                await self._idempotency_cache.set(idempotency_cache_key, result)
            
            return ResponseFrame(
                id=frame.id,
                ok=True,
                payload=result,
            )
        except Exception as e:
            return ResponseFrame(
                id=frame.id,
                ok=False,
                error={"code": "INTERNAL_ERROR", "message": str(e)},
            )
    
    async def push_event(
        self,
        connection_id: str,
        event: str,
        payload: Any,
        *,
        state_version: Optional[str] = None,
    ) -> Optional[EventFrame]:
        """


        
        Args:
            connection_id:connection ID
            event:event name
            payload:event payload
            state_version:
            
        Returns:
            Event frameor None(connection at)
        
"""
        conn = self._connections.get(connection_id)
        if not conn:
            return None
        
        async with self._lock:
            seq = self._event_seq.get(connection_id, 0) + 1
            self._event_seq[connection_id] = seq
        
        return EventFrame(
            event=event,
            payload=payload,
            seq=seq,
            state_version=state_version,
        )
    
    async def broadcast_event(
        self,
        event: str,
        payload: Any,
        *,
        filter_fn: Optional[Callable[[ConnectionInfo], bool]] = None,
    ) -> list[str]:
        """


        
        Args:
            event:event name
            payload:event payload
            filter_fn:filter count
            
        Returns:
            connection ID list
        
"""
        recipients = []
        
        for conn_id, conn in self._connections.items():
            if filter_fn and not filter_fn(conn):
                continue
            
            frame = await self.push_event(conn_id, event, payload)
            if frame:
                recipients.append(conn_id)
        
        return recipients
    
    def list_connections(self) -> list[ConnectionInfo]:
        """connection"""
        return list(self._connections.values())
    
    async def cleanup_idle_connections(self, idle_seconds: int = 300) -> list[str]:
        """

connection
        
        Args:
            idle_seconds:timeout in seconds
            
        Returns:
            connection ID list
        
"""
        now = datetime.now(timezone.utc)
        idle = []
        
        for conn_id, conn in self._connections.items():
            delta = (now - conn.last_activity).total_seconds()
            if delta > idle_seconds:
                idle.append(conn_id)
        
        for conn_id in idle:
            await self.disconnect(conn_id)
        
        return idle


class GatewayMessageParser:
    """


messageparse
 
 parseand WebSocket JSON message.
 
"""
    
    @staticmethod
    def parse(message: str) -> tuple[Optional[str], Optional[BaseModel], Optional[str]]:
        """

parsemessage
        
        Args:
            message:JSON messagecharacters
            
        Returns:
            (Frame type, parse,)
        
"""
        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            return None, None, f"Invalid JSON: {e}"
        
        frame_type = data.get("type")
        
        if frame_type == "connect":
            try:
                return "connect", ConnectFrame(**data), None
            except Exception as e:
                return None, None, f"Invalid connect frame: {e}"
        
        elif frame_type == "req":
            try:
                return "req", RequestFrame(**data), None
            except Exception as e:
                return None, None, f"Invalid request frame: {e}"
        
        else:
            return None, None, f"Unknown frame type: {frame_type}"
    
    @staticmethod
    def serialize(frame: BaseModel) -> str:
        """JSON"""
        return frame.model_dump_json()
