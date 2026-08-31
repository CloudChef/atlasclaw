# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Persistence and authentication operations for opaque user access tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.models import UserAccessTokenModel, UserModel


ACCESS_TOKEN_PREFIX = "ac_pat_v1_"
_TOKEN_SECRET_BYTES = 32


def is_user_access_token(value: str) -> bool:
    """Return whether a credential uses the supported opaque token version prefix.

    Args:
        value: Raw bearer credential extracted by authentication middleware.

    Returns:
        ``True`` only for the current ``ac_pat_v1_`` format; this check identifies
        the authentication path but does not establish that the secret is valid.
    """
    return str(value or "").startswith(ACCESS_TOKEN_PREFIX)


def _digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return f"{ACCESS_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_SECRET_BYTES)}"


def _token_hint(token: str) -> str:
    return f"{ACCESS_TOKEN_PREFIX}...{token[-4:]}"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _role_identifiers(raw_roles: object) -> list[str]:
    if isinstance(raw_roles, dict):
        return [str(name) for name, enabled in raw_roles.items() if bool(enabled)]
    if isinstance(raw_roles, list):
        return [str(name) for name in raw_roles if str(name).strip()]
    return []


class UserAccessTokenService:
    """Manage opaque credentials whose privileges always follow their owner.

    Methods operate inside caller-owned database transactions. Plaintext exists only
    during creation/authentication, while persistence retains a one-way digest and
    non-secret lifecycle metadata for rotation, revocation, and auditing.
    """

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        name: str,
    ) -> tuple[UserAccessTokenModel, str]:
        """Create and stage an opaque token for one persisted user.

        Args:
            session: Active transaction; the caller owns commit/rollback behavior.
            user_id: Persisted owner whose current roles govern later requests.
            name: Non-empty user-visible integration label.

        Returns:
            The refreshed metadata model and one-time plaintext secret. Only its
            SHA-256 digest and safe hint are stored.

        Raises:
            ValueError: If the normalized name is empty.
        """
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("Token name cannot be empty")

        plaintext = _new_token()
        model = UserAccessTokenModel(
            user_id=user_id,
            name=normalized_name,
            token_digest=_digest_token(plaintext),
            token_hint=_token_hint(plaintext),
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        return model, plaintext

    @staticmethod
    async def list_for_user(
        session: AsyncSession,
        user_id: str,
    ) -> list[UserAccessTokenModel]:
        """Load token metadata for one owner in newest-first order.

        Args:
            session: Active database session.
            user_id: Persisted owner used as the mandatory query boundary.

        Returns:
            ORM models for active and revoked tokens. API response schemas remain
            responsible for excluding the stored digest.
        """
        result = await session.execute(
            select(UserAccessTokenModel)
            .where(UserAccessTokenModel.user_id == user_id)
            .order_by(UserAccessTokenModel.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def revoke(
        session: AsyncSession,
        *,
        token_id: str,
        user_id: str,
    ) -> Optional[UserAccessTokenModel]:
        """Mark an owned token revoked without deleting its audit metadata.

        Args:
            session: Active transaction; the caller owns commit/rollback behavior.
            token_id: Stable token identifier.
            user_id: Owner boundary that prevents cross-user revocation.

        Returns:
            The refreshed model, including an existing or newly assigned revocation
            timestamp, or ``None`` when the owned identifier does not exist.
        """
        result = await session.execute(
            select(UserAccessTokenModel)
            .where(
                UserAccessTokenModel.id == token_id,
                UserAccessTokenModel.user_id == user_id,
            )
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        if model.revoked_at is None:
            model.revoked_at = _utc_now()
            await session.flush()
            await session.refresh(model)
        return model

    @staticmethod
    async def authenticate(
        session: AsyncSession,
        plaintext: str,
    ) -> Optional[UserInfo]:
        """Resolve an opaque secret to its current active owner and authorization.

        Args:
            session: Active database session used for lookup and last-use tracking.
            plaintext: Bearer secret supplied by the caller; it is never persisted.

        Returns:
            A request identity with ``auth_type='api_token'`` and current DB roles,
            or ``None`` for unsupported, unknown, revoked, or inactive-owner tokens.

        Side Effects:
            Updates ``last_used_at`` for successful authentication. The returned
            identity deliberately omits the plaintext from ``raw_token``.
        """
        if not is_user_access_token(plaintext):
            return None

        digest = _digest_token(plaintext)
        result = await session.execute(
            select(UserAccessTokenModel, UserModel)
            .join(UserModel, UserModel.id == UserAccessTokenModel.user_id)
            .where(UserAccessTokenModel.token_digest == digest)
        )
        row = result.one_or_none()
        if row is None:
            return None
        token, user = row
        if token.revoked_at is not None or not user.is_active:
            return None
        if not hmac.compare_digest(token.token_digest, digest):
            return None

        token.last_used_at = _utc_now()
        await session.flush()
        roles = _role_identifiers(user.roles)
        return UserInfo(
            user_id=user.id,
            display_name=user.display_name or user.username,
            tenant_id="default",
            roles=roles,
            raw_token="",
            provider_subject=f"api_token:{user.id}",
            extra={
                "api_token_id": token.id,
                "api_token_name": token.name,
                "is_admin": any(role.lower() == "admin" for role in roles),
            },
            auth_type="api_token",
        )
