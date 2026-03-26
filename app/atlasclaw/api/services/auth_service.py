# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ...auth.jwt_token import issue_atlas_token, verify_atlas_token
from ...auth.models import AuthenticationError
from ...session.context import ChatType as SessionChatType
from ...session.context import SessionKey, SessionScope
from ..deps_context import (
    APIContext,
    extract_atlas_token_from_request,
    get_api_context,
    is_admin_from_roles,
    resolve_workspace_path,
)
from ..schemas import LocalLoginRequest


def get_auth_config_or_400(request: Request, providers: tuple[str, ...]):
    from ...auth.config import AuthConfig

    auth_config: AuthConfig = getattr(request.app.state.config, "auth", None)
    if not auth_config or auth_config.provider.lower() not in providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider not configured, expected one of: {', '.join(providers)}",
        )
    return auth_config


def build_sso_provider(auth_config):
    import logging
    _logger = logging.getLogger(__name__)
    provider_name = auth_config.provider
    if provider_name == "dingtalk":
        from ...auth.providers.dingtalk_sso import DingTalkSSOProvider

        dt_config = auth_config.dingtalk.expanded()
        _logger.warning(f"[DEBUG] DingTalk raw redirect_uri: {auth_config.dingtalk.redirect_uri}")
        _logger.warning(f"[DEBUG] DingTalk expanded redirect_uri: {dt_config.redirect_uri}")
        provider = DingTalkSSOProvider(
            issuer="https://login.dingtalk.com",
            client_id=dt_config.app_key,
            client_secret=dt_config.app_secret,
            redirect_uri=dt_config.redirect_uri,
            scopes=dt_config.scopes,
            pkce_enabled=dt_config.pkce_enabled,
            pkce_method=dt_config.pkce_method,
            corp_id=dt_config.corp_id,
            subject_field=dt_config.subject_field,
        )
        return provider, dt_config.redirect_uri.startswith("https://")

    from ...auth.providers.oidc_sso import OIDCSSOProvider

    oidc_config = auth_config.oidc.expanded()
    provider = OIDCSSOProvider(
        issuer=oidc_config.issuer,
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        redirect_uri=oidc_config.redirect_uri,
        authorization_endpoint=oidc_config.authorization_endpoint,
        token_endpoint=oidc_config.token_endpoint,
        userinfo_endpoint=oidc_config.userinfo_endpoint,
        jwks_uri=oidc_config.jwks_uri,
        scopes=oidc_config.scopes,
        pkce_enabled=oidc_config.pkce_enabled,
        pkce_method=oidc_config.pkce_method,
    )
    return provider, oidc_config.redirect_uri.startswith("https://")


async def perform_local_login(request: Request, body: LocalLoginRequest) -> Response:
    from ...auth.providers.local import LocalAuthProvider
    from ...core.workspace import UserWorkspaceInitializer

    auth_config = get_auth_config_or_400(request, ("local",))
    jwt_cfg = auth_config.jwt.expanded()

    provider = LocalAuthProvider()
    try:
        auth_result = await provider.authenticate(f"{body.username}:{body.password}")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Local authentication failed: {exc}",
        )

    ctx = get_api_context()
    key = SessionKey(
        agent_id="main",
        channel="web",
        chat_type=SessionChatType.DM,
        user_id=auth_result.subject,
    )
    session_key_str = key.to_string(scope=SessionScope.MAIN)
    session = await ctx.session_manager.get_or_create(session_key_str)
    workspace_path = resolve_workspace_path(request, ctx=ctx)
    UserWorkspaceInitializer(workspace_path, auth_result.subject).initialize()

    roles = auth_result.roles if isinstance(auth_result.roles, list) else []
    auth_type = auth_result.extra.get("auth_type", "local")
    is_admin = is_admin_from_roles(roles)

    session.display_name = auth_result.display_name or body.username
    if not isinstance(session.extra, dict):
        session.extra = {}
    session.extra["auth_type"] = auth_type
    session.extra["roles"] = roles
    session.extra["is_admin"] = is_admin

    atlas_token = issue_atlas_token(
        subject=auth_result.subject,
        is_admin=is_admin,
        roles=roles,
        auth_type=auth_type,
        secret_key=jwt_cfg.secret_key,
        expires_minutes=jwt_cfg.expires_minutes,
        issuer=jwt_cfg.issuer,
    )

    secure_cookie = request.url.scheme == "https"
    response = JSONResponse(
        content={
            "success": True,
            "user": {
                "id": auth_result.subject,
                "username": body.username,
                "display_name": auth_result.display_name or body.username,
                "auth_type": auth_type,
                "roles": roles,
                "is_admin": is_admin,
            },
            "session": {
                "key": session_key_str,
                "created_at": session.created_at.isoformat(),
            },
            "token": atlas_token,
            "token_type": "Bearer",
            "header_name": jwt_cfg.header_name,
        },
    )
    response.set_cookie(
        key="atlasclaw_session",
        value=session_key_str,
        path="/",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    response.set_cookie(
        key=jwt_cfg.cookie_name,
        value=atlas_token,
        path="/",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    return response


async def begin_sso_login(request: Request) -> Response:
    import secrets

    auth_config = get_auth_config_or_400(request, ("oidc", "dingtalk"))
    provider, secure_cookie = build_sso_provider(auth_config)
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = provider.generate_pkce()
    auth_url = provider.build_authorization_url(state, code_challenge)
    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key="sso_state",
        value=state,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=600,
    )
    response.set_cookie(
        key="pkce_verifier",
        value=code_verifier,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=600,
    )
    return response


async def complete_sso_login(
    request: Request,
    *,
    code: str,
    state: str,
    error: str,
    error_description: str,
) -> Response:
    from ...auth.shadow_store import ShadowUserStore
    from ...core.workspace import UserWorkspaceInitializer

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"IdP error: {error} - {error_description}",
        )

    cookie_state = request.cookies.get("sso_state")
    code_verifier = request.cookies.get("pkce_verifier")
    if not state or state != cookie_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing state parameter",
        )
    if not code_verifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PKCE verifier missing or expired",
        )

    auth_config = get_auth_config_or_400(request, ("oidc", "dingtalk"))
    jwt_cfg = auth_config.jwt.expanded()
    provider, secure_cookie = build_sso_provider(auth_config)

    try:
        auth_result = await provider.complete_login(code, code_verifier)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SSO authentication failed: {exc}",
        )

    workspace_path = resolve_workspace_path(request)
    shadow_store = ShadowUserStore(workspace_path=workspace_path)
    shadow_user = await shadow_store.get_or_create(
        provider=auth_config.provider.lower(),
        result=auth_result,
    )
    user_id = shadow_user.user_id
    UserWorkspaceInitializer(workspace_path, user_id).initialize()

    ctx = get_api_context()
    key = SessionKey(
        agent_id="main",
        channel="web",
        chat_type=SessionChatType.DM,
        user_id=user_id,
    )
    session_key_str = key.to_string(scope=SessionScope.MAIN)
    session = await ctx.session_manager.get_or_create(session_key_str)

    roles = auth_result.roles if isinstance(auth_result.roles, list) else []
    auth_type = auth_result.extra.get("auth_type", "oidc")
    is_admin = is_admin_from_roles(roles)
    if not isinstance(session.extra, dict):
        session.extra = {}
    session.extra["auth_type"] = auth_type
    session.extra["roles"] = roles
    session.extra["is_admin"] = is_admin

    atlas_token = issue_atlas_token(
        subject=user_id,
        is_admin=is_admin,
        roles=roles,
        auth_type=auth_type,
        secret_key=jwt_cfg.secret_key,
        expires_minutes=jwt_cfg.expires_minutes,
        issuer=jwt_cfg.issuer,
    )

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="atlasclaw_session",
        value=session_key_str,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    response.set_cookie(
        key=jwt_cfg.cookie_name,
        value=atlas_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    if auth_result.raw_token:
        response.set_cookie(
            key="CloudChef-Authenticate",
            value=auth_result.raw_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
        )
    if auth_result.id_token:
        response.set_cookie(
            key="oidc_id_token",
            value=auth_result.id_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
        )

    response.delete_cookie("sso_state")
    response.delete_cookie("pkce_verifier")
    return response


async def get_current_user_payload(request: Request) -> dict[str, Any]:
    from ...auth.config import AuthConfig

    auth_config: AuthConfig = getattr(request.app.state.config, "auth", None)
    if not auth_config:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    jwt_cfg = auth_config.jwt.expanded()
    token = extract_atlas_token_from_request(
        request,
        jwt_cfg.header_name,
        jwt_cfg.cookie_name,
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        jwt_payload = verify_atlas_token(
            token=token,
            secret_key=jwt_cfg.secret_key,
            issuer=jwt_cfg.issuer,
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc

    session_key = request.cookies.get("atlasclaw_session")
    if not session_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session missing",
        )

    ctx = get_api_context()
    session = await ctx.session_manager.get_session(session_key)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    parsed_key = SessionKey.from_string(session.session_key)
    session_user_id = parsed_key.user_id or "default"
    jwt_user_id = str(jwt_payload.get("sub", "")).strip() or "default"
    if session_user_id != jwt_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session user mismatch",
        )

    roles = jwt_payload.get("roles", [])
    if not isinstance(roles, list):
        roles = []

    return {
        "user_id": jwt_user_id,
        "session_key": session.session_key,
        "auth_type": str(jwt_payload.get("auth_type", "")),
        "roles": roles,
        "display_name": session.display_name or jwt_user_id,
        "is_admin": bool(jwt_payload.get("is_admin", jwt_payload.get("admin", False))),
        "login_time": jwt_payload.get("login_time", ""),
        "metadata": session.to_dict(),
    }


async def logout_user(request: Request, redirect: bool = True) -> Response:
    from ...auth.config import AuthConfig

    session_key = request.cookies.get("atlasclaw_session")
    if session_key:
        ctx = get_api_context()
        await ctx.session_manager.delete_session(session_key)

    auth_config: AuthConfig = request.app.state.config.auth
    idp_logout_url: Optional[str] = None
    if auth_config and auth_config.provider == "oidc" and redirect:
        oidc_config = auth_config.oidc.expanded()
        if oidc_config.end_session_endpoint:
            post_logout_uri = str(request.base_url).rstrip("/") + "/api/auth/login"
            id_token_hint = request.cookies.get("oidc_id_token", "")
            logout_params = (
                f"?post_logout_redirect_uri={post_logout_uri}"
                f"&client_id={oidc_config.client_id}"
            )
            if id_token_hint:
                logout_params += f"&id_token_hint={id_token_hint}"
            idp_logout_url = f"{oidc_config.end_session_endpoint}{logout_params}"

    if idp_logout_url:
        response = RedirectResponse(url=idp_logout_url, status_code=302)
    else:
        response = JSONResponse(content={"status": "logged_out"})

    response.delete_cookie("atlasclaw_session")
    if auth_config and getattr(auth_config, "jwt", None):
        response.delete_cookie(auth_config.jwt.expanded().cookie_name)
    response.delete_cookie("AtlasClaw-Authenticate")
    response.delete_cookie("CloudChef-Authenticate")
    response.delete_cookie("oidc_id_token")
    response.delete_cookie("sso_state")
    response.delete_cookie("pkce_verifier")
    return response
