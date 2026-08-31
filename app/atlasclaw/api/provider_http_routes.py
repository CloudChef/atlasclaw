# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Generic authenticated dispatcher for provider-declared HTTP APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
import logging
import tempfile
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse, Response

from app.atlasclaw.api.deps_context import get_api_context
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_provider_instance_access,
    get_authorization_context,
)
from app.atlasclaw.core.provider_catalog import get_provider_catalog_instances
from app.atlasclaw.core.provider_http_runtime import (
    ProviderHttpContext,
    ProviderHttpRuntimeError,
    ProviderVisualRuntime,
    invoke_provider_http_handler,
)


router = APIRouter(tags=["Provider Runtime API"])
logger = logging.getLogger(__name__)
_MAX_PROVIDER_HTTP_BODY_BYTES = 128 * 1024 * 1024
_BODY_MEMORY_SPOOL_BYTES = 1024 * 1024
_BODY_REPLAY_CHUNK_BYTES = 1024 * 1024


def _validate_content_length(request: Request, *, max_body_bytes: int) -> None:
    """Reject an invalid or over-limit declared request length before dispatch."""
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid Content-Length header") from exc
    if content_length < 0:
        raise HTTPException(status_code=422, detail="Invalid Content-Length header")
    if content_length > max_body_bytes:
        raise HTTPException(status_code=413, detail="Provider request body exceeds Core limit")


@asynccontextmanager
async def _body_limited_request(
    request: Request,
    *,
    max_body_bytes: int = _MAX_PROVIDER_HTTP_BODY_BYTES,
) -> AsyncIterator[Request]:
    """Buffer the complete bounded body before a provider handler can run.

    ``Content-Length`` is only an early rejection hint. Reading the complete ASGI
    stream into a disk-backed spool prevents handlers that ignore or partially read
    their request body from bypassing the ceiling before producing side effects.
    """
    _validate_content_length(request, max_body_bytes=max_body_bytes)
    upstream_receive = request.receive
    spool = tempfile.SpooledTemporaryFile(
        max_size=min(max_body_bytes, _BODY_MEMORY_SPOOL_BYTES),
        mode="w+b",
    )
    body_bytes = 0
    try:
        async for chunk in request.stream():
            body_bytes += len(chunk)
            if body_bytes > max_body_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="Provider request body exceeds Core limit",
                )
            spool.write(chunk)
        spool.seek(0)
        replay_complete = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replay_complete
            if replay_complete:
                return await upstream_receive()
            # Starlette probes disconnect state inside an already-cancelled AnyIO
            # scope. Yield before reading so that probe cannot consume replay data.
            await anyio.lowlevel.checkpoint()
            chunk = spool.read(_BODY_REPLAY_CHUNK_BYTES)
            more_body = spool.tell() < body_bytes
            replay_complete = not more_body
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": more_body,
            }

        yield Request(request.scope, receive=replay_receive)
    finally:
        spool.close()


def _runtime_error_response(exc: Exception, *, provider_ref: str, route_path: str) -> HTTPException:
    """Map a provider's structured failure to the stable Core HTTP error envelope."""
    status_code = int(getattr(exc, "status_code", 500))
    code = str(getattr(exc, "code", "provider_http_runtime_failed"))
    detail = str(getattr(exc, "detail", "Provider HTTP operation failed"))
    if status_code >= 500:
        logger.exception(
            "Provider HTTP operation failed: provider=%s route=%s code=%s",
            provider_ref,
            route_path,
            code,
        )
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": detail},
    )


def _build_provider_http_response(result: Any, *, status_code: int) -> JSONResponse:
    """Materialize an eager JSON response while the request spool is still open."""
    if isinstance(result, (Response, Iterator, AsyncIterator)):
        raise ProviderHttpRuntimeError(
            "Provider HTTP handlers must return a JSON-serializable value",
            code="provider_http_response_type_invalid",
        )
    try:
        content = jsonable_encoder(result)
        return JSONResponse(status_code=status_code, content=content)
    except Exception as exc:
        raise ProviderHttpRuntimeError(
            "Provider HTTP handler returned a non-JSON value",
            code="provider_http_response_type_invalid",
        ) from exc


@router.api_route(
    "/providers/{provider_type}/{provider_instance}/{runtime_path:path}",
    methods=["DELETE", "GET", "PATCH", "POST", "PUT"],
)
async def dispatch_provider_http_request(
    provider_type: str,
    provider_instance: str,
    runtime_path: str,
    request: Request,
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> Response:
    """Dispatch one authorized request to a route declared by the provider package."""
    normalized_type = str(provider_type or "").strip().lower()
    normalized_instance = str(provider_instance or "").strip()
    normalized_path = str(runtime_path or "").strip("/")
    if not normalized_type or not normalized_instance or not normalized_path:
        raise HTTPException(status_code=404, detail="Provider API route not found")

    api_context = get_api_context()
    registry = getattr(api_context, "service_provider_registry", None)
    matcher = getattr(registry, "match_provider_http_route", None)
    if not callable(matcher):
        raise HTTPException(status_code=503, detail="Provider HTTP registry is unavailable")
    route, path_params = matcher(normalized_type, request.method, normalized_path)
    if route is None:
        raise HTTPException(status_code=404, detail="Provider API route not found")

    ensure_provider_instance_access(authz, normalized_type, normalized_instance)
    if route.access == "admin" and not authz.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    catalog = await get_provider_catalog_instances()
    provider_config = catalog.get(normalized_type, {}).get(normalized_instance)
    if not isinstance(provider_config, dict):
        raise HTTPException(status_code=404, detail="Provider instance not found")
    runtime_config = dict(provider_config)
    runtime_config["instance_name"] = normalized_instance
    user_extra = authz.user.extra if isinstance(authz.user.extra, dict) else {}
    provider_context = ProviderHttpContext(
        provider_type=normalized_type,
        provider_instance=normalized_instance,
        provider_config=runtime_config,
        path_params=path_params,
        user_id=str(authz.user.user_id or ""),
        tenant_id=str(authz.user.tenant_id or ""),
        is_admin=authz.is_admin,
        token_id=str(user_extra.get("api_token_id") or ""),
        visual_runtime=ProviderVisualRuntime(api_context.agent_runner),
    )
    provider_ref = f"{normalized_type}.{normalized_instance}"
    try:
        async with _body_limited_request(request) as provider_request:
            result: Any = await invoke_provider_http_handler(
                route,
                provider_request,
                provider_context,
            )
            response = _build_provider_http_response(
                result,
                status_code=route.success_status,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise _runtime_error_response(
            exc,
            provider_ref=provider_ref,
            route_path=normalized_path,
        ) from exc

    logger.info(
        "Provider HTTP operation completed: provider=%s method=%s route=%s actor=%s token=%s",
        provider_ref,
        request.method,
        route.path,
        provider_context.user_id,
        provider_context.token_id,
    )
    return response
