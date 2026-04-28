# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""LocalAuthProvider — username/password authentication backed by UserModel."""

from __future__ import annotations

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.base import AuthProvider
from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.orm.user import UserService


class LocalAuthProvider(AuthProvider):
    """Authenticate local users with `username:password` credential format."""

    def provider_name(self) -> str:
        return "local"

    async def authenticate(self, credential: str) -> AuthResult:
        if not credential:
            raise AuthenticationError("Local credential is empty")

        parts = credential.split(":", 1)
        if len(parts) != 2:
            raise AuthenticationError("Local credential format must be 'username:password'")

        username = parts[0].strip()
        password = parts[1]

        if not username or not password:
            raise AuthenticationError("Username or password is empty")

        try:
            async with get_db_manager().get_session() as session:
                user = await UserService.authenticate(session, username, password)
        except RuntimeError as exc:
            raise AuthenticationError("Database is not initialized") from exc

        if user is None:
            raise AuthenticationError("Invalid username or password")

        roles: list[str] = []
        if isinstance(user.roles, dict):
            roles = [k for k, v in user.roles.items() if bool(v)]
        elif isinstance(user.roles, list):
            roles = [str(x) for x in user.roles]

        if user.is_admin and "admin" not in roles:
            roles.append("admin")

        auth_type = user.auth_type or "local"

        return AuthResult(
            subject=user.username,
            display_name=user.display_name or user.username,
            email=user.email or "",
            roles=roles,
            tenant_id="default",
            raw_token=credential,
            extra={
                "auth_type": auth_type,
                "db_user_id": user.id,
            },
        )
