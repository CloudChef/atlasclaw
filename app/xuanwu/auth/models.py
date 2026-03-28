"""
Auth data models: UserInfo, AuthResult, ShadowUser, AuthenticationError.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class UserInfo:
    """
    Authenticated user identity injected into SkillDeps.

    Attributes:
        user_id: Internal shadow user UUID (or "anonymous" / "default").
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
    Not persisted — consumed by AuthStrategy to create/lookup a ShadowUser.
    """
    subject: str                    # External subject (provider-specific ID / email)
    display_name: str = ""
    email: str = ""
    roles: list[str] = field(default_factory=list)
    tenant_id: str = "default"
    raw_token: str = ""
    id_token: str = ""              # OIDC ID Token (used for id_token_hint on logout)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShadowUser:
    """
    Internal user record that persists the link between an external identity
    and the Xuanwu runtime. Stored in <workspace>/users.json.
    """
    user_id: str                    # Internal UUID
    provider: str                   # e.g. "smartcmp", "oidc", "none", "local"
    subject: str                    # External subject ID
    display_name: str = ""
    tenant_id: str = "default"
    roles: list[str] = field(default_factory=list)
    auth_type: str = "local"       # e.g. "local", "oidc:keycloak"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


    @classmethod
    def create(
        cls,
        provider: str,
        subject: str,
        result: AuthResult,
    ) -> "ShadowUser":
        auth_type = result.extra.get("auth_type", "")
        if not auth_type:
            if provider == "local":
                auth_type = "local"
            elif provider == "oidc":
                provider_id = result.extra.get("provider_id", "default")
                auth_type = f"oidc:{provider_id}"
            else:
                auth_type = provider

        return cls(
            user_id=str(uuid.uuid4()),
            provider=provider,
            subject=subject,
            display_name=result.display_name,
            tenant_id=result.tenant_id,
            roles=list(result.roles),
            auth_type=auth_type,
        )


    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "provider": self.provider,
            "subject": self.subject,
            "display_name": self.display_name,
            "tenant_id": self.tenant_id,
            "roles": self.roles,
            "auth_type": self.auth_type,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


    @classmethod
    def from_dict(cls, d: dict) -> "ShadowUser":
        provider = d["provider"]
        auth_type = d.get("auth_type", "")
        if not auth_type:
            auth_type = "local" if provider == "local" else provider

        return cls(
            user_id=d["user_id"],
            provider=d["provider"],
            subject=d["subject"],
            display_name=d.get("display_name", ""),
            tenant_id=d.get("tenant_id", "default"),
            roles=d.get("roles", []),
            auth_type=auth_type,
            created_at=datetime.fromisoformat(
                d.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
            last_seen_at=datetime.fromisoformat(
                d.get("last_seen_at", datetime.now(timezone.utc).isoformat())
            ),
        )


    def to_user_info(
        self,
        raw_token: str = "",
        extra: Optional[dict] = None,
    ) -> UserInfo:
        return UserInfo(
            user_id=self.user_id,
            display_name=self.display_name,
            tenant_id=self.tenant_id,
            roles=list(self.roles),
            raw_token=raw_token,
            provider_subject=f"{self.provider}:{self.subject}",
            extra=extra or {},
            auth_type=self.auth_type,
        )

