# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.models import ANONYMOUS_USER, UserInfo
from ..core.workspace_downloads import collect_workspace_download_references_from_payloads
from ..session.context import ChatType as SessionChatType
from ..session.context import SessionKey, SessionScope
from ..session.queue import QueueMode
from .deps_context import APIContext, get_api_context
from .schemas import (
    CompactRequest,
    QueueModeRequest,
    SessionCreateRequest,
    SessionHistoryMessage,
    SessionHistoryResponse,
    SessionResetRequest,
    SessionResponse,
    SessionThreadCreateRequest,
    StatusResponse,
)


def _current_user(request_obj: Request) -> UserInfo:
    return getattr(request_obj.state, "user_info", ANONYMOUS_USER)


def _resolve_scope(request: SessionCreateRequest) -> SessionScope:
    try:
        return SessionScope(request.scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid session scope: {request.scope}",
        ) from exc


def _resolve_chat_type(value: str) -> SessionChatType:
    try:
        return SessionChatType(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid chat_type: {value}",
        ) from exc


def _resolve_session_scope_for_thread(account_id: str) -> SessionScope:
    return (
        SessionScope.PER_ACCOUNT_CHANNEL_PEER
        if account_id and account_id != "default"
        else SessionScope.PER_CHANNEL_PEER
    )


def _resolve_peer_id(
    auth_user: UserInfo,
    request: SessionCreateRequest | SessionThreadCreateRequest,
) -> str:
    return request.peer_id or auth_user.user_id or "default"


def _build_session_key(
    auth_user: UserInfo,
    request: SessionCreateRequest | SessionThreadCreateRequest,
    *,
    thread_id: str | None = None,
) -> SessionKey:
    return SessionKey(
        agent_id=request.agent_id,
        channel=request.channel,
        account_id=getattr(request, "account_id", "default") or "default",
        chat_type=_resolve_chat_type(request.chat_type),
        user_id=auth_user.user_id,
        peer_id=_resolve_peer_id(auth_user, request),
        thread_id=thread_id,
    )


def _build_session_response(session_key: str, session: Any) -> SessionResponse:
    key = SessionKey.from_string(session_key)
    return SessionResponse(
        session_key=session_key,
        agent_id=key.agent_id,
        channel=key.channel,
        user_id=key.user_id,
        account_id=key.account_id,
        chat_type=key.chat_type.value,
        peer_id=key.peer_id,
        thread_id=key.thread_id,
        created_at=session.created_at,
        last_activity=session.updated_at,
        message_count=getattr(session, "message_count", 0),
        total_tokens=session.total_tokens,
        title=getattr(session, "title", "") or "",
        title_status=getattr(session, "title_status", "empty") or "empty",
    )


def _ensure_session_owner(auth_user: UserInfo, session_key: str) -> SessionKey:
    parsed = SessionKey.from_string(session_key)
    if parsed.user_id != auth_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_key}",
        )
    return parsed


def _infer_session_key_scope(session_key: str) -> SessionScope:
    """Infer the serialized session-key scope from its structural segments."""
    parts = str(session_key or "").split(":")
    rest_start = 4 if len(parts) > 3 and parts[0] == "agent" and parts[2] == "user" else 2
    rest = parts[rest_start:]
    if len(rest) == 1 and rest[0] == "main":
        return SessionScope.MAIN
    if len(rest) == 2:
        return SessionScope.PER_PEER
    if len(rest) >= 4 and rest[2] in {item.value for item in SessionChatType}:
        return SessionScope.PER_ACCOUNT_CHANNEL_PEER
    return SessionScope.PER_CHANNEL_PEER


def _canonical_session_key(session_key: str) -> str:
    """Normalize a route-captured session key back to the canonical encoded form."""
    parsed = SessionKey.from_string(session_key)
    return parsed.to_string(scope=_infer_session_key_scope(session_key))


def _collect_workspace_download_references(
    *,
    entry: Any,
    workspace_path: Any,
    user_id: str,
    seen_paths: set[str],
) -> list[dict[str, str]]:
    payloads: list[Any] = []
    content = getattr(entry, "content", None)
    if content:
        payloads.append(content)
    tool_results = getattr(entry, "tool_results", [])
    if isinstance(tool_results, list):
        for result in tool_results:
            if isinstance(result, dict):
                payloads.append(
                    result if result.get("is_error") is True else result.get("content", result)
                )
    return collect_workspace_download_references_from_payloads(
        payloads,
        workspace_path=workspace_path,
        user_id=user_id,
        seen_paths=seen_paths,
    )


def _build_session_history_response(
    transcript: list[Any],
    *,
    workspace_path: Any,
    user_id: str,
) -> SessionHistoryResponse:
    messages: list[SessionHistoryMessage] = []
    pending_workspace_downloads: list[dict[str, str]] = []
    pending_download_paths: set[str] = set()
    for entry in transcript:
        role = str(getattr(entry, "role", "") or "").strip().lower()
        content = getattr(entry, "content", "")
        if role == "user":
            pending_workspace_downloads = []
            pending_download_paths = set()
        if role == "tool" or getattr(entry, "tool_results", []):
            pending_workspace_downloads.extend(
                _collect_workspace_download_references(
                    entry=entry,
                    workspace_path=workspace_path,
                    user_id=user_id,
                    seen_paths=pending_download_paths,
                )
            )
        if role == "tool":
            continue

        if role not in {"user", "assistant"} or not content:
            continue

        workspace_downloads = []
        if role == "assistant" and not getattr(entry, "tool_calls", []):
            workspace_downloads = list(pending_workspace_downloads)
            pending_workspace_downloads = []
            pending_download_paths = set()

        messages.append(
            SessionHistoryMessage(
                role=role,
                content=content,
                timestamp=entry.timestamp,
                workspace_downloads=workspace_downloads,
            )
        )
    return SessionHistoryResponse(messages=messages)


def register_session_routes(router: APIRouter) -> None:
    @router.get("/sessions", response_model=list[SessionResponse])
    async def list_sessions(
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> list[SessionResponse]:
        """List all sessions owned by the current user across all channels."""
        auth_user = _current_user(request_obj)
        manager = ctx.session_manager_router.for_user(auth_user.user_id)
        all_sessions = await manager.list_sessions()
        user_sessions = [_build_session_response(session.session_key, session) for session in all_sessions]

        user_sessions.sort(key=lambda s: s.last_activity or s.created_at, reverse=True)
        return user_sessions

    @router.post("/sessions", response_model=SessionResponse)
    async def create_session(
        request_obj: Request,
        request: SessionCreateRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionResponse:
        auth_user = _current_user(request_obj)
        key = _build_session_key(auth_user, request)
        session_key_str = key.to_string(scope=_resolve_scope(request))
        manager = ctx.session_manager_router.for_user(auth_user.user_id)
        session = await manager.get_or_create(session_key_str)
        return _build_session_response(session_key_str, session)

    @router.post("/sessions/threads", response_model=SessionResponse)
    async def create_thread_session(
        request_obj: Request,
        request: SessionThreadCreateRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionResponse:
        auth_user = _current_user(request_obj)
        thread_id = uuid.uuid4().hex
        key = _build_session_key(auth_user, request, thread_id=thread_id)
        session_key_str = key.to_string(
            scope=_resolve_session_scope_for_thread(request.account_id),
        )
        manager = ctx.session_manager_router.for_user(auth_user.user_id)
        session = await manager.get_or_create(session_key_str)
        return _build_session_response(session_key_str, session)

    @router.get("/sessions/{session_key:path}/history", response_model=SessionHistoryResponse)
    async def get_session_history(
        request_obj: Request,
        session_key: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionHistoryResponse:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        session = await manager.get_session(session_key)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )
        transcript = await manager.load_transcript(session_key)
        return _build_session_history_response(
            transcript,
            workspace_path=manager.workspace_path,
            user_id=manager.user_id,
        )

    @router.post("/sessions/{session_key:path}/reset")
    async def reset_session(
        request_obj: Request,
        session_key: str,
        request: SessionResetRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        await manager.reset_session(session_key, archive=request.archive)
        return {"status": "reset", "session_key": session_key}

    @router.delete("/sessions/{session_key:path}")
    async def delete_session(
        request_obj: Request,
        session_key: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        success = await manager.delete_session(session_key)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )
        return {"status": "deleted", "session_key": session_key}

    @router.get("/sessions/{session_key:path}/status", response_model=StatusResponse)
    async def get_status(
        request_obj: Request,
        session_key: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> StatusResponse:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        session = await manager.get_session(session_key)
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

    @router.post("/sessions/{session_key:path}/queue")
    async def set_queue_mode(
        request_obj: Request,
        session_key: str,
        request: QueueModeRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        try:
            mode = QueueMode(request.mode)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid queue mode: {request.mode}",
            )

        ctx.session_queue.set_session_mode(session_key, mode)
        return {"session_key": session_key, "queue_mode": request.mode}

    @router.post("/sessions/{session_key:path}/compact")
    async def trigger_compact(
        request_obj: Request,
        session_key: str,
        request: CompactRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        session = await manager.get_session(session_key)
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

    @router.get("/sessions/{session_key:path}", response_model=SessionResponse)
    async def get_session(
        request_obj: Request,
        session_key: str,
        ctx: APIContext = Depends(get_api_context),
    ) -> SessionResponse:
        auth_user = _current_user(request_obj)
        _ensure_session_owner(auth_user, session_key)
        session_key = _canonical_session_key(session_key)
        manager = ctx.session_manager_router.for_session_key(session_key)
        session = await manager.get_session(session_key)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_key}",
            )
        return _build_session_response(session_key, session)
