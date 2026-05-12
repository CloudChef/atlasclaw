# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.atlasclaw.api.websocket import ConnectionInfo, WebSocketManager


class TestWebSocketIdempotency:
    """WebSocket idempotency cache behavior."""

    @pytest.mark.asyncio
    async def test_idempotency_is_scoped_per_connection(self):
        """The same client key must not share cached responses across connections."""
        manager = WebSocketManager()

        async def whoami_handler(conn_info, message):
            return {"user_id": conn_info.user_id, "message": message}

        manager.register_handler("whoami", whoami_handler)

        alice_ws = AsyncMock()
        bob_ws = AsyncMock()
        alice = ConnectionInfo(connection_id="conn-alice", user_id="alice")
        bob = ConnectionInfo(connection_id="conn-bob", user_id="bob")

        await manager._handle_request(
            alice_ws,
            alice,
            {
                "id": "req-alice",
                "method": "whoami",
                "params": {"message": "from-alice"},
                "idempotency_key": "shared-key",
            },
        )
        await manager._handle_request(
            bob_ws,
            bob,
            {
                "id": "req-bob",
                "method": "whoami",
                "params": {"message": "from-bob"},
                "idempotency_key": "shared-key",
            },
        )

        alice_payload = alice_ws.send_json.call_args_list[-1][0][0]["payload"]
        bob_payload = bob_ws.send_json.call_args_list[-1][0][0]["payload"]

        assert alice_payload == {"user_id": "alice", "message": "from-alice"}
        assert bob_payload == {"user_id": "bob", "message": "from-bob"}
