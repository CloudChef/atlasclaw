# -*- coding: utf-8 -*-
"""Tests for the Xuanwu runtime client and built-in runtime tools."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.xuanwu.core.deps import SkillDeps
from app.xuanwu.skills.registry import SkillRegistry
from app.xuanwu.tools.catalog import ToolCatalog, ToolProfile
from app.xuanwu.tools.registration import register_builtin_tools
from app.xuanwu.tools.runtime.xuanwu_runtime_client import (
    XuanwuRuntimeClient,
    XuanwuRuntimeProtocolError,
    XuanwuRuntimeResponse,
    XuanwuRuntimeTransportError,
)
from app.xuanwu.tools.runtime.xuanwu_runtime_tools import (
    xuanwu_runtime_call_tool,
    xuanwu_runtime_status_tool,
)


def _build_context(*, extra: dict | None = None, user_token: str = "user-token"):
    deps = SkillDeps(user_token=user_token, extra=extra or {})
    return SimpleNamespace(deps=deps)


class TestXuanwuRuntimeClient:
    @pytest.mark.asyncio
    async def test_get_status_returns_typed_response(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = str(request.url)
            captured["auth"] = request.headers.get("Xuanwu-Authenticate")
            return httpx.Response(
                200,
                json={"status": "ok", "version": "2026.03"},
                request=request,
            )

        client = XuanwuRuntimeClient(
            base_url="https://runtime.example",
            auth_token="runtime-token",
            transport=httpx.MockTransport(handler),
        )

        response = await client.get_status()

        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert captured["path"] == "https://runtime.example/status"
        assert captured["auth"] == "runtime-token"

    @pytest.mark.asyncio
    async def test_request_raises_protocol_error_for_http_error_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": "upstream unavailable"}, request=request)

        client = XuanwuRuntimeClient(
            base_url="https://runtime.example",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(XuanwuRuntimeProtocolError, match="502"):
            await client.get_status()

    @pytest.mark.asyncio
    async def test_request_raises_protocol_error_for_non_object_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "an", "object"], request=request)

        client = XuanwuRuntimeClient(
            base_url="https://runtime.example",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(XuanwuRuntimeProtocolError, match="JSON object"):
            await client.get_status()

    def test_from_deps_requires_runtime_base_url(self):
        deps = SkillDeps(user_token="user-token")

        with pytest.raises(XuanwuRuntimeProtocolError, match="not configured"):
            XuanwuRuntimeClient.from_deps(deps)


class TestXuanwuRuntimeTools:
    @pytest.mark.asyncio
    async def test_runtime_status_tool_returns_runtime_payload(self, monkeypatch):
        class StubClient:
            async def get_status(self) -> XuanwuRuntimeResponse:
                return XuanwuRuntimeResponse(
                    status_code=200,
                    data={"status": "ok", "workers": 3},
                )

        monkeypatch.setattr(
            "app.xuanwu.tools.runtime.xuanwu_runtime_tools.XuanwuRuntimeClient.from_deps",
            lambda deps: StubClient(),
        )

        result = await xuanwu_runtime_status_tool(_build_context())

        assert result["is_error"] is False
        assert result["details"]["status_code"] == 200
        assert result["details"]["runtime"]["workers"] == 3
        assert "runtime status: ok" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_runtime_call_tool_returns_error_details_when_client_fails(self, monkeypatch):
        def fail_from_deps(deps):
            raise XuanwuRuntimeTransportError("runtime offline")

        monkeypatch.setattr(
            "app.xuanwu.tools.runtime.xuanwu_runtime_tools.XuanwuRuntimeClient.from_deps",
            fail_from_deps,
        )

        result = await xuanwu_runtime_call_tool(
            _build_context(),
            tool_name="shell_command",
            arguments={"command": "echo hi"},
        )

        assert result["is_error"] is True
        assert result["details"]["error_type"] == "XuanwuRuntimeTransportError"
        assert "runtime offline" in result["content"][0]["text"]


class TestXuanwuRuntimeRegistration:
    def test_coding_profile_includes_xuanwu_runtime_tools(self):
        tools = ToolCatalog.get_tools_by_profile(ToolProfile.CODING)

        assert "xuanwu_runtime_status" in tools
        assert "xuanwu_runtime_call" in tools

    def test_register_builtin_tools_registers_xuanwu_runtime_handlers(self):
        registry = SkillRegistry()

        registered = register_builtin_tools(registry, profile=ToolProfile.FULL)

        assert "xuanwu_runtime_status" in registered
        assert "xuanwu_runtime_call" in registered
        assert registry.get("xuanwu_runtime_status") is not None
        assert registry.get("xuanwu_runtime_call") is not None
