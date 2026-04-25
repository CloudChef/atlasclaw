# Copyright 2021  Qianyun, Inc. All rights reserved.


"""
AuthMiddleware — FastAPI/Starlette middleware for request authentication.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.jwt_token import verify_atlas_token
from app.atlasclaw.auth.models import ANONYMOUS_USER, AuthenticationError, UserInfo
from app.atlasclaw.auth.strategy import AuthStrategy
from app.atlasclaw.core.base_path import build_base_path_url, normalize_base_path

logger = logging.getLogger(__name__)

_SKIP_PATHS = frozenset({"/api/health", "/ping", "/favicon.ico", "/docs", "/openapi.json"})

_SSO_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/local/login",
    "/api/auth/callback",
    "/api/auth/logout",
    "/api/auth/me",
})

_CMP_PUBLIC_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/local/login",
    "/api/auth/callback",
    "/api/auth/logout",
})

_STATIC_PREFIXES = (
    "/static/",
    "/styles/",
    "/scripts/",
    "/locales/",
    "/user-content/",
    "/config.json",
    "/index.html",
    "/login.html",
)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        strategy: AuthStrategy,
        auth_config: Optional[AuthConfig] = None,
        anonymous_fallback: bool = False,
        oidc_redirect_uri: str = "",
    ) -> None:
        super().__init__(app)
        self._strategy = strategy
        self._auth_config = auth_config or AuthConfig(enabled=False)
        self._anonymous_fallback = anonymous_fallback
        self._oidc_redirect_uri = oidc_redirect_uri

        jwt_cfg = self._auth_config.jwt.expanded()
        oidc_cfg = self._auth_config.oidc.expanded()
        host_cfg = self._auth_config.host.expanded()
        self._atlas_header_name = (jwt_cfg.header_name or "AtlasClaw-Authenticate").strip()
        self._atlas_cookie_name = (jwt_cfg.cookie_name or "AtlasClaw-Authenticate").strip()
        self._host_header_name = (
            host_cfg.header_name or "AtlasClaw-Host-Authenticate"
        ).strip()
        self._host_cookie_name = (
            host_cfg.cookie_name or "AtlasClaw-Host-Authenticate"
        ).strip()
        self._atlas_issuer = jwt_cfg.issuer
        self._atlas_secret = jwt_cfg.secret_key
        self._ocbc_enabled = bool(oidc_cfg.ocbc_enabled)


    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            request.state.user_info = ANONYMOUS_USER
            return await call_next(request)

        if request.url.path in _SKIP_PATHS:
            request.state.user_info = ANONYMOUS_USER
            return await call_next(request)


        if request.url.path.startswith(_STATIC_PREFIXES):
            request.state.user_info = ANONYMOUS_USER
            return await call_next(request)

        if self._anonymous_fallback:
            request.state.user_info = ANONYMOUS_USER
            return await call_next(request)

        provider_name = self._current_provider_name()

        if provider_name == "none":
            try:
                request.state.user_info = await self._strategy.resolve_user("")
            except Exception:
                request.state.user_info = ANONYMOUS_USER
            return await call_next(request)

        # CMP mode: support both local AtlasClaw admin JWT sessions and CMP cookie auth.
        # AtlasClaw JWT is checked first so backend management login remains available.
        if provider_name == "cmp":
            atlas_token = self._extract_atlas_token(request)
            if atlas_token:
                try:
                    payload = verify_atlas_token(
                        token=atlas_token,
                        secret_key=self._atlas_secret,
                        issuer=self._atlas_issuer,
                    )
                except AuthenticationError as exc:
                    logger.debug("Atlas token verification failed in cmp mode: %s", exc)
                    return self._auth_failed_response(request)

                request.state.user_info = self._build_user_info_from_payload(
                    payload,
                    atlas_token,
                )
                return await call_next(request)

            if request.url.path in _CMP_PUBLIC_PATHS:
                request.state.user_info = ANONYMOUS_USER
                return await call_next(request)

            from app.atlasclaw.auth.providers.cmp import CMPAuthProvider
            cookies = dict(request.cookies)
            logger.warning("CMP DEBUG: path=%s cookies=%s", request.url.path, list(cookies.keys()))
            cmp_provider = self._strategy.primary_provider
            if not isinstance(cmp_provider, CMPAuthProvider):
                return JSONResponse(status_code=500, content={"detail": "CMP provider misconfigured"})
            try:
                auth_result = await cmp_provider.authenticate_from_cookies(cookies)
                logger.warning("CMP DEBUG: auth_result subject=%s", auth_result.subject)
                shadow = await self._strategy._shadow_store.get_or_create(
                    provider="cmp", result=auth_result,
                )
                logger.warning("CMP DEBUG: shadow user_id=%s", shadow.user_id)
                self._strategy.ensure_user_workspace(shadow.user_id)
                provider_cookie_context = {
                    "provider_cookie_available": True,
                    "provider_cookie_token": auth_result.raw_token,
                }
                request.state.user_info = shadow.to_user_info(
                    raw_token=auth_result.raw_token,
                    extra={**auth_result.extra, **provider_cookie_context},
                )
                logger.warning("CMP DEBUG: user_info set, proceeding")
                return await call_next(request)
            except AuthenticationError as exc:
                logger.warning("CMP cookie auth FAILED (AuthenticationError): %s", exc)
                return self._auth_failed_response(request)
            except Exception as exc:
                logger.warning("CMP cookie auth FAILED (unexpected): %s %s", type(exc).__name__, exc)
                return self._auth_failed_response(request)

        # SSO paths: skip auth for non-CMP providers (login, callback, etc.)
        if request.url.path in _SSO_PATHS:
            request.state.user_info = ANONYMOUS_USER
            return await call_next(request)

        if provider_name != "none":
            atlas_token = self._extract_atlas_token(request)
            if not atlas_token:
                return self._auth_failed_response(request)

            try:
                payload = verify_atlas_token(
                    token=atlas_token,
                    secret_key=self._atlas_secret,
                    issuer=self._atlas_issuer,
                )

            except AuthenticationError as exc:
                logger.debug("Atlas token verification failed: %s", exc)
                return self._auth_failed_response(request)

            provider_sso_token = ""
            if provider_name not in {"local", "none", "cmp", ""}:
                provider_sso_token = self._extract_host_token(request)

            jwt_user_info = self._build_user_info_from_payload(
                payload,
                atlas_token,
                provider_sso_token=provider_sso_token,
            )
            if provider_name != "local":
                self._strategy.ensure_user_workspace(jwt_user_info.user_id)

            if provider_name == "oidc" and self._ocbc_enabled:
                oidc_token = self._extract_host_token(request)
                if not oidc_token:
                    return self._auth_failed_response(request)

                try:
                    oidc_user = await self._strategy.resolve_user(oidc_token)
                except AuthenticationError as exc:
                    logger.debug("OIDC token verification failed (OCBC): %s", exc)
                    return self._auth_failed_response(request)

                provider_subject = oidc_user.provider_subject or ""
                oidc_subject = provider_subject.split(":", 1)[1] if ":" in provider_subject else ""
                if oidc_user.user_id != jwt_user_info.user_id and (
                    not oidc_subject or oidc_subject != jwt_user_info.user_id
                ):
                    logger.debug(
                        "OIDC identity mismatch (OCBC): oidc_user_id=%s oidc_subject=%s jwt_sub=%s",
                        oidc_user.user_id,
                        oidc_subject,
                        jwt_user_info.user_id,
                    )
                    return self._auth_failed_response(request)

            request.state.user_info = jwt_user_info
            return await call_next(request)

        credential = self._extract_provider_credential(request)
        if not credential:
            return self._auth_failed_response(request)

        try:
            request.state.user_info = await self._strategy.resolve_user(credential)
            return await call_next(request)
        except AuthenticationError as exc:
            logger.debug("Auth failed for %s: %s", request.url.path, exc)
            return self._auth_failed_response(request)

    def _build_user_info_from_payload(
        self,
        payload: dict,
        raw_token: str,
        *,
        provider_sso_token: str = "",
    ) -> UserInfo:
        roles = payload.get("roles", [])
        if not isinstance(roles, list):
            roles = []

        user_id = str(payload.get("sub", "")).strip() or "default"
        auth_type = str(payload.get("auth_type", "local")).strip() or "local"
        external_subject = str(payload.get("external_subject", "")).strip()
        provider_subject = str(payload.get("provider_subject", "")).strip()

        # Include is_admin in extra for guards to check
        extra = {
            "login_time": payload.get("login_time", ""),
            "is_admin": payload.get("is_admin", False),
            "provider_sso_available": bool(str(provider_sso_token or "").strip()),
            "provider_sso_token": str(provider_sso_token or "").strip(),
        }
        if external_subject:
            extra["external_subject"] = external_subject

        return UserInfo(
            user_id=user_id,
            display_name=str(payload.get("display_name", "")).strip() or user_id,
            tenant_id="default",
            roles=roles,
            raw_token=raw_token,
            provider_subject=provider_subject or (f"{auth_type}:{external_subject}" if external_subject else f"{auth_type}:{user_id}"),
            extra=extra,
            auth_type=auth_type,
        )

    def _current_provider_name(self) -> str:
        provider = self._strategy.primary_provider
        return provider.provider_name() if provider is not None else "none"

    def _auth_failed_response(self, request: Request):
        config = getattr(request.app.state, "config", None)
        base_path = normalize_base_path(getattr(config, "base_path", ""))
        if request.url.path == "/" or self._is_browser_request(request):
            provider_name = self._current_provider_name()

            # SSO providers (oidc, dingtalk, etc.) redirect to /api/auth/login
            # Use reverse exclusion pattern: all providers except local/none/empty are SSO
            # This way, new SSO providers (feishu, wecom, etc.) work without code changes
            if provider_name not in ("local", "none", "cmp", ""):
                return RedirectResponse(
                    url=build_base_path_url(base_path, "/api/auth/login"),
                    status_code=302,
                )

            original = build_base_path_url(base_path, request.url.path)
            if request.url.query:
                original = f"{original}?{request.url.query}"
            login_path = build_base_path_url(base_path, "/login.html")
            redirect_url = f"{login_path}?redirect={quote(original, safe='')}"
            return RedirectResponse(url=redirect_url, status_code=302)
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


    def _extract_atlas_token(self, request: Request) -> str:
        for header_name in (self._atlas_header_name, "AtlasClaw-Authenticate"):
            token = request.headers.get(header_name, "").strip()
            if token:
                return token

        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()

        for cookie_name in (self._atlas_cookie_name, "AtlasClaw-Authenticate"):
            token = request.cookies.get(cookie_name, "").strip()
            if token:
                return token

        return ""

    def _extract_host_token(self, request: Request) -> str:
        token = request.headers.get(self._host_header_name, "").strip()
        if token:
            return token
        token = request.cookies.get(self._host_cookie_name, "").strip()
        if token:
            return token
        return ""

    def _extract_provider_credential(self, request: Request) -> str:
        token = request.headers.get(self._auth_config.header_name, "").strip()
        if token:
            return token

        token = request.headers.get(self._host_header_name, "").strip()
        if token:
            return token

        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()

        token = request.cookies.get(self._auth_config.header_name, "").strip()
        if token:
            return token

        token = request.cookies.get(self._host_cookie_name, "").strip()
        if token:
            return token

        return ""

    @staticmethod
    def _is_browser_request(request: Request) -> bool:
        accept = request.headers.get("accept", "")
        xhr = request.headers.get("x-requested-with", "")
        return "text/html" in accept and xhr.lower() != "xmlhttprequest"


def setup_auth_middleware(
    app,
    auth_config: Optional[object],
    shadow_store: Optional[object] = None,
) -> None:
    from app.atlasclaw.auth.config import AuthConfig
    from app.atlasclaw.auth.strategy import create_auth_strategy

    if auth_config is None:
        from app.atlasclaw.auth.providers.none import NoneProvider
        from app.atlasclaw.auth.shadow_store import ShadowUserStore

        _store = shadow_store or ShadowUserStore()
        _provider = NoneProvider(default_user_id="anonymous")
        strategy = AuthStrategy(providers=[_provider], shadow_store=_store, cache_ttl_seconds=0)

        app.add_middleware(AuthMiddleware, strategy=strategy, auth_config=None, anonymous_fallback=True)
        logger.info("AuthMiddleware: anonymous fallback mode (no auth config)")
        return

    if isinstance(auth_config, dict):
        auth_config = AuthConfig(**auth_config)

    strategy = create_auth_strategy(auth_config, shadow_store)
    if strategy is None:
        logger.warning("AuthMiddleware: create_auth_strategy returned None, using anonymous")
        app.add_middleware(
            AuthMiddleware,
            strategy=AuthStrategy(
                providers=[
                    __import__(
                        "app.atlasclaw.auth.providers.none", fromlist=["NoneProvider"]
                    ).NoneProvider()
                ],
                shadow_store=__import__(
                    "app.atlasclaw.auth.shadow_store", fromlist=["ShadowUserStore"]
                ).ShadowUserStore(),
            ),

            auth_config=None,
            anonymous_fallback=True,
        )
        return

    oidc_redirect_uri = ""
    if auth_config.provider.lower() == "oidc":
        oidc_redirect_uri = auth_config.oidc.expanded().redirect_uri

    app.add_middleware(
        AuthMiddleware,
        strategy=strategy,
        auth_config=auth_config,
        anonymous_fallback=False,
        oidc_redirect_uri=oidc_redirect_uri,
    )
    logger.info(
        "AuthMiddleware: registered with provider=%r, standalone_sso=%s, ocbc=%s",
        auth_config.provider,
        bool(oidc_redirect_uri),
        bool(auth_config.oidc.ocbc_enabled),
    )
