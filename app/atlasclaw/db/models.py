# -*- coding: utf-8 -*-
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(100), nullable=False, default="local", index=True)

    # Roles and permissions
    roles: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)


    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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

    def __repr__(self) -> str:
        return f"<UserModel(id={self.id}, username={self.username})>"


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
