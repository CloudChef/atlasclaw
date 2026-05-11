# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Unit tests for Gateway, frame models, idempotency, and message parsing."""

import json

import pytest

from app.atlasclaw.api.gateway import (
    ConnectFrame,
    ConnectionInfo,
    ConnectionState,
    EventFrame,
    Gateway,
    GatewayMessageParser,
    HelloOkFrame,
    IdempotencyCache,
    RequestFrame,
    ResponseFrame,
)


class TestIdempotencyCache:
    """IdempotencyCache tests."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test setting and retrieving a cached value."""
        cache = IdempotencyCache(ttl_seconds=60)
        await cache.set("key-1", {"result": "ok"})
        result = await cache.get("key-1")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        """Test retrieving a missing key."""
        cache = IdempotencyCache()
        result = await cache.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_entry(self):
        """Test expired cache entries."""
        cache = IdempotencyCache(ttl_seconds=0)
        await cache.set("key-1", "value")
        # A zero TTL expires immediately.
        result = await cache.get("key-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test cleanup of expired cache entries."""
        cache = IdempotencyCache(ttl_seconds=0)
        await cache.set("expired-1", "v1")
        await cache.set("expired-2", "v2")
        count = await cache.cleanup()
        assert count == 2


class TestFrameModels:
    """Frame model tests."""

    def test_connect_frame(self):
        """Test connect frames."""
        frame = ConnectFrame(
            device_id="device-123",
            auth_token="tok-xxx",
            platform="ios",
        )
        assert frame.type == "connect"
        assert frame.device_id == "device-123"
        assert frame.auth_token == "tok-xxx"

    def test_hello_ok_frame(self):
        """Test hello-ok frames."""
        frame = HelloOkFrame(
            connection_id="conn-1",
            server_time="2025-01-01T00:00:00Z",
        )
        assert frame.type == "hello-ok"
        assert frame.connection_id == "conn-1"

    def test_request_frame(self):
        """Test request frames."""
        frame = RequestFrame(
            id="req-1",
            method="agent.run",
            params={"message": "hello"},
            idempotency_key="idem-1",
        )
        assert frame.type == "req"
        assert frame.method == "agent.run"
        assert frame.idempotency_key == "idem-1"

    def test_response_frame_ok(self):
        """Test successful response frames."""
        frame = ResponseFrame(id="req-1", ok=True, payload={"data": 42})
        assert frame.ok
        assert frame.payload["data"] == 42

    def test_response_frame_error(self):
        """Test error response frames."""
        frame = ResponseFrame(
            id="req-1",
            ok=False,
            error={"code": "NOT_FOUND", "message": "Resource not found"},
        )
        assert not frame.ok
        assert frame.error["code"] == "NOT_FOUND"

    def test_event_frame(self):
        """Test event frames."""
        frame = EventFrame(
            event="message",
            payload={"text": "hello"},
            seq=1,
        )
        assert frame.type == "event"
        assert frame.seq == 1


class TestGateway:
    """Gateway tests."""

    @pytest.mark.asyncio
    async def test_connect(self):
        """Test connecting a device."""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1", platform="test")
        hello = await gw.connect("conn-1", frame)

        assert isinstance(hello, HelloOkFrame)
        assert hello.connection_id == "conn-1"

    @pytest.mark.asyncio
    async def test_connect_with_auth(self):
        """Test connecting with authentication."""
        def auth_handler(token):
            if token == "valid-token":
                return {"user_id": "u1", "tenant_id": "t1"}
            return None

        gw = Gateway(auth_handler=auth_handler)
        frame = ConnectFrame(device_id="dev-1", auth_token="valid-token")
        hello = await gw.connect("conn-1", frame)

        conn = await gw.get_connection("conn-1")
        assert conn.state == ConnectionState.AUTHENTICATED
        assert conn.user_id == "u1"

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnecting a connection."""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        await gw.disconnect("conn-1")
        assert await gw.get_connection("conn-1") is None

    @pytest.mark.asyncio
    async def test_handle_request_method_not_found(self):
        """Test requests for unknown methods."""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        req = RequestFrame(id="req-1", method="unknown.method")
        resp = await gw.handle_request("conn-1", req)
        assert not resp.ok
        assert resp.error["code"] == "METHOD_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_handle_request_not_connected(self):
        """Test requests from unknown connections."""
        gw = Gateway()
        req = RequestFrame(id="req-1", method="test")
        resp = await gw.handle_request("ghost", req)
        assert not resp.ok
        assert resp.error["code"] == "NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_handle_request_success(self):
        """Test successful request handling."""
        gw = Gateway()

        async def echo_handler(conn, params):
            return {"echo": params.get("msg", "")}

        gw.register_method("echo", echo_handler)

        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        req = RequestFrame(id="req-1", method="echo", params={"msg": "hello"})
        resp = await gw.handle_request("conn-1", req)
        assert resp.ok
        assert resp.payload["echo"] == "hello"

    @pytest.mark.asyncio
    async def test_handle_request_handler_error(self):
        """Test handler exceptions."""
        gw = Gateway()

        async def bad_handler(conn, params):
            raise RuntimeError("boom")

        gw.register_method("bad", bad_handler)

        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        req = RequestFrame(id="req-1", method="bad")
        resp = await gw.handle_request("conn-1", req)
        assert not resp.ok
        assert resp.error["code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_idempotency(self):
        """Test idempotency cache reuse."""
        gw = Gateway()
        call_count = 0

        async def counting_handler(conn, params):
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        gw.register_method("count", counting_handler)

        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        req = RequestFrame(
            id="req-1", method="count", idempotency_key="idem-abc",
        )

        resp1 = await gw.handle_request("conn-1", req)
        resp2 = await gw.handle_request("conn-1", req)

        # The handler should only be called once.
        assert call_count == 1
        assert resp1.payload == resp2.payload

    @pytest.mark.asyncio
    async def test_idempotency_is_scoped_per_connection(self):
        """The same client key must not share cached responses across connections."""
        gw = Gateway()

        async def whoami_handler(conn, params):
            return {"user_id": conn.user_id, "message": params["message"]}

        gw.register_method("whoami", whoami_handler)

        await gw.connect("conn-alice", ConnectFrame(device_id="dev-a"))
        await gw.connect("conn-bob", ConnectFrame(device_id="dev-b"))

        alice_conn = await gw.get_connection("conn-alice")
        bob_conn = await gw.get_connection("conn-bob")
        assert alice_conn is not None
        assert bob_conn is not None
        alice_conn.user_id = "alice"
        bob_conn.user_id = "bob"

        alice_req = RequestFrame(
            id="req-alice",
            method="whoami",
            params={"message": "from-alice"},
            idempotency_key="shared-key",
        )
        bob_req = RequestFrame(
            id="req-bob",
            method="whoami",
            params={"message": "from-bob"},
            idempotency_key="shared-key",
        )

        alice_resp = await gw.handle_request("conn-alice", alice_req)
        bob_resp = await gw.handle_request("conn-bob", bob_req)

        assert alice_resp.payload == {"user_id": "alice", "message": "from-alice"}
        assert bob_resp.payload == {"user_id": "bob", "message": "from-bob"}

    @pytest.mark.asyncio
    async def test_push_event(self):
        """Test pushing events."""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        event = await gw.push_event("conn-1", "notification", {"msg": "hi"})
        assert event is not None
        assert event.event == "notification"
        assert event.seq == 1

    @pytest.mark.asyncio
    async def test_push_event_seq_increments(self):
        """Test event sequence increments."""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        e1 = await gw.push_event("conn-1", "a", {})
        e2 = await gw.push_event("conn-1", "b", {})
        assert e2.seq > e1.seq

    @pytest.mark.asyncio
    async def test_push_event_no_connection(self):
        """Test pushing events to a missing connection."""
        gw = Gateway()
        result = await gw.push_event("ghost", "test", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_broadcast_event(self):
        """Test broadcasting events."""
        gw = Gateway()
        for i in range(3):
            await gw.connect(f"conn-{i}", ConnectFrame(device_id=f"dev-{i}"))

        recipients = await gw.broadcast_event("update", {"version": "2.0"})
        assert len(recipients) == 3

    @pytest.mark.asyncio
    async def test_broadcast_with_filter(self):
        """Test filtered broadcasts."""
        gw = Gateway()
        gw._auth_handler = lambda t: {"user_id": t, "tenant_id": "t1"}

        await gw.connect("conn-a", ConnectFrame(device_id="a", auth_token="u1"))
        await gw.connect("conn-b", ConnectFrame(device_id="b", auth_token="u2"))

        recipients = await gw.broadcast_event(
            "private", {"data": 1},
            filter_fn=lambda c: c.user_id == "u1",
        )
        assert recipients == ["conn-a"]

    @pytest.mark.asyncio
    async def test_list_connections(self):
        """Test listing connections."""
        gw = Gateway()
        await gw.connect("c1", ConnectFrame(device_id="d1"))
        await gw.connect("c2", ConnectFrame(device_id="d2"))

        conns = gw.list_connections()
        assert len(conns) == 2

    @pytest.mark.asyncio
    async def test_method_decorator(self):
        """Test the method decorator."""
        gw = Gateway()

        @gw.method("greet")
        async def greet(conn, params):
            return {"greeting": f"Hello {params.get('name', 'World')}"}

        await gw.connect("c1", ConnectFrame(device_id="d1"))
        req = RequestFrame(id="r1", method="greet", params={"name": "Test"})
        resp = await gw.handle_request("c1", req)
        assert resp.ok
        assert resp.payload["greeting"] == "Hello Test"


class TestGatewayMessageParser:
    """GatewayMessageParser tests."""

    def test_parse_connect(self):
        """Test parsing connect frames."""
        msg = json.dumps({"type": "connect", "device_id": "dev-1"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type == "connect"
        assert frame.device_id == "dev-1"
        assert error is None

    def test_parse_request(self):
        """Test parsing request frames."""
        msg = json.dumps({"type": "req", "id": "r1", "method": "test"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type == "req"
        assert frame.method == "test"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        frame_type, frame, error = GatewayMessageParser.parse("not json")
        assert frame_type is None
        assert error is not None
        assert "Invalid JSON" in error

    def test_parse_unknown_type(self):
        """Test parsing unknown frame types."""
        msg = json.dumps({"type": "unknown"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type is None
        assert "Unknown frame type" in error

    def test_serialize(self):
        """Test frame serialization."""
        frame = HelloOkFrame(connection_id="c1", server_time="now")
        serialized = GatewayMessageParser.serialize(frame)
        data = json.loads(serialized)
        assert data["type"] == "hello-ok"
        assert data["connection_id"] == "c1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
