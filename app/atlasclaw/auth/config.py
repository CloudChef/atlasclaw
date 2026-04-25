# Copyright 2021  Qianyun, Inc. All rights reserved.


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
DEFAULT_JWT_SECRET = "atlasclaw-dev-secret"
DEFAULT_HOST_AUTH_NAME = "AtlasClaw-Host-Authenticate"


def expand_env(value: str) -> str:
    """Replace ${VAR_NAME} with os.environ.get(VAR_NAME, original)."""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


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


class DingTalkAuthConfig(BaseModel):
    """DingTalk SSO provider configuration."""
    app_key: str = ""
    app_secret: str = ""
    corp_id: str = ""
    redirect_uri: str = ""
    scopes: list[str] = ["openid", "corpid"]
    pkce_enabled: bool = True
    pkce_method: str = "S256"
    subject_field: str = "unionId"

    def expanded(self) -> "DingTalkAuthConfig":
        """Expand environment variable placeholders ${VAR_NAME}."""
        return DingTalkAuthConfig(
            app_key=expand_env(self.app_key),
            app_secret=expand_env(self.app_secret),
            corp_id=expand_env(self.corp_id),
            redirect_uri=expand_env(self.redirect_uri),
            scopes=self.scopes,
            pkce_enabled=self.pkce_enabled,
            pkce_method=self.pkce_method,
            subject_field=self.subject_field,
        )


class HostAuthConfig(BaseModel):
    """Embedded host token names used for host system handoff."""

    header_name: str = DEFAULT_HOST_AUTH_NAME
    cookie_name: str = DEFAULT_HOST_AUTH_NAME

    def expanded(self) -> "HostAuthConfig":
        return HostAuthConfig(
            header_name=expand_env(self.header_name),
            cookie_name=expand_env(self.cookie_name),
        )


class CMPAuthConfig(BaseModel):
    """CMP platform cookie-based authentication configuration."""

    token_cookie_name: str = ""
    login_id_cookie_name: str = "userLoginId"
    username_cookie_name: str = "username"
    user_id_cookie_name: str = "userId"
    tenant_id_cookie_name: str = "tenant_id"

    def expanded(self) -> "CMPAuthConfig":
        return CMPAuthConfig(
            token_cookie_name=expand_env(self.token_cookie_name),
            login_id_cookie_name=expand_env(self.login_id_cookie_name),
            username_cookie_name=expand_env(self.username_cookie_name),
            user_id_cookie_name=expand_env(self.user_id_cookie_name),
            tenant_id_cookie_name=expand_env(self.tenant_id_cookie_name),
        )


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
    secret_key: str = DEFAULT_JWT_SECRET
    expires_minutes: int = 480

    def _resolve_secret_key(self) -> str:
        expanded_secret = expand_env(self.secret_key).strip()
        if expanded_secret and not _ENV_RE.fullmatch(expanded_secret):
            return expanded_secret
        env_secret = os.environ.get("ATLASCLAW_JWT_SECRET", "").strip()
        if env_secret:
            return env_secret
        return DEFAULT_JWT_SECRET

    def expanded(self) -> "JWTAuthConfig":
        return JWTAuthConfig(
            header_name=expand_env(self.header_name),
            cookie_name=expand_env(self.cookie_name),
            issuer=expand_env(self.issuer),
            secret_key=self._resolve_secret_key(),
            expires_minutes=self.expires_minutes,
        )



class AuthConfig(BaseModel):

    """Top-level auth configuration block in atlasclaw.json."""
    enabled: bool = True          # Set to false to disable auth (anonymous mode)
    provider: str = "none"
    header_name: str = "AtlasClaw-Authenticate"
    token_prefix: str = ""
    cache_ttl_seconds: int = 300


    oidc: OIDCAuthConfig = OIDCAuthConfig()
    dingtalk: DingTalkAuthConfig = DingTalkAuthConfig()
    host: HostAuthConfig = HostAuthConfig()
    cmp: CMPAuthConfig = CMPAuthConfig()
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
        elif p == "local":
            if self.local.enabled and not self.local.default_admin_username:
                raise ValueError(
                    "auth.local.default_admin_username is required when auth.provider='local'"
                )
        elif p == "cmp":
            pass  # CMP uses browser cookies, no config validation needed
        elif p == "dingtalk":
            dt = self.dingtalk.expanded()
            if not dt.app_key:
                raise ValueError(
                    "auth.dingtalk.app_key is required when auth.provider='dingtalk'"
                )
            if not dt.app_secret:
                raise ValueError(
                    "auth.dingtalk.app_secret is required when auth.provider='dingtalk'"
                )
