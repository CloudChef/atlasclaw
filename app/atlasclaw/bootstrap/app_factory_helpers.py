# -*- coding: utf-8 -*-
"""Reusable app-factory helpers extracted from main.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


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


def mount_frontend(app: FastAPI, frontend_dir: Path) -> None:
    """Mount frontend static folders and HTML entry routes."""
    if not frontend_dir.exists():
        return

    def _serve_spa_index():
        index_path = frontend_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return {"error": "Frontend not found"}

    static_dir = frontend_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    try:
        from app.atlasclaw.core.config import get_config

        workspace_public_dir = Path(get_config().workspace.path).resolve() / "public"
        workspace_public_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/user-content", StaticFiles(directory=str(workspace_public_dir)), name="user-content")
    except Exception:
        pass

    scripts_dir = frontend_dir / "scripts"
    if scripts_dir.exists():
        app.mount("/scripts", StaticFiles(directory=str(scripts_dir)), name="scripts")

    styles_dir = frontend_dir / "styles"
    if styles_dir.exists():
        app.mount("/styles", StaticFiles(directory=str(styles_dir)), name="styles")

    locales_dir = frontend_dir / "locales"
    if locales_dir.exists():
        app.mount("/locales", StaticFiles(directory=str(locales_dir)), name="locales")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return _serve_spa_index()

    @app.get("/channels.html", include_in_schema=False)
    async def serve_channels():
        channels_path = frontend_dir / "channels.html"
        if channels_path.exists():
            return FileResponse(str(channels_path))
        return {"error": "Channels page not found"}

    @app.get("/login.html", include_in_schema=False)
    async def serve_login():
        login_path = frontend_dir / "login.html"
        if login_path.exists():
            return FileResponse(str(login_path))
        return {"error": "Login page not found"}

    @app.get("/admin/users", include_in_schema=False)
    async def serve_admin_users():
        return _serve_spa_index()

    @app.get("/models.html", include_in_schema=False)
    async def serve_models():
        models_path = frontend_dir / "models.html"
        if models_path.exists():
            return FileResponse(str(models_path))
        return {"error": "Models page not found"}

    @app.get("/config.json", include_in_schema=False)
    async def serve_config():
        config_path = frontend_dir / "config.json"
        if config_path.exists():
            return FileResponse(str(config_path))
        return {"apiBaseUrl": ""}


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
        from app.atlasclaw.auth.shadow_store import ShadowUserStore
        from app.atlasclaw.core.config import get_config

        config = get_config()
        auth = config.auth if config else None
        if isinstance(auth, dict):
            auth = AuthConfig(**auth)
        if auth is not None and not auth.enabled:
            auth = None

        auth_workspace_path = str(Path(config.workspace.path).resolve())
        shadow_store = ShadowUserStore(workspace_path=auth_workspace_path)
        setup_auth_middleware(app, auth, shadow_store=shadow_store)

        app.state.config = config
        if auth is not None and isinstance(auth, AuthConfig):
            app.state.config.auth = auth
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(f"Config setup warning: {exc}")
