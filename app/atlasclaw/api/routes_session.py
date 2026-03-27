# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.models import ANONYMOUS_USER, UserInfo
from ..session.context import ChatType as SessionChatType
from ..session.context import SessionKey, SessionScope
from ..session.manager import SessionManager
from ..session.queue import QueueMode
from .deps_context import APIContext, get_api_context
from .schemas import (
    CompactRequest,
    QueueModeRequest,
    SessionCreateRequest,
    SessionResetRequest,
    SessionResponse,
    StatusResponse,
)


def register_session_routes(router: APIRouter) -> None:
    def _require_session_ownership(session_key: str, request_obj: Request) -> UserInfo:
        """
        Verify that the authenticated user owns the given session.

        Returns the authenticated UserInfo on success.
        Raises HTTPException(403) if ownership check fails.
        Raises HTTPException(401) if user is anonymous.
        """
        auth_user: UserInfo = getattr(request_obj.state, "user_info", ANONYMOUS_USER)

        # Reject anonymous users
        if auth_user.user_id == ANONYMOUS_USER.user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to access sessions",
            )

        # Verify ownership via session key
        try:
            key = SessionKey.from_string(session_key)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid session key format",
            )

        if key.user_id != auth_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this session",
            )

        return auth_user

    @router.get("/sessions", response_model=list[SessionResponse])
    async def list_sessions(
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> list[SessionResponse]:
        """List all sessions for the current user."""
        auth_user: UserInfo = getattr(request_obj.state, "user_info", ANONYMOUS_USER)

        # Reject anonymous users
        if auth_user.user_id == ANONYMOUS_USER.user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to list sessions",
            )

        # Create user-scoped SessionManager for isolation at storage level
        # Respect legacy vs workspace-based SessionManager configuration
        if getattr(ctx.session_manager, "_legacy_mode", False):
            scoped_mgr = SessionManager(
                agents_dir=str(ctx.session_manager.agents_dir),
                agent_id=ctx.session_manager.agent_id,
                user_id=auth_user.user_id,
            )
        else:
            scoped_mgr = SessionManager(
                workspace_path=str(ctx.session_manager.workspace_path),
                user_id=auth_user.user_id,
            )

        # Now list_sessions() only returns sessions from this user's directory
        all_sessions = await scoped_mgr.list_sessions()

        # Build response (no longer need user_id filtering)
        user_sessions = []
        for session in all_sessions:
            try:
                key = SessionKey.from_string(session.session_key)
                user_sessions.append(
                    SessionResponse(
                        session_key=session.session_key,
                        agent_id=key.agent_id,
                        channel=key.channel,
                        user_id=key.user_id,
                        created_at=session.created_at,
                        last_activity=session.updated_at,
                        message_count=getattr(session, "message_count", 0),
                        total_tokens=session.total_tokens,
                    )
                )
            except Exception:
                continue

        # Sort by last_activity descending (most recent first)
        user_sessions.sort(key=lambda s: s.last_activity or s.created_at, reverse=True)
        return user_sessions

    @router.post("/sessions", response_model=SessionResponse)
    async def create_session(
        request_obj: Request,
        request: SessionCreateRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionResponse:
        auth_user: UserInfo = getattr(request_obj.state, "user_info", ANONYMOUS_USER)

        key = SessionKey(
            agent_id=request.agent_id,
            channel=request.channel,
            chat_type=SessionChatType(request.chat_type),
            user_id=auth_user.user_id,
        )
        session_key_str = key.to_string(scope=SessionScope(request.scope))
        session = await ctx.session_manager.get_or_create(session_key_str)

        return SessionResponse(
            session_key=session_key_str,
            agent_id=key.agent_id,
            channel=key.channel,
            user_id=key.user_id,
            created_at=session.created_at,
            last_activity=session.updated_at,
            message_count=getattr(session, "message_count", 0),
            total_tokens=session.total_tokens,
        )

    @router.get("/sessions/{session_key}", response_model=SessionResponse)
    async def get_session(
        session_key: str,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionResponse:
        _require_session_ownership(session_key, request_obj)
        session = await ctx.session_manager.get_session(session_key)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )

        key = SessionKey.from_string(session_key)
        return SessionResponse(
            session_key=session_key,
            agent_id=key.agent_id,
            channel=key.channel,
            user_id=key.user_id,
            created_at=session.created_at,
            last_activity=session.updated_at,
            message_count=getattr(session, "message_count", 0),
            total_tokens=session.total_tokens,
        )

    @router.post("/sessions/{session_key}/reset")
    async def reset_session(
        session_key: str,
        request: SessionResetRequest,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        _require_session_ownership(session_key, request_obj)
        await ctx.session_manager.reset_session(session_key, archive=request.archive)
        return {"status": "reset", "session_key": session_key}

    @router.delete("/sessions/{session_key}")
    async def delete_session(
        session_key: str,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        _require_session_ownership(session_key, request_obj)
        success = await ctx.session_manager.delete_session(session_key)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )
        return {"status": "deleted", "session_key": session_key}

    @router.get("/sessions/{session_key}/status", response_model=StatusResponse)
    async def get_status(
        session_key: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> StatusResponse:
        session = await ctx.session_manager.get_session(session_key)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )

        queue_mode = ctx.session_queue.get_mode(session_key)
        queue_size = ctx.session_queue.queue_size(session_key)
        return StatusResponse(
            session_key=session_key,
            context_tokens=session.context_tokens,
            input_tokens=session.input_tokens,
            output_tokens=session.output_tokens,
            queue_mode=queue_mode.value,
            queue_size=queue_size,
        )

    @router.post("/sessions/{session_key}/queue")
    async def set_queue_mode(
        session_key: str,
        request: QueueModeRequest,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        _require_session_ownership(session_key, request_obj)
        try:
            mode = QueueMode(request.mode)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid queue mode: {request.mode}",
            )

        ctx.session_queue.set_session_mode(session_key, mode)
        return {"session_key": session_key, "queue_mode": request.mode}

    @router.post("/sessions/{session_key}/compact")
    async def trigger_compact(
        session_key: str,
        request: CompactRequest,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        _require_session_ownership(session_key, request_obj)
        session = await ctx.session_manager.get_session(session_key)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )

        return {
            "session_key": session_key,
            "status": "compaction_triggered",
            "instruction": request.instruction,
        }
