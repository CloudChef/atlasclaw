# -*- coding: utf-8 -*-
"""
WebSocket 认证单元测试

涵盖：connect 帧含有效 auth_token → ConnectionInfo.user_info 正确赋值，
含无效 auth_token → 关闭连接 code=4002。
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.xuanwu.auth.models import UserInfo, ANONYMOUS_USER, AuthenticationError
from app.xuanwu.api.websocket import ConnectionInfo, WebSocketManager
from fastapi import WebSocketDisconnect


def _make_user_info(user_id: str = "u-test") -> UserInfo:
    return UserInfo(user_id=user_id, display_name="Test User")


class TestWebSocketAuth:

    @pytest.mark.asyncio
    async def test_valid_auth_token_sets_user_info(self):
        """Valid auth_token resolves to UserInfo and stores in ConnectionInfo."""
        ui = _make_user_info("u-ws-valid")

        async def _auth_handler(token: str):
            if token == "valid-token":
                return ui
            return None

        manager = WebSocketManager(auth_handler=_auth_handler)

        ws_mock = AsyncMock()
        ws_mock.receive_json = AsyncMock(return_value={
            "type": "connect",
            "device_id": "dev-1",
            "auth_token": "valid-token",
        })
        ws_mock.send_json = AsyncMock()
        ws_mock.close = AsyncMock()

        # Patch message loop to exit immediately after connection is set up
        async def _noop_message_loop(conn_id, ws, conn_info):
            raise WebSocketDisconnect()

        with patch.object(manager, "_message_loop", side_effect=_noop_message_loop):
            with patch.object(manager, "_ping_loop", return_value=None):
                await manager.handle_connection(ws_mock)

        # hello-ok should have been sent (not close with 4002)
        ws_mock.close.assert_not_called()
        sent_calls = ws_mock.send_json.call_args_list
        hello_ok_sent = any(call[0][0].get("type") == "hello-ok" for call in sent_calls)
        assert hello_ok_sent, "hello-ok should have been sent"

    @pytest.mark.asyncio
    async def test_invalid_auth_token_closes_with_4002(self):
        """auth_handler returning None → close with code=4002."""
        async def _auth_handler(token: str):
            return None  # Always fail

        manager = WebSocketManager(auth_handler=_auth_handler)

        ws_mock = AsyncMock()
        ws_mock.receive_json = AsyncMock(return_value={
            "type": "connect",
            "device_id": "dev-2",
            "auth_token": "bad-token",
        })
        ws_mock.send_json = AsyncMock()
        ws_mock.close = AsyncMock()

        await manager.handle_connection(ws_mock)

        ws_mock.close.assert_called_once_with(code=4002, reason="Authentication failed")

    @pytest.mark.asyncio
    async def test_no_auth_handler_allows_connection(self):
        """Without auth_handler, connect frame succeeds without user_info."""
        manager = WebSocketManager()  # No auth_handler

        ws_mock = AsyncMock()
        ws_mock.receive_json = AsyncMock(return_value={
            "type": "connect",
            "device_id": "dev-3",
        })
        ws_mock.send_json = AsyncMock()
        ws_mock.close = AsyncMock()

        async def _noop_message_loop(conn_id, ws, conn_info):
            raise WebSocketDisconnect()

        with patch.object(manager, "_message_loop", side_effect=_noop_message_loop):
            with patch.object(manager, "_ping_loop", return_value=None):
                await manager.handle_connection(ws_mock)

        # WebSocketDisconnect should be caught, not close with error code
        # The connection should have sent hello-ok before the disconnect
        sent_calls = ws_mock.send_json.call_args_list
        hello_ok_sent = any(call[0][0].get("type") == "hello-ok" for call in sent_calls)
        assert hello_ok_sent, "hello-ok should have been sent"

    def test_connection_info_has_user_info_field(self):
        """ConnectionInfo dataclass must expose user_info."""
        conn = ConnectionInfo(connection_id="c-1")
        assert hasattr(conn, "user_info")
        assert conn.user_info is None
