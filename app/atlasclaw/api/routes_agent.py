# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status

from ..auth.models import ANONYMOUS_USER, UserInfo
from ..auth.guards import get_authorization_context, AuthorizationContext
from ..session.context import SessionKey
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


async def _ensure_runnable_session(ctx: APIContext, auth_user: UserInfo, session_key: str) -> None:
    parsed = SessionKey.from_string(session_key)
    if parsed.user_id != auth_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_key}",
        )

    manager = ctx.session_manager_router.for_user(auth_user.user_id)
    session = await manager.get_session(session_key)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_key}",
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
        await _ensure_runnable_session(ctx, user_info, request.session_key)
        request_cookies = dict(request_obj.cookies)
        provider_config = build_provider_config(ctx)
        safe_message = normalize_user_message(request.message)
        init_run(ctx, run_id, request.session_key, safe_message, request.timeout_seconds)

        # Resolve user skill permissions for agent context filtering.
        # This is fail-closed: if permission resolution fails, the run is
        # rejected rather than falling through without permissions.
        #
        # Sentinel semantics:
        #   user_skill_permissions = None  -> no RBAC (anonymous / no-DB mode)
        #   user_skill_permissions = []    -> RBAC resolved, no grants (deny-all)
        #   user_skill_permissions = [...]  -> RBAC resolved, per-skill grants
        user_skill_permissions: list[dict] | None = None
        user_provider_permissions: list[dict] | None = None
        try:
            from app.atlasclaw.db.database import get_db_manager
            db_mgr = get_db_manager()
            if db_mgr is None or db_mgr._session_factory is None:
                # No database configured (anonymous / file-only mode) -- skip RBAC.
                print("[SkillFilter] No DB available, skipping permission resolution")
            else:
                db_session = db_mgr._session_factory()
                try:
                    from ..auth.guards import resolve_authorization_context
                    authz = await resolve_authorization_context(db_session, user_info)
                    user_skill_permissions = (
                        authz.permissions.get("skills", {}).get("skill_permissions", [])
                    )
                    user_provider_permissions = (
                        authz.permissions.get("providers", {}).get("provider_permissions", [])
                    )
                    disabled_skills = [
                        s.get("skill_id") for s in user_skill_permissions
                        if not s.get("enabled")
                    ]
                    print(
                        f"[SkillFilter] user={user_info.user_id} total_perms={len(user_skill_permissions)} disabled={disabled_skills}"
                    )
                    # Admin with empty skill_permissions retains open access;
                    # signal this downstream by leaving the sentinel as None
                    # ("no RBAC filtering") instead of [] ("deny-all").
                    if authz.is_admin and not user_skill_permissions:
                        user_skill_permissions = None
                    await db_session.commit()
                except Exception as exc:
                    await db_session.rollback()
                    print(f"[SkillFilter] Permission resolution failed (fail-closed): {exc}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to resolve skill permissions for this run.",
                    ) from exc
                finally:
                    await db_session.close()
        except HTTPException:
            raise
        except Exception as exc:
            print(f"[SkillFilter] DB access failed (fail-closed): {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to resolve skill permissions for this run.",
            ) from exc

        request_context = request.context or {}
        # Always pass RBAC result to runtime when DB is available (including
        # empty list which means deny-all).  Only skip when RBAC is not
        # enabled at all (None sentinel).
        if user_skill_permissions is not None:
            request_context = {
                **(request_context or {}),
                "_user_skill_permissions": user_skill_permissions,
            }
        if user_provider_permissions is not None:
            request_context = {
                **(request_context or {}),
                "_provider_permissions": user_provider_permissions,
            }

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
            request_context,
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
