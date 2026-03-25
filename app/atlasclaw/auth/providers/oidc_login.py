"""OIDCLoginProvider — implements OAuth2 Authorization Code flow with PKCE."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.atlasclaw.auth.models import AuthResult, AuthenticationError

logger = logging.getLogger(__name__)


class OIDCLoginProvider:
    """
    SSO Login flow with PKCE (Proof Key for Code Exchange).
    
    Flow:
    1. Generate PKCE code_verifier + code_challenge
    2. Redirect user to IdP authorization endpoint
    3. Handle callback: exchange code for tokens
    4. Validate ID token, optionally fetch userinfo
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
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes or ["openid", "profile", "email"]
        self._pkce_enabled = pkce_enabled
        self._pkce_method = pkce_method

        # Auto-discover endpoints if not provided
        self._authorization_endpoint = authorization_endpoint or f"{self._issuer}/oauth/authorize"
        self._token_endpoint = token_endpoint or f"{self._issuer}/oauth/token"
        self._userinfo_endpoint = userinfo_endpoint or f"{self._issuer}/oauth/userinfo"
        self._jwks_uri = jwks_uri or f"{self._issuer}/.well-known/jwks.json"

    def generate_pkce(self) -> tuple[str, str]:
        """Generate (code_verifier, code_challenge) pair."""
        if not self._pkce_enabled:
            return "", ""
        
        # code_verifier: 43-128 chars, URL-safe base64
        verifier = base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode().rstrip("=")
        
        if self._pkce_method == "S256":
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).decode().rstrip("=")
        else:  # plain
            challenge = verifier
            
        return verifier, challenge

    def build_authorization_url(self, state: str, code_challenge: str = "") -> str:
        """Build IdP authorization URL with PKCE."""
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
            
        return f"{self._authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        """Exchange authorization code for tokens."""
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
                logger.error(
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
        """Fetch user info from IdP."""
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
                resp = await client.get(
                    self._userinfo_endpoint,

                    headers={"Authorization": f"Bearer {access_token}"}
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch userinfo: {exc}")
            return {}

    async def validate_id_token(self, id_token: str) -> dict[str, Any]:
        """Validate ID token and return claims."""
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

    async def complete_login(self, code: str, code_verifier: str = "") -> AuthResult:
        """Complete SSO login flow and return AuthResult."""
        # Exchange code for tokens
        tokens = await self.exchange_code(code, code_verifier)
        
        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")
        
        if not id_token:
            raise AuthenticationError("No id_token in token response")
            
        # Validate ID token
        id_claims = await self.validate_id_token(id_token)
        
        # Optionally fetch userinfo for additional claims
        userinfo = {}
        if access_token:
            userinfo = await self.fetch_userinfo(access_token)
            
        # Merge claims (ID token takes precedence)
        claims = {**userinfo, **id_claims}
        
        subject = claims.get("sub", "")
        if not subject:
            raise AuthenticationError("Missing 'sub' claim in ID token")
            
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
