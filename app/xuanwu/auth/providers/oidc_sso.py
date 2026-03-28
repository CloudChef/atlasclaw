# -*- coding: utf-8 -*-
"""OIDCSSOProvider — standard OIDC SSO implementation based on BaseSSOProvider."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.xuanwu.auth.models import AuthResult, AuthenticationError
from app.xuanwu.auth.providers.sso_base import BaseSSOProvider

logger = logging.getLogger(__name__)


class OIDCSSOProvider(BaseSSOProvider):
    """
    Standard OIDC SSO Provider based on BaseSSOProvider.

    Implements OAuth2 Authorization Code flow with PKCE support,
    ID token validation via JWKS, and userinfo fetching.

    Flow:
    1. Generate PKCE code_verifier + code_challenge (inherited from base)
    2. Redirect user to IdP authorization endpoint (inherited from base)
    3. Handle callback: exchange code for tokens
    4. Validate ID token, fetch userinfo, extract identity
    """

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        """
        Exchange authorization code for tokens.

        Sends a form-encoded POST request to the token endpoint.
        Uses HTTP Basic Auth if client_secret is configured.
        Supports PKCE code_verifier.

        Args:
            code: Authorization code from IdP callback
            code_verifier: PKCE code_verifier (if PKCE is enabled)

        Returns:
            dict[str, Any]: Token response containing access_token, id_token, etc.

        Raises:
            AuthenticationError: If token exchange fails
        """
        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
        }

        if self._pkce_enabled and code_verifier:
            payload["code_verifier"] = code_verifier

        # Confidential client: use HTTP Basic Auth (preferred by Keycloak)
        # Public client: no secret at all
        auth = None
        if self._client_secret:
            auth = (self._client_id, self._client_secret)

        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=True) as client:
                resp = await client.post(
                    self._token_endpoint,
                    data=payload,
                    auth=auth,
                )
                logger.debug(
                    "[TokenExchange] status=%s body=%s",
                    resp.status_code,
                    resp.text[:500],
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(f"Token exchange failed: {exc.response.text}")
            raise AuthenticationError(f"Token exchange failed: {exc.response.status_code}")
        except Exception as exc:
            logger.error(f"Token exchange error: {exc}")
            raise AuthenticationError(f"Token exchange failed: {exc}")

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """
        Fetch user info from IdP userinfo endpoint.

        Sends a GET request with Bearer token authorization.
        Returns empty dict on failure (does not raise exception).

        Args:
            access_token: OAuth2 access token

        Returns:
            dict[str, Any]: User info claims, or empty dict on failure
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
                resp = await client.get(
                    self._userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch userinfo: {exc}")
            return {}

    async def validate_id_token(self, id_token: str) -> dict[str, Any]:
        """
        Validate ID token and return claims.

        Fetches JWKS from IdP, finds matching public key by kid,
        and validates the token signature and standard claims.

        Args:
            id_token: JWT ID token from token response

        Returns:
            dict[str, Any]: Validated ID token claims

        Raises:
            AuthenticationError: If validation fails (expired, invalid signature, etc.)
        """
        try:
            import jwt as pyjwt
            from jwt.algorithms import RSAAlgorithm
        except ImportError:
            raise AuthenticationError(
                "PyJWT is required for OIDC authentication. "
                "Install it with: pip install PyJWT[crypto]"
            )

        # Fetch JWKS
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
                resp = await client.get(self._jwks_uri)
                resp.raise_for_status()
                jwks = resp.json()
        except Exception as exc:
            raise AuthenticationError(f"Failed to fetch JWKS: {exc}")

        # Get unverified header to find kid
        try:
            unverified_header = pyjwt.get_unverified_header(id_token)
        except Exception as exc:
            raise AuthenticationError(f"Invalid JWT header: {exc}")

        kid = unverified_header.get("kid")

        # Find matching key
        public_key = None
        for jwk in jwks.get("keys", []):
            if kid is None or jwk.get("kid") == kid:
                try:
                    public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
                    break
                except Exception:
                    continue

        if public_key is None:
            raise AuthenticationError(f"No matching public key found for kid={kid!r}")

        # Validate token
        try:
            payload = pyjwt.decode(
                id_token,
                public_key,
                algorithms=["RS256", "RS384", "RS512"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"verify_exp": True},
            )
            return payload
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("ID token has expired")
        except pyjwt.InvalidAudienceError:
            raise AuthenticationError("ID token audience mismatch")
        except pyjwt.InvalidIssuerError:
            raise AuthenticationError("ID token issuer mismatch")
        except pyjwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid ID token: {exc}")

    def extract_identity(self, tokens: dict[str, Any], userinfo: dict[str, Any]) -> AuthResult:
        """
        Extract identity from tokens and userinfo.

        Note: This method is synchronous but id_token validation is async.
        The actual validation is done in complete_login before calling this method,
        passing validated claims via tokens["_id_claims"].

        Args:
            tokens: Token response (must include "_id_claims" with validated claims)
            userinfo: User info from userinfo endpoint

        Returns:
            AuthResult: Authentication result with extracted identity

        Raises:
            AuthenticationError: If required claims are missing
        """
        # Get pre-validated id_token claims (set by complete_login)
        id_claims = tokens.get("_id_claims", {})

        # Merge claims (ID token takes precedence over userinfo)
        claims = {**userinfo, **id_claims}

        subject = claims.get("sub", "")
        if not subject:
            raise AuthenticationError("Missing 'sub' claim in ID token")

        access_token = tokens.get("access_token", "")
        id_token = tokens.get("id_token", "")

        return AuthResult(
            subject=subject,
            display_name=claims.get("name", claims.get("preferred_username", "")),
            email=claims.get("email", ""),
            roles=claims.get("roles", []) or claims.get("groups", []) or [],
            tenant_id=claims.get("tenant_id", claims.get("org_id", "default")),
            raw_token=access_token or id_token,
            id_token=id_token,
            extra=dict(claims),
        )

    async def complete_login(self, code: str, code_verifier: str = "") -> AuthResult:
        """
        Complete SSO login flow and return AuthResult.

        Orchestrates the full OIDC login flow:
        1. Exchange authorization code for tokens
        2. Validate ID token (OIDC-specific step)
        3. Fetch userinfo for additional claims
        4. Extract and return identity

        Args:
            code: Authorization code from IdP callback
            code_verifier: PKCE code_verifier (if PKCE is enabled)

        Returns:
            AuthResult: Authentication result

        Raises:
            AuthenticationError: If any step fails
        """
        # Step 1: Exchange code for tokens
        tokens = await self.exchange_code(code, code_verifier)

        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token", "")

        if not id_token:
            raise AuthenticationError("No id_token in token response")

        # Step 2: Validate ID token (OIDC-specific)
        id_claims = await self.validate_id_token(id_token)

        # Store validated claims in tokens for extract_identity
        tokens["_id_claims"] = id_claims

        # Step 3: Fetch userinfo for additional claims
        userinfo = {}
        if access_token:
            userinfo = await self.fetch_userinfo(access_token)

        # Step 4: Extract identity and return AuthResult
        return self.extract_identity(tokens, userinfo)
