# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Narrow host services exposed to provider backend extensions (Standard v1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HOST_API_VERSION = "1.0"


@dataclass(frozen=True)
class ProviderUser:
    """Authenticated actor a provider extension runs on behalf of."""

    user_id: str
    display_name: str = ""
    tenant_id: str = "default"
    is_admin: bool = False
    roles: tuple[str, ...] = ()
    locale: str = ""


@dataclass(frozen=True)
class ProviderFileRef:
    """One file the provider wants the agent run to see."""

    path: Path
    name: str
    mime: str = ""


@dataclass(frozen=True)
class ProviderSessionSpec:
    """Session reuse policy for a provider-initiated agent run."""

    kind: str = "ephemeral"
    key: str = ""
    retain: bool = False


@dataclass(frozen=True)
class ProviderArtifact:
    """One file produced by a provider-initiated agent run."""

    name: str
    path: Path
    mime: str
    size: int
    source: str = "download_path"


@dataclass
class ProviderHostServices:
    """Host surface available as ``context.host`` inside provider handlers."""

    workspace_path: Path
    data_dir: Path
    user: ProviderUser
    agent: Any = None
    config: dict[str, Any] = field(default_factory=dict)
    provider_type: str = ""
    work_dir: Path = Path(".")

    @property
    def api_version(self) -> str:
        """Return the Host Services contract version."""
        return HOST_API_VERSION

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read one provider instance config value."""
        return (self.config or {}).get(key, default)

    async def emit_event(self, topic: str, payload: dict) -> None:
        """Emit a provider-scoped audit event (v1: structured log only)."""
        logger.info(
            "provider_extension_event topic=%s provider=%s user=%s payload_keys=%s",
            str(topic or "")[:80],
            self.provider_type,
            self.user.user_id,
            sorted((payload or {}).keys()),
        )


def provider_data_dir(workspace_path: str | Path, provider_type: str, user: ProviderUser) -> Path:
    """Return the isolated data directory for one provider and user."""
    root = (
        Path(workspace_path).resolve()
        / ".provider-data"
        / str(provider_type or "").strip().lower()
        / (user.tenant_id or "default")
        / (user.user_id or "anonymous")
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def coerce_user(user: Any) -> ProviderUser:
    """Build a ProviderUser from a core UserInfo-like object."""
    return ProviderUser(
        user_id=str(getattr(user, "user_id", "") or "anonymous"),
        display_name=str(getattr(user, "display_name", "") or ""),
        tenant_id=str(getattr(user, "tenant_id", "") or "default"),
        is_admin=bool(getattr(user, "is_admin", False)),
        roles=tuple(str(role) for role in (getattr(user, "roles", None) or [])),
        locale="",
    )


def resolve_provider_root(workspace_path: str | Path, provider_type: str) -> Optional[Path]:
    """Return the provider package root inside the configured providers tree."""
    from app.atlasclaw.core.config import get_config

    configured_root = str(getattr(get_config(), "providers_root", "") or "").strip()
    if not configured_root:
        return None
    root = Path(configured_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    candidate = root / provider_type
    if candidate.is_dir():
        return candidate.resolve()
    return None
