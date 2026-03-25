# -*- coding: utf-8 -*-
"""Service operations for Model configuration."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.models import ModelConfigModel
from app.atlasclaw.db.orm.model_token_config import encrypt_api_key, decrypt_api_key, mask_api_key

logger = logging.getLogger(__name__)


class ModelConfigService:
    """Service operations for Model configuration."""

    @staticmethod
    async def create(session: AsyncSession, data: "ModelConfigCreate") -> ModelConfigModel:
        """Create a new Model configuration.

        Args:
            session: Database session
            data: Model configuration creation data

        Returns:
            Created ModelConfigModel
        """
        # Encrypt API key if provided
        api_key_encrypted = None
        if data.api_key:
            api_key_encrypted = encrypt_api_key(data.api_key)

        # Convert capabilities dict to JSON string
        capabilities_json = None
        if data.capabilities:
            capabilities_json = json.dumps(data.capabilities)

        # Convert config dict to JSON string
        config_json = None
        if data.config:
            config_json = json.dumps(data.config)

        model_config = ModelConfigModel(
            name=data.name,
            display_name=data.display_name,
            provider=data.provider,
            model_id=data.model_id,
            base_url=data.base_url,
            api_key=api_key_encrypted,
            api_type=data.api_type,
            context_window=data.context_window,
            max_tokens=data.max_tokens,
            temperature=data.temperature,
            description=data.description,
            capabilities_json=capabilities_json,
            priority=data.priority,
            weight=data.weight,
            is_active=data.is_active,
            config_json=config_json,
        )
        session.add(model_config)
        await session.flush()
        await session.refresh(model_config)
        logger.info(f"Created model config: {model_config.name} (id={model_config.id})")
        return model_config

    @staticmethod
    async def get_by_id(session: AsyncSession, id: str) -> Optional[ModelConfigModel]:
        """Get Model configuration by ID.

        Args:
            session: Database session
            id: Model configuration ID

        Returns:
            ModelConfigModel or None
        """
        result = await session.execute(
            select(ModelConfigModel).where(ModelConfigModel.id == id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> Optional[ModelConfigModel]:
        """Get Model configuration by name.

        Args:
            session: Database session
            name: Model configuration name

        Returns:
            ModelConfigModel or None
        """
        result = await session.execute(
            select(ModelConfigModel).where(ModelConfigModel.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        provider: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[ModelConfigModel], int]:
        """List all Model configurations with optional filtering.

        Args:
            session: Database session
            provider: Filter by provider
            is_active: Filter by active status
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (list of model configs, total count)
        """
        query = select(ModelConfigModel)

        if provider:
            query = query.where(ModelConfigModel.provider == provider)
        if is_active is not None:
            query = query.where(ModelConfigModel.is_active == is_active)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        # Get paginated results
        query = query.offset((page - 1) * page_size).limit(page_size)
        query = query.order_by(ModelConfigModel.priority.desc(), ModelConfigModel.created_at.desc())

        result = await session.execute(query)
        model_configs = list(result.scalars().all())

        return model_configs, total

    @staticmethod
    async def list_active(session: AsyncSession) -> List[ModelConfigModel]:
        """List all active Model configurations for runtime use.

        Args:
            session: Database session

        Returns:
            List of active ModelConfigModel sorted by priority
        """
        result = await session.execute(
            select(ModelConfigModel)
            .where(ModelConfigModel.is_active == True)
            .order_by(ModelConfigModel.priority.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        session: AsyncSession, id: str, data: "ModelConfigUpdate"
    ) -> Optional[ModelConfigModel]:
        """Update a Model configuration.

        Args:
            session: Database session
            id: Model configuration ID
            data: Update data

        Returns:
            Updated ModelConfigModel or None
        """
        model_config = await ModelConfigService.get_by_id(session, id)
        if model_config is None:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # Encrypt new API key if provided
        if "api_key" in update_data and update_data["api_key"]:
            update_data["api_key"] = encrypt_api_key(update_data["api_key"])
        elif "api_key" in update_data:
            del update_data["api_key"]

        # Convert capabilities dict to JSON string if provided
        if "capabilities" in update_data:
            capabilities = update_data.pop("capabilities")
            if capabilities is not None:
                update_data["capabilities_json"] = json.dumps(capabilities)
            else:
                update_data["capabilities_json"] = None

        # Convert config dict to JSON string if provided
        if "config" in update_data:
            config = update_data.pop("config")
            if config is not None:
                update_data["config_json"] = json.dumps(config)
            else:
                update_data["config_json"] = None

        for key, value in update_data.items():
            setattr(model_config, key, value)

        model_config.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(model_config)

        logger.info(f"Updated model config: {model_config.name} (id={model_config.id})")
        return model_config

    @staticmethod
    async def delete(session: AsyncSession, id: str) -> bool:
        """Delete a Model configuration.

        Args:
            session: Database session
            id: Model configuration ID

        Returns:
            True if deleted, False if not found
        """
        model_config = await ModelConfigService.get_by_id(session, id)
        if model_config is None:
            return False

        await session.delete(model_config)
        logger.info(f"Deleted model config: {model_config.name} (id={model_config.id})")
        return True

    @staticmethod
    def get_decrypted_api_key(model: ModelConfigModel) -> Optional[str]:
        """Get decrypted API key from a ModelConfigModel.

        Args:
            model: ModelConfigModel instance

        Returns:
            Decrypted API key or None
        """
        if model.api_key is None:
            return None
        try:
            return decrypt_api_key(model.api_key)
        except Exception as e:
            logger.error(f"Failed to decrypt API key for model config {model.id}: {e}")
            return None

    @staticmethod
    def get_masked_api_key(model: ModelConfigModel) -> Optional[str]:
        """Get masked API key for display.

        Args:
            model: ModelConfigModel instance

        Returns:
            Masked API key or None
        """
        api_key = ModelConfigService.get_decrypted_api_key(model)
        if api_key is None:
            return None
        return mask_api_key(api_key)

    @staticmethod
    def get_capabilities(model: ModelConfigModel) -> Optional[Dict[str, Any]]:
        """Get capabilities dict from JSON string.

        Args:
            model: ModelConfigModel instance

        Returns:
            Capabilities dict or None
        """
        if model.capabilities_json is None:
            return None
        try:
            return json.loads(model.capabilities_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse capabilities_json for model config {model.id}: {e}")
            return None

    @staticmethod
    def get_config(model: ModelConfigModel) -> Optional[Dict[str, Any]]:
        """Get config dict from JSON string.

        Args:
            model: ModelConfigModel instance

        Returns:
            Config dict or None
        """
        if model.config_json is None:
            return None
        try:
            return json.loads(model.config_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config_json for model config {model.id}: {e}")
            return None
