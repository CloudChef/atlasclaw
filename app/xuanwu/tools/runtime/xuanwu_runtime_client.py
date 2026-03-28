# -*- coding: utf-8 -*-
"""Typed client wrapper for the external Xuanwu runtime service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.xuanwu.core.deps import SkillDeps


class XuanwuRuntimeError(RuntimeError):
    """Base exception for runtime client failures."""


class XuanwuRuntimeTransportError(XuanwuRuntimeError):
    """Raised when the runtime cannot be reached."""


class XuanwuRuntimeProtocolError(XuanwuRuntimeError):
    """Raised when the runtime responds with an invalid payload or status."""


@dataclass
class XuanwuRuntimeRequest:
    """Typed request payload sent to the runtime service."""

    method: str
    path: str
    json_body: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None


@dataclass
class XuanwuRuntimeResponse:
    """Typed response envelope returned by the runtime service."""

    status_code: int
    data: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class XuanwuRuntimeClient:
    """Focused async client for runtime status checks and tool invocations."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: Optional[str] = None,
        timeout_seconds: float = 30.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        default_headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._default_headers = dict(default_headers or {})

    @classmethod
    def from_deps(cls, deps: SkillDeps) -> "XuanwuRuntimeClient":
        """Build a runtime client from request-scoped dependencies."""

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        base_url = str(
            extra.get("xuanwu_runtime_base_url")
            or extra.get("runtime_base_url")
            or os.getenv("XUANWU_RUNTIME_BASE_URL")
            or ""
        ).strip()
        if not base_url:
            raise XuanwuRuntimeProtocolError("Xuanwu runtime is not configured")

        timeout_value = (
            extra.get("xuanwu_runtime_timeout_seconds")
            or extra.get("runtime_timeout_seconds")
            or os.getenv("XUANWU_RUNTIME_TIMEOUT_SECONDS")
            or 30
        )
        try:
            timeout_seconds = float(timeout_value)
        except (TypeError, ValueError):
            timeout_seconds = 30.0

        auth_token = str(
            extra.get("xuanwu_runtime_token")
            or extra.get("runtime_token")
            or os.getenv("XUANWU_RUNTIME_TOKEN")
            or deps.user_token
            or ""
        ).strip() or None

        return cls(
            base_url=base_url,
            auth_token=auth_token,
            timeout_seconds=timeout_seconds,
        )

    async def request(self, request: XuanwuRuntimeRequest) -> XuanwuRuntimeResponse:
        """Execute a typed runtime request and validate the response contract."""

        headers = dict(self._default_headers)
        if self.auth_token:
            headers.setdefault("Xuanwu-Authenticate", self.auth_token)

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    request.method.upper(),
                    request.path,
                    json=request.json_body,
                    params=request.params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise XuanwuRuntimeTransportError(f"Failed to reach Xuanwu runtime: {exc}") from exc

        return self._parse_response(response)

    async def get_status(self) -> XuanwuRuntimeResponse:
        """Fetch runtime health and metadata."""

        return await self.request(XuanwuRuntimeRequest(method="GET", path="/status"))

    async def invoke_tool(
        self,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> XuanwuRuntimeResponse:
        """Invoke a named tool through the runtime service."""

        safe_name = quote(tool_name, safe="")
        return await self.request(
            XuanwuRuntimeRequest(
                method="POST",
                path=f"/tools/{safe_name}",
                json_body={"arguments": dict(arguments or {})},
            )
        )

    def _parse_response(self, response: httpx.Response) -> XuanwuRuntimeResponse:
        """Validate runtime response status and body shape."""

        if response.status_code >= 400:
            raise XuanwuRuntimeProtocolError(
                f"Xuanwu runtime returned HTTP {response.status_code}: {self._safe_body_text(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise XuanwuRuntimeProtocolError(
                "Xuanwu runtime returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise XuanwuRuntimeProtocolError(
                "Xuanwu runtime response must be a JSON object"
            )

        return XuanwuRuntimeResponse(
            status_code=response.status_code,
            data=payload,
            headers=dict(response.headers),
        )

    @staticmethod
    def _safe_body_text(response: httpx.Response) -> str:
        """Return a compact human-readable response body for error messages."""

        body = response.text.strip()
        if not body:
            return "<empty body>"
        if len(body) > 300:
            return body[:297] + "..."
        return body
