"""
Auth configuration models loaded from the `auth` section of atlasclaw.json.
Supports ${ENV_VAR} substitution in all string fields.
"""

from __future__ import annotations

import os
import re
from typing import Any
from pydantic import BaseModel

_ENV_RE = re.compile(r'\$\{([^}]+)\}')


def expand_env(value: str) -> str:
    """Replace ${VAR_NAME} with os.environ.get(VAR_NAME, original)."""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


class SmartCMPAuthConfig(BaseModel):
    """SmartCMP provider configuration."""
    validate_url: str = ""
    api_base_url: str = ""

    def expanded(self) -> "SmartCMPAuthConfig":
        return SmartCMPAuthConfig(
            validate_url=expand_env(self.validate_url),
            api_base_url=expand_env(self.api_base_url),
        )


class OIDCAuthConfig(BaseModel):
    """OIDC / OAuth2 provider configuration."""
    # Token validation settings
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    jwks_uri: str = ""
    scopes: list[str] = ["openid", "profile", "email"]
    
    # SSO login flow settings
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    redirect_uri: str = ""
    end_session_endpoint: str = ""  # Keycloak logout URL
    pkce_enabled: bool = True
    pkce_method: str = "S256"

    def expanded(self) -> "OIDCAuthConfig":
        return OIDCAuthConfig(
            issuer=expand_env(self.issuer),
            client_id=expand_env(self.client_id),
            client_secret=expand_env(self.client_secret),
            jwks_uri=expand_env(self.jwks_uri),
            scopes=self.scopes,
            authorization_endpoint=expand_env(self.authorization_endpoint),
            token_endpoint=expand_env(self.token_endpoint),
            userinfo_endpoint=expand_env(self.userinfo_endpoint),
            end_session_endpoint=expand_env(self.end_session_endpoint),
            redirect_uri=expand_env(self.redirect_uri),
            pkce_enabled=self.pkce_enabled,
            pkce_method=self.pkce_method,
        )


class APIKeyAuthConfig(BaseModel):
    """Static API key provider configuration."""
    # Mapping: api_key_value -> {user_id, roles, display_name, ...}
    keys: dict[str, dict[str, Any]] = {}


class NoneAuthConfig(BaseModel):
    """No-auth / development mode provider configuration."""
    default_user_id: str = "default"


class AuthConfig(BaseModel):
    """Top-level auth configuration block in atlasclaw.json."""
    enabled: bool = True          # Set to false to disable auth (anonymous mode)
    provider: str = "none"
    header_name: str = "CloudChef-Authenticate"
    token_prefix: str = ""
    cache_ttl_seconds: int = 300

    smartcmp: SmartCMPAuthConfig = SmartCMPAuthConfig()
    oidc: OIDCAuthConfig = OIDCAuthConfig()
    api_key: APIKeyAuthConfig = APIKeyAuthConfig()
    none: NoneAuthConfig = NoneAuthConfig()

    def validate_provider_config(self) -> None:
        """
        Raise ValueError if the active provider has missing required fields.
        Called at startup time.
        """
        p = self.provider.lower()
        if p == "oidc":
            oidc = self.oidc.expanded()
            if not oidc.issuer:
                raise ValueError(
                    "auth.oidc.issuer is required when auth.provider='oidc'"
                )
            if not oidc.client_id:
                raise ValueError(
                    "auth.oidc.client_id is required when auth.provider='oidc'"
                )
        elif p == "smartcmp":
            smartcmp = self.smartcmp.expanded()
            if not smartcmp.validate_url:
                raise ValueError(
                    "auth.smartcmp.validate_url is required when auth.provider='smartcmp'"
                )
