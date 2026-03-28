# -*- coding: utf-8 -*-
"""DingTalkSSOProvider — DingTalk SSO implementation with proprietary protocol."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.xuanwu.auth.models import AuthResult, AuthenticationError
from app.xuanwu.auth.providers.sso_base import BaseSSOProvider

logger = logging.getLogger(__name__)


class DingTalkSSOProvider(BaseSSOProvider):
    """
    DingTalk SSO Provider implementing DingTalk's proprietary OIDC protocol.

    Key differences from standard OIDC:
    1. Authorization URL requires prompt=consent parameter
    2. Token exchange uses JSON body with camelCase field names
    3. UserInfo uses proprietary header x-acs-dingtalk-access-token
    4. Subject is extracted from userinfo unionId/openId, not id_token.sub
    """

    def __init__(
        self,
        issuer: str = "https://login.dingtalk.com",
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        authorization_endpoint: str = "",
        token_endpoint: str = "",
        userinfo_endpoint: str = "",
        jwks_uri: str = "",
        scopes: list[str] | None = None,
        pkce_enabled: bool = True,
        pkce_method: str = "S256",
        corp_id: str = "",
        subject_field: str = "unionId",
    ) -> None:
        """
        Initialize DingTalk SSO Provider.

        Args:
            issuer: IdP issuer URL, defaults to DingTalk login domain
            client_id: OAuth2 client ID (DingTalk AppKey)
            client_secret: OAuth2 client secret (DingTalk AppSecret)
            redirect_uri: Authorization callback URI
            authorization_endpoint: Auth endpoint URL
            token_endpoint: Token endpoint URL
            userinfo_endpoint: UserInfo endpoint URL
            jwks_uri: JWKS endpoint URL (unused by DingTalk, kept for compatibility)
            scopes: Requested scopes, defaults to ["openid", "corpid"]
            pkce_enabled: Whether to enable PKCE
            pkce_method: PKCE method
            corp_id: Enterprise corpId for tenant_id mapping
            subject_field: Subject field name, "unionId" or "openId"
        """
        dingtalk_auth_endpoint = authorization_endpoint or "https://login.dingtalk.com/oauth2/auth"
        dingtalk_token_endpoint = token_endpoint or "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
        dingtalk_userinfo_endpoint = userinfo_endpoint or "https://api.dingtalk.com/v1.0/contact/users/me"

        default_scopes = scopes or ["openid", "corpid"]

        super().__init__(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            authorization_endpoint=dingtalk_auth_endpoint,
            token_endpoint=dingtalk_token_endpoint,
            userinfo_endpoint=dingtalk_userinfo_endpoint,
            jwks_uri=jwks_uri,
            scopes=default_scopes,
            pkce_enabled=pkce_enabled,
            pkce_method=pkce_method,
        )

        self._corp_id = corp_id
        self._subject_field = subject_field

    def _extra_auth_params(self) -> dict[str, str]:
        """
        Return extra DingTalk authorization URL parameters.

        DingTalk requires prompt=consent for proper access_token delegation.

        Returns:
            dict[str, str]: Contains prompt=consent
        """
        return {"prompt": "consent"}

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        """
        Exchange authorization code for tokens.

        DingTalk-specific handling:
        1. Uses JSON body instead of form-encoded
        2. Field names use camelCase
        3. No HTTP Basic Auth

        Args:
            code: Authorization code
            code_verifier: PKCE code_verifier (if PKCE enabled)

        Returns:
            dict[str, Any]: Token response with accessToken, refreshToken, etc.

        Raises:
            AuthenticationError: If token exchange fails
        """
        payload: dict[str, str] = {
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "code": code,
            "grantType": "authorization_code",
        }

        if self._pkce_enabled and code_verifier:
            payload["codeVerifier"] = code_verifier

        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=True) as client:
                resp = await client.post(
                    self._token_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                logger.debug(
                    "[DingTalk TokenExchange] status=%s body=%s",
                    resp.status_code,
                    resp.text[:500] if resp.text else "",
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response else str(exc)
            logger.error(f"DingTalk token exchange failed: {error_text}")
            raise AuthenticationError(f"Token exchange failed: {exc.response.status_code}")
        except Exception as exc:
            logger.error(f"DingTalk token exchange error: {exc}")
            raise AuthenticationError(f"Token exchange failed: {exc}")

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """
        Fetch DingTalk user info using access_token.

        DingTalk uses proprietary header x-acs-dingtalk-access-token
        instead of standard Authorization: Bearer.

        Args:
            access_token: Access token

        Returns:
            dict[str, Any]: User info dictionary, empty dict on failure
        """
        if not access_token:
            logger.warning("DingTalk fetch_userinfo: empty access_token")
            return {}

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
                resp = await client.get(
                    self._userinfo_endpoint,
                    headers={"x-acs-dingtalk-access-token": access_token},
                )
                logger.debug(
                    "[DingTalk UserInfo] status=%s body=%s",
                    resp.status_code,
                    resp.text[:500] if resp.text else "",
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning(f"DingTalk failed to fetch userinfo: {exc}")
            return {}

    def extract_identity(self, tokens: dict[str, Any], userinfo: dict[str, Any]) -> AuthResult:
        """
        Extract DingTalk identity from tokens and userinfo.

        DingTalk identity extraction rules:
        - subject: Use configured subject_field (default unionId), fallback to openId
        - display_name: From nick field
        - email: From email field (if available)
        - tenant_id: Use configured corp_id, fallback to userinfo.corpId
        - raw_token: Use DingTalk's accessToken (camelCase)

        Args:
            tokens: Token response
            userinfo: User info dictionary

        Returns:
            AuthResult: Authentication result
        """
        subject = userinfo.get(self._subject_field, "") or userinfo.get("openId", "")
        display_name = userinfo.get("nick", "")
        email = userinfo.get("email", "")
        tenant_id = self._corp_id if self._corp_id else userinfo.get("corpId", "default")
        raw_token = tokens.get("accessToken", "")

        extra: dict[str, Any] = {
            "auth_type": "oidc:dingtalk",
            "provider_id": "dingtalk",
            **userinfo,
        }

        return AuthResult(
            subject=subject,
            display_name=display_name,
            email=email,
            tenant_id=tenant_id,
            raw_token=raw_token,
            extra=extra,
        )

    async def complete_login(self, code: str, code_verifier: str = "") -> AuthResult:
        """
        Complete DingTalk SSO login flow.

        Overrides base method to handle DingTalk's camelCase token response.

        Args:
            code: Authorization code
            code_verifier: PKCE code_verifier (if PKCE enabled)

        Returns:
            AuthResult: Authentication result
        """
        tokens = await self.exchange_code(code, code_verifier)

        access_token = tokens.get("accessToken", "")
        userinfo = await self.fetch_userinfo(access_token)

        return self.extract_identity(tokens, userinfo)
