# -*- coding: utf-8 -*-
"""Built-in tools that proxy through the configured Xuanwu runtime service."""

from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult
from app.xuanwu.tools.runtime.xuanwu_runtime_client import (
    XuanwuRuntimeClient,
    XuanwuRuntimeError,
)

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


def _payload_to_text(payload: Any) -> str:
    """Render runtime payloads into compact text for the tool transcript."""

    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def xuanwu_runtime_status_tool(ctx: "RunContext[SkillDeps]") -> dict:
    """Inspect the current runtime connection status."""

    try:
        client = XuanwuRuntimeClient.from_deps(ctx.deps)
        response = await client.get_status()
        status = str(response.data.get("status", "unknown"))
        return ToolResult.text(
            f"runtime status: {status}",
            details={
                "status_code": response.status_code,
                "runtime": response.data,
            },
        ).to_dict()
    except XuanwuRuntimeError as exc:
        return ToolResult.error(
            f"runtime status failed: {exc}",
            details={"error_type": exc.__class__.__name__},
        ).to_dict()


async def xuanwu_runtime_call_tool(
    ctx: "RunContext[SkillDeps]",
    tool_name: str,
    arguments: Optional[dict[str, Any]] = None,
) -> dict:
    """Invoke a named tool through the configured runtime endpoint."""

    try:
        client = XuanwuRuntimeClient.from_deps(ctx.deps)
        response = await client.invoke_tool(tool_name, arguments=arguments)
        result_payload = response.data.get("result", response.data)
        return ToolResult.text(
            _payload_to_text(result_payload),
            details={
                "tool_name": tool_name,
                "status_code": response.status_code,
                "runtime": response.data,
            },
        ).to_dict()
    except XuanwuRuntimeError as exc:
        return ToolResult.error(
            f"runtime call failed for {tool_name}: {exc}",
            details={
                "tool_name": tool_name,
                "error_type": exc.__class__.__name__,
            },
        ).to_dict()
