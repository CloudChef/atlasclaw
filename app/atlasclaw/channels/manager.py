# -*- coding: utf-8 -*-
"""Channel manager for managing channel connections."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .handler import ChannelHandler
from .models import ChannelConnection, InboundMessage, OutboundMessage
from .registry import ChannelRegistry
from app.atlasclaw.db.orm.channel_config import ChannelConfigService

if TYPE_CHECKING:
    from app.atlasclaw.agent.runner import AgentRunner
    from app.atlasclaw.core.deps import SkillDeps

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manager for channel connections lifecycle."""

    def __init__(self, workspace_path: Path):
        """Initialize channel manager.

        Args:
            workspace_path: Path to workspace directory (kept for compatibility)
        """
        self._workspace_path = workspace_path
        self._active_connections: Dict[str, ChannelHandler] = {}
        self._agent_runner: Optional["AgentRunner"] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
    
    def set_agent_runner(self, agent_runner: "AgentRunner") -> None:
        """Set the agent runner for processing messages.
        
        Args:
            agent_runner: AgentRunner instance for processing messages
        """
        self._agent_runner = agent_runner
        # Capture the event loop for async operations from sync callbacks
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

    async def initialize_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Initialize and start a channel connection.

        For long-connection channels, this will establish the persistent connection.
        For webhook channels, this will register the webhook handler.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if initialized successfully
        """
        try:
            from app.atlasclaw.db import get_db_manager

            # Get connection config from database
            async with get_db_manager().get_session() as session:
                channel = await ChannelConfigService.get_by_id(session, connection_id)
                if not channel or channel.user_id != user_id or channel.type != channel_type:
                    logger.error(f"Connection not found: {user_id}/{channel_type}/{connection_id}")
                    return False

                connection_config = ChannelConfigService.to_channel_config(channel)

            # Get handler class
            handler_class = ChannelRegistry.get(channel_type)
            if not handler_class:
                logger.error(f"Channel type not found: {channel_type}")
                return False

            # Create instance
            instance_key = f"{user_id}:{channel_type}:{connection_id}"
            handler = ChannelRegistry.create_instance(
                instance_key,
                channel_type,
                connection_config["config"]
            )

            if not handler:
                logger.error(f"Failed to create handler instance: {instance_key}")
                return False

            # Setup handler
            if not await handler.setup(connection_config["config"]):
                logger.error(f"Handler setup failed: {instance_key}")
                return False

            # Set message callback for long-connection mode
            if handler.supports_long_connection:
                handler.set_message_callback(
                    lambda msg: self._on_message_received(user_id, channel_type, connection_id, msg)
                )

            # Start handler (for long-connection, this establishes the connection)
            if not await handler.start(None):  # TODO: pass proper context
                logger.error(f"Handler start failed: {instance_key}")
                return False

            # For long-connection handlers, also call connect()
            if handler.supports_long_connection:
                if not await handler.connect():
                    logger.error(f"Long connection failed: {instance_key}")
                    await handler.stop()
                    return False
                logger.info(f"Long connection established: {instance_key}")

            # Register as active connection
            self._active_connections[instance_key] = handler
            ChannelRegistry.register_connection(ChannelConnection(
                id=channel.id,
                name=channel.name,
                channel_type=channel.type,
                config=channel.config or {},
                enabled=channel.is_active,
                is_default=channel.is_default,
            ))

            logger.info(f"Channel connection initialized: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize connection: {e}")
            return False

    def _on_message_received(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage
    ) -> None:
        """Handle incoming message from long connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
            message: Received message
        """
        logger.info(f"[ChannelManager] Message received from {channel_type}/{connection_id}: {message.content[:50]}...")
        
        # Schedule async processing on the event loop
        if self._event_loop and self._agent_runner:
            asyncio.run_coroutine_threadsafe(
                self._process_message_async(user_id, channel_type, connection_id, message),
                self._event_loop
            )
        else:
            logger.warning("[ChannelManager] No event loop or agent runner available for message processing")
    
    async def _process_message_async(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage
    ) -> None:
        """Async message processing - routes to agent and sends reply.
        
        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
            message: Received message
        """
        try:
            logger.info(f"[ChannelManager] Processing message: {message.content[:50]}...")
            
            # Get handler for sending reply
            instance_key = f"{user_id}:{channel_type}:{connection_id}"
            handler = self._active_connections.get(instance_key)
            
            if not handler:
                logger.error(f"[ChannelManager] No handler found for {instance_key}")
                return
            
            # Create session key
            session_key = f"channel:{channel_type}:{connection_id}:{message.chat_id}"
            
            # Import SkillDeps locally to avoid circular imports
            from app.atlasclaw.core.deps import SkillDeps
            from app.atlasclaw.auth.models import ANONYMOUS_USER
            
            deps = SkillDeps(
                user_info=ANONYMOUS_USER,
                peer_id=message.sender_id,
                session_key=session_key,
                channel=channel_type,
                extra={},
            )
            
            # Collect response from agent
            response_text = ""
            event_count = 0
            logger.debug(f"[ChannelManager] Starting to collect events for message: {message.content[:30]}...")
            async for event in self._agent_runner.run(
                session_key=session_key,
                user_message=message.content,
                deps=deps,
                max_tool_calls=10,
                timeout_seconds=120,
            ):
                event_count += 1
                logger.debug(f"[ChannelManager] Event {event_count}: type={event.type}")
                # Collect text deltas
                if event.type == "assistant":
                    response_text += event.content or ""
                elif event.type == "error":
                    logger.error(f"[ChannelManager] Agent error: {event.error}")
                    response_text = f"Processing error: {event.error}"
                    break
            
            logger.info(f"[ChannelManager] Processed {event_count} events, response length: {len(response_text)}")
            
            # Send reply back to channel
            if response_text:
                outbound = OutboundMessage(
                    chat_id=message.chat_id,
                    content=response_text,
                    content_type="text",
                    reply_to=message.message_id,
                    metadata=message.metadata,  # Pass metadata for session_webhook etc.
                )
                logger.debug(f"[ChannelManager] Sending reply to chat_id={message.chat_id}...")
                result = await handler.send_message(outbound)
                if result.success:
                    logger.info(f"[ChannelManager] Reply sent successfully to {channel_type}/{connection_id}")
                else:
                    logger.error(f"[ChannelManager] Failed to send reply: {result.error}")
            else:
                logger.warning("[ChannelManager] No response generated from agent")
                
        except Exception as e:
            logger.error(f"[ChannelManager] Error processing message: {e}", exc_info=True)

    async def stop_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Stop a channel connection.

        For long-connection channels, this will gracefully close the connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if stopped successfully
        """
        try:
            instance_key = f"{user_id}:{channel_type}:{connection_id}"
            handler = self._active_connections.get(instance_key)

            if not handler:
                logger.warning(f"Connection not active: {instance_key}")
                return False

            # For long-connection handlers, disconnect first
            if handler.supports_long_connection:
                await handler.disconnect()
                logger.info(f"Long connection disconnected: {instance_key}")

            await handler.stop()
            del self._active_connections[instance_key]

            logger.info(f"Channel connection stopped: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to stop connection: {e}")
            return False

    async def route_inbound_message(
        self,
        channel_type: str,
        connection_id: str,
        request: Any
    ) -> Optional[InboundMessage]:
        """Route incoming message to session manager.

        Args:
            channel_type: Channel type
            connection_id: Connection identifier
            request: Raw request data

        Returns:
            Standardized InboundMessage or None
        """
        try:
            # Get handler instance
            handler = self._get_handler_for_connection(channel_type, connection_id)
            if not handler:
                logger.error(f"No handler for connection: {channel_type}/{connection_id}")
                return None

            # Handle inbound message
            inbound = await handler.handle_inbound(request)
            if not inbound:
                return None

            # TODO: Route to SessionManager
            # session_manager = get_session_manager()
            # await session_manager.handle_message(inbound)

            return inbound

        except Exception as e:
            logger.error(f"Failed to route inbound message: {e}")
            return None

    def _get_handler_for_connection(
        self,
        channel_type: str,
        connection_id: str
    ) -> Optional[ChannelHandler]:
        """Get handler instance for a connection.

        Args:
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            Handler instance or None
        """
        # Try to find in active connections
        for key, handler in self._active_connections.items():
            if key.endswith(f":{channel_type}:{connection_id}"):
                return handler

        # Try to get from registry
        return ChannelRegistry.get_instance(connection_id)

    def get_user_connections(
        self,
        user_id: str,
        channel_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all connections for a user.

        Note: This is a sync wrapper for backwards compatibility.
        For async usage, use get_user_connections_async instead.

        Args:
            user_id: User identifier
            channel_type: Optional channel type filter

        Returns:
            List of connection info
        """
        # Return cached active connections for sync access
        # For full data, use get_user_connections_async
        result = []
        for key, handler in self._active_connections.items():
            parts = key.split(":")
            if len(parts) == 3:
                conn_user_id, conn_type, conn_id = parts
                if conn_user_id == user_id:
                    if channel_type is None or conn_type == channel_type:
                        result.append({
                            "id": conn_id,
                            "channel_type": conn_type,
                            "enabled": True,  # Active connections are enabled
                        })
        return result

    async def get_user_connections_async(
        self,
        user_id: str,
        channel_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all connections for a user from database.

        Args:
            user_id: User identifier
            channel_type: Optional channel type filter

        Returns:
            List of connection info
        """
        from app.atlasclaw.db import get_db_manager

        result = []
        async with get_db_manager().get_session() as session:
            if channel_type:
                channels = await ChannelConfigService.list_by_user_and_type(
                    session, user_id, channel_type
                )
            else:
                channels = await ChannelConfigService.list_by_user(session, user_id)

            for channel in channels:
                result.append(ChannelConfigService.to_channel_config(channel))

        return result

    async def enable_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Enable a connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if enabled successfully
        """
        from app.atlasclaw.db import get_db_manager

        async with get_db_manager().get_session() as session:
            channel = await ChannelConfigService.update_status(session, connection_id, True)
            if not channel:
                return False

        return await self.initialize_connection(user_id, channel_type, connection_id)

    async def disable_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Disable a connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if disabled successfully
        """
        from app.atlasclaw.db import get_db_manager

        await self.stop_connection(user_id, channel_type, connection_id)

        async with get_db_manager().get_session() as session:
            channel = await ChannelConfigService.update_status(session, connection_id, False)
            return channel is not None
