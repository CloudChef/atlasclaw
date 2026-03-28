# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status

from ...auth.models import ANONYMOUS_USER, UserInfo
from ...core.security_guard import encode_if_untrusted
from ...session.context import SessionKey
from ..deps_context import APIContext, build_scoped_deps

logger = logging.getLogger(__name__)


def build_provider_config(ctx: APIContext) -> dict[str, Any]:
    if ctx.service_provider_registry:
        return ctx.service_provider_registry.get_all_instance_configs()
    return {}


def init_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    message: str,
    timeout_seconds: int,
) -> None:
    ctx.active_runs[run_id] = {
        "status": "running",
        "session_key": session_key,
        "started_at": datetime.now(timezone.utc),
        "message": message,
        "timeout_seconds": timeout_seconds,
    }
    ctx.sse_manager.create_stream(run_id)


def normalize_user_message(message: str) -> str:
    normalized, _ = encode_if_untrusted(message)
    return normalized


def get_run_or_404(ctx: APIContext, run_id: str) -> dict[str, Any]:
    run_info = ctx.active_runs.get(run_id)
    if not run_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run not found: {run_id}",
        )
    return run_info


def abort_run(ctx: APIContext, run_id: str) -> None:
    run_info = get_run_or_404(ctx, run_id)
    run_info["status"] = "aborted"


async def execute_agent_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    message: str,
    timeout_seconds: int,
    user_info: Optional[UserInfo] = None,
    request_cookies: Optional[dict[str, str]] = None,
    provider_config: Optional[dict[str, Any]] = None,
    request_context: Optional[dict[str, Any]] = None,
) -> None:
    _user_info = user_info or ANONYMOUS_USER

    try:
        target_agent_id = SessionKey.from_string(session_key).agent_id or "main"
        runner = None
        if ctx.agent_runners:
            runner = ctx.agent_runners.get(target_agent_id) or ctx.agent_runners.get("main")
        if runner is None:
            runner = ctx.agent_runner

        if not runner:
            raise RuntimeError(
                "AgentRunner not configured. Ensure LLM provider is properly configured in xuanwu.json",
            )

        deps = build_scoped_deps(
            ctx,
            _user_info,
            session_key,
            request_cookies=request_cookies,
            provider_config=provider_config,
            extra={
                "agent_id": target_agent_id,
                "context": request_context or {},
            },
        )

        async for event in runner.run(
            session_key=session_key,
            user_message=message,
            deps=deps,
            timeout_seconds=timeout_seconds,
        ):
            if event.type == "lifecycle":
                ctx.sse_manager.push_lifecycle(run_id, event.phase)
            elif event.type == "assistant":
                ctx.sse_manager.push_assistant(run_id, event.content)
            elif event.type == "tool":
                result_str = str(event.content) if event.content else None
                ctx.sse_manager.push_tool(
                    run_id,
                    event.tool,
                    event.phase,
                    result=result_str,
                )
            elif event.type == "error":
                ctx.sse_manager.push_error(run_id, event.error)
            elif event.type == "thinking":
                ctx.sse_manager.push_thinking(
                    run_id,
                    event.phase,
                    event.content,
                    metadata=event.metadata if event.metadata else None,
                )

        if run_id in ctx.active_runs:
            ctx.active_runs[run_id]["status"] = "completed"
            ctx.active_runs[run_id]["completed_at"] = datetime.now(timezone.utc)

    except asyncio.TimeoutError:
        ctx.sse_manager.push_error(run_id, "Agent execution timed out")
        ctx.sse_manager.push_lifecycle(run_id, "error")
        if run_id in ctx.active_runs:
            ctx.active_runs[run_id]["status"] = "timeout"
            ctx.active_runs[run_id]["error"] = "Execution timed out"

    except Exception as e:
        error_msg = str(e)
        ctx.sse_manager.push_error(run_id, error_msg)
        ctx.sse_manager.push_lifecycle(run_id, "error")
        if run_id in ctx.active_runs:
            ctx.active_runs[run_id]["status"] = "error"
            ctx.active_runs[run_id]["error"] = error_msg

    finally:
        ctx.sse_manager.close_stream(run_id)

