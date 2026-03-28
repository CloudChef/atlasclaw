# -*- coding: utf-8 -*-

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status

from ..auth.models import ANONYMOUS_USER, UserInfo
from .deps_context import APIContext, get_api_context
from .schemas import AgentRunRequest, AgentRunResponse, AgentStatusResponse
from .services.run_service import (
    abort_run,
    build_provider_config,
    execute_agent_run,
    get_run_or_404,
    init_run,
    normalize_user_message,
)


def register_agent_routes(router: APIRouter) -> None:
    @router.post("/agent/run", response_model=AgentRunResponse)
    async def start_agent_run(
        request_obj: Request,
        request: AgentRunRequest,
        background_tasks: BackgroundTasks,
        ctx: APIContext = Depends(get_api_context),
    ) -> AgentRunResponse:
        run_id = str(uuid.uuid4())
        user_info: UserInfo = getattr(request_obj.state, "user_info", ANONYMOUS_USER)
        request_cookies = dict(request_obj.cookies)
        provider_config = build_provider_config(ctx)
        safe_message = normalize_user_message(request.message)
        init_run(ctx, run_id, request.session_key, safe_message, request.timeout_seconds)

        background_tasks.add_task(
            execute_agent_run,
            ctx,
            run_id,
            request.session_key,
            safe_message,
            request.timeout_seconds,
            user_info,
            request_cookies,
            provider_config,
            request.context,
        )

        return AgentRunResponse(
            run_id=run_id,
            status="running",
            session_key=request.session_key,
        )

    @router.get("/agent/runs/{run_id}/stream")
    async def stream_agent_run(
        run_id: str,
        last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
        ctx: APIContext = Depends(get_api_context),
    ):
        if run_id not in ctx.active_runs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )
        return await ctx.sse_manager.create_response(run_id, last_event_id=last_event_id)

    @router.get("/agent/runs/{run_id}", response_model=AgentStatusResponse)
    async def get_agent_status(
        run_id: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> AgentStatusResponse:
        run_info = get_run_or_404(ctx, run_id)

        return AgentStatusResponse(
            run_id=run_id,
            status=run_info.get("status", "unknown"),
            started_at=run_info.get("started_at"),
            completed_at=run_info.get("completed_at"),
            tokens_used=run_info.get("tokens_used", 0),
            error=run_info.get("error"),
        )

    @router.post("/agent/runs/{run_id}/abort")
    async def abort_agent_run(
        run_id: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        abort_run(ctx, run_id)
        return {"status": "aborted", "run_id": run_id}

