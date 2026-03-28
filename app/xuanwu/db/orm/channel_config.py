# -*- coding: utf-8 -*-
"""Service operations for Channel configuration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.core.encryption import encrypt_json, decrypt_json, FORMAT_PREFIX
from app.xuanwu.db.models import ChannelModel
from app.xuanwu.db.schemas import ChannelCreate, ChannelUpdate

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


class ChannelConfigService:
    """Service operations for Channel configuration."""

    @staticmethod
    async def create(session: AsyncSession, channel_data: ChannelCreate) -> ChannelModel:
        """Create a new Channel.

        Args:
            session: Database session
            channel_data: Channel creation data

        Returns:
            Created Channel model
        """
        channel = ChannelModel(
            user_id=channel_data.user_id,
            name=channel_data.name,
            type=channel_data.type,
            config=_encrypt_config(channel_data.config),
            is_active=channel_data.is_active,
            is_default=channel_data.is_default,
        )
        session.add(channel)
        await session.flush()
        await session.refresh(channel)
        logger.info(f"Created channel: {channel.name} (id={channel.id})")
        return channel

    @staticmethod
    async def get_by_id(session: AsyncSession, channel_id: str) -> Optional[ChannelModel]:
        """Get Channel by ID.

        Args:
            session: Database session
            channel_id: Channel ID

        Returns:
            Channel model or None
        """
        result = await session.execute(
            select(ChannelModel).where(ChannelModel.id == channel_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        session: AsyncSession, name: str, user_id: Optional[str] = None
    ) -> Optional[ChannelModel]:
        """Get Channel by name (optionally filtered by user).

        Args:
            session: Database session
            name: Channel name
            user_id: Optional user ID filter

        Returns:
            Channel model or None
        """
        query = select(ChannelModel).where(ChannelModel.name == name)
        if user_id:
            query = query.where(ChannelModel.user_id == user_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        user_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[ChannelModel], int]:
        """List all Channels with optional filtering.

        Args:
            session: Database session
            user_id: Filter by user ID
            channel_type: Filter by channel type
            is_active: Filter by active status
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (list of channels, total count)
        """
        query = select(ChannelModel)

        if user_id:
            query = query.where(ChannelModel.user_id == user_id)
        if channel_type:
            query = query.where(ChannelModel.type == channel_type)
        if is_active is not None:
            query = query.where(ChannelModel.is_active == is_active)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        # Get paginated results
        query = query.offset((page - 1) * page_size).limit(page_size)
        query = query.order_by(ChannelModel.created_at.desc())

        result = await session.execute(query)
        channels = list(result.scalars().all())

        return channels, total

    @staticmethod
    async def list_by_user(session: AsyncSession, user_id: str) -> List[ChannelModel]:
        """List all Channels for a specific user.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            List of Channel models
        """
        result = await session.execute(
            select(ChannelModel)
            .where(ChannelModel.user_id == user_id)
            .order_by(ChannelModel.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_by_user_and_type(
        session: AsyncSession, user_id: str, channel_type: str
    ) -> List[ChannelModel]:
        """List all Channels for a specific user and channel type.

        Args:
            session: Database session
            user_id: User ID
            channel_type: Channel type (feishu, dingtalk, wecom, etc.)

        Returns:
            List of Channel models
        """
        result = await session.execute(
            select(ChannelModel)
            .where(ChannelModel.user_id == user_id)
            .where(ChannelModel.type == channel_type)
            .order_by(ChannelModel.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_active_by_type(
        session: AsyncSession, channel_type: str
    ) -> List[ChannelModel]:
        """List all active Channels of a specific type.

        Args:
            session: Database session
            channel_type: Channel type (feishu, dingtalk, wecom, etc.)

        Returns:
            List of active Channel models
        """
        result = await session.execute(
            select(ChannelModel)
            .where(ChannelModel.type == channel_type)
            .where(ChannelModel.is_active == True)
            .order_by(ChannelModel.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        session: AsyncSession, channel_id: str, channel_data: ChannelUpdate
    ) -> Optional[ChannelModel]:
        """Update a Channel.

        Args:
            session: Database session
            channel_id: Channel ID
            channel_data: Update data

        Returns:
            Updated Channel model or None
        """
        channel = await ChannelConfigService.get_by_id(session, channel_id)
        if channel is None:
            return None

        update_data = channel_data.model_dump(exclude_unset=True)
        
        # Encrypt config if provided
        if "config" in update_data and update_data["config"] is not None:
            update_data["config"] = _encrypt_config(update_data["config"])
        
        for key, value in update_data.items():
            setattr(channel, key, value)

        channel.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(channel)

        logger.info(f"Updated channel: {channel.name} (id={channel.id})")
        return channel

    @staticmethod
    async def update_status(
        session: AsyncSession, channel_id: str, is_active: bool
    ) -> Optional[ChannelModel]:
        """Update channel active status.

        Args:
            session: Database session
            channel_id: Channel ID
            is_active: New active status

        Returns:
            Updated Channel model or None
        """
        channel = await ChannelConfigService.get_by_id(session, channel_id)
        if channel is None:
            return None

        channel.is_active = is_active
        channel.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(channel)

        logger.info(f"Updated channel status: {channel.name} (id={channel.id}, is_active={is_active})")
        return channel

    @staticmethod
    async def delete(session: AsyncSession, channel_id: str) -> bool:
        """Delete a Channel.

        Args:
            session: Database session
            channel_id: Channel ID

        Returns:
            True if deleted, False if not found
        """
        channel = await ChannelConfigService.get_by_id(session, channel_id)
        if channel is None:
            return False

        await session.delete(channel)
        logger.info(f"Deleted channel: {channel.name} (id={channel.id})")
        return True

    @staticmethod
    async def deactivate_by_user(session: AsyncSession, user_id: str) -> int:
        """Deactivate all channels for a user (when user is deleted).

        Args:
            session: Database session
            user_id: User ID

        Returns:
            Number of channels deactivated
        """
        channels = await ChannelConfigService.list_by_user(session, user_id)
        count = 0
        for channel in channels:
            channel.is_active = False
            channel.user_id = None  # Clear user association
            count += 1
        await session.flush()
        logger.info(f"Deactivated {count} channels for user {user_id}")
        return count

    @staticmethod
    def to_channel_config(channel: ChannelModel) -> Dict[str, Any]:
        """Convert Channel model to config format for runtime use.
        
        Automatically decrypts config field for API response.

        Args:
            channel: Channel model

        Returns:
            Channel config dict with decrypted config
        """
        # Decrypt config for API response
        config = _decrypt_config(channel.config)
        
        return {
            "id": channel.id,
            "name": channel.name,
            "channel_type": channel.type,
            "config": config,
            "enabled": channel.is_active,
            "is_default": channel.is_default,
            "user_id": channel.user_id,
        }
