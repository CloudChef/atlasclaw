# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Model configuration routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_permission,
    get_authorization_context,
)
from app.atlasclaw.bootstrap.startup_helpers import build_token_entries
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.models import ModelConfigModel
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.orm.model_token_config import mask_api_key
from app.atlasclaw.db.schemas import (
    FileModelConfigResponse,
    ModelConfigCreate,
    ModelConfigListResponse,
    ModelConfigResponse,
    ModelConfigUpdate,
)

router = APIRouter(tags=["Database API"])


def _file_model_configs(request: Request) -> list[FileModelConfigResponse]:
    """Expose only display fields from the loaded configuration, never raw credentials."""
    config = getattr(request.app.state, "config", None)
    if config is None or getattr(config, "model", None) is None:
        return []
    if not config.model.tokens and "primary" not in config.model.model_fields_set:
        return []
    active, _ = build_token_entries(config)
    configured, _ = build_token_entries(config, include_unusable=True)
    available = {(entry.token_id, entry.credential_fingerprint) for entry in active}
    return [FileModelConfigResponse(
        id=f"file:{index}", name=entry.token_id, provider=entry.provider, model_id=entry.model,
        base_url=entry.base_url, api_key_masked=mask_api_key(entry.api_key) if entry.api_key else None,
        api_type=entry.api_type, context_window=entry.context_window,
        max_tokens=config.model.max_tokens, temperature=config.model.temperature,
        capabilities=entry.capabilities, priority=entry.priority, weight=entry.weight,
        is_active=(entry.token_id, entry.credential_fingerprint) in available,
    ) for index, entry in enumerate(configured)]


def _model_config_to_response(model: ModelConfigModel) -> ModelConfigResponse:
    """Convert ORM model to API response."""
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
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ModelConfigResponse:
    """Create a new Model configuration."""
    ensure_permission(authz, "model_configs.create", detail="Missing permission: model_configs.create")
    existing = await ModelConfigService.get_by_name(session, model_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Model config '{model_data.name}' already exists")

    model = await ModelConfigService.create(session, model_data)
    return _model_config_to_response(model)


@router.get("/model-configs", response_model=ModelConfigListResponse)
async def list_model_configs(
    request: Request,
    provider: Optional[str] = Query(None, description="Filter by provider"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ModelConfigListResponse:
    """List all Model configurations with optional filtering."""
    ensure_permission(authz, "model_configs.view", detail="Missing permission: model_configs.view")
    models, total = await ModelConfigService.list_all(
        session,
        provider=provider,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return ModelConfigListResponse(
        model_configs=[_model_config_to_response(model) for model in models],
        total=total,
        file_models=[model for model in _file_model_configs(request)
                     if (provider is None or model.provider == provider)
                     and (is_active is None or model.is_active == is_active)],
    )


@router.get("/model-configs/{config_id}", response_model=ModelConfigResponse)
async def get_model_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ModelConfigResponse:
    """Get Model configuration by ID."""
    ensure_permission(authz, "model_configs.view", detail="Missing permission: model_configs.view")
    model = await ModelConfigService.get_by_id(session, config_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_config_to_response(model)


@router.put("/model-configs/{config_id}", response_model=ModelConfigResponse)
async def update_model_config(
    config_id: str,
    model_data: ModelConfigUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ModelConfigResponse:
    """Update a Model configuration."""
    ensure_permission(authz, "model_configs.edit", detail="Missing permission: model_configs.edit")
    if config_id.startswith("file:"):
        raise HTTPException(status_code=403, detail="Configuration-file models are read-only")
    model = await ModelConfigService.update(session, config_id, model_data)
    if model is None:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_config_to_response(model)


@router.delete("/model-configs/{config_id}", status_code=204)
async def delete_model_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete a Model configuration."""
    ensure_permission(authz, "model_configs.delete", detail="Missing permission: model_configs.delete")
    if config_id.startswith("file:"):
        raise HTTPException(status_code=403, detail="Configuration-file models are read-only")
    deleted = await ModelConfigService.delete(session, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model config not found")
