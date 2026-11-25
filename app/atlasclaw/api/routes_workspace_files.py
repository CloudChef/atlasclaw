# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..auth.guards import get_current_user, get_optional_authorization_context
from ..auth.models import UserInfo
from ..core.workspace_downloads import WorkspaceDownloadError, open_workspace_download_file
from .deps_context import APIContext, get_api_context, resolve_workspace_path


def _safe_attachment_name(file_path: Path) -> str:
    safe = "".join(
        char if char.isprintable() and char not in {"/", "\\", '"', ";"} else "_"
        for char in file_path.name.strip()
    )
    return safe or "download"


def _http_exception_for_workspace_download_error(exc: WorkspaceDownloadError) -> HTTPException:
    if exc.reason == "not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.reason == "forbidden":
        status_code = status.HTTP_403_FORBIDDEN
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=exc.detail)


def _iter_file_descriptor(fd: int, chunk_size: int = 1024 * 1024):
    with os.fdopen(fd, "rb", closefd=True) as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk


def register_workspace_file_routes(router: APIRouter) -> None:
    @router.get("/workspace/files/download")
    async def download_workspace_file(
        request: Request,
        path: str = Query(...),
        inline: bool = Query(False),
        user: UserInfo = Depends(get_current_user),
        ctx: APIContext = Depends(get_api_context),
    ) -> StreamingResponse:
        await get_optional_authorization_context(request)
        workspace_path = resolve_workspace_path(request, ctx=ctx)
        try:
            opened_file = open_workspace_download_file(
                workspace_path=workspace_path,
                user_id=user.user_id,
                requested_path=path,
            )
        except WorkspaceDownloadError as exc:
            raise _http_exception_for_workspace_download_error(exc) from exc
        safe_name = _safe_attachment_name(opened_file.path)
        media_type = mimetypes.guess_type(opened_file.path.name)[0] or "application/octet-stream"
        inline_types = {
            "application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif",
            "text/plain", "text/markdown", "text/html",
        }
        headers = {
            "Content-Length": str(opened_file.stat_result.st_size),
            "X-Content-Type-Options": "nosniff",
        }
        if inline and media_type in inline_types:
            headers["Content-Disposition"] = f"inline; filename*=utf-8''{quote(safe_name)}"
            if media_type == "text/html":
                # Generated HTML is untrusted: neutralize scripts and same-origin access.
                headers["Content-Security-Policy"] = (
                    "sandbox; default-src 'none'; img-src data: blob:; "
                    "style-src 'unsafe-inline'; font-src data:"
                )
        else:
            headers["Content-Disposition"] = f"attachment; filename*=utf-8''{quote(safe_name)}"
            media_type = "application/octet-stream"
        return StreamingResponse(
            _iter_file_descriptor(opened_file.fd),
            media_type=media_type,
            headers=headers,
        )
