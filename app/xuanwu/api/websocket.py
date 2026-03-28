# -*- coding: utf-8 -*-
"""



WebSocket manager

implement WebSocket connection management, streaming, heartbeat-related features.
corresponds to tasks.md 7.1 and 7.3.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from ..auth.models import UserInfo


class FrameType(Enum):
    """WebSocket frame type"""
    CONNECT = "connect"
    HELLO_OK = "hello-ok"
    REQUEST = "req"
    RESPONSE = "res"
    EVENT = "event"
    PING = "ping"
    PONG = "pong"


@dataclass
class ConnectionInfo:
    """

Connection information
    
    Attributes:
        connection_id:connection ID
        device_id:ID
        user_id:user ID
        auth_token:
        user_info: Authenticated UserInfo (Optional[UserInfo])
        connected_at:connection
        last_ping:ping
        session_keys:session keylist
        metadata:additional metadata
    
"""
    connection_id: str
    device_id: str = ""
    user_id: str = ""
    auth_token: str = ""
    user_info: Optional[Any] = None  # Optional[UserInfo]
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_ping: float = field(default_factory=time.time)
    session_keys: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class WebSocketManager:
    """


WebSocket manager
 
 management WebSocket connection, handle messages and streaming.
 
 for mat:
 - connect:, contains and
 - hello-ok:connection, contains presence and health snapshot
 - req/res:/ mode
 - event:
 - ping/pong:heartbeat
 
 Example usage::
 
 manager = WebSocketManager()
 
 @app.websocket("/ws")
 async def websocket_endpoint(websocket:WebSocket):
 await manager.handle_connection(websocket)
 
"""
    
    def __init__(
        self,
        *,
        ping_interval: float = 30.0,
        ping_timeout: float = 10.0,
        auth_handler: Optional[Callable[[str], Coroutine[Any, Any, Optional[Any]]]] = None,
    ) -> None:
        """

initialize WebSocket manager
        
        Args:
            ping_interval:heartbeat()
            ping_timeout:heartbeat()
            auth_handler: Async callable that receives auth_token and returns Optional[UserInfo]
        
"""
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._auth_handler = auth_handler
        
        # connection
        self._connections: dict[str, tuple[WebSocket, ConnectionInfo]] = {}
        
        # session
        self._session_subscribers: dict[str, set[str]] = {}
        
        # handle
        self._request_handlers: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}
        
        # etc.
        self._idempotency_cache: dict[str, tuple[Any, float]] = {}
        self._idempotency_ttl = 60.0  # 60
        
    def register_handler(
        self,
        method: str,
        handler: Callable[..., Coroutine[Any, Any, Any]]
    ) -> None:
        """

register handle
        
        Args:
            method:method name
            handler:handle count
        
"""
        self._request_handlers[method] = handler
        
    async def handle_connection(self, websocket: WebSocket) -> None:
        """
handle WebSocket connection
        
        Args:
            websocket:WebSocket connection
        
"""
        connection_id = str(uuid.uuid4())
        
        try:
            await websocket.accept()
            
            # etc. connect
            connect_data = await self._wait_for_connect(websocket)
            if not connect_data:
                await websocket.close(code=4001, reason="Invalid connect frame")
                return
                
            # 
            user_info = None
            if self._auth_handler and connect_data.get("auth_token"):
                user_info = await self._auth_handler(connect_data["auth_token"])
                if not user_info:
                    await websocket.close(code=4002, reason="Authentication failed")
                    return
                    
            # createConnection information
            conn_info = ConnectionInfo(
                connection_id=connection_id,
                device_id=connect_data.get("device_id", ""),
                user_id=user_info.user_id if user_info else connect_data.get("user_id", ""),
                auth_token=connect_data.get("auth_token", ""),
                user_info=user_info,
            )
            
            # registerconnection
            self._connections[connection_id] = (websocket, conn_info)
            
            # hello-ok
            await self._send_frame(websocket, {
                "type": FrameType.HELLO_OK.value,
                "connection_id": connection_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # heartbeat
            ping_task = asyncio.create_task(self._ping_loop(connection_id))
            
            # message
            try:
                await self._message_loop(connection_id, websocket, conn_info)
            finally:
                ping_task.cancel()
                
        except WebSocketDisconnect:
            pass
        except Exception as e:
            await websocket.close(code=4000, reason=str(e))
        finally:
            self._cleanup_connection(connection_id)
            
    async def _wait_for_connect(
        self,
        websocket: WebSocket,
        timeout: float = 5.0
    ) -> Optional[dict[str, Any]]:
        """etc. connect"""
        try:
            data = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=timeout
            )
            
            if data.get("type") != FrameType.CONNECT.value:
                return None
                
            return data
            
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None
            
    async def _message_loop(
        self,
        connection_id: str,
        websocket: WebSocket,
        conn_info: ConnectionInfo
    ) -> None:
        """message handling"""
        while True:
            try:
                data = await websocket.receive_json()
                await self._handle_frame(connection_id, websocket, conn_info, data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # 
                await self._send_frame(websocket, {
                    "type": "error",
                    "error": str(e)
                })
                
    async def _handle_frame(
        self,
        connection_id: str,
        websocket: WebSocket,
        conn_info: ConnectionInfo,
        data: dict[str, Any]
    ) -> None:
        """handle to"""
        frame_type = data.get("type")
        
        if frame_type == FrameType.PING.value:
            # pong
            conn_info.last_ping = time.time()
            await self._send_frame(websocket, {"type": FrameType.PONG.value})
            
        elif frame_type == FrameType.REQUEST.value:
            # Handle a request
            await self._handle_request(websocket, conn_info, data)
            
    async def _handle_request(
        self,
        websocket: WebSocket,
        conn_info: ConnectionInfo,
        data: dict[str, Any]
    ) -> None:
        """Handle a request"""
        request_id = data.get("id")
        method = data.get("method")
        params = data.get("params", {})
        idempotency_key = data.get("idempotency_key")
        
        # etc.check
        if idempotency_key:
            cached = self._check_idempotency(idempotency_key)
            if cached is not None:
                await self._send_frame(websocket, {
                    "type": FrameType.RESPONSE.value,
                    "id": request_id,
                    "ok": True,
                    "payload": cached
                })
                return
                
        # handle
        handler = self._request_handlers.get(method)
        if not handler:
            await self._send_frame(websocket, {
                "type": FrameType.RESPONSE.value,
                "id": request_id,
                "ok": False,
                "error": f"Unknown method: {method}"
            })
            return
            
        # execute handle
        try:
            result = await handler(conn_info, **params)
            
            # etc.
            if idempotency_key:
                self._cache_idempotency(idempotency_key, result)
                
            await self._send_frame(websocket, {
                "type": FrameType.RESPONSE.value,
                "id": request_id,
                "ok": True,
                "payload": result
            })
            
        except Exception as e:
            await self._send_frame(websocket, {
                "type": FrameType.RESPONSE.value,
                "id": request_id,
                "ok": False,
                "error": str(e)
            })
            
    async def _ping_loop(self, connection_id: str) -> None:
        """heartbeat"""
        while connection_id in self._connections:
            await asyncio.sleep(self._ping_interval)
            
            conn = self._connections.get(connection_id)
            if not conn:
                break
                
            websocket, conn_info = conn
            
            # check
            if time.time() - conn_info.last_ping > self._ping_interval + self._ping_timeout:
                await websocket.close(code=4003, reason="Ping timeout")
                break
                
            # ping
            try:
                await self._send_frame(websocket, {"type": FrameType.PING.value})
            except Exception:
                break
                
    async def _send_frame(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """"""
        await websocket.send_json(data)
        
    def _cleanup_connection(self, connection_id: str) -> None:
        """connection"""
        conn = self._connections.pop(connection_id, None)
        if conn:
            _, conn_info = conn
            # session
            for session_key in conn_info.session_keys:
                if session_key in self._session_subscribers:
                    self._session_subscribers[session_key].discard(connection_id)
                    
    def _check_idempotency(self, key: str) -> Optional[Any]:
        """check etc."""
        # entry
        now = time.time()
        expired = [k for k, (_, t) in self._idempotency_cache.items()
                  if now - t > self._idempotency_ttl]
        for k in expired:
            del self._idempotency_cache[k]
            
        cached = self._idempotency_cache.get(key)
        if cached:
            return cached[0]
        return None
        
    def _cache_idempotency(self, key: str, result: Any) -> None:
        """etc."""
        self._idempotency_cache[key] = (result, time.time())
        
    # ----- -----
    
    async def push_event(
        self,
        connection_id: str,
        event: str,
        payload: Any,
        *,
        seq: Optional[int] = None,
    ) -> bool:
        """

connection
        
        Args:
            connection_id:connection ID
            event:event name
            payload:event payload
            seq:(optional)
            
        Returns:
            
        
"""
        conn = self._connections.get(connection_id)
        if not conn:
            return False
            
        websocket, _ = conn
        
        frame = {
            "type": FrameType.EVENT.value,
            "event": event,
            "payload": payload
        }
        if seq is not None:
            frame["seq"] = seq
            
        try:
            await self._send_frame(websocket, frame)
            return True
        except Exception:
            return False
            
    async def broadcast_to_session(
        self,
        session_key: str,
        event: str,
        payload: Any
    ) -> int:
        """

session
        
        Args:
            session_key:session key
            event:event name
            payload:event payload
            
        Returns:
            connectioncount
        
"""
        subscribers = self._session_subscribers.get(session_key, set())
        sent = 0
        
        for conn_id in list(subscribers):
            if await self.push_event(conn_id, event, payload):
                sent += 1
                
        return sent
        
    def subscribe_session(self, connection_id: str, session_key: str) -> bool:
        """

session
        
        Args:
            connection_id:connection ID
            session_key:session key
            
        Returns:
            
        
"""
        conn = self._connections.get(connection_id)
        if not conn:
            return False
            
        _, conn_info = conn
        
        if session_key not in conn_info.session_keys:
            conn_info.session_keys.append(session_key)
            
        if session_key not in self._session_subscribers:
            self._session_subscribers[session_key] = set()
        self._session_subscribers[session_key].add(connection_id)
        
        return True
        
    def unsubscribe_session(self, connection_id: str, session_key: str) -> bool:
        """

session
        
        Args:
            connection_id:connection ID
            session_key:session key
            
        Returns:
            
        
"""
        conn = self._connections.get(connection_id)
        if conn:
            _, conn_info = conn
            if session_key in conn_info.session_keys:
                conn_info.session_keys.remove(session_key)
                
        if session_key in self._session_subscribers:
            self._session_subscribers[session_key].discard(connection_id)
            
        return True
        
    def get_connection_count(self) -> int:
        """get connectioncount"""
        return len(self._connections)
        
    def get_connection_info(self, connection_id: str) -> Optional[ConnectionInfo]:
        """Get connection information"""
        conn = self._connections.get(connection_id)
        if conn:
            return conn[1]
        return None
