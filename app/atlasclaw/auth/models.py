# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
Auth data models: UserInfo, AuthResult, AuthenticationError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class UserInfo:
    """
    Authenticated user identity injected into SkillDeps.

    Attributes:
        user_id: Authenticated runtime user ID (or "anonymous" / "default").
        display_name: Human-readable display name.
        tenant_id: Tenant/org identifier (default "default").
        roles: List of role strings.
        raw_token: Original auth credential passed by the client.
        provider_subject: Composite key "{provider}:{subject}" linking to the
            external identity source.
        extra: Extension context, may include provider_type, available_providers, etc.
        auth_type: Authentication source type. e.g. "local", "oidc:keycloak".
    """
    user_id: str
    display_name: str = ""
    tenant_id: str = "default"
    roles: list[str] = field(default_factory=list)
    raw_token: str = ""
    provider_subject: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    auth_type: str = ""


    @property
    def is_anonymous(self) -> bool:
        return self.user_id == "anonymous"

    @property
    def is_default(self) -> bool:
        return self.user_id == "default"


# Shared anonymous sentinel (no-auth / fallback mode)
ANONYMOUS_USER = UserInfo(user_id="anonymous", display_name="Anonymous")


@dataclass
class AuthResult:
    """
    Result returned by AuthProvider.authenticate().
    Not persisted; consumed by auth flows to create/lookup a DB user.
    """
    subject: str                    # External subject (provider-specific ID / email)
    display_name: str = ""
    email: str = ""
    roles: list[str] = field(default_factory=list)
    tenant_id: str = "default"
    raw_token: str = ""
    id_token: str = ""              # OIDC ID Token (used for id_token_hint on logout)
    extra: dict[str, Any] = field(default_factory=dict)
