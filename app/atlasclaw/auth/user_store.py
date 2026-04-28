# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Auth user-store helpers for external authentication providers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.models import AuthResult, AuthenticationError, UserInfo
from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.models import UserModel
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate

logger = logging.getLogger(__name__)

DEFAULT_EXTERNAL_USER_ROLES: dict[str, bool] = {"user": True}


def extract_role_identifiers(raw_roles: Any) -> list[str]:
    """Normalize role identifiers from DB JSON storage."""
    if isinstance(raw_roles, dict):
        return [
            str(identifier)
            for identifier, enabled in raw_roles.items()
            if bool(enabled) and str(identifier).strip()
        ]
    if isinstance(raw_roles, list):
        return [str(identifier) for identifier in raw_roles if str(identifier).strip()]
    return []


def _auth_type_for_result(provider: str, result: AuthResult) -> str:
    auth_type = str((result.extra or {}).get("auth_type", "") or "").strip()
    if auth_type:
        return auth_type
    return str(provider or "external").strip() or "external"


def _normalize_subject(result: AuthResult) -> str:
    subject = str(result.subject or "").strip()
    if not subject:
        raise ValueError("Authenticated external subject is empty")
    return subject


async def ensure_db_user_for_auth_result(
    session: AsyncSession,
    *,
    provider: str,
    result: AuthResult,
    default_roles: Optional[Mapping[str, bool]] = None,
) -> UserModel:
    """Return the DB user for an external identity, creating it when missing.

    External users are keyed by ``AuthResult.subject`` in ``users.username``.
    New users receive the Standard User role by default. Existing active users
    keep their assigned roles; inactive users are rejected without login updates.
    """
    subject = _normalize_subject(result)
    user = await UserService.get_by_username(session, subject)
    if user is not None:
        if not user.is_active:
            raise AuthenticationError("User account is inactive")

        changed = False
        display_name = str(result.display_name or "").strip()
        if display_name and not user.display_name:
            user.display_name = display_name
            changed = True

        email = str(result.email or "").strip() or None
        if email and not user.email:
            existing_email_user = await UserService.get_by_email(session, email)
            if existing_email_user is None:
                user.email = email
                changed = True

        user.last_login_at = datetime.utcnow()
        changed = True
        if changed:
            await session.flush()
            await session.refresh(user)
        return user

    roles = dict(default_roles or DEFAULT_EXTERNAL_USER_ROLES)
    email = str(result.email or "").strip() or None
    if email and await UserService.get_by_email(session, email):
        email = None

    try:
        user = await UserService.create(
            session,
            UserCreate(
                username=subject,
                email=email,
                password=None,
                display_name=str(result.display_name or "").strip() or subject,
                roles=roles,
                auth_type=_auth_type_for_result(provider, result),
                is_active=True,
            ),
        )
        user.last_login_at = datetime.utcnow()
        await session.flush()
        await session.refresh(user)
        logger.info(
            "Created DB user for external auth: provider=%s subject=%s id=%s",
            provider,
            subject,
            user.id,
        )
        return user
    except IntegrityError:
        await session.rollback()
        user = await UserService.get_by_username(session, subject)
        if user is None:
            raise
        if not user.is_active:
            raise AuthenticationError("User account is inactive")
        return user


def build_user_info_from_db_user(
    user: UserModel,
    *,
    provider: str,
    result: AuthResult,
    raw_token: str = "",
    extra: Optional[dict[str, Any]] = None,
    auth_type: str = "",
) -> UserInfo:
    """Build request ``UserInfo`` from a DB user and external auth result."""
    subject = str(result.subject or user.username or "").strip()
    info_extra = dict(extra or {})
    if subject:
        info_extra.setdefault("external_subject", subject)
    if user.id:
        info_extra.setdefault("db_user_id", user.id)

    resolved_auth_type = (
        str(auth_type or "").strip()
        or _auth_type_for_result(provider, result)
        or str(user.auth_type or "").strip()
    )

    return UserInfo(
        user_id=str(user.id or "").strip() or str(user.username or "").strip(),
        display_name=user.display_name or result.display_name or user.username,
        tenant_id=result.tenant_id or "default",
        roles=extract_role_identifiers(user.roles),
        raw_token=raw_token or result.raw_token,
        provider_subject=f"{provider}:{subject}" if subject else provider,
        extra=info_extra,
        auth_type=resolved_auth_type,
    )


async def resolve_user_info_for_auth_result(
    *,
    provider: str,
    result: AuthResult,
    raw_token: str = "",
    extra: Optional[dict[str, Any]] = None,
    auth_type: str = "",
    default_roles: Optional[Mapping[str, bool]] = None,
) -> UserInfo:
    """Resolve an external auth result to DB-backed request identity.

    This high-level helper owns DB session lifecycle so callers in the auth
    pipeline do not need to know how DB users are materialized.
    """
    db_manager = get_db_manager()
    if not db_manager.is_initialized:
        raise AuthenticationError("Database is not initialized")

    try:
        async with db_manager.get_session() as session:
            db_user = await ensure_db_user_for_auth_result(
                session,
                provider=provider,
                result=result,
                default_roles=default_roles,
            )
    except RuntimeError as exc:
        raise AuthenticationError("Database is not initialized") from exc
    except ValueError as exc:
        raise AuthenticationError(str(exc)) from exc

    return build_user_info_from_db_user(
        db_user,
        provider=provider,
        result=result,
        raw_token=raw_token,
        extra=extra,
        auth_type=auth_type,
    )
