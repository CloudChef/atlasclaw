# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Generic authenticated dispatcher for provider-declared HTTP APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
import logging
import tempfile
from pathlib import Path
from typing import Any

import anyio
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse, Response, StreamingResponse

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
from app.atlasclaw.core.provider_agent_host import ProviderAgentHost
from app.atlasclaw.core.provider_host_services import (
    ProviderHostServices,
    ProviderUser,
    provider_data_dir,
)
from app.atlasclaw.core.security_guard import ensure_user_work_dir


router = APIRouter(tags=["Provider Runtime API"])
logger = logging.getLogger(__name__)
_MAX_PROVIDER_HTTP_BODY_BYTES = 256 * 1024 * 1024
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


def _build_provider_http_response(
    result: Any,
    *,
    status_code: int,
    response_mode: str = "json",
) -> Response:
    """Materialize a provider handler result as JSON or a binary response."""
    if isinstance(result, (StreamingResponse, Iterator, AsyncIterator)):
        raise ProviderHttpRuntimeError(
            "Provider streaming responses are not supported",
            code="provider_http_response_type_invalid",
        )
    if isinstance(result, Response):
        if response_mode != "binary":
            raise ProviderHttpRuntimeError(
                "Provider JSON routes must return a JSON-serializable value",
                code="provider_http_response_type_invalid",
            )
        result.headers.setdefault("X-Content-Type-Options", "nosniff")
        media_type = str(getattr(result, "media_type", "") or "").lower()
        if media_type.startswith("text/html"):
            result.headers.setdefault(
                "Content-Security-Policy",
                "sandbox; default-src 'none'; img-src data: blob:; "
                "style-src 'unsafe-inline'; font-src data:",
            )
        return result
    if response_mode == "binary":
        raise ProviderHttpRuntimeError(
            "Provider binary routes must return a Response",
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

    schema = registry.get_provider_schema_definition(normalized_type)
    extension_only_default = (
        bool(getattr(schema, "extension_only", False))
        and normalized_instance == "default"
    )
    if not extension_only_default:
        ensure_provider_instance_access(authz, normalized_type, normalized_instance)
    if route.access == "admin" and not authz.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    catalog = await get_provider_catalog_instances()
    provider_config = catalog.get(normalized_type, {}).get(normalized_instance)
    if provider_config is None and extension_only_default:
        provider_config = {}
    if not isinstance(provider_config, dict):
        raise HTTPException(status_code=404, detail="Provider instance not found")
    runtime_config = dict(provider_config)
    runtime_config["instance_name"] = normalized_instance
    user_extra = authz.user.extra if isinstance(authz.user.extra, dict) else {}
    workspace_path = api_context.session_manager_router.for_user(
        str(authz.user.user_id or ""),
    ).workspace_path
    provider_user = ProviderUser(
        user_id=str(authz.user.user_id or "anonymous"),
        display_name=str(getattr(authz.user, "display_name", "") or ""),
        tenant_id=str(getattr(authz.user, "tenant_id", "") or "default"),
        is_admin=bool(authz.is_admin),
        roles=tuple(str(role) for role in (getattr(authz.user, "roles", None) or [])),
        locale=str(user_extra.get("ui_locale") or user_extra.get("locale") or ""),
    )
    data_dir = provider_data_dir(workspace_path, normalized_type, provider_user)
    agent_host = ProviderAgentHost(
        api_context,
        authz=authz,
        provider_type=normalized_type,
        user=provider_user,
        data_dir=data_dir,
        provider_config=runtime_config,
        request_cookies=dict(request.cookies),
    )
    host_services = ProviderHostServices(
        workspace_path=Path(workspace_path).resolve(),
        data_dir=data_dir,
        user=provider_user,
        agent=agent_host,
        config=runtime_config,
        provider_type=normalized_type,
        work_dir=Path(ensure_user_work_dir(workspace_path, provider_user.user_id)).resolve(),
    )
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
        host=host_services,
    )
    provider_ref = f"{normalized_type}.{normalized_instance}"
    try:
        async with _body_limited_request(request, max_body_bytes=route.max_body_bytes) as provider_request:
            try:
                result: Any = await asyncio.wait_for(
                    invoke_provider_http_handler(route, provider_request, provider_context),
                    timeout=float(route.timeout_seconds or 30.0),
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail={"code": "provider_route_timeout", "message": "Provider route timed out"},
                ) from exc
            response = _build_provider_http_response(
                result,
                status_code=route.success_status,
                response_mode=route.response,
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
