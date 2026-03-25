# -*- coding: utf-8 -*-
"""Database API routes for configuration management.

Provides REST endpoints for:
- /api/agent-configs - Agent configuration management
- /api/token-configs - Token configuration management
- /api/provider-configs - Service provider configuration management
- /api/model-configs - Model configuration management
- /api/users - User management

Note: Channel configuration is managed via /api/channels routes.
"""

from __future__ import annotations

import httpx
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as PydanticBaseModel
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
    ModelConfigCreate,
    ModelConfigUpdate,
    ModelConfigResponse,
    ModelConfigListResponse,
)
from app.atlasclaw.db.orm.agent_config import AgentConfigService
from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.models import ModelConfigModel


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


# ============== Model Config Routes ==============


def _model_config_to_response(model: ModelConfigModel) -> ModelConfigResponse:
    """Convert ModelConfigModel to ModelConfigResponse with masked API key and parsed JSON fields."""
    return ModelConfigResponse(
        id=model.id,
        name=model.name,
        display_name=model.display_name,
        provider=model.provider,
        model_id=model.model_id,
        base_url=model.base_url,
        api_key_masked=ModelConfigService.get_masked_api_key(model),
        api_type=model.api_type,
        context_window=model.context_window,
        max_tokens=model.max_tokens,
        temperature=model.temperature,
        description=model.description,
        capabilities=ModelConfigService.get_capabilities(model),
        priority=model.priority,
        weight=model.weight,
        is_active=model.is_active,
        config=ModelConfigService.get_config(model),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.post("/model-configs", response_model=ModelConfigResponse, status_code=201)
async def create_model_config(
    model_data: ModelConfigCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ModelConfigResponse:
    """Create a new Model configuration."""
    existing = await ModelConfigService.get_by_name(session, model_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Model config '{model_data.name}' already exists")

    model = await ModelConfigService.create(session, model_data)
    return _model_config_to_response(model)


@router.get("/model-configs", response_model=ModelConfigListResponse)
async def list_model_configs(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> ModelConfigListResponse:
    """List all Model configurations with optional filtering."""
    models, total = await ModelConfigService.list_all(
        session, provider=provider, is_active=is_active, page=page, page_size=page_size
    )
    return ModelConfigListResponse(
        model_configs=[_model_config_to_response(m) for m in models],
        total=total,
    )


@router.get("/model-configs/{config_id}", response_model=ModelConfigResponse)
async def get_model_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ModelConfigResponse:
    """Get Model configuration by ID."""
    model = await ModelConfigService.get_by_id(session, config_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_config_to_response(model)


@router.put("/model-configs/{config_id}", response_model=ModelConfigResponse)
async def update_model_config(
    config_id: str,
    model_data: ModelConfigUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> ModelConfigResponse:
    """Update a Model configuration."""
    model = await ModelConfigService.update(session, config_id, model_data)
    if model is None:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_config_to_response(model)


@router.delete("/model-configs/{config_id}", status_code=204)
async def delete_model_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a Model configuration."""
    deleted = await ModelConfigService.delete(session, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model config not found")


# ============================================================
# Provider Info Routes
# ============================================================

@router.get("/providers")
async def get_providers():
    """
    Get all available providers with their preset configurations and model lists.
    Used by frontend to populate provider/model dropdowns without hardcoding.
    """
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS, PROVIDER_MODELS

    result = {}
    for name, preset in BUILTIN_PROVIDERS.items():
        result[name] = {
            "base_url": preset.base_url,
            "api_type": preset.api_type,
            "models": PROVIDER_MODELS.get(name, []),
        }
    return result


class FetchModelsRequest(PydanticBaseModel):
    """Request body for fetching models from a provider."""
    provider: str
    base_url: str = ""
    api_key: str = ""


@router.post("/providers/fetch-models")
async def fetch_provider_models(body: FetchModelsRequest):
    """
    Dynamically fetch available models from a provider's API.
    Uses the provider's /models endpoint (OpenAI-compatible) or equivalent.
    Falls back to built-in preset list on failure.

    Security: Only allows requests to whitelisted provider base_urls
    from BUILTIN_PROVIDERS to prevent SSRF attacks.
    """
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS, PROVIDER_MODELS

    preset = BUILTIN_PROVIDERS.get(body.provider)
    if not preset:
        # Unknown provider — only return preset list, never make outbound requests
        return {"models": PROVIDER_MODELS.get(body.provider, []), "source": "preset"}

    # Use server-side preset base_url only — ignore client-supplied base_url to prevent SSRF
    base_url = preset.base_url
    api_key = body.api_key
    api_type = preset.api_type

    if not base_url or not api_key:
        return {"models": PROVIDER_MODELS.get(body.provider, []), "source": "preset"}

    try:
        headers = {}
        url = ""

        if api_type == "anthropic":
            # Anthropic uses x-api-key header and different endpoint
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            url = f"{base_url.rstrip('/')}/v1/models"
        elif api_type == "google":
            # Google Gemini uses query param for key
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        else:
            # OpenAI-compatible: GET {base_url}/models with Bearer token
            headers = {"Authorization": f"Bearer {api_key}"}
            clean_url = base_url.rstrip("/")
            if clean_url.endswith("/v1"):
                url = f"{clean_url}/models"
            else:
                url = f"{clean_url}/v1/models"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            return {"models": PROVIDER_MODELS.get(body.provider, []), "source": "preset", "error": f"HTTP {resp.status_code}"}

        data = resp.json()

        # Parse response based on api_type
        model_ids = []
        if api_type == "google":
            # Google returns {models: [{name: "models/gemini-pro", ...}]}
            for m in data.get("models", []):
                name = m.get("name", "")
                if name.startswith("models/"):
                    name = name[7:]  # Strip "models/" prefix
                if name:
                    model_ids.append(name)
        else:
            # OpenAI-compatible and Anthropic: {data: [{id: "gpt-4o", ...}]}
            for m in data.get("data", []):
                mid = m.get("id", "")
                if mid:
                    model_ids.append(mid)

        # Sort alphabetically for consistent display
        model_ids.sort()

        return {"models": model_ids, "source": "api"}

    except Exception:
        # Fall back to preset on any error — do not expose internal details
        return {
            "models": PROVIDER_MODELS.get(body.provider, []),
            "source": "preset",
            "error": "upstream_error",
        }
