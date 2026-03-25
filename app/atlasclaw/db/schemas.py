# -*- coding: utf-8 -*-
"""Pydantic schemas for API request/response validation.

These schemas are used for:
- API request body validation
- API response serialization
- Data transfer between layers
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ============== Agent Schemas ==============


class AgentBase(BaseModel):
    """Base schema for Agent."""

    name: str = Field(..., min_length=1, max_length=100, description="Agent unique name")
    display_name: str = Field(..., min_length=1, max_length=200, description="Agent display name")
    identity: Optional[Dict[str, Any]] = Field(default=None, description="IDENTITY.md content")
    user: Optional[Dict[str, Any]] = Field(default=None, description="USER.md content")
    soul: Optional[Dict[str, Any]] = Field(default=None, description="SOUL.md content")
    memory: Optional[Dict[str, Any]] = Field(default=None, description="MEMORY.md content")
    is_active: bool = Field(default=True, description="Whether agent is active")


class AgentCreate(AgentBase):
    """Schema for creating a new Agent."""

    pass


class AgentUpdate(BaseModel):
    """Schema for updating an existing Agent."""

    display_name: Optional[str] = Field(default=None, max_length=200)
    identity: Optional[Dict[str, Any]] = None
    user: Optional[Dict[str, Any]] = None
    soul: Optional[Dict[str, Any]] = None
    memory: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AgentResponse(AgentBase):
    """Schema for Agent API response."""

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentListResponse(BaseModel):
    """Schema for Agent list API response."""

    agents: List[AgentResponse]
    total: int


# ============== Token Schemas ==============


class TokenBase(BaseModel):
    """Base schema for Token."""

    name: str = Field(..., min_length=1, max_length=100, description="Token unique name")
    provider: str = Field(..., min_length=1, max_length=50, description="Provider name (e.g., openai, deepseek)")
    model: str = Field(..., min_length=1, max_length=100, description="Model name")
    base_url: Optional[str] = Field(default=None, max_length=500, description="API base URL")
    priority: int = Field(default=100, ge=0, le=1000, description="Token priority (higher = preferred)")
    weight: int = Field(default=100, ge=1, le=1000, description="Token weight for weighted selection")
    is_active: bool = Field(default=True, description="Whether token is active")


class TokenCreate(TokenBase):
    """Schema for creating a new Token."""

    api_key: Optional[str] = Field(default=None, description="API key (will be encrypted)")


class TokenUpdate(BaseModel):
    """Schema for updating an existing Token."""

    name: Optional[str] = Field(default=None, max_length=100)
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=100)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, description="New API key (will be encrypted)")
    priority: Optional[int] = Field(default=None, ge=0, le=1000)
    weight: Optional[int] = Field(default=None, ge=1, le=1000)
    is_active: Optional[bool] = None


class TokenResponse(TokenBase):
    """Schema for Token API response."""

    id: str
    api_key_masked: Optional[str] = Field(default=None, description="Masked API key (e.g., sk-xxx...xxx)")
    rate_limit_remaining: Optional[int] = None
    rate_limit_reset: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TokenListResponse(BaseModel):
    """Schema for Token list API response."""

    tokens: List[TokenResponse]
    total: int


# ============== Service Provider Config Schemas ==============


class ServiceProviderConfigBase(BaseModel):
    """Base schema for service provider instance config."""

    provider_type: str = Field(..., min_length=1, max_length=100, description="Provider type")
    instance_name: str = Field(..., min_length=1, max_length=100, description="Instance name")
    config: Dict[str, Any] = Field(default_factory=dict, description="Provider instance config")
    is_active: bool = Field(default=True, description="Whether this instance is active")


class ServiceProviderConfigCreate(ServiceProviderConfigBase):
    """Schema for creating a service provider instance config."""

    pass


class ServiceProviderConfigUpdate(BaseModel):
    """Schema for updating a service provider instance config."""

    provider_type: Optional[str] = Field(default=None, max_length=100)
    instance_name: Optional[str] = Field(default=None, max_length=100)
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class ServiceProviderConfigResponse(ServiceProviderConfigBase):
    """Schema for service provider instance config API response."""

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ServiceProviderConfigListResponse(BaseModel):
    """Schema for service provider instance config list API response."""

    provider_configs: List[ServiceProviderConfigResponse]
    total: int


# ============== User Schemas ==============



class UserBase(BaseModel):
    """Base schema for User."""

    username: str = Field(..., min_length=1, max_length=100, description="Username")
    email: Optional[str] = Field(default=None, max_length=255, description="Email address")
    display_name: Optional[str] = Field(default=None, max_length=200, description="Display name")
    roles: Optional[Dict[str, Any]] = Field(default=None, description="User roles")
    auth_type: str = Field(default="local", max_length=100, description="Auth type: local or oidc:{provider_id}")
    is_active: bool = Field(default=True, description="Whether user is active")
    is_admin: bool = Field(default=False, description="Whether user is admin")



class UserCreate(UserBase):
    """Schema for creating a new User."""

    password: Optional[str] = Field(default=None, min_length=1, description="Password (will be hashed)")


class UserUpdate(BaseModel):
    """Schema for updating an existing User."""

    email: Optional[str] = Field(default=None, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=200)
    password: Optional[str] = Field(default=None, min_length=1, description="New password")
    roles: Optional[Dict[str, Any]] = None
    auth_type: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    avatar_url: Optional[str] = Field(default=None, max_length=500)



class UserResponse(UserBase):
    """Schema for User API response."""

    id: str
    avatar_url: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    """Schema for User list API response."""

    users: List[UserResponse]
    total: int


class ProfileUpdate(BaseModel):
    """Schema for user self-service profile update."""

    display_name: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = Field(None, max_length=255)
    avatar_url: Optional[str] = Field(None, max_length=500)


class PasswordChange(BaseModel):
    """Schema for user password change."""

    current_password: str
    new_password: str = Field(min_length=1)


# ============== Channel Schemas ==============


class ChannelBase(BaseModel):
    """Base schema for Channel."""

    name: str = Field(..., min_length=1, max_length=100, description="Channel name")
    type: str = Field(..., min_length=1, max_length=50, description="Channel type (feishu, dingtalk, wecom, etc.)")
    config: Optional[Dict[str, Any]] = Field(default=None, description="Channel-specific configuration")
    is_active: bool = Field(default=True, description="Whether channel is active")
    is_default: bool = Field(default=False, description="Whether this is the default channel for this type")


class ChannelCreate(ChannelBase):
    """Schema for creating a new Channel."""

    user_id: Optional[str] = Field(default=None, description="Owner user ID")


class ChannelUpdate(BaseModel):
    """Schema for updating an existing Channel."""

    name: Optional[str] = Field(default=None, max_length=100)
    type: Optional[str] = Field(default=None, max_length=50)
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    user_id: Optional[str] = None


class ChannelResponse(ChannelBase):
    """Schema for Channel API response."""

    id: str
    user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChannelListResponse(BaseModel):
    """Schema for Channel list API response."""

    channels: List[ChannelResponse]
    total: int


# ============== Model Config Schemas ==============


class ModelConfigCreate(BaseModel):
    """Schema for creating a new Model Config."""

    name: str = Field(..., min_length=1, max_length=100, description="Model unique name")
    display_name: Optional[str] = Field(default=None, max_length=200, description="Model display name")
    provider: str = Field(..., min_length=1, max_length=50, description="Provider name (e.g., openai, anthropic)")
    model_id: str = Field(..., min_length=1, max_length=200, description="Actual model identifier sent to API")
    base_url: Optional[str] = Field(default=None, max_length=500, description="API base URL")
    api_key: Optional[str] = Field(default=None, description="API key (will be encrypted)")
    api_type: str = Field(default="openai", max_length=20, description="API type: openai or anthropic")
    context_window: int = Field(default=128000, ge=1, description="Context window size")
    max_tokens: int = Field(default=4096, ge=1, description="Maximum output tokens")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Temperature for generation")
    description: Optional[str] = Field(default=None, description="Model description")
    capabilities: Optional[Dict[str, Any]] = Field(default=None, description="Model capabilities as JSON")
    priority: int = Field(default=0, ge=0, description="Model priority")
    weight: int = Field(default=100, ge=1, le=1000, description="Model weight for weighted selection")
    is_active: bool = Field(default=True, description="Whether model is active")
    config: Optional[Dict[str, Any]] = Field(default=None, description="Extra configuration as JSON")


class ModelConfigUpdate(BaseModel):
    """Schema for updating an existing Model Config."""

    display_name: Optional[str] = Field(default=None, max_length=200)
    provider: Optional[str] = Field(default=None, max_length=50)
    model_id: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, description="New API key (will be encrypted)")
    api_type: Optional[str] = Field(default=None, max_length=20)
    context_window: Optional[int] = Field(default=None, ge=1)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    description: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    priority: Optional[int] = Field(default=None, ge=0)
    weight: Optional[int] = Field(default=None, ge=1, le=1000)
    is_active: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class ModelConfigResponse(BaseModel):
    """Schema for Model Config API response."""

    id: str
    name: str
    display_name: Optional[str] = None
    provider: str
    model_id: str
    base_url: Optional[str] = None
    api_key_masked: Optional[str] = Field(default=None, description="Masked API key (e.g., sk-xxx...xxx)")
    api_type: str
    context_window: int
    max_tokens: int
    temperature: float
    description: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    priority: int
    weight: int
    is_active: bool
    config: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ModelConfigListResponse(BaseModel):
    """Schema for Model Config list API response."""

    model_configs: List[ModelConfigResponse]
    total: int


# ============== Pagination Schemas ==============


class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        """Calculate offset for database query."""
        return (self.page - 1) * self.page_size
