# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""CMPAuthProvider — authenticate users via CMP cookies.

When AtlasClaw is deployed behind the same Nginx as CMP, the browser
automatically sends CMP cookies including identity fields and a host
authentication token cookie configured by the embedding environment.

This provider extracts user identity directly from these cookies —
no CMP API call needed.
"""

from __future__ import annotations

import logging
from typing import Dict
from urllib.parse import unquote

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.base import AuthProvider

logger = logging.getLogger(__name__)


class CMPAuthProvider(AuthProvider):
    """Extract user identity from CMP cookies (zero network calls).

    Required cookies:
      - host auth token cookie: proves the user is logged in to the host
      - login ID cookie: subject for session isolation

    Optional cookies:
      - ``username``: display name (URL-encoded)
      - ``userId``: CMP internal UUID
      - ``useremail``: email (may be AES-encrypted)
      - ``tenant_id``: tenant identifier
    """

    def __init__(
        self,
        *,
        token_cookie_name: str,
        login_id_cookie_name: str = "userLoginId",
        username_cookie_name: str = "username",
        user_id_cookie_name: str = "userId",
        tenant_id_cookie_name: str = "tenant_id",
    ) -> None:
        self._token_cookie_name = str(token_cookie_name or "").strip()
        self._login_id_cookie_name = str(login_id_cookie_name or "").strip() or "userLoginId"
        self._username_cookie_name = str(username_cookie_name or "").strip() or "username"
        self._user_id_cookie_name = str(user_id_cookie_name or "").strip() or "userId"
        self._tenant_id_cookie_name = str(tenant_id_cookie_name or "").strip() or "tenant_id"

    def provider_name(self) -> str:
        return "cmp"

    async def authenticate(self, credential: str) -> AuthResult:
        """Not used for CMP — use authenticate_from_cookies() instead."""
        raise AuthenticationError(
            "CMP provider requires cookies, not a single credential. "
            "Use authenticate_from_cookies() instead."
        )

    async def authenticate_from_cookies(self, cookies: Dict[str, str]) -> AuthResult:
        """Extract user identity from CMP cookies.

        Args:
            cookies: All cookies from the HTTP request.

        Returns:
            AuthResult with user identity.

        Raises:
            AuthenticationError: If required cookies are missing.
        """
        host_token = cookies.get(self._token_cookie_name, "").strip()
        if not host_token:
            raise AuthenticationError("Missing host authentication cookie")

        # userLoginId is the primary identity key
        login_id = cookies.get(self._login_id_cookie_name, "").strip()
        if not login_id:
            raise AuthenticationError("Missing login ID cookie")

        # Optional fields
        raw_username = cookies.get(self._username_cookie_name, "").strip()
        display_name = unquote(raw_username) if raw_username else login_id

        user_id = cookies.get(self._user_id_cookie_name, "").strip()
        tenant_id = cookies.get(self._tenant_id_cookie_name, "default").strip() or "default"

        logger.info(
            "CMP cookie auth: loginId=%s, name=%s, tenant=%s",
            login_id, display_name, tenant_id,
        )

        return AuthResult(
            subject=login_id,
            display_name=display_name,
            tenant_id=tenant_id,
            raw_token=host_token,
            extra={
                "auth_type": "cookie",
                "user_id": user_id,
            },
        )
