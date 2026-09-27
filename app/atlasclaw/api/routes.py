# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
REST API composition and request validation logging.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError

from .deps_context import APIContext, get_api_context, set_api_context
from .routes_agent import register_agent_routes
from .routes_auth import register_auth_routes
from .routes_embed import register_embed_routes
from .routes_hooks import register_hook_routes
from .routes_session import register_session_routes
from .routes_skills_memory import register_skills_memory_routes
from .routes_webhook import register_webhook_routes
from .routes_workspace_files import register_workspace_file_routes
from .routes_chat_attachments import register_chat_attachment_routes

logger = logging.getLogger(__name__)


def _safe_decode_request_body(body: bytes, max_chars: int = 1000) -> str:
    if not body:
        return "<empty>"
    try:
        parsed = json.loads(body)
        text = json.dumps(parsed, ensure_ascii=True, sort_keys=True)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        text = body.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return f"{text[:max_chars]}...<truncated>"
    return text


def install_request_validation_logging(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        body = (
            "<multipart omitted>"
            if request.headers.get("content-type", "").startswith("multipart/")
            else _safe_decode_request_body(await request.body())
        )
        logger.warning(
            "Request validation failed: method=%s path=%s errors=%s body=%s",
            request.method,
            request.url.path,
            exc.errors(),
            body,
        )
        return await request_validation_exception_handler(request, exc)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["AtlasClaw API"])
    register_chat_attachment_routes(router)
    register_session_routes(router)
    register_hook_routes(router)
    register_agent_routes(router)
    register_skills_memory_routes(router)
    register_webhook_routes(router)
    register_auth_routes(router)
    register_embed_routes(router)
    register_workspace_file_routes(router)

    @router.get("/health")
    async def health_check() -> dict[str, Any]:
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return router
