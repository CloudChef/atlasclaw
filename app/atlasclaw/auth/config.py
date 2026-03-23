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
    ocbc_enabled: bool = True

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
            ocbc_enabled=self.ocbc_enabled,
            authorization_endpoint=expand_env(self.authorization_endpoint),
            token_endpoint=expand_env(self.token_endpoint),
            userinfo_endpoint=expand_env(self.userinfo_endpoint),
            end_session_endpoint=expand_env(self.end_session_endpoint),
            redirect_uri=expand_env(self.redirect_uri),
            pkce_enabled=self.pkce_enabled,
            pkce_method=self.pkce_method,
        )


class DingTalkOIDCAuthConfig(BaseModel):
    """DingTalk Enterprise OIDC configuration via IDaaS."""
    # Token validation settings
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    discovery_url: str = ""       # defaults to {issuer}/.well-known/openid-configuration
    jwks_uri: str = ""

    # SSO login flow settings (DingTalk-specific endpoints)
    authorization_endpoint: str = "https://login.dingtalk.com/oauth2/auth"
    token_endpoint: str = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
    userinfo_endpoint: str = "https://api.dingtalk.com/v1.0/contact/users/me"
    end_session_endpoint: str = ""
    redirect_uri: str = ""
    scopes: list[str] = ["openid"]  # DingTalk only supports openid scope
    pkce_enabled: bool = False       # DingTalk does not support PKCE
    pkce_method: str = "S256"

    # DingTalk-specific settings
    corp_id: str = ""             # DingTalk corpId for tenant isolation
    sub_mapping: str = "userid"   # sub claim mapping hint (e.g. "userid", "unionid")

    def expanded(self) -> "DingTalkOIDCAuthConfig":
        return DingTalkOIDCAuthConfig(
            issuer=expand_env(self.issuer),
            client_id=expand_env(self.client_id),
            client_secret=expand_env(self.client_secret),
            discovery_url=expand_env(self.discovery_url),
            jwks_uri=expand_env(self.jwks_uri),
            authorization_endpoint=expand_env(self.authorization_endpoint),
            token_endpoint=expand_env(self.token_endpoint),
            userinfo_endpoint=expand_env(self.userinfo_endpoint),
            end_session_endpoint=expand_env(self.end_session_endpoint),
            redirect_uri=expand_env(self.redirect_uri),
            scopes=self.scopes,
            pkce_enabled=self.pkce_enabled,
            pkce_method=self.pkce_method,
            corp_id=expand_env(self.corp_id),
            sub_mapping=self.sub_mapping,
        )


class APIKeyAuthConfig(BaseModel):
    """Static API key provider configuration."""
    # Mapping: api_key_value -> {user_id, roles, display_name, ...}
    keys: dict[str, dict[str, Any]] = {}


class NoneAuthConfig(BaseModel):
    """No-auth / development mode provider configuration."""
    default_user_id: str = "default"


class LocalAuthConfig(BaseModel):
    """Local username/password auth provider configuration."""

    enabled: bool = True
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"


class JWTAuthConfig(BaseModel):
    """AtlasClaw local JWT configuration."""

    header_name: str = "AtlasClaw-Authenticate"
    cookie_name: str = "AtlasClaw-Authenticate"
    issuer: str = "atlasclaw"
    secret_key: str = "atlasclaw-dev-secret"
    expires_minutes: int = 480

    def expanded(self) -> "JWTAuthConfig":
        return JWTAuthConfig(
            header_name=expand_env(self.header_name),
            cookie_name=expand_env(self.cookie_name),
            issuer=expand_env(self.issuer),
            secret_key=expand_env(self.secret_key),
            expires_minutes=self.expires_minutes,
        )



class AuthConfig(BaseModel):

    """Top-level auth configuration block in atlasclaw.json."""
    enabled: bool = True          # Set to false to disable auth (anonymous mode)
    provider: str = "none"
    header_name: str = "AtlasClaw-Authenticate"
    token_prefix: str = ""
    cache_ttl_seconds: int = 300


    smartcmp: SmartCMPAuthConfig = SmartCMPAuthConfig()
    oidc: OIDCAuthConfig = OIDCAuthConfig()
    dingtalk_oidc: DingTalkOIDCAuthConfig = DingTalkOIDCAuthConfig()
    api_key: APIKeyAuthConfig = APIKeyAuthConfig()
    none: NoneAuthConfig = NoneAuthConfig()
    local: LocalAuthConfig = LocalAuthConfig()
    jwt: JWTAuthConfig = JWTAuthConfig()



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
        elif p == "dingtalk_oidc":
            dt = self.dingtalk_oidc.expanded()
            if not dt.issuer:
                raise ValueError(
                    "auth.dingtalk_oidc.issuer is required when auth.provider='dingtalk_oidc'"
                )
            if not dt.client_id:
                raise ValueError(
                    "auth.dingtalk_oidc.client_id is required when auth.provider='dingtalk_oidc'"
                )
        elif p == "smartcmp":
            smartcmp = self.smartcmp.expanded()
            if not smartcmp.validate_url:
                raise ValueError(
                    "auth.smartcmp.validate_url is required when auth.provider='smartcmp'"
                )
        elif p == "local":
            if self.local.enabled and not self.local.default_admin_username:
                raise ValueError(
                    "auth.local.default_admin_username is required when auth.provider='local'"
                )

        jwt_cfg = self.jwt.expanded()
        if p in {"local", "oidc", "dingtalk_oidc"} and not jwt_cfg.secret_key:
            raise ValueError(
                "auth.jwt.secret_key is required when auth.provider is 'local', 'oidc', or 'dingtalk_oidc'"
            )
