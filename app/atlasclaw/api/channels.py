# -*- coding: utf-8 -*-
"""Channel management API routes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.channels.registry import ChannelRegistry
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.orm.channel_config import ChannelConfigService, _decrypt_config
from app.atlasclaw.db.schemas import ChannelCreate, ChannelUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

# Global channel manager instance (will be set during app startup)
_channel_manager: Optional[ChannelManager] = None


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
    # TODO: Implement proper user authentication
    return request.headers.get("X-User-Id", "default")


# Request/Response Models

class ConnectionCreateRequest(BaseModel):
    """Request model for creating a connection."""
    name: str
    config: Dict[str, Any] = {}
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


class ChannelTypeResponse(BaseModel):
    """Response model for a channel type."""
    type: str
    name: str
    icon: Optional[str] = None
    mode: str
    connection_count: int = 0


class ValidationResponse(BaseModel):
    """Response model for config validation."""
    valid: bool
    errors: List[str] = []


# Routes

@router.get("")
async def list_channel_types(
    request: Request,
    session: AsyncSession = Depends(get_db_session)
) -> List[ChannelTypeResponse]:
    """List all available channel types with connection counts.
    
    Returns:
        List of channel types with their info
    """
    user_id = get_current_user_id(request)
    channels = ChannelRegistry.list_channels()
    
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
            connection_count=len(connections)
        ))
    
    return result


@router.get("/{channel_type}/schema")
async def get_channel_schema(channel_type: str) -> Dict[str, Any]:
    """Get configuration schema for a channel type.
    
    Args:
        channel_type: Channel type identifier
        
    Returns:
        JSON Schema for channel configuration
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    
    # Create temporary instance to get schema
    try:
        handler = handler_class({})
        return handler.describe_schema()
    except Exception as e:
        logger.error(f"Failed to get schema for {channel_type}: {e}")
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


@router.get("/{channel_type}/connections")
async def list_connections(
    channel_type: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """List all connections for a channel type.
    
    Args:
        channel_type: Channel type identifier
        
    Returns:
        List of connections
    """
    user_id = get_current_user_id(request)
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    
    connections = await ChannelConfigService.list_by_user_and_type(
        session, user_id, channel_type
    )
    
    return {
        "channel_type": channel_type,
        "connections": [
            ChannelConfigService.to_channel_config(conn)
            for conn in connections
        ]
    }


@router.post("/{channel_type}/connections")
async def create_connection(
    channel_type: str,
    data: ConnectionCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session)
) -> ConnectionResponse:
    """Create a new channel connection.
    
    Args:
        channel_type: Channel type identifier
        data: Connection data
        
    Returns:
        Created connection
    """
    user_id = get_current_user_id(request)
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    
    # Create channel config in database
    channel_data = ChannelCreate(
        user_id=user_id,
        name=data.name,
        type=channel_type,
        config=data.config,
        is_active=data.enabled,
        is_default=data.is_default,
    )
    
    channel = await ChannelConfigService.create(session, channel_data)
    
    # Decrypt config for response
    config = _decrypt_config(channel.config)
    
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
    session: AsyncSession = Depends(get_db_session)
) -> ConnectionResponse:
    """Update an existing channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        data: Update data
        
    Returns:
        Updated connection
    """
    user_id = get_current_user_id(request)
    
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    # Build update data
    update_data = ChannelUpdate()
    if data.name is not None:
        update_data.name = data.name
    if data.config is not None:
        update_data.config = data.config
    if data.enabled is not None:
        update_data.is_active = data.enabled
    if data.is_default is not None:
        update_data.is_default = data.is_default
    
    channel = await ChannelConfigService.update(session, connection_id, update_data)
    
    # Decrypt config for response
    config = _decrypt_config(channel.config)
    
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
    session: AsyncSession = Depends(get_db_session)
) -> JSONResponse:
    """Delete a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    user_id = get_current_user_id(request)
    
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


@router.post("/{channel_type}/connections/{connection_id}/verify")
async def verify_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session)
) -> ValidationResponse:
    """Verify a connection's configuration.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Validation result
    """
    user_id = get_current_user_id(request)
    
    channel = await ChannelConfigService.get_by_id(session, connection_id)
    if not channel or channel.user_id != user_id or channel.type != channel_type:
        raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
    
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
    
    # Create handler instance and validate
    try:
        config = channel.config or {}
        handler = handler_class(config)
        result = await handler.validate_config(config)
        return ValidationResponse(valid=result.valid, errors=result.errors)
    except Exception as e:
        logger.error(f"Validation failed for {connection_id}: {e}")
        return ValidationResponse(valid=False, errors=[str(e)])


@router.post("/{channel_type}/connections/{connection_id}/enable")
async def enable_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager)
) -> JSONResponse:
    """Enable a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    user_id = get_current_user_id(request)
    
    if not await manager.enable_connection(user_id, channel_type, connection_id):
        raise HTTPException(status_code=500, detail="Failed to enable connection")
    
    return JSONResponse(content={"status": "ok", "message": "Connection enabled"})


@router.post("/{channel_type}/connections/{connection_id}/disable")
async def disable_connection(
    channel_type: str,
    connection_id: str,
    request: Request,
    manager: ChannelManager = Depends(get_channel_manager)
) -> JSONResponse:
    """Disable a channel connection.
    
    Args:
        channel_type: Channel type identifier
        connection_id: Connection identifier
        
    Returns:
        Success response
    """
    user_id = get_current_user_id(request)
    
    if not await manager.disable_connection(user_id, channel_type, connection_id):
        raise HTTPException(status_code=500, detail="Failed to disable connection")
    
    return JSONResponse(content={"status": "ok", "message": "Connection disabled"})
