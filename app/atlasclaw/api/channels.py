# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel management API routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_channel_type_access,
    filter_channel_types_for_authz,
    get_authorization_context,
    has_permission,
)
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.channels.qr_provisioning import (
    ChannelProvisioningConnection,
    ChannelProvisioningRequest,
    ChannelProvisioningSession,
)
from app.atlasclaw.channels.registry import ChannelRegistry
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.orm.channel_config import ChannelConfigService, _decrypt_config
from app.atlasclaw.db.orm.channel_provisioning import ChannelProvisioningSessionService
from app.atlasclaw.db.schemas import ChannelCreate, ChannelUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

# Global channel manager instance (will be set during app startup)
_channel_manager: Optional[ChannelManager] = None
VALIDATION_TIMEOUT_SECONDS = 3.2
PROVISIONING_SESSION_TTL_SECONDS = 300


def _normalize_channel_config(
    user_id: str,
    config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate and normalize generic channel config extensions."""
    del user_id
    normalized_config: Dict[str, Any] = dict(config or {})
    normalized_config.pop("provider_type", None)
    normalized_config.pop("provider_binding", None)
    normalized_config.pop("provider_bindings", None)
    return normalized_config


def _expand_channel_config_for_response(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return channel config without legacy provider-binding helper fields."""
    expanded_config: Dict[str, Any] = dict(config or {})
    expanded_config.pop("provider_type", None)
    expanded_config.pop("provider_binding", None)
    expanded_config.pop("provider_bindings", None)
    return expanded_config


async def _ensure_owned_channel_connection(
    session: AsyncSession,
    *,
    user_id: str,
    channel_type: str,
    connection_id: str,
) -> None:
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")


def _normalize_channel_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return channel schema without legacy provider-binding helper fields."""
    base_schema = dict(schema or {})
    properties = dict(base_schema.get("properties") or {})
    properties.pop("provider_type", None)
    properties.pop("provider_binding", None)
    properties.pop("provider_bindings", None)
    base_schema["properties"] = properties
    required = base_schema.get("required")
    if isinstance(required, list):
        base_schema["required"] = [
            item
            for item in required
            if item not in {"provider_type", "provider_binding", "provider_bindings"}
        ]
    return base_schema


def get_channel_manager() -> ChannelManager:
    """Get channel manager instance."""
    if _channel_manager is None:
        raise HTTPException(status_code=500, detail="Channel manager not initialized")
    return _channel_manager


def set_channel_manager(manager: ChannelManager) -> None:
    """Set channel manager instance."""
    global _channel_manager
    _channel_manager = manager


def get_current_user_id(request: Request) -> str:
    """Get current user ID from request.
    
    For now, returns a default user. In production, this would
    extract user info from authentication.
    """
    user_info = getattr(request.state, "user_info", None)
    if user_info is not None and getattr(user_info, "user_id", None):
        return str(user_info.user_id)
    return request.headers.get("X-User-Id", "default")


# Request/Response Models

class ConnectionCreateRequest(BaseModel):
    """Request model for creating a connection."""
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class ConnectionUpdateRequest(BaseModel):
    """Request model for updating a connection."""
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class ConnectionResponse(BaseModel):
    """Response model for a connection."""
    id: str
    name: str
    channel_type: str
    config: Dict[str, Any]
    enabled: bool
    is_default: bool
    runtime_status: str = "disconnected"  # connected/disconnected/connecting/error


class ChannelTypeResponse(BaseModel):
    """Response model for a channel type."""
    type: str
    name: str
    icon: Optional[str] = None
    mode: str
    connection_count: int = 0
    provisioning: Dict[str, Any] = Field(default_factory=dict)


class ValidationResponse(BaseModel):
    """Response model for config validation."""
    valid: bool
    errors: List[str] = Field(default_factory=list)


class ConfigValidationRequest(BaseModel):
    """Request model for config validation without saving."""
    config: Dict[str, Any]


class ProvisioningConnectionSummary(BaseModel):
    """Connection summary returned by a completed provisioning session."""
    id: str
    name: str
    channel_type: str
    enabled: bool = True


class ProvisioningSessionResponse(BaseModel):
    """Response model for one-click channel provisioning sessions."""
    session_id: str
    channel_type: str
    status: str
    qr_url: str = ""
    qr_image_url: Optional[str] = None
    expires_at: datetime
    refresh_after_seconds: int = 60
    instructions_i18n_key: str = ""
    error: Optional[str] = None
    connection: Optional[ProvisioningConnectionSummary] = None


def _get_channel_handler_or_404(channel_type: str):
    """Return a registered channel handler class or raise 404."""
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    return handler_class


def _ensure_provisioning_supported(handler_class: Any, channel_type: str) -> None:
    """Raise 400 when a channel type does not expose one-click provisioning."""
    if not getattr(handler_class, "supports_provisioning", False):
        raise HTTPException(
            status_code=400,
            detail=f"Channel type does not support provisioning: {channel_type}",
        )


def _get_accessible_provisioning_handler_or_404(
    authz: AuthorizationContext,
    channel_type: str,
) -> Any:
    """Return an accessible provisioning-capable handler class or raise an HTTP error."""
    handler_class = _get_channel_handler_or_404(channel_type)
    ensure_channel_type_access(authz, channel_type)
    _ensure_provisioning_supported(handler_class, channel_type)
    return handler_class


async def _get_owned_provisioning_session_or_404(
    db_session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    channel_type: str,
) -> ChannelProvisioningSession:
    """Return an owned provisioning session or raise a consistent 404."""
    provision_session = await ChannelProvisioningSessionService.get_owned(
        db_session,
        session_id=session_id,
        user_id=user_id,
        channel_type=channel_type,
    )
    if not provision_session:
        raise HTTPException(status_code=404, detail=f"Provisioning session not found: {session_id}")
    return provision_session


def _get_provisioning_user_code_groups(handler_class: Any) -> int:
    """Return the handler's pairing code group count with conservative bounds."""
    try:
        groups = int(getattr(handler_class, "provisioning_user_code_groups", 3) or 3)
    except (TypeError, ValueError):
        groups = 3
    return min(max(groups, 1), 6)


def _provisioning_session_response(
    session: ChannelProvisioningSession,
) -> ProvisioningSessionResponse:
    """Serialize a provisioning session without exposing channel secrets."""
    connection = None
    if session.connection_id and session.connection_name:
        connection = ProvisioningConnectionSummary(
            id=session.connection_id,
            name=session.connection_name,
            channel_type=session.channel_type,
            enabled=True,
        )
    return ProvisioningSessionResponse(
        session_id=session.session_id,
        channel_type=session.channel_type,
        status=session.public_status(),
        qr_url=session.qr_url,
        qr_image_url=session.qr_image_url,
        expires_at=session.expires_at,
        refresh_after_seconds=session.refresh_after_seconds,
        instructions_i18n_key=session.instructions_i18n_key,
        error=session.error,
        connection=connection,
    )


async def _save_provisioned_connection(
    *,
    channel_type: str,
    provision_session: ChannelProvisioningSession,
    provisioned_connection: ChannelProvisioningConnection,
    manager: ChannelManager,
    db_session: AsyncSession,
) -> ProvisioningSessionResponse:
    """Persist a provisioned connection and mark the provisioning session complete."""
    claimed_session = await ChannelProvisioningSessionService.claim_completion(
        db_session,
        provision_session,
    )
    if not claimed_session:
        raise HTTPException(status_code=409, detail="Provisioning session is not active")
    if claimed_session.status == "completed":
        return _provisioning_session_response(claimed_session)
    provision_session = claimed_session

    normalized_config = _normalize_channel_config(
        provision_session.user_id,
        provisioned_connection.config,
    )
    channel = await ChannelConfigService.create(
        db_session,
        ChannelCreate(
            user_id=provision_session.user_id,
            name=provisioned_connection.name,
            type=channel_type,
            config=normalized_config,
            is_active=True,
            is_default=provisioned_connection.is_default,
        ),
    )
    logger.info("Auto-starting provisioned connection: %s/%s/%s", provision_session.user_id, channel_type, channel.id)
    asyncio.create_task(
        manager._background_initialize(provision_session.user_id, channel_type, channel.id)
    )
    provision_session = await ChannelProvisioningSessionService.complete(
        db_session,
        provision_session,
        connection_id=channel.id,
        connection_name=channel.name,
    )
    return _provisioning_session_response(provision_session)


async def _poll_and_complete_provisioning_session(
    *,
    channel_type: str,
    handler_class: Any,
    provision_session: ChannelProvisioningSession,
    manager: ChannelManager,
    db_session: AsyncSession,
) -> ProvisioningSessionResponse:
    """Poll a platform-owned provisioning flow and persist a connection when ready."""
    if provision_session.public_status() == "expired":
        return _provisioning_session_response(provision_session)
    if provision_session.status == "completed":
        return _provisioning_session_response(provision_session)
    if provision_session.status not in {"pending", "scanned", "authorizing"}:
        return _provisioning_session_response(provision_session)

    try:
        handler = handler_class({})
        provisioned_connection = await handler.poll_provisioning_connection(provision_session)
    except ValueError as exc:
        await ChannelProvisioningSessionService.mark_status(
            db_session,
            provision_session,
            "failed",
            error=str(exc),
        )
        return _provisioning_session_response(provision_session)
    except Exception as exc:
        await ChannelProvisioningSessionService.mark_status(
            db_session,
            provision_session,
            "failed",
            error="Failed to poll platform provisioning",
        )
        logger.error("Failed to poll provisioning session for %s: %s", channel_type, exc)
        return _provisioning_session_response(provision_session)

    if not provisioned_connection:
        provision_session = await ChannelProvisioningSessionService.save_mutable_state(
            db_session,
            provision_session,
        )
        return _provisioning_session_response(provision_session)

    return await _save_provisioned_connection(
        channel_type=channel_type,
        provision_session=provision_session,
        provisioned_connection=provisioned_connection,
        manager=manager,
        db_session=db_session,
    )


async def _start_platform_provisioning_session(
    *,
    request: Request,
    db_session: AsyncSession,
    handler_class: Any,
    channel_type: str,
    provision_session: ChannelProvisioningSession,
) -> ChannelProvisioningSession:
    """Ask a channel handler to attach platform QR details to a session."""
    try:
        handler = handler_class({})
        start = await handler.create_provisioning_session(
            ChannelProvisioningRequest(
                user_id=provision_session.user_id,
                channel_type=channel_type,
                session_id=provision_session.session_id,
                state_token=provision_session.state_token,
                user_code=provision_session.user_code,
                expires_at=provision_session.expires_at,
                base_url=str(request.base_url).rstrip("/"),
            )
        )
        return await ChannelProvisioningSessionService.attach_start(
            db_session,
            provision_session.session_id,
            start,
        )
    except Exception as exc:
        provision_session = await ChannelProvisioningSessionService.mark_status(
            db_session,
            provision_session,
            "failed",
            error=str(exc),
        )
        logger.error("Failed to start provisioning session for %s: %s", channel_type, exc)
        return provision_session


# Routes

@router.get("")
async def list_channel_types(
    request: Request,
    include_all: bool = Query(False, description="Return the full catalog for permission governance"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> List[ChannelTypeResponse]:
    """List all available channel types with connection counts.
    
    Returns:
        List of channel types with their info
    """
    user_id = authz.user.user_id
    channels = ChannelRegistry.list_channels()
    if include_all:
        if not (
            has_permission(authz, "roles.manage_permissions")
            or has_permission(authz, "channels.manage_permissions")
        ):
            raise HTTPException(
                status_code=403,
                detail="Missing permission to access full channel catalog",
            )
    else:
        channels = filter_channel_types_for_authz(authz, channels)
    
    result = []
    for channel in channels:
        # Count connections for this channel type from database
        connections, _ = await ChannelConfigService.list_all(
            session, user_id=user_id, channel_type=channel["type"]
        )
        
        result.append(ChannelTypeResponse(
            type=channel["type"],
            name=channel.get("name", channel["type"]),
            icon=channel.get("icon"),
            mode=channel.get("mode", "bidirectional"),
            connection_count=len(connections),
            provisioning=channel.get("provisioning") or {},
        ))
    
    return result


@router.get("/{channel_type}/schema")
async def get_channel_schema(
    channel_type: str,
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> Dict[str, Any]:
    """Get configuration schema for a channel type.
    
    Args:
        channel_type: Channel type identifier
        
    Returns:
        JSON Schema for channel configuration
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)
    
    # Create temporary instance to get schema
    try:
        handler = handler_class({})
        schema = _normalize_channel_schema(handler.describe_schema())
        schema["provisioning"] = handler_class.describe_provisioning()
        return schema
    except Exception as e:
        logger.error(f"Failed to get schema for {channel_type}: {e}")
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


@router.post("/{channel_type}/provisioning-sessions")
async def create_provisioning_session(
    channel_type: str,
    request: Request,
    db_session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ProvisioningSessionResponse:
    """Create a short-lived QR provisioning session for a channel type."""
    handler_class = _get_accessible_provisioning_handler_or_404(authz, channel_type)

    provision_session = await ChannelProvisioningSessionService.create(
        db_session,
        user_id=authz.user.user_id,
        channel_type=channel_type,
        ttl_seconds=PROVISIONING_SESSION_TTL_SECONDS,
        user_code_groups=_get_provisioning_user_code_groups(handler_class),
    )
    provision_session = await _start_platform_provisioning_session(
        channel_type=channel_type,
        handler_class=handler_class,
        request=request,
        db_session=db_session,
        provision_session=provision_session,
    )
    return _provisioning_session_response(provision_session)


@router.get("/{channel_type}/provisioning-sessions/{session_id}")
async def get_provisioning_session(
    channel_type: str,
    session_id: str,
    db_session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ProvisioningSessionResponse:
    """Return the current state of an owned provisioning session."""
    _get_accessible_provisioning_handler_or_404(authz, channel_type)
    provision_session = await _get_owned_provisioning_session_or_404(
        db_session,
        session_id=session_id,
        user_id=authz.user.user_id,
        channel_type=channel_type,
    )
    return _provisioning_session_response(provision_session)


@router.post("/{channel_type}/provisioning-sessions/{session_id}/poll")
async def poll_provisioning_session(
    channel_type: str,
    session_id: str,
    manager: ChannelManager = Depends(get_channel_manager),
    db_session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ProvisioningSessionResponse:
    """Poll platform-owned provisioning state for an owned provisioning session."""
    handler_class = _get_accessible_provisioning_handler_or_404(authz, channel_type)
    provision_session = await _get_owned_provisioning_session_or_404(
        db_session,
        session_id=session_id,
        user_id=authz.user.user_id,
        channel_type=channel_type,
    )
    return await _poll_and_complete_provisioning_session(
        channel_type=channel_type,
        handler_class=handler_class,
        provision_session=provision_session,
        manager=manager,
        db_session=db_session,
    )


@router.post("/{channel_type}/provisioning-sessions/{session_id}/refresh")
async def refresh_provisioning_session(
    channel_type: str,
    session_id: str,
    request: Request,
    db_session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ProvisioningSessionResponse:
    """Refresh QR details for an owned non-terminal provisioning session."""
    handler_class = _get_accessible_provisioning_handler_or_404(authz, channel_type)
    provision_session = await _get_owned_provisioning_session_or_404(
        db_session,
        session_id=session_id,
        user_id=authz.user.user_id,
        channel_type=channel_type,
    )
    if provision_session.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Provisioning session cannot be refreshed")

    provision_session = await ChannelProvisioningSessionService.refresh(
        db_session,
        provision_session,
        ttl_seconds=PROVISIONING_SESSION_TTL_SECONDS,
        user_code_groups=_get_provisioning_user_code_groups(handler_class),
    )
    provision_session = await _start_platform_provisioning_session(
        channel_type=channel_type,
        handler_class=handler_class,
        request=request,
        db_session=db_session,
        provision_session=provision_session,
    )
    return _provisioning_session_response(provision_session)


@router.delete("/{channel_type}/provisioning-sessions/{session_id}")
async def cancel_provisioning_session(
    channel_type: str,
    session_id: str,
    db_session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ProvisioningSessionResponse:
    """Cancel an owned provisioning session."""
    _get_accessible_provisioning_handler_or_404(authz, channel_type)
    provision_session = await _get_owned_provisioning_session_or_404(
        db_session,
        session_id=session_id,
        user_id=authz.user.user_id,
        channel_type=channel_type,
    )
    if provision_session.status != "completed":
        provision_session = await ChannelProvisioningSessionService.cancel(
            db_session,
            provision_session,
        )
    return _provisioning_session_response(provision_session)


@router.get("/{channel_type}/connections")
async def list_connections(
    channel_type: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> Dict[str, Any]:
    """List all connections for a channel type.
    
    Args:
        channel_type: Channel type identifier
        
    Returns:
        List of connections with runtime status
    """
    user_id = authz.user.user_id
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)
    
    connections = await ChannelConfigService.list_by_user_and_type(
        session, user_id, channel_type
    )
    
    # Build response with runtime status
    result = []
    for conn in connections:
        conn_data = ChannelConfigService.to_channel_config(conn)
        conn_data["config"] = _expand_channel_config_for_response(conn_data.get("config"))
        # Get runtime status from channel manager
        runtime_status = manager.get_connection_runtime_status(conn.id)
        conn_data["runtime_status"] = runtime_status
        result.append(conn_data)
    
    return {
        "channel_type": channel_type,
        "connections": result
    }


@router.post("/{channel_type}/connections")
async def create_connection(
    channel_type: str,
    data: ConnectionCreateRequest,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ConnectionResponse:
    """Create a new channel connection.
    
    Args:
        channel_type: Channel type identifier
        data: Connection data
        
    Returns:
        Created connection
    """
    user_id = authz.user.user_id
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)

    try:
        normalized_config = _normalize_channel_config(user_id, data.config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Create channel config in database
    channel_data = ChannelCreate(
        user_id=user_id,
        name=data.name,
        type=channel_type,
        config=normalized_config,
        is_active=data.enabled,
        is_default=data.is_default,
    )
    
    channel = await ChannelConfigService.create(session, channel_data)
    
    # Decrypt config for response
    config = _expand_channel_config_for_response(_decrypt_config(channel.config))
    
    # Auto-start connection if enabled
    if channel.is_active:
        logger.info(f"Auto-starting new connection: {user_id}/{channel_type}/{channel.id}")
        asyncio.create_task(
            manager._background_initialize(user_id, channel_type, channel.id)
        )

    return ConnectionResponse(
        id=channel.id,
        name=channel.name,
        channel_type=channel.type,
        config=config,
        enabled=channel.is_active,
        is_default=channel.is_default,
    )


@router.patch("/{channel_type}/connections/{connection_id}")
async def update_connection(
    channel_type: str,
    connection_id: str,
    data: ConnectionUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ConnectionResponse:
    """Update an existing channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        data: Update data
        
    Returns:
        Updated connection
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)
    user_id = authz.user.user_id
    
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    # Build update data
    update_data = ChannelUpdate()
    if data.name is not None:
        update_data.name = data.name
    if data.config is not None:
        try:
            update_data.config = _normalize_channel_config(user_id, data.config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if data.enabled is not None:
        update_data.is_active = data.enabled
    if data.is_default is not None:
        update_data.is_default = data.is_default
    
    channel = await ChannelConfigService.update(session, connection_id, update_data)
    
    # Decrypt config for response
    config = _expand_channel_config_for_response(_decrypt_config(channel.config))
    
    return ConnectionResponse(
        id=channel.id,
        name=channel.name,
        channel_type=channel.type,
        config=config,
        enabled=channel.is_active,
        is_default=channel.is_default,
    )


@router.delete("/{channel_type}/connections/{connection_id}")
async def delete_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> JSONResponse:
    """Delete a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)
    user_id = authz.user.user_id
    
    # Verify ownership
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    # Stop connection if active
    await manager.stop_connection(user_id, channel_type, connection_id)
    
    # Delete from database
    if not await ChannelConfigService.delete(session, connection_id):
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    return JSONResponse(content={"status": "ok", "message": "Connection deleted"})


@router.post("/{channel_type}/validate-config")
async def validate_config(
    channel_type: str,
    data: ConfigValidationRequest,
    request: Request,
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ValidationResponse:
    """Validate channel configuration without saving to database.
    
    Args:
        channel_type: Channel type identifier
        data: Configuration data to validate
        
    Returns:
        Validation result
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    ensure_channel_type_access(authz, channel_type)

    try:
        normalized_config = _normalize_channel_config(authz.user.user_id, data.config)
    except ValueError as exc:
        return ValidationResponse(valid=False, errors=[str(exc)])

    try:
        handler = handler_class(normalized_config)
        result = await asyncio.wait_for(
            handler.validate_config(normalized_config),
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
        return ValidationResponse(valid=result.valid, errors=result.errors)
    except asyncio.TimeoutError:
        logger.warning("Config validation timed out for %s", channel_type)
        return ValidationResponse(
            valid=False,
            errors=[f"Validation timed out after {int(VALIDATION_TIMEOUT_SECONDS)} seconds"],
        )
    except Exception as e:
        logger.error(f"Config validation failed for {channel_type}: {e}")
        return ValidationResponse(valid=False, errors=[str(e)])


@router.post("/{channel_type}/connections/{connection_id}/verify")
async def verify_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ValidationResponse:
    """Verify a connection's configuration.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Validation result
    """
    ensure_channel_type_access(authz, channel_type)
    user_id = authz.user.user_id
    
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    
    # Create handler instance and validate
    try:
        config = _decrypt_config(channel.config)
        try:
            normalized_config = _normalize_channel_config(user_id, config)
        except ValueError as exc:
            return ValidationResponse(valid=False, errors=[str(exc)])

        handler = handler_class(normalized_config)
        result = await asyncio.wait_for(
            handler.validate_config(normalized_config),
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
        return ValidationResponse(valid=result.valid, errors=result.errors)
    except asyncio.TimeoutError:
        logger.warning("Connection verification timed out for %s", connection_id)
        return ValidationResponse(
            valid=False,
            errors=[f"Validation timed out after {int(VALIDATION_TIMEOUT_SECONDS)} seconds"],
        )
    except Exception as e:
        logger.error(f"Validation failed for {connection_id}: {e}")
        return ValidationResponse(valid=False, errors=[str(e)])


@router.post("/{channel_type}/connections/{connection_id}/enable")
async def enable_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> JSONResponse:
    """Enable a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    ensure_channel_type_access(authz, channel_type)
    user_id = authz.user.user_id
    await _ensure_owned_channel_connection(
        session,
        user_id=user_id,
        channel_type=channel_type,
        connection_id=connection_id,
    )
    
    if not await manager.enable_connection(user_id, channel_type, connection_id):
        raise HTTPException(status_code=500, detail="Failed to enable connection")
    
    return JSONResponse(content={"status": "ok", "message": "Connection enabled, initializing in background"})


@router.post("/{channel_type}/connections/{connection_id}/disable")
async def disable_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> JSONResponse:
    """Disable a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    ensure_channel_type_access(authz, channel_type)
    user_id = authz.user.user_id
    await _ensure_owned_channel_connection(
        session,
        user_id=user_id,
        channel_type=channel_type,
        connection_id=connection_id,
    )
    
    if not await manager.disable_connection(user_id, channel_type, connection_id):
        raise HTTPException(status_code=500, detail="Failed to disable connection")
    
    return JSONResponse(content={"status": "ok", "message": "Connection disabled"})
