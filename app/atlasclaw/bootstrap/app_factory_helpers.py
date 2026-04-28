# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Reusable app-factory helpers extracted from main.py."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.atlasclaw.core.base_path import build_base_path_url, normalize_base_path, strip_base_path


_BASE_PATH_TOKEN = "__ATLASCLAW_BASE_PATH__"
_BASE_PATH_JSON_TOKEN = "__ATLASCLAW_BASE_PATH_JSON__"


def _resolve_base_path() -> str:
    try:
        from app.atlasclaw.core.config import get_config

        return normalize_base_path(get_config().base_path)
    except Exception:
        return ""


def _resolve_app_base_path(app: FastAPI) -> str:
    config = getattr(app.state, "config", None)
    return normalize_base_path(getattr(config, "base_path", "")) or _resolve_base_path()


def render_frontend_html(frontend_path: Path) -> HTMLResponse | dict[str, str]:
    if not frontend_path.exists():
        return {"error": "Frontend not found"}

    content = frontend_path.read_text(encoding="utf-8")
    base_path = _resolve_base_path()
    content = content.replace(_BASE_PATH_TOKEN, base_path)
    content = content.replace(_BASE_PATH_JSON_TOKEN, json.dumps(base_path))
    return HTMLResponse(content=content)


class StaticFileCacheMiddleware(BaseHTTPMiddleware):
    """Add no-cache headers for frontend static resource paths."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/static/", "/scripts/", "/styles/", "/locales/", "/user-content/")) or path in {
            "/",
            "/account",
            "/admin/users",
            "/channels",
            "/models",
            "/login.html",
            "/channels.html",
            "/models.html",
        } or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


class ExternalBasePathMiddleware(BaseHTTPMiddleware):
    """Accept proxied requests with the external base_path still attached."""

    async def dispatch(self, request: Request, call_next):
        base_path = _resolve_app_base_path(request.app)
        original_path = request.scope.get("path", "")
        normalized_path = strip_base_path(base_path, original_path)

        if base_path and normalized_path != original_path:
            request.scope["atlasclaw_external_path"] = original_path
            request.scope["path"] = normalized_path
            raw_path = request.scope.get("raw_path")
            if raw_path is not None:
                request.scope["raw_path"] = normalized_path.encode("utf-8")

        return await call_next(request)


def mount_frontend(app: FastAPI, frontend_dir: Path) -> None:
    """Mount frontend static folders and HTML entry routes."""
    if not frontend_dir.exists():
        return

    external_base_path = _resolve_base_path()

    def _serve_spa_index():
        index_path = frontend_dir / "index.html"
        return render_frontend_html(index_path)

    def _mount_static_alias(url_path: str, directory: Path, name: str) -> None:
        if not directory.exists():
            return
        app.mount(url_path, StaticFiles(directory=str(directory)), name=name)
        if external_base_path:
            app.mount(
                f"{external_base_path}{url_path}",
                StaticFiles(directory=str(directory)),
                name=f"{name}-external-base",
            )

    static_dir = frontend_dir / "static"
    _mount_static_alias("/static", static_dir, "static")

    try:
        from app.atlasclaw.core.config import get_config

        workspace_public_dir = Path(get_config().workspace.path).resolve() / "public"
        workspace_public_dir.mkdir(parents=True, exist_ok=True)
        _mount_static_alias("/user-content", workspace_public_dir, "user-content")
    except Exception:
        pass

    scripts_dir = frontend_dir / "scripts"
    _mount_static_alias("/scripts", scripts_dir, "scripts")

    styles_dir = frontend_dir / "styles"
    _mount_static_alias("/styles", styles_dir, "styles")

    locales_dir = frontend_dir / "locales"
    _mount_static_alias("/locales", locales_dir, "locales")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return _serve_spa_index()

    @app.get("/channels.html", include_in_schema=False)
    async def serve_channels():
        return RedirectResponse(
            url=build_base_path_url(_resolve_base_path(), "/channels"),
            status_code=302,
        )

    @app.get("/login.html", include_in_schema=False)
    async def serve_login():
        login_path = frontend_dir / "login.html"
        if login_path.exists():
            return render_frontend_html(login_path)
        return {"error": "Login page not found"}

    @app.get("/admin/users", include_in_schema=False)
    async def serve_admin_users():
        return _serve_spa_index()

    @app.get("/models.html", include_in_schema=False)
    async def serve_models():
        return RedirectResponse(
            url=build_base_path_url(_resolve_base_path(), "/models"),
            status_code=302,
        )

    @app.get("/config.json", include_in_schema=False)
    async def serve_config():
        config_path = frontend_dir / "config.json"
        payload: dict[str, object] = {}
        if config_path.exists():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}

        base_path = _resolve_base_path()
        payload["basePath"] = base_path
        if not str(payload.get("apiBaseUrl", "")).strip():
            payload["apiBaseUrl"] = base_path
        return JSONResponse(payload)


def register_core_routers(
    app: FastAPI,
    *,
    api_router,
    channel_hooks_router,
    channels_router,
    agent_info_router,
    db_api_router,
) -> None:
    """Register all core API routers to app."""
    app.include_router(api_router)
    app.include_router(channel_hooks_router)
    app.include_router(channels_router)
    app.include_router(agent_info_router)
    app.include_router(db_api_router)


def setup_auth_middleware_from_config(app: FastAPI) -> None:
    """Install auth middleware from active config with graceful fallback."""
    try:
        from app.atlasclaw.auth.config import AuthConfig
        from app.atlasclaw.auth.middleware import setup_auth_middleware
        from app.atlasclaw.core.config import get_config

        config = get_config()
        auth = config.auth if config else None
        if isinstance(auth, dict):
            auth = AuthConfig(**auth)
        if auth is not None and not auth.enabled:
            auth = None

        auth_workspace_path = str(Path(config.workspace.path).resolve())
        setup_auth_middleware(app, auth, workspace_path=auth_workspace_path)

        app.state.config = config
        if auth is not None and isinstance(auth, AuthConfig):
            app.state.config.auth = auth
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(f"Config setup warning: {exc}")
