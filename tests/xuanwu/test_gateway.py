# -*- coding: utf-8 -*-
"""
Gateway 网关模块单元测试

测试 Gateway、IdempotencyCache、Frame 协议、GatewayMessageParser 等组件。
"""

import json

import pytest

from app.xuanwu.api.gateway import (
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
    """IdempotencyCache 测试类"""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """测试设置和获取"""
        cache = IdempotencyCache(ttl_seconds=60)
        await cache.set("key-1", {"result": "ok"})
        result = await cache.get("key-1")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        """测试获取不存在的键"""
        cache = IdempotencyCache()
        result = await cache.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_entry(self):
        """测试过期条目"""
        cache = IdempotencyCache(ttl_seconds=0)
        await cache.set("key-1", "value")
        # TTL 为 0，立即过期
        result = await cache.get("key-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """测试清理过期条目"""
        cache = IdempotencyCache(ttl_seconds=0)
        await cache.set("expired-1", "v1")
        await cache.set("expired-2", "v2")
        count = await cache.cleanup()
        assert count == 2


class TestFrameModels:
    """帧模型测试类"""

    def test_connect_frame(self):
        """测试连接帧"""
        frame = ConnectFrame(
            device_id="device-123",
            auth_token="tok-xxx",
            platform="ios",
        )
        assert frame.type == "connect"
        assert frame.device_id == "device-123"
        assert frame.auth_token == "tok-xxx"

    def test_hello_ok_frame(self):
        """测试连接成功帧"""
        frame = HelloOkFrame(
            connection_id="conn-1",
            server_time="2025-01-01T00:00:00Z",
        )
        assert frame.type == "hello-ok"
        assert frame.connection_id == "conn-1"

    def test_request_frame(self):
        """测试请求帧"""
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
        """测试成功响应帧"""
        frame = ResponseFrame(id="req-1", ok=True, payload={"data": 42})
        assert frame.ok
        assert frame.payload["data"] == 42

    def test_response_frame_error(self):
        """测试错误响应帧"""
        frame = ResponseFrame(
            id="req-1",
            ok=False,
            error={"code": "NOT_FOUND", "message": "Resource not found"},
        )
        assert not frame.ok
        assert frame.error["code"] == "NOT_FOUND"

    def test_event_frame(self):
        """测试事件帧"""
        frame = EventFrame(
            event="message",
            payload={"text": "hello"},
            seq=1,
        )
        assert frame.type == "event"
        assert frame.seq == 1


class TestGateway:
    """Gateway 测试类"""

    @pytest.mark.asyncio
    async def test_connect(self):
        """测试连接"""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1", platform="test")
        hello = await gw.connect("conn-1", frame)

        assert isinstance(hello, HelloOkFrame)
        assert hello.connection_id == "conn-1"

    @pytest.mark.asyncio
    async def test_connect_with_auth(self):
        """测试带认证的连接"""
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
        """测试断开连接"""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        await gw.disconnect("conn-1")
        assert await gw.get_connection("conn-1") is None

    @pytest.mark.asyncio
    async def test_handle_request_method_not_found(self):
        """测试未知方法"""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        req = RequestFrame(id="req-1", method="unknown.method")
        resp = await gw.handle_request("conn-1", req)
        assert not resp.ok
        assert resp.error["code"] == "METHOD_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_handle_request_not_connected(self):
        """测试未连接时请求"""
        gw = Gateway()
        req = RequestFrame(id="req-1", method="test")
        resp = await gw.handle_request("ghost", req)
        assert not resp.ok
        assert resp.error["code"] == "NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_handle_request_success(self):
        """测试成功请求"""
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
        """测试处理器异常"""
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
        """测试幂等性缓存"""
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

        # 处理器只应被调用一次
        assert call_count == 1
        assert resp1.payload == resp2.payload

    @pytest.mark.asyncio
    async def test_push_event(self):
        """测试推送事件"""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        event = await gw.push_event("conn-1", "notification", {"msg": "hi"})
        assert event is not None
        assert event.event == "notification"
        assert event.seq == 1

    @pytest.mark.asyncio
    async def test_push_event_seq_increments(self):
        """测试事件序列号递增"""
        gw = Gateway()
        frame = ConnectFrame(device_id="dev-1")
        await gw.connect("conn-1", frame)

        e1 = await gw.push_event("conn-1", "a", {})
        e2 = await gw.push_event("conn-1", "b", {})
        assert e2.seq > e1.seq

    @pytest.mark.asyncio
    async def test_push_event_no_connection(self):
        """测试推送到不存在的连接"""
        gw = Gateway()
        result = await gw.push_event("ghost", "test", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_broadcast_event(self):
        """测试广播事件"""
        gw = Gateway()
        for i in range(3):
            await gw.connect(f"conn-{i}", ConnectFrame(device_id=f"dev-{i}"))

        recipients = await gw.broadcast_event("update", {"version": "2.0"})
        assert len(recipients) == 3

    @pytest.mark.asyncio
    async def test_broadcast_with_filter(self):
        """测试带过滤的广播"""
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
        """测试列出连接"""
        gw = Gateway()
        await gw.connect("c1", ConnectFrame(device_id="d1"))
        await gw.connect("c2", ConnectFrame(device_id="d2"))

        conns = gw.list_connections()
        assert len(conns) == 2

    @pytest.mark.asyncio
    async def test_method_decorator(self):
        """测试方法装饰器"""
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
    """GatewayMessageParser 测试类"""

    def test_parse_connect(self):
        """测试解析连接帧"""
        msg = json.dumps({"type": "connect", "device_id": "dev-1"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type == "connect"
        assert frame.device_id == "dev-1"
        assert error is None

    def test_parse_request(self):
        """测试解析请求帧"""
        msg = json.dumps({"type": "req", "id": "r1", "method": "test"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type == "req"
        assert frame.method == "test"

    def test_parse_invalid_json(self):
        """测试解析无效 JSON"""
        frame_type, frame, error = GatewayMessageParser.parse("not json")
        assert frame_type is None
        assert error is not None
        assert "Invalid JSON" in error

    def test_parse_unknown_type(self):
        """测试解析未知类型"""
        msg = json.dumps({"type": "unknown"})
        frame_type, frame, error = GatewayMessageParser.parse(msg)
        assert frame_type is None
        assert "Unknown frame type" in error

    def test_serialize(self):
        """测试序列化"""
        frame = HelloOkFrame(connection_id="c1", server_time="now")
        serialized = GatewayMessageParser.serialize(frame)
        data = json.loads(serialized)
        assert data["type"] == "hello-ok"
        assert data["connection_id"] == "c1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
