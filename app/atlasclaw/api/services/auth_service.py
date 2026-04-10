# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ...auth.jwt_token import issue_atlas_token, verify_atlas_token
from ...auth.models import AuthenticationError
from ...core.base_path import (
    build_base_path_url,
    cookie_path_for_base_path,
    normalize_base_path,
)
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


def _get_base_path(request: Request) -> str:
    config = getattr(request.app.state, "config", None)
    return normalize_base_path(getattr(config, "base_path", ""))


def _build_app_url(request: Request, path: str) -> str:
    return build_base_path_url(_get_base_path(request), path)


def _build_external_app_url(request: Request, path: str) -> str:
    return f"{str(request.base_url).rstrip('/')}{_build_app_url(request, path)}"


def _cookie_path(request: Request) -> str:
    return cookie_path_for_base_path(_get_base_path(request))


def _delete_cookie(response: Response, request: Request, key: str) -> None:
    path = _cookie_path(request)
    response.delete_cookie(key, path=path)
    if path != "/":
        response.delete_cookie(key, path="/")


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
        path=_cookie_path(request),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    response.set_cookie(
        key=jwt_cfg.cookie_name,
        value=atlas_token,
        path=_cookie_path(request),
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
        path=_cookie_path(request),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=600,
    )
    response.set_cookie(
        key="pkce_verifier",
        value=code_verifier,
        path=_cookie_path(request),
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
    session.display_name = auth_result.display_name or shadow_user.display_name or user_id

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
        additional_claims={
            "display_name": session.display_name,
            "external_subject": auth_result.subject,
            "provider_subject": f"{auth_config.provider.lower()}:{auth_result.subject}",
        },
    )

    response = RedirectResponse(url=_build_app_url(request, "/"), status_code=302)
    response.set_cookie(
        key="atlasclaw_session",
        value=session_key_str,
        path=_cookie_path(request),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    response.set_cookie(
        key=jwt_cfg.cookie_name,
        value=atlas_token,
        path=_cookie_path(request),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    if auth_result.raw_token:
        response.set_cookie(
            key="CloudChef-Authenticate",
            value=auth_result.raw_token,
            path=_cookie_path(request),
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
        )
    if auth_result.id_token:
        response.set_cookie(
            key="oidc_id_token",
            value=auth_result.id_token,
            path=_cookie_path(request),
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
        )

    _delete_cookie(response, request, "sso_state")
    _delete_cookie(response, request, "pkce_verifier")
    return response


async def load_profile_snapshot(
    *,
    user_id: str,
    auth_type: str = "",
    workspace_path: str = "",
    external_subject: str = "",
) -> dict[str, Any]:
    from ...auth.shadow_store import ShadowUserStore
    from ...db.database import get_db_manager
    from ...db.orm.user import UserService

    async def _resolve_federated_subject() -> str:
        normalized_auth_type = str(auth_type or "").strip().lower()
        if normalized_auth_type == "local":
            return ""

        normalized_external_subject = str(external_subject or "").strip()
        if normalized_external_subject:
            return normalized_external_subject

        if not workspace_path:
            return ""

        try:
            shadow_store = ShadowUserStore(workspace_path=workspace_path)
            shadow_user = await shadow_store.get_by_id(user_id)
        except Exception:
            return ""

        if not shadow_user:
            return ""

        return str(shadow_user.subject or "").strip()

    async def _load_db_profile(username: str) -> dict[str, Any]:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return {}

        try:
            db_manager = get_db_manager()
            async with db_manager.get_session() as db_session:
                user = await UserService.get_by_username(db_session, normalized_username)
                if not user:
                    return {}
                return {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "display_name": user.display_name or user.username,
                    "avatar_url": user.avatar_url,
                    "roles": user.roles or {},
                    "auth_type": user.auth_type,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                    "created_at": user.created_at,
                    "last_login_at": user.last_login_at,
                    "updated_at": user.updated_at,
                }
        except Exception:
            return {}

    db_profile = await _load_db_profile(user_id)
    if db_profile:
        return db_profile

    federated_subject = await _resolve_federated_subject()
    if federated_subject and federated_subject != str(user_id or "").strip():
        db_profile = await _load_db_profile(federated_subject)
        if db_profile:
            return db_profile

    normalized_auth_type = str(auth_type or "").strip().lower()
    if not workspace_path or normalized_auth_type == "local":
        return {}

    try:
        shadow_store = ShadowUserStore(workspace_path=workspace_path)
        shadow_user = await shadow_store.get_by_id(user_id)
        if not shadow_user:
            return {}

        roles = {str(role): True for role in shadow_user.roles}
        is_admin = any(str(role).lower() == "admin" for role in shadow_user.roles)
        display_name = shadow_user.display_name or shadow_user.subject or shadow_user.user_id
        username = shadow_user.subject or shadow_user.user_id

        return {
            "id": shadow_user.user_id,
            "username": username,
            "email": None,
            "display_name": display_name,
            "avatar_url": None,
            "roles": roles,
            "auth_type": shadow_user.auth_type or auth_type,
            "is_active": True,
            "is_admin": is_admin,
            "created_at": shadow_user.created_at,
            "last_login_at": shadow_user.last_seen_at,
            "updated_at": shadow_user.last_seen_at,
        }
    except Exception:
        return {}


async def get_current_user_payload(request: Request) -> dict[str, Any]:
    from ...auth.config import AuthConfig
    from ...auth.guards import resolve_authorization_context
    from ...auth.models import UserInfo
    from ...db.database import get_db_manager

    auth_config: AuthConfig = getattr(request.app.state.config, "auth", None)
    if not auth_config:
        user_info = getattr(request.state, "user_info", None)
        if user_info:
            return {
                "user_id": user_info.user_id,
                "display_name": user_info.display_name,
                "provider": "none",
            }
        return {
            "user_id": "anonymous",
            "display_name": "Anonymous",
            "provider": "none",
        }

    # CMP mode: user identity already resolved by middleware from cookies
    if auth_config.provider == "cmp":
        user_info = getattr(request.state, "user_info", None)
        if user_info and user_info.user_id != "anonymous":
            return {
                "user_id": user_info.user_id,
                "display_name": user_info.display_name,
                "provider": "cmp",
                "auth_type": "cmp",
                "tenant_id": user_info.tenant_id,
            }
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

    profile_overrides = await load_profile_snapshot(
        user_id=jwt_user_id,
        auth_type=str(jwt_payload.get("auth_type", "")),
        workspace_path=resolve_workspace_path(request, ctx=ctx),
        external_subject=str(jwt_payload.get("external_subject", "")),
    )

    effective_permissions: dict[str, Any] = {}
    effective_role_identifiers = roles
    effective_is_admin = bool(jwt_payload.get("is_admin", jwt_payload.get("admin", False)))

    try:
        db_manager = get_db_manager()
        async with db_manager.get_session() as db_session:
            authz = await resolve_authorization_context(
                db_session,
                UserInfo(
                    user_id=jwt_user_id,
                    display_name=profile_overrides.get("display_name", session.display_name or jwt_user_id),
                    roles=roles,
                    provider_subject=str(jwt_payload.get("provider_subject", "")),
                    auth_type=str(jwt_payload.get("auth_type", "")),
                    extra={
                        "is_admin": effective_is_admin,
                        "external_subject": str(jwt_payload.get("external_subject", "")),
                    },
                ),
            )
        effective_permissions = authz.permissions
        effective_role_identifiers = authz.role_identifiers
        effective_is_admin = authz.is_admin
    except Exception:
        # Fall back to the token payload if the DB-backed RBAC snapshot cannot be resolved.
        effective_permissions = {}

    return {
        "user_id": jwt_user_id,
        "username": profile_overrides.get("username", jwt_user_id),
        "session_key": session.session_key,
        "auth_type": str(jwt_payload.get("auth_type", "")),
        "roles": effective_role_identifiers,
        "role_identifiers": effective_role_identifiers,
        "display_name": profile_overrides.get("display_name", session.display_name or jwt_user_id),
        "email": profile_overrides.get("email"),
        "avatar_url": profile_overrides.get("avatar_url"),
        "is_active": profile_overrides.get("is_active", True),
        "created_at": profile_overrides.get("created_at"),
        "last_login_at": profile_overrides.get("last_login_at"),
        "is_admin": effective_is_admin,
        "permissions": effective_permissions,
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
            post_logout_uri = _build_external_app_url(request, "/api/auth/login")
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

    _delete_cookie(response, request, "atlasclaw_session")
    if auth_config and getattr(auth_config, "jwt", None):
        _delete_cookie(response, request, auth_config.jwt.expanded().cookie_name)
    _delete_cookie(response, request, "AtlasClaw-Authenticate")
    _delete_cookie(response, request, "CloudChef-Authenticate")
    _delete_cookie(response, request, "oidc_id_token")
    _delete_cookie(response, request, "sso_state")
    _delete_cookie(response, request, "pkce_verifier")
    return response
