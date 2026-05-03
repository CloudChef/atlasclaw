# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel registry for managing channel handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from .handler import ChannelHandler
from .models import ChannelConnection

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Registry for channel handler classes and their instances."""
    
    _handlers: Dict[str, Type[ChannelHandler]] = {}
    _instances: Dict[str, ChannelHandler] = {}
    _connections: Dict[str, ChannelConnection] = {}
    
    @classmethod
    def register(cls, channel_type: str, handler_class: Type[ChannelHandler]) -> None:
        """Register a channel handler class.
        
        Args:
            channel_type: Unique channel type identifier
            handler_class: Channel handler class
        """
        if not issubclass(handler_class, ChannelHandler):
            raise ValueError(f"Handler must inherit from ChannelHandler: {handler_class}")
        
        cls._handlers[channel_type] = handler_class
        logger.info(f"Registered channel handler: {channel_type}")
    
    @classmethod
    def get(cls, channel_type: str) -> Optional[Type[ChannelHandler]]:
        """Get channel handler class by type.
        
        Args:
            channel_type: Channel type identifier
            
        Returns:
            Handler class or None if not found
        """
        return cls._handlers.get(channel_type)
    
    @classmethod
    def list_channels(cls) -> List[Dict[str, Any]]:
        """List all registered channel types.
        
        Returns:
            List of channel info dicts with type, name, icon, mode
        """
        result = []
        for channel_type, handler_class in cls._handlers.items():
            result.append({
                "type": channel_type,
                "name": handler_class.channel_name or channel_type,
                "icon": handler_class.channel_icon,
                "mode": handler_class.channel_mode.value,
                "provisioning": handler_class.describe_provisioning(),
            })
        return result
    
    @classmethod
    def create_instance(
        cls,
        channel_id: str,
        channel_type: str,
        config: Dict[str, Any]
    ) -> Optional[ChannelHandler]:
        """Create and cache a channel handler instance.
        
        Args:
            channel_id: Unique instance identifier
            channel_type: Channel type
            config: Channel configuration
            
        Returns:
            Handler instance or None if type not found
        """
        handler_class = cls.get(channel_type)
        if not handler_class:
            logger.error(f"Channel type not found: {channel_type}")
            return None
        
        instance = handler_class(config)
        cls._instances[channel_id] = instance
        return instance
    
    @classmethod
    def get_instance(cls, channel_id: str) -> Optional[ChannelHandler]:
        """Get cached channel handler instance.
        
        Args:
            channel_id: Instance identifier
            
        Returns:
            Handler instance or None if not found
        """
        return cls._instances.get(channel_id)
    
    @classmethod
    def register_connection(cls, connection: ChannelConnection) -> None:
        """Register a channel connection.
        
        Args:
            connection: Channel connection configuration
        """
        cls._connections[connection.id] = connection
    
    @classmethod
    def get_connection(cls, connection_id: str) -> Optional[ChannelConnection]:
        """Get channel connection by ID.
        
        Args:
            connection_id: Connection identifier
            
        Returns:
            Channel connection or None if not found
        """
        return cls._connections.get(connection_id)
