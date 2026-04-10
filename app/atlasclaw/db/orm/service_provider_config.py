# -*- coding: utf-8 -*-
"""Service operations for service provider instance configuration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.core.encryption import encrypt_json, decrypt_json, FORMAT_PREFIX
from app.atlasclaw.db.models import ServiceProviderConfigModel
from app.atlasclaw.db.schemas import ServiceProviderConfigCreate, ServiceProviderConfigUpdate

logger = logging.getLogger(__name__)


def _is_encrypted(config: Any) -> bool:
    """Check if config is already encrypted (string with prefix)."""
    return isinstance(config, str) and config.startswith(FORMAT_PREFIX)


def _encrypt_config(config: dict[str, Any]) -> str:
    """Encrypt config dict to encrypted string."""
    if config is None:
        return "{}"
    return encrypt_json(config)


def _decrypt_config(config: Any) -> dict[str, Any]:
    """Decrypt config from encrypted string or return as-is."""
    if config is None:
        return {}
    if isinstance(config, str) and config.startswith(FORMAT_PREFIX):
        return decrypt_json(config)
    # Legacy: plain dict stored as JSON
    if isinstance(config, dict):
        return config
    return {}


class ServiceProviderConfigService:
    """Service operations for service provider instance configuration."""

    @staticmethod
    def get_config(item: ServiceProviderConfigModel) -> dict[str, Any]:
        """Get decrypted config payload for API responses."""
        return _decrypt_config(item.config)

    @staticmethod
    async def create(
        session: AsyncSession,
        provider_data: ServiceProviderConfigCreate,
    ) -> ServiceProviderConfigModel:
        item = ServiceProviderConfigModel(
            provider_type=provider_data.provider_type,
            instance_name=provider_data.instance_name,
            config=_encrypt_config(provider_data.config),
            is_active=provider_data.is_active,
        )
        session.add(item)
        await session.flush()
        await session.refresh(item)
        logger.info(
            "Created service provider config: %s.%s (id=%s)",
            item.provider_type,
            item.instance_name,
            item.id,
        )
        return item

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        config_id: str,
    ) -> Optional[ServiceProviderConfigModel]:
        result = await session.execute(
            select(ServiceProviderConfigModel).where(ServiceProviderConfigModel.id == config_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_provider_instance(
        session: AsyncSession,
        provider_type: str,
        instance_name: str,
    ) -> Optional[ServiceProviderConfigModel]:
        result = await session.execute(
            select(ServiceProviderConfigModel).where(
                ServiceProviderConfigModel.provider_type == provider_type,
                ServiceProviderConfigModel.instance_name == instance_name,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        provider_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ServiceProviderConfigModel], int]:
        query = select(ServiceProviderConfigModel)

        if provider_type:
            query = query.where(ServiceProviderConfigModel.provider_type == provider_type)
        if is_active is not None:
            query = query.where(ServiceProviderConfigModel.is_active == is_active)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar() or 0

        query = query.order_by(
            ServiceProviderConfigModel.provider_type.asc(),
            ServiceProviderConfigModel.instance_name.asc(),
        )
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        return list(result.scalars().all()), total

    @staticmethod
    async def list_active_as_nested(
        session: AsyncSession,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        result = await session.execute(
            select(ServiceProviderConfigModel).where(ServiceProviderConfigModel.is_active == True)
        )
        rows = result.scalars().all()

        nested: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            provider_bucket = nested.setdefault(row.provider_type, {})
            provider_bucket[row.instance_name] = _decrypt_config(row.config)

        return nested

    @staticmethod
    async def update(
        session: AsyncSession,
        config_id: str,
        provider_data: ServiceProviderConfigUpdate,
    ) -> Optional[ServiceProviderConfigModel]:
        item = await ServiceProviderConfigService.get_by_id(session, config_id)
        if item is None:
            return None

        update_data = provider_data.model_dump(exclude_unset=True)
        
        # Encrypt config if provided
        if "config" in update_data and update_data["config"] is not None:
            update_data["config"] = _encrypt_config(update_data["config"])
        
        for key, value in update_data.items():
            setattr(item, key, value)

        item.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(item)
        logger.info(
            "Updated service provider config: %s.%s (id=%s)",
            item.provider_type,
            item.instance_name,
            item.id,
        )
        return item

    @staticmethod
    async def delete(
        session: AsyncSession,
        config_id: str,
    ) -> bool:
        item = await ServiceProviderConfigService.get_by_id(session, config_id)
        if item is None:
            return False

        await session.delete(item)
        logger.info(
            "Deleted service provider config: %s.%s (id=%s)",
            item.provider_type,
            item.instance_name,
            item.id,
        )
        return True
