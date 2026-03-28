# -*- coding: utf-8 -*-
"""Model configuration routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.db import get_db_session_dependency as get_db_session
from app.xuanwu.db.models import ModelConfigModel
from app.xuanwu.db.orm.model_config import ModelConfigService
from app.xuanwu.db.schemas import (
    ModelConfigCreate,
    ModelConfigListResponse,
    ModelConfigResponse,
    ModelConfigUpdate,
)

router = APIRouter(tags=["Database API"])


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
        session,
        provider=provider,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return ModelConfigListResponse(
        model_configs=[_model_config_to_response(model) for model in models],
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
