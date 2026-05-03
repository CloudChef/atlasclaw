# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""SQLAlchemy ORM models for AtlasClaw database entities.

Models:
- AgentModel: Agent configuration storage
- TokenModel: Token/API key configuration
- UserModel: User accounts
- ChannelModel: User channel configurations
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON, UniqueConstraint

from sqlalchemy.dialects.mysql import JSON as MySQLJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.atlasclaw.db.database import Base


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


def _extract_role_identifiers(raw_roles: Any) -> list[str]:
    """Normalize role identifiers from JSON-backed role storage."""
    if isinstance(raw_roles, dict):
        return [str(identifier) for identifier, enabled in raw_roles.items() if bool(enabled)]
    if isinstance(raw_roles, list):
        return [str(identifier) for identifier in raw_roles if str(identifier).strip()]
    return []


class AgentModel(Base):
    """Agent configuration stored in database.

    Stores agent identity, user context, soul, and memory configuration.
    """

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Agent configuration as JSON
    identity: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    user: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    soul: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    memory: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentModel(id={self.id}, name={self.name})>"


class TokenModel(Base):
    """Token/API key configuration for LLM providers.

    Supports multiple tokens per provider with priority and weight-based selection.
    API keys are stored encrypted.
    """

    __tablename__ = "tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # Provider configuration
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Selection configuration
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Rate limit state (populated from API headers)
    rate_limit_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rate_limit_reset: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<TokenModel(id={self.id}, name={self.name}, provider={self.provider})>"


class ServiceProviderConfigModel(Base):
    """Service provider instance configuration stored in database."""

    __tablename__ = "service_provider_configs"
    __table_args__ = (
        UniqueConstraint("provider_type", "instance_name", name="uq_provider_instance"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    provider_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    instance_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ServiceProviderConfigModel(id={self.id}, "
            f"provider_type={self.provider_type}, instance_name={self.instance_name})>"
        )


class UserModel(Base):
    """User account for authentication and authorization.

    Passwords are stored as bcrypt hashes.
    """


    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_type", "username", name="uq_users_auth_type_username"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(100), nullable=False, default="local", index=True)

    # Roles and permissions
    roles: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)


    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Profile
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Metadata
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    channels: Mapped[list["ChannelModel"]] = relationship(
        "ChannelModel", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        """Return the effective admin state derived from assigned roles."""
        return any(identifier.lower() == "admin" for identifier in _extract_role_identifiers(self.roles))

    def __repr__(self) -> str:
        return f"<UserModel(id={self.id}, username={self.username})>"


class RoleModel(Base):
    """Role definition for permission management."""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    identifier: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permissions: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<RoleModel(id={self.id}, identifier={self.identifier})>"


class ChannelModel(Base):
    """User channel configuration.

    Stores configuration for different access channels (WebSocket, SSE, REST, etc.).
    """

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # feishu, dingtalk, wecom, etc.

    # Channel-specific configuration
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user: Mapped[Optional["UserModel"]] = relationship("UserModel", back_populates="channels")

    def __repr__(self) -> str:
        return f"<ChannelModel(id={self.id}, name={self.name}, type={self.type})>"


class ChannelProvisioningSessionModel(Base):
    """Short-lived channel provisioning session persisted for external callbacks."""

    __tablename__ = "channel_provisioning_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    state_token: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)

    qr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qr_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instructions_i18n_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    connection_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    platform_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    refresh_after_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ChannelProvisioningSessionModel(id={self.id}, channel_type={self.channel_type})>"


class AuditLogModel(Base):
    """Audit log for tracking database changes.

    Records create/update/delete operations on all entities.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    # What was changed
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # CREATE, UPDATE, DELETE

    # Who made the change
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # Change details
    old_value: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<AuditLogModel(id={self.id}, entity={self.entity_type}:{self.entity_id}, action={self.action})>"


class ModelConfigModel(Base):
    """Model configuration for LLM models.

    Stores configuration for different LLM models including API settings,
    token limits, and capabilities.
    """

    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Provider configuration
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    api_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    api_type: Mapped[str] = mapped_column(String(20), default="openai", nullable=False)

    # Model parameters
    context_window: Mapped[int] = mapped_column(Integer, default=128000, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)

    # Description and capabilities
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capabilities_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Selection configuration
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Extra configuration
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ModelConfigModel(id={self.id}, name={self.name}, provider={self.provider})>"
