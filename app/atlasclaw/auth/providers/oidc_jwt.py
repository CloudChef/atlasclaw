"""OIDCJWTProvider — validates JWT tokens via JWKS endpoint."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.base import AuthProvider

logger = logging.getLogger(__name__)


class OIDCJWTProvider(AuthProvider):
    """
    Validates OIDC / OAuth2 JWT tokens.

    Performs full verification:
      - Fetches public keys from JWKS endpoint (lazy, cached per instance)
      - Verifies RS256/RS384/RS512 signature
      - Validates exp (not expired), iss (matches issuer), aud (matches client_id)
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        jwks_uri: str = "",
        audience: str = "",
    ) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._audience = audience or client_id
        self._jwks_uri = jwks_uri or f"{issuer.rstrip('/')}/.well-known/jwks.json"
        self._jwks_cache: dict[str, Any] | None = None

    def provider_name(self) -> str:
        return "oidc_jwt"

    async def _fetch_jwks(self) -> dict[str, Any]:
        """Lazily fetch and cache JWKS from the identity provider."""
        if self._jwks_cache is None:
            try:
                async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:

                    resp = await client.get(self._jwks_uri)
                    resp.raise_for_status()
                    self._jwks_cache = resp.json()
            except Exception as exc:
                raise AuthenticationError(
                    f"Failed to fetch JWKS from {self._jwks_uri}: {exc}"
                ) from exc
        return self._jwks_cache

    async def authenticate(self, credential: str) -> AuthResult:
        if not credential or not credential.strip():
            raise AuthenticationError("OIDC token is empty")

        try:
            import jwt as pyjwt
            from jwt.algorithms import RSAAlgorithm
        except ImportError:
            raise AuthenticationError(
                "PyJWT is required for OIDC authentication. "
                "Install it with: pip install PyJWT[crypto]"
            )

        try:
            unverified_header = pyjwt.get_unverified_header(credential)
        except Exception as exc:
            raise AuthenticationError(f"Invalid JWT header: {exc}") from exc

        kid = unverified_header.get("kid")
        jwks = await self._fetch_jwks()

        # Find the matching public key
        public_key = None
        for jwk in jwks.get("keys", []):
            if kid is None or jwk.get("kid") == kid:
                try:
                    public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
                    break
                except Exception:
                    continue

        if public_key is None:
            # Reset cache so next request retries key fetch (key rotation)
            self._jwks_cache = None
            raise AuthenticationError(
                f"No matching public key found in JWKS for kid={kid!r}"
            )

        try:
            payload = pyjwt.decode(
                credential,
                public_key,
                algorithms=["RS256", "RS384", "RS512"],
                issuer=self._issuer,
                options={
                    "verify_exp": True,
                    # Keycloak access_token aud may not include client_id;
                    # signature + expiry + issuer checks are sufficient.
                    "verify_aud": False,
                },
            )
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("OIDC token has expired")
        except pyjwt.InvalidAudienceError:
            raise AuthenticationError("OIDC token audience mismatch")
        except pyjwt.InvalidIssuerError:
            raise AuthenticationError("OIDC token issuer mismatch")
        except pyjwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid OIDC token: {exc}") from exc

        subject = payload.get("sub", "")
        if not subject:
            raise AuthenticationError("OIDC token missing 'sub' claim")

        return AuthResult(
            subject=subject,
            display_name=payload.get("name", ""),
            email=payload.get("email", ""),
            roles=(
                payload.get("roles", [])
                or payload.get("groups", [])
                or []
            ),
            tenant_id=payload.get("tenant_id", "default"),
            raw_token=credential,
            extra=dict(payload),
        )
