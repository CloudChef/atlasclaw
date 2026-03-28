# -*- coding: utf-8 -*-
"""Channel webhook routes for receiving messages from external platforms."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.xuanwu.channels.registry import ChannelRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-hooks", tags=["channel-hooks"])


@router.post("/{channel_type}/{connection_id}")
async def receive_channel_webhook(
    channel_type: str,
    connection_id: str,
    request: Request
) -> JSONResponse:
    """Receive webhook from external channel platform.
    
    Args:
        channel_type: Channel type (e.g., feishu, slack)
        connection_id: Connection identifier
        request: FastAPI request object
        
    Returns:
        JSON response
    """
    try:
        # Get handler class
        handler_class = ChannelRegistry.get(channel_type)
        if not handler_class:
            logger.error(f"Channel type not found: {channel_type}")
            raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")
        
        # Get handler instance
        handler = ChannelRegistry.get_instance(connection_id)
        if not handler:
            # Try to create instance from connection config
            # This requires ChannelManager to be available
            logger.warning(f"Handler instance not found for: {connection_id}")
            raise HTTPException(status_code=404, detail=f"Connection not found: {connection_id}")
        
        # Parse request body
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            data = {"body": body.decode("utf-8")}
        
        # Add request metadata
        request_data = {
            "headers": dict(request.headers),
            "query_params": dict(request.query_params),
            "body": data,
        }
        
        # Handle inbound message
        inbound = await handler.handle_inbound(request_data)
        
        if not inbound:
            logger.warning(f"Failed to parse inbound message from {channel_type}")
            raise HTTPException(status_code=400, detail="Invalid message format")
        
        # TODO: Route to SessionManager
        # from app.xuanwu.session.manager import get_session_manager
        # session_manager = get_session_manager()
        # await session_manager.handle_message(inbound)
        
        return JSONResponse(content={"status": "ok", "message_id": inbound.message_id})
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to handle channel webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{channel_type}/{connection_id}")
async def verify_channel_webhook(
    channel_type: str,
    connection_id: str,
    request: Request
) -> JSONResponse:
    """Verify webhook endpoint (for platforms that require verification).
    
    Args:
        channel_type: Channel type
        connection_id: Connection identifier
        request: FastAPI request object
        
    Returns:
        JSON response
    """
    try:
        # Some platforms (like Feishu) require challenge verification
        params = dict(request.query_params)
        
        if "challenge" in params:
            # Return challenge for verification
            return JSONResponse(content={"challenge": params["challenge"]})
        
        return JSONResponse(content={"status": "ok"})
        
    except Exception as e:
        logger.error(f"Failed to verify webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
