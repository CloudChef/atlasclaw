# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Service operations for channel QR provisioning sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
import uuid
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.channels.qr_provisioning import (
    ChannelProvisioningSession,
    ChannelProvisioningStart,
    generate_user_code,
    normalize_user_code,
    utcnow,
)
from app.atlasclaw.db.models import ChannelProvisioningSessionModel


ACTIVE_PROVISIONING_STATUSES = frozenset({"pending", "scanned", "authorizing"})


def _to_db_datetime(value: datetime) -> datetime:
    """Convert a datetime to naive UTC for current SQLAlchemy DateTime columns."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _from_db_datetime(value: datetime) -> datetime:
    """Convert a database datetime to timezone-aware UTC."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _db_utcnow() -> datetime:
    """Return naive UTC now for the existing SQLAlchemy DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ChannelProvisioningSessionService:
    """Database-backed service for channel provisioning sessions."""

    @staticmethod
    def to_session(model: ChannelProvisioningSessionModel) -> ChannelProvisioningSession:
        """Convert a database model into the runtime provisioning dataclass."""
        return ChannelProvisioningSession(
            session_id=model.id,
            user_id=model.user_id,
            channel_type=model.channel_type,
            state_token=model.state_token,
            user_code=model.user_code,
            status=model.status,
            qr_url=model.qr_url or "",
            qr_image_url=model.qr_image_url,
            instructions_i18n_key=model.instructions_i18n_key or "",
            error=model.error,
            connection_id=model.connection_id,
            connection_name=model.connection_name,
            platform_state=dict(model.platform_state or {}),
            refresh_after_seconds=model.refresh_after_seconds,
            expires_at=_from_db_datetime(model.expires_at),
            created_at=_from_db_datetime(model.created_at),
            updated_at=_from_db_datetime(model.updated_at),
        )

    @staticmethod
    async def prune(session: AsyncSession, *, ttl_seconds: int = 300) -> None:
        """Remove expired provisioning sessions after a grace window."""
        cutoff = _to_db_datetime(utcnow() - timedelta(seconds=ttl_seconds))
        await session.execute(
            delete(ChannelProvisioningSessionModel).where(
                ChannelProvisioningSessionModel.expires_at < cutoff
            )
        )
        await session.flush()

    @staticmethod
    async def _generate_unique_user_code(
        session: AsyncSession,
        *,
        channel_type: str,
        groups: int = 3,
        exclude_session_id: Optional[str] = None,
    ) -> str:
        """Generate a pairing code that does not collide with active DB sessions."""
        result = await session.execute(
            select(ChannelProvisioningSessionModel.id, ChannelProvisioningSessionModel.user_code)
            .where(ChannelProvisioningSessionModel.channel_type == channel_type)
            .where(ChannelProvisioningSessionModel.expires_at >= _to_db_datetime(utcnow()))
        )
        existing_codes = {
            normalize_user_code(user_code)
            for row_session_id, user_code in result.all()
            if user_code and row_session_id != exclude_session_id
        }
        for _ in range(16):
            user_code = generate_user_code(groups=groups)
            if normalize_user_code(user_code) not in existing_codes:
                return user_code
        return generate_user_code(groups=groups)

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        channel_type: str,
        ttl_seconds: int = 300,
        user_code_groups: int = 3,
    ) -> ChannelProvisioningSession:
        """Create a pending provisioning session with fresh tokens."""
        await ChannelProvisioningSessionService.prune(session, ttl_seconds=ttl_seconds)
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)
        model = ChannelProvisioningSessionModel(
            id=uuid.uuid4().hex,
            user_id=user_id,
            channel_type=channel_type,
            state_token=secrets.token_urlsafe(32),
            user_code=await ChannelProvisioningSessionService._generate_unique_user_code(
                session,
                channel_type=channel_type,
                groups=user_code_groups,
            ),
            expires_at=_to_db_datetime(expires_at),
            platform_state={},
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)

    @staticmethod
    async def get(
        session: AsyncSession,
        session_id: str,
        *,
        ttl_seconds: int = 300,
    ) -> Optional[ChannelProvisioningSession]:
        """Return a provisioning session by ID after pruning old entries."""
        await ChannelProvisioningSessionService.prune(session, ttl_seconds=ttl_seconds)
        model = await session.get(ChannelProvisioningSessionModel, session_id)
        return ChannelProvisioningSessionService.to_session(model) if model else None

    @staticmethod
    async def get_owned(
        session: AsyncSession,
        *,
        session_id: str,
        user_id: str,
        channel_type: str,
    ) -> Optional[ChannelProvisioningSession]:
        """Return a provisioning session if it belongs to a user and channel type."""
        result = await session.execute(
            select(ChannelProvisioningSessionModel)
            .where(ChannelProvisioningSessionModel.id == session_id)
            .where(ChannelProvisioningSessionModel.user_id == user_id)
            .where(ChannelProvisioningSessionModel.channel_type == channel_type)
        )
        model = result.scalar_one_or_none()
        return ChannelProvisioningSessionService.to_session(model) if model else None

    @staticmethod
    async def attach_start(
        session: AsyncSession,
        session_id: str,
        start: ChannelProvisioningStart,
    ) -> ChannelProvisioningSession:
        """Attach platform QR details to an existing provisioning session."""
        model = await session.get(ChannelProvisioningSessionModel, session_id)
        if model is None:
            raise KeyError(session_id)
        model.qr_url = start.qr_url
        model.qr_image_url = start.qr_image_url
        if start.user_code:
            model.user_code = start.user_code
        model.platform_state = dict(start.platform_state or {})
        model.expires_at = _to_db_datetime(start.expires_at)
        model.refresh_after_seconds = start.refresh_after_seconds
        model.instructions_i18n_key = start.instructions_i18n_key
        model.updated_at = _db_utcnow()
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)

    @staticmethod
    async def refresh(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
        *,
        ttl_seconds: int = 300,
        user_code_groups: int = 3,
    ) -> ChannelProvisioningSession:
        """Refresh tokens and expiry for a non-terminal provisioning session."""
        model = await session.get(ChannelProvisioningSessionModel, provision_session.session_id)
        if model is None:
            raise KeyError(provision_session.session_id)
        model.state_token = secrets.token_urlsafe(32)
        model.user_code = await ChannelProvisioningSessionService._generate_unique_user_code(
            session,
            channel_type=provision_session.channel_type,
            groups=user_code_groups,
            exclude_session_id=provision_session.session_id,
        )
        model.status = "pending"
        model.error = None
        model.platform_state = {}
        model.expires_at = _to_db_datetime(utcnow() + timedelta(seconds=ttl_seconds))
        model.updated_at = _db_utcnow()
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)

    @staticmethod
    async def save_mutable_state(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
    ) -> ChannelProvisioningSession:
        """Persist handler-mutated status, expiry, error, and platform state."""
        model = await session.get(ChannelProvisioningSessionModel, provision_session.session_id)
        if model is None:
            raise KeyError(provision_session.session_id)
        model.status = provision_session.status
        model.error = provision_session.error
        model.platform_state = dict(provision_session.platform_state or {})
        model.expires_at = _to_db_datetime(provision_session.expires_at)
        model.refresh_after_seconds = provision_session.refresh_after_seconds
        model.updated_at = _db_utcnow()
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)

    @staticmethod
    async def mark_status(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> ChannelProvisioningSession:
        """Update non-sensitive provisioning status details."""
        provision_session.status = status
        provision_session.error = error
        return await ChannelProvisioningSessionService.save_mutable_state(session, provision_session)

    @staticmethod
    async def claim_completion(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
    ) -> Optional[ChannelProvisioningSession]:
        """Atomically claim an active provisioning session for completion."""
        if provision_session.public_status() == "expired":
            return None
        result = await session.execute(
            update(ChannelProvisioningSessionModel)
            .where(ChannelProvisioningSessionModel.id == provision_session.session_id)
            .where(ChannelProvisioningSessionModel.status.in_(ACTIVE_PROVISIONING_STATUSES))
            .values(status="completing", updated_at=_db_utcnow())
        )
        await session.flush()
        latest = await ChannelProvisioningSessionService.get(session, provision_session.session_id)
        if result.rowcount == 1:
            return latest
        if latest and latest.status == "completed":
            return latest
        return None

    @staticmethod
    async def complete(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
        *,
        connection_id: str,
        connection_name: str,
    ) -> ChannelProvisioningSession:
        """Mark a claimed provisioning session completed with saved connection details."""
        model = await session.get(ChannelProvisioningSessionModel, provision_session.session_id)
        if model is None:
            raise KeyError(provision_session.session_id)
        model.status = "completed"
        model.connection_id = connection_id
        model.connection_name = connection_name
        model.error = None
        model.updated_at = _db_utcnow()
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)

    @staticmethod
    async def cancel(
        session: AsyncSession,
        provision_session: ChannelProvisioningSession,
    ) -> ChannelProvisioningSession:
        """Cancel a provisioning session that has not already completed."""
        model = await session.get(ChannelProvisioningSessionModel, provision_session.session_id)
        if model is None:
            raise KeyError(provision_session.session_id)
        model.status = "cancelled"
        model.updated_at = _db_utcnow()
        await session.flush()
        await session.refresh(model)
        return ChannelProvisioningSessionService.to_session(model)
