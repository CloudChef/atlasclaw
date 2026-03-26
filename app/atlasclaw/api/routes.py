# -*- coding: utf-8 -*-
"""
REST API composition and request validation logging.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .deps_context import APIContext, get_api_context, set_api_context
from .routes_agent import register_agent_routes
from .routes_auth import register_auth_routes
from .routes_session import register_session_routes
from .routes_skills_memory import register_skills_memory_routes
from .routes_webhook import register_webhook_routes

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
        body = await request.body()
        logger.warning(
            "Request validation failed: method=%s path=%s errors=%s body=%s",
            request.method,
            request.url.path,
            exc.errors(),
            _safe_decode_request_body(body),
        )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["AtlasClaw API"])
    register_session_routes(router)
    register_agent_routes(router)
    register_skills_memory_routes(router)
    register_webhook_routes(router)
    register_auth_routes(router)

    @router.get("/health")
    async def health_check() -> dict[str, Any]:
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return router
