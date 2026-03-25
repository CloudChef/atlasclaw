# -*- coding: utf-8 -*-
"""Database API routes for configuration management.

Provides REST endpoints for:
- /api/agent-configs - Agent configuration management
- /api/token-configs - Token configuration management
- /api/users - User management

Note: Channel configuration is managed via /api/channels routes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.schemas import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentListResponse,
    TokenCreate,
    TokenUpdate,
    TokenResponse,
    TokenListResponse,
    ServiceProviderConfigCreate,
    ServiceProviderConfigUpdate,
    ServiceProviderConfigResponse,
    ServiceProviderConfigListResponse,
    UserCreate,

    UserUpdate,
    UserResponse,
    UserListResponse,
)
from app.atlasclaw.db.orm.agent_config import AgentConfigService
from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.orm.user import UserService


router = APIRouter(prefix="/api", tags=["Database API"])


# ============== Agent Config Routes ==============


@router.post("/agent-configs", response_model=AgentResponse, status_code=201)
async def create_agent_config(
    agent_data: AgentCreate,
    session: AsyncSession = Depends(get_db_session),
) -> AgentResponse:
    """Create a new Agent configuration."""
    existing = await AgentConfigService.get_by_name(session, agent_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent '{agent_data.name}' already exists")
    
    agent = await AgentConfigService.create(session, agent_data)
    return AgentResponse.model_validate(agent)


@router.get("/agent-configs", response_model=AgentListResponse)
async def list_agent_configs(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentListResponse:
    """List all Agent configurations with optional filtering."""
    agents, total = await AgentConfigService.list_all(session, is_active=is_active, page=page, page_size=page_size)
    return AgentListResponse(
        agents=[AgentResponse.model_validate(a) for a in agents],
        total=total,
    )


@router.get("/agent-configs/{agent_id}", response_model=AgentResponse)
async def get_agent_config(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> AgentResponse:
    """Get Agent configuration by ID."""
    agent = await AgentConfigService.get_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return AgentResponse.model_validate(agent)


@router.put("/agent-configs/{agent_id}", response_model=AgentResponse)
async def update_agent_config(
    agent_id: str,
    agent_data: AgentUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> AgentResponse:
    """Update an Agent configuration."""
    agent = await AgentConfigService.update(session, agent_id, agent_data)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return AgentResponse.model_validate(agent)


@router.delete("/agent-configs/{agent_id}", status_code=204)
async def delete_agent_config(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete an Agent configuration."""
    deleted = await AgentConfigService.delete(session, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent config not found")


# ============== Token Config Routes ==============


@router.post("/token-configs", response_model=TokenResponse, status_code=201)
async def create_token_config(
    token_data: TokenCreate,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Create a new Token configuration."""
    existing = await ModelTokenConfigService.get_by_name(session, token_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Token '{token_data.name}' already exists")
    
    token = await ModelTokenConfigService.create(session, token_data)
    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.get("/token-configs", response_model=TokenListResponse)
async def list_token_configs(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> TokenListResponse:
    """List all Token configurations with optional filtering."""
    tokens, total = await ModelTokenConfigService.list_all(session, provider=provider, is_active=is_active, page=page, page_size=page_size)
    
    responses = []
    for token in tokens:
        r = TokenResponse.model_validate(token)
        r.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
        responses.append(r)
    
    return TokenListResponse(tokens=responses, total=total)


@router.get("/token-configs/{token_id}", response_model=TokenResponse)
async def get_token_config(
    token_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Get Token configuration by ID."""
    token = await ModelTokenConfigService.get_by_id(session, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token config not found")

    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.put("/token-configs/{token_id}", response_model=TokenResponse)
async def update_token_config(
    token_id: str,
    token_data: TokenUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Update a Token configuration."""
    token = await ModelTokenConfigService.update(session, token_id, token_data)
    if token is None:
        raise HTTPException(status_code=404, detail="Token config not found")

    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.delete("/token-configs/{token_id}", status_code=204)
async def delete_token_config(
    token_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a Token configuration."""
    deleted = await ModelTokenConfigService.delete(session, token_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Token config not found")


# ============== Service Provider Config Routes ==============


@router.post("/provider-configs", response_model=ServiceProviderConfigResponse, status_code=201)
async def create_provider_config(
    provider_data: ServiceProviderConfigCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ServiceProviderConfigResponse:
    """Create a new service provider instance configuration."""
    existing = await ServiceProviderConfigService.get_by_provider_instance(
        session,
        provider_data.provider_type,
        provider_data.instance_name,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider config '{provider_data.provider_type}.{provider_data.instance_name}' already exists"
            ),
        )

    item = await ServiceProviderConfigService.create(session, provider_data)
    return ServiceProviderConfigResponse.model_validate(item)


@router.get("/provider-configs", response_model=ServiceProviderConfigListResponse)
async def list_provider_configs(
    provider_type: Optional[str] = Query(None, description="Filter by provider type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> ServiceProviderConfigListResponse:
    """List service provider instance configurations with optional filtering."""
    items, total = await ServiceProviderConfigService.list_all(
        session,
        provider_type=provider_type,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return ServiceProviderConfigListResponse(
        provider_configs=[ServiceProviderConfigResponse.model_validate(i) for i in items],
        total=total,
    )


@router.get("/provider-configs/{config_id}", response_model=ServiceProviderConfigResponse)
async def get_provider_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ServiceProviderConfigResponse:
    """Get service provider instance config by ID."""
    item = await ServiceProviderConfigService.get_by_id(session, config_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return ServiceProviderConfigResponse.model_validate(item)


@router.put("/provider-configs/{config_id}", response_model=ServiceProviderConfigResponse)
async def update_provider_config(
    config_id: str,
    provider_data: ServiceProviderConfigUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> ServiceProviderConfigResponse:
    """Update a service provider instance config."""
    update_payload = provider_data.model_dump(exclude_unset=True)

    target_provider_type = update_payload.get("provider_type")
    target_instance_name = update_payload.get("instance_name")
    if target_provider_type or target_instance_name:
        current = await ServiceProviderConfigService.get_by_id(session, config_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Provider config not found")

        check_provider_type = target_provider_type or current.provider_type
        check_instance_name = target_instance_name or current.instance_name
        duplicate = await ServiceProviderConfigService.get_by_provider_instance(
            session,
            check_provider_type,
            check_instance_name,
        )
        if duplicate and duplicate.id != config_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Provider config '{check_provider_type}.{check_instance_name}' already exists"
                ),
            )

    item = await ServiceProviderConfigService.update(session, config_id, provider_data)
    if item is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return ServiceProviderConfigResponse.model_validate(item)


@router.delete("/provider-configs/{config_id}", status_code=204)
async def delete_provider_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a service provider instance config."""
    deleted = await ServiceProviderConfigService.delete(session, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider config not found")


# ============== User Routes ==============



@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Create a new User."""
    existing = await UserService.get_by_username(session, user_data.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"User '{user_data.username}' already exists")

    if user_data.email:
        existing_email = await UserService.get_by_email(session, user_data.email)
        if existing_email:
            raise HTTPException(status_code=409, detail=f"Email '{user_data.email}' already exists")

    user = await UserService.create(session, user_data)
    return UserResponse.model_validate(user)


@router.get("/users", response_model=UserListResponse)
async def list_users(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search by username, email, or display name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> UserListResponse:
    """List all Users with optional filtering."""
    users, total = await UserService.list_all(session, is_active=is_active, search=search, page=page, page_size=page_size)
    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get User by ID."""
    user = await UserService.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Update a User."""
    user = await UserService.update(session, user_id, user_data)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a User."""
    deleted = await UserService.delete(session, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
