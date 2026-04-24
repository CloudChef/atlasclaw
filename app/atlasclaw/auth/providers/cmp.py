# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""CMPAuthProvider — authenticate users via CMP cookies.

When AtlasClaw is deployed behind the same Nginx as CMP, the browser
automatically sends CMP cookies including ``userLoginId``, ``username``,
``userId``, ``tenant_id``, and ``CloudChef-Authenticate``.

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
      - ``CloudChef-Authenticate``: CMP auth token (proves user is logged in)
      - ``userLoginId``: login ID used as subject for session isolation

    Optional cookies:
      - ``username``: display name (URL-encoded)
      - ``userId``: CMP internal UUID
      - ``useremail``: email (may be AES-encrypted)
      - ``tenant_id``: tenant identifier
    """

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
        # CloudChef-Authenticate must be present (proves login)
        cmp_token = cookies.get("CloudChef-Authenticate", "").strip()
        if not cmp_token:
            raise AuthenticationError("Missing CloudChef-Authenticate cookie")

        # userLoginId is the primary identity key
        login_id = cookies.get("userLoginId", "").strip()
        if not login_id:
            raise AuthenticationError("Missing userLoginId cookie")

        # Optional fields
        raw_username = cookies.get("username", "").strip()
        display_name = unquote(raw_username) if raw_username else login_id

        user_id = cookies.get("userId", "").strip()
        tenant_id = cookies.get("tenant_id", "default").strip() or "default"

        logger.info(
            "CMP cookie auth: loginId=%s, name=%s, tenant=%s",
            login_id, display_name, tenant_id,
        )

        return AuthResult(
            subject=login_id,
            display_name=display_name,
            tenant_id=tenant_id,
            raw_token=cmp_token,
            extra={
                "auth_type": "cookie",
                "user_id": user_id,
            },
        )
