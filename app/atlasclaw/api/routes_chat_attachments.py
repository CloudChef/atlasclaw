# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Authenticated chat images; URLs work under the configured reverse-proxy prefix."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.atlasclaw.agent.model_capabilities import image_capabilities, session_model
from app.atlasclaw.auth.guards import get_current_user
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.chat_attachments import ChatAttachments, ImageInputError, MAX_FILE_BYTES, MAX_FILES
from app.atlasclaw.session.context import SessionKey
from .deps_context import APIContext, get_api_context
from .routes_agent import _ensure_runnable_session


def attachment_store(ctx: APIContext, user: UserInfo) -> ChatAttachments:
    """
    Scope attachment access to the authenticated user's configured workspace.
    """
    return ChatAttachments(ctx.session_manager_router.for_user(user.user_id).workspace_path, user.user_id)


def image_http_error(error: ImageInputError) -> HTTPException:
    """
    Preserve the status and stable image error code used by frontend translations.
    """
    return HTTPException(error.status, detail={"code": error.code, "message": error.message})


def image_runner(ctx, session_key):
    """
    Resolve the session's agent runner, falling back to the application default.
    """
    agent_id = SessionKey.from_string(session_key).agent_id
    return (ctx.agent_runners or {}).get(agent_id) or ctx.agent_runner


def register_chat_attachment_routes(router: APIRouter) -> None:
    """
    Register authenticated image routes before the catch-all session routes.
    """
    @router.post("/chat/attachments", status_code=201)
    async def upload_images(
        request: Request,
        session_key: str = Form(...), files: list[UploadFile] = File(...),
        user: UserInfo = Depends(get_current_user), ctx: APIContext = Depends(get_api_context),
    ):
        """
        Store drafts for an owned session and discard them if the client disconnects.
        """
        await _ensure_runnable_session(ctx, user, session_key)
        try:
            store = attachment_store(ctx, user)
            attachments = await store.upload(session_key, files)
            if await request.is_disconnected():
                for ref in attachments:
                    await store.delete(identifier=ref["id"])
            return {"attachments": attachments}
        except ImageInputError as exc:
            raise image_http_error(exc) from exc
        finally:
            for file in files:
                await file.close()

    @router.get("/chat/attachments/{attachment_id}/content")
    async def image_content(
        attachment_id: str, user: UserInfo = Depends(get_current_user),
        ctx: APIContext = Depends(get_api_context),
    ):
        """
        Serve an owned image without allowing browser or shared-cache persistence.
        """
        try:
            data, media = await attachment_store(ctx, user).content(attachment_id)
            return Response(data, media_type=media, headers={
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            })
        except ImageInputError as exc:
            raise image_http_error(exc) from exc

    @router.delete("/chat/attachments/{attachment_id}", status_code=204)
    async def delete_draft(
        attachment_id: str, user: UserInfo = Depends(get_current_user),
        ctx: APIContext = Depends(get_api_context),
    ):
        """
        Delete an owned unsent image; attachments already bound to history remain.
        """
        try:
            await attachment_store(ctx, user).delete(identifier=attachment_id)
        except ImageInputError as exc:
            raise image_http_error(exc) from exc

    @router.get("/sessions/{session_key:path}/input-capabilities")
    async def input_capabilities(
        session_key: str, user: UserInfo = Depends(get_current_user),
        ctx: APIContext = Depends(get_api_context),
    ):
        """
        Report the effective model's image declaration and per-submission limits.
        """
        await _ensure_runnable_session(ctx, user, session_key)
        runner = image_runner(ctx, session_key)
        token = session_model(runner, session_key)
        capabilities = await image_capabilities(runner)
        return {
            "model": token.model if token else "", "vision": capabilities.get(token.token_id, False) if token else False,
            "max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
        }
