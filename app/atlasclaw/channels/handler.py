# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel handler abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from .models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    OutboundMessage,
    SendResult,
)
from .qr_provisioning import (
    ChannelProvisioningConnection,
    ChannelProvisioningRequest,
    ChannelProvisioningSession,
    ChannelProvisioningStart,
)


class ChannelHandler(ABC):
    """Abstract base class for all channel handlers.
    
    Both built-in channels (WebSocket, SSE, REST) and extension channels
    (Feishu, Slack, WhatsApp) must implement this interface.
    
    Supports both long-connection mode (WebSocket/Socket) and webhook mode.
    Long-connection is preferred for real-time messaging.
    """
    
    # Class attributes - must be defined by subclasses
    channel_type: str = ""
    channel_name: str = ""
    channel_icon: str = ""
    channel_mode: ChannelMode = ChannelMode.BIDIRECTIONAL
    
    # Connection mode support
    supports_long_connection: bool = False  # Whether this handler supports long connection
    supports_webhook: bool = False  # Whether this handler supports webhook mode

    # One-click provisioning support
    supports_provisioning: bool = False
    provisioning_default_mode: str = "manual"
    provisioning_manual_config_available: bool = True
    provisioning_instructions_i18n_key: str = ""
    provisioning_user_code_groups: int = 3
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize handler with configuration.
        
        Args:
            config: Channel connection configuration
        """
        self.config = config or {}
        self._status = ConnectionStatus.DISCONNECTED
        self._message_callback: Optional[Callable[[InboundMessage], None]] = None
        self._connection_task: Optional[Any] = None
    
    # Lifecycle methods
    @abstractmethod
    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        """Initialize channel with configuration.
        
        Args:
            connection_config: Connection-specific configuration
            
        Returns:
            True if setup successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def start(self, context: Any) -> bool:
        """Start the channel.
        
        For built-in channels: start server/listener
        For extension channels: establish long connection to platform
        
        Args:
            context: Application context
            
        Returns:
            True if started successfully, False otherwise
        """
        pass
    
    @abstractmethod
    async def stop(self) -> bool:
        """Stop the channel and cleanup resources.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        pass
    
    # Long connection methods
    async def connect(self) -> bool:
        """Establish long connection to platform.
        
        This method is called for handlers that support long-connection mode.
        Should establish WebSocket or similar persistent connection.
        
        Returns:
            True if connected successfully
        """
        if not self.supports_long_connection:
            return False
        
        # Subclasses should override this for long-connection support
        return False
    
    async def disconnect(self) -> bool:
        """Disconnect from platform.
        
        Returns:
            True if disconnected successfully
        """
        if not self.supports_long_connection:
            return True
        
        # Subclasses should override this for long-connection support
        return True
    
    async def reconnect(self) -> bool:
        """Reconnect to platform after connection loss.
        
        Returns:
            True if reconnected successfully
        """
        await self.disconnect()
        return await self.connect()
    
    def set_message_callback(self, callback: Callable[[InboundMessage], None]) -> None:
        """Set callback for incoming messages in long-connection mode.
        
        Args:
            callback: Function to call when message is received
        """
        self._message_callback = callback
    
    def _on_message_received(self, message: InboundMessage) -> None:
        """Internal method to handle incoming messages.
        
        Args:
            message: Received message
        """
        if self._message_callback:
            self._message_callback(message)
    
    # Message handling methods
    @abstractmethod
    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Handle incoming message from external platform.
        
        Args:
            request: Raw request from platform
            
        Returns:
            Standardized InboundMessage or None if invalid
        """
        pass
    
    @abstractmethod
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """Send message to external platform.
        
        Args:
            outbound: Standardized outbound message
            
        Returns:
            SendResult with success status
        """
        pass
    
    # Configuration methods
    @abstractmethod
    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        """Validate channel configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            Validation result with errors if any
        """
        pass
    
    @abstractmethod
    def describe_schema(self) -> Dict[str, Any]:
        """Return configuration schema for UI form generation.
        
        Returns:
            JSON Schema describing required configuration fields
        """
        pass

    @classmethod
    def describe_provisioning(cls) -> Dict[str, Any]:
        """Return one-click provisioning metadata for API and UI clients.

        Subclasses set the class attributes; the base method keeps the API
        response shape consistent across channels.
        """
        return {
            "supported": cls.supports_provisioning,
            "default_mode": cls.provisioning_default_mode,
            "manual_config_available": cls.provisioning_manual_config_available,
            "instructions_i18n_key": cls.provisioning_instructions_i18n_key,
        }

    async def create_provisioning_session(
        self,
        request: ChannelProvisioningRequest,
    ) -> ChannelProvisioningStart:
        """Create platform QR details for a new provisioning session.

        Implementations should only start registration and return QR metadata;
        connection creation belongs in `poll_provisioning_connection` after the
        platform flow completes.
        """
        del request
        raise NotImplementedError(f"{self.channel_type} does not support provisioning")

    async def poll_provisioning_connection(
        self,
        session: ChannelProvisioningSession,
    ) -> Optional[ChannelProvisioningConnection]:
        """Poll platform-owned provisioning state and return a connection when ready.

        Handlers may mutate transient session fields such as status,
        `platform_state`, or `refresh_after_seconds`; the API layer persists
        those changes after polling.
        """
        del session
        return None
    
    # Status methods
    async def health_check(self) -> bool:
        """Check if channel is healthy.
        
        Returns:
            True if channel is healthy
        """
        return self._status == ConnectionStatus.CONNECTED
    
    def get_status(self) -> ConnectionStatus:
        """Get current connection status.
        
        Returns:
            Current connection status
        """
        return self._status
    
    # Capability methods
    def supports_typing(self) -> bool:
        """Check if channel supports typing indicator.
        
        Returns:
            True if typing indicator is supported
        """
        return False
    
    def supports_media(self) -> bool:
        """Check if channel supports media messages.
        
        Returns:
            True if media (images, files) is supported
        """
        return False
    
    def supports_thread(self) -> bool:
        """Check if channel supports threads/topics.
        
        Returns:
            True if threading is supported
        """
        return False
    
    async def send_typing_indicator(self, chat_id: str, duration: int = 3) -> bool:
        """Send typing indicator to chat.
        
        Args:
            chat_id: Target chat ID
            duration: Duration in seconds
            
        Returns:
            True if sent successfully
        """
        return False
