# -*- coding: utf-8 -*-
"""BaseSSOProvider — Abstract base class for SSO/OIDC with reusable PKCE/state/callback flow."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from abc import ABC, abstractmethod
from typing import Any, Optional
from urllib.parse import urlencode

from app.xuanwu.auth.models import AuthResult

logger = logging.getLogger(__name__)


class BaseSSOProvider(ABC):
    """
    Abstract base class for SSO providers with PKCE and authorization URL building.

    Subclasses must implement protocol-specific methods:
    - exchange_code: Token exchange with IdP
    - fetch_userinfo: Fetch user info from IdP
    - extract_identity: Extract identity from tokens/userinfo

    Flow:
    1. Generate PKCE code_verifier + code_challenge
    2. Redirect user to IdP authorization endpoint
    3. Handle callback: exchange code for tokens
    4. Fetch userinfo and extract identity
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str = "",
        redirect_uri: str = "",
        authorization_endpoint: str = "",
        token_endpoint: str = "",
        userinfo_endpoint: str = "",
        jwks_uri: str = "",
        scopes: Optional[list[str]] = None,
        pkce_enabled: bool = True,
        pkce_method: str = "S256",
    ) -> None:
        """
        Initialize SSO Provider.

        Args:
            issuer: IdP issuer URL (trailing slash auto-stripped)
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret (empty for public clients)
            redirect_uri: Authorization callback URI
            authorization_endpoint: Auth endpoint URL (auto-derived if empty)
            token_endpoint: Token endpoint URL (auto-derived if empty)
            userinfo_endpoint: UserInfo endpoint URL (auto-derived if empty)
            jwks_uri: JWKS endpoint URL (auto-derived if empty)
            scopes: Requested scopes, defaults to ["openid", "profile", "email"]
            pkce_enabled: Whether to enable PKCE
            pkce_method: PKCE method, "S256" or "plain"
        """
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes or ["openid", "profile", "email"]
        self._pkce_enabled = pkce_enabled
        self._pkce_method = pkce_method

        self._authorization_endpoint = authorization_endpoint or f"{self._issuer}/oauth/authorize"
        self._token_endpoint = token_endpoint or f"{self._issuer}/oauth/token"
        self._userinfo_endpoint = userinfo_endpoint or f"{self._issuer}/oauth/userinfo"
        self._jwks_uri = jwks_uri or f"{self._issuer}/.well-known/jwks.json"

    # -------------------------------------------------------------------------
    # Public methods (no override needed)
    # -------------------------------------------------------------------------

    def generate_pkce(self) -> tuple[str, str]:
        """
        Generate PKCE (code_verifier, code_challenge) pair.

        Returns:
            tuple[str, str]: (code_verifier, code_challenge), or ("", "") if PKCE disabled
        """
        if not self._pkce_enabled:
            return "", ""

        verifier = base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode().rstrip("=")

        if self._pkce_method == "S256":
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).decode().rstrip("=")
        else:
            challenge = verifier

        return verifier, challenge

    def build_authorization_url(self, state: str, code_challenge: str = "") -> str:
        """
        Build IdP authorization URL.

        Args:
            state: CSRF protection state parameter
            code_challenge: PKCE code_challenge (if PKCE enabled)

        Returns:
            str: Complete authorization URL
        """
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
        }

        if self._pkce_enabled and code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = self._pkce_method

        extra_params = self._extra_auth_params()
        if extra_params:
            params.update(extra_params)

        return f"{self._authorization_endpoint}?{urlencode(params)}"

    # -------------------------------------------------------------------------
    # Template method
    # -------------------------------------------------------------------------

    async def complete_login(self, code: str, code_verifier: str = "") -> AuthResult:
        """
        Complete SSO login flow and return AuthResult (template method).

        Orchestrates the flow:
        1. Call exchange_code to get tokens
        2. Call fetch_userinfo to get user info
        3. Call extract_identity to build AuthResult

        Args:
            code: Authorization code
            code_verifier: PKCE code_verifier (if PKCE enabled)

        Returns:
            AuthResult: Authentication result
        """
        tokens = await self.exchange_code(code, code_verifier)

        access_token = tokens.get("access_token", "")
        userinfo = await self.fetch_userinfo(access_token)

        return self.extract_identity(tokens, userinfo)

    # -------------------------------------------------------------------------
    # Abstract methods (subclass must implement)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        """
        Exchange authorization code for tokens.

        Args:
            code: Authorization code
            code_verifier: PKCE code_verifier (if PKCE enabled)

        Returns:
            dict[str, Any]: Token response with access_token, id_token, refresh_token, etc.
        """
        ...

    @abstractmethod
    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """
        Fetch user info using access_token.

        Args:
            access_token: Access token

        Returns:
            dict[str, Any]: User info dictionary
        """
        ...

    @abstractmethod
    def extract_identity(self, tokens: dict[str, Any], userinfo: dict[str, Any]) -> AuthResult:
        """
        Extract identity from tokens and userinfo.

        Args:
            tokens: Token response
            userinfo: User info dictionary

        Returns:
            AuthResult: Authentication result
        """
        ...

    # -------------------------------------------------------------------------
    # Optional override (has default implementation)
    # -------------------------------------------------------------------------

    def _extra_auth_params(self) -> dict[str, str]:
        """
        Return extra authorization URL parameters.

        Subclasses can override to add provider-specific params,
        e.g., DingTalk requires prompt=consent.

        Returns:
            dict[str, str]: Extra parameters, default empty
        """
        return {}
