# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider UI extension discovery and static asset serving."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.atlasclaw.auth.guards import AuthorizationContext, get_authorization_context
from app.atlasclaw.core.provider_extension_registry import (
    ExtensionAssetError,
    get_provider_extension_registry,
)

logger = logging.getLogger(__name__)

_MAX_ASSET_BYTES = 8 * 1024 * 1024
_HTML_EXTENSIONS = frozenset({".html", ".htm"})


def _resolve_locale(request: Request, locale: str = "") -> str:
    """Return the requested locale, falling back to the first Accept-Language entry."""
    if locale.strip():
        return locale.strip()
    header = request.headers.get("accept-language", "")
    return header.split(",", 1)[0].split(";", 1)[0].strip() or "en-US"


def _asset_response(path: Path) -> Response:
    """Read one bounded provider asset and build a safe static response."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc
    if size > _MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="Asset exceeds the size limit")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc

    registry = get_provider_extension_registry()
    media_type = registry.mime_type_for(path)
    is_html = path.suffix.lower() in _HTML_EXTENSIONS
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store" if is_html else "private, max-age=60",
    }
    if is_html:
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content=data, media_type=media_type, headers=headers)


def register_ui_extension_routes(router: APIRouter) -> None:
    """Register provider UI extension discovery and asset routes."""

    @router.get("/ui/extensions")
    async def list_ui_extensions(
        request: Request,
        locale: str = Query(""),
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> dict:
        """Return navigation entries the current user may open."""
        registry = get_provider_extension_registry()
        resolved_locale = _resolve_locale(request, locale)
        return {
            "host": {"version": 1, "locale": resolved_locale},
            "items": registry.list_nav(
                is_admin=bool(getattr(authz, "is_admin", False)),
                locale=resolved_locale,
            ),
        }

    @router.get("/ui/extensions/{provider_type}/_assets/{asset_path:path}")
    async def get_provider_asset(
        provider_type: str,
        asset_path: str,
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> Response:
        """Serve an icon or image from the provider's ``assets/`` directory."""
        _ = authz
        registry = get_provider_extension_registry()
        try:
            path = registry.resolve_provider_asset(provider_type, asset_path)
        except ExtensionAssetError as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        return _asset_response(path)

    @router.get("/ui/extensions/{provider_type}/{nav_id}/{asset_path:path}")
    async def get_ui_asset(
        provider_type: str,
        nav_id: str,
        asset_path: str,
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> Response:
        """Serve one file from a provider UI extension with a strict path jail."""
        registry = get_provider_extension_registry()
        entry = registry.resolve_nav(provider_type, nav_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Extension not found")
        if entry.nav.access == "admin" and not bool(getattr(authz, "is_admin", False)):
            raise HTTPException(status_code=403, detail="Admin privileges required")
        try:
            path = registry.resolve_asset(entry, asset_path)
        except ExtensionAssetError as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        return _asset_response(path)
