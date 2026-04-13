# -*- coding: utf-8 -*-
"""Channel manager for managing channel connections."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .handler import ChannelHandler
from .models import ChannelConnection, ConnectionStatus, InboundMessage, OutboundMessage
from .registry import ChannelRegistry
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.orm.channel_config import ChannelConfigService
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope

if TYPE_CHECKING:
    from app.atlasclaw.agent.runner import AgentRunner
    from app.atlasclaw.core.deps import SkillDeps
    from app.atlasclaw.session.router import SessionManagerRouter

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
        self._runtime_status_by_connection_id: Dict[str, ConnectionStatus] = {}
        self._agent_runner: Optional["AgentRunner"] = None
        self._session_manager_router: Optional["SessionManagerRouter"] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    def _set_connection_runtime_status(
        self,
        connection_id: str,
        status: ConnectionStatus,
    ) -> None:
        """Persist the latest known runtime status for a connection."""
        self._runtime_status_by_connection_id[connection_id] = status

    @staticmethod
    def _map_runtime_status(status: ConnectionStatus) -> str:
        """Convert enum runtime state to API response text."""
        status_map = {
            ConnectionStatus.CONNECTED: "connected",
            ConnectionStatus.CONNECTING: "connecting",
            ConnectionStatus.DISCONNECTED: "disconnected",
            ConnectionStatus.ERROR: "error",
        }
        return status_map.get(status, "disconnected")
    
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

    def set_session_manager_router(self, session_manager_router: "SessionManagerRouter") -> None:
        """Set the per-user session manager router used by channel traffic."""
        self._session_manager_router = session_manager_router

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
        self._set_connection_runtime_status(connection_id, ConnectionStatus.CONNECTING)

        try:
            from app.atlasclaw.db import get_db_manager

            # Get connection config from database
            async with get_db_manager().get_session() as session:
                channel = await ChannelConfigService.get_by_id(session, connection_id)
                if not channel or channel.user_id != user_id or channel.type != channel_type:
                    logger.error(f"Connection not found: {user_id}/{channel_type}/{connection_id}")
                    self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                    return False

                connection_config = ChannelConfigService.to_channel_config(channel)

            # Get handler class
            handler_class = ChannelRegistry.get(channel_type)
            if not handler_class:
                logger.error(f"Channel type not found: {channel_type}")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
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
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                return False

            # Setup handler
            if not await handler.setup(connection_config["config"]):
                logger.error(f"Handler setup failed: {instance_key}")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                return False

            # Set message callback for long-connection mode
            if handler.supports_long_connection:
                handler.set_message_callback(
                    lambda msg: self._on_message_received(user_id, channel_type, connection_id, msg)
                )

            # Start handler (for long-connection, this establishes the connection)
            if not await handler.start(None):  # TODO: pass proper context
                logger.error(f"Handler start failed: {instance_key}")
                failure_status = handler.get_status()
                if failure_status == ConnectionStatus.DISCONNECTED:
                    failure_status = ConnectionStatus.ERROR
                self._set_connection_runtime_status(connection_id, failure_status)
                return False

            # For long-connection handlers, also call connect()
            if handler.supports_long_connection:
                if not await handler.connect():
                    logger.error(f"Long connection failed: {instance_key}")
                    await handler.stop()
                    failure_status = handler.get_status()
                    if failure_status == ConnectionStatus.DISCONNECTED:
                        failure_status = ConnectionStatus.ERROR
                    self._set_connection_runtime_status(connection_id, failure_status)
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
            self._set_connection_runtime_status(connection_id, handler.get_status())

            logger.info(f"Channel connection initialized: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize connection: {e}")
            self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
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
            
            from app.atlasclaw.core.deps import SkillDeps
            from app.atlasclaw.core.trace import enrich_trace_metadata

            user_info = UserInfo(user_id=user_id, display_name=user_id.capitalize())
            session_key = self._build_channel_session_key(
                owner_user_id=user_id,
                channel_type=channel_type,
                connection_id=connection_id,
                message=message,
            )
            scoped_session_manager = (
                self._session_manager_router.for_user(user_id)
                if self._session_manager_router is not None
                else None
            )

            deps = SkillDeps(
                user_info=user_info,
                peer_id=self._resolve_peer_id(message),
                session_key=session_key,
                channel=channel_type,
                session_manager=scoped_session_manager,
                extra=enrich_trace_metadata(
                    session_key,
                    extra={
                        "channel_connection_id": connection_id,
                        "external_sender_id": message.sender_id,
                        "external_chat_id": message.chat_id,
                        "external_chat_type": self._resolve_chat_type(message).value,
                    },
                ),
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


    def _resolve_chat_type(self, message: InboundMessage) -> ChatType:
        """Map provider-specific metadata to canonical chat types."""
        metadata = message.metadata or {}
        raw_type = (
            metadata.get("chat_type")
            or metadata.get("conversation_type")
            or metadata.get("conversationType")
            or ""
        )
        raw_type = str(raw_type).strip().lower()
        if raw_type in {"group", "groupchat", "chat", "2"}:
            return ChatType.GROUP
        if raw_type in {"channel"}:
            return ChatType.CHANNEL
        if raw_type in {"thread"}:
            return ChatType.THREAD
        if raw_type in {"p2p", "single", "im", "1", "private"}:
            return ChatType.DM
        if message.chat_id and message.chat_id != message.sender_id:
            return ChatType.GROUP
        return ChatType.DM

    def _resolve_peer_id(self, message: InboundMessage) -> str:
        """Return the peer identity that should own the conversation state."""
        chat_type = self._resolve_chat_type(message)
        if chat_type in {ChatType.GROUP, ChatType.CHANNEL, ChatType.THREAD}:
            return message.chat_id or message.sender_id or "default"
        return message.sender_id or message.chat_id or "default"

    def _build_channel_session_key(
        self,
        *,
        owner_user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage,
    ) -> str:
        """Build canonical session keys for inbound channel traffic."""
        key = SessionKey(
            agent_id="main",
            user_id=owner_user_id,
            channel=channel_type,
            account_id=connection_id,
            chat_type=self._resolve_chat_type(message),
            peer_id=self._resolve_peer_id(message),
        )
        return key.to_string(scope=SessionScope.PER_ACCOUNT_CHANNEL_PEER)

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
                self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
                return False

            # For long-connection handlers, disconnect first
            if handler.supports_long_connection:
                await handler.disconnect()
                logger.info(f"Long connection disconnected: {instance_key}")

            await handler.stop()
            del self._active_connections[instance_key]
            self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)

            logger.info(f"Channel connection stopped: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to stop connection: {e}")
            self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
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
            True if enabled successfully (DB status updated, initialization started in background)
        """
        from app.atlasclaw.db import get_db_manager

        # Step 1: Update DB status (synchronous, fast)
        async with get_db_manager().get_session() as session:
            channel = await ChannelConfigService.update_status(session, connection_id, True)
            if not channel:
                return False

        # Step 2: Initialize connection in background (async, don't block API response)
        self._set_connection_runtime_status(connection_id, ConnectionStatus.CONNECTING)
        asyncio.create_task(self._background_initialize(user_id, channel_type, connection_id))
        return True

    async def _background_initialize(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> None:
        """Initialize connection in background. Status is tracked via handler._status.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
        """
        try:
            result = await self.initialize_connection(user_id, channel_type, connection_id)
            if not result:
                logger.warning(
                    f"Background connection initialization failed: "
                    f"{channel_type}/{connection_id}"
                )
        except Exception as e:
            logger.error(
                f"Background connection initialization error: "
                f"{channel_type}/{connection_id}: {e}"
            )

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
            if channel is not None:
                self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
            return channel is not None

    def get_connection_runtime_status(
        self,
        connection_id: str
    ) -> str:
        """Get runtime connection status for a specific connection.
        
        Searches _active_connections by connection_id suffix,
        since connection_id is globally unique (UUID).
        
        Args:
            connection_id: Connection identifier (UUID)
        
        Returns:
            Runtime status string: "connected", "disconnected", "connecting", or "error"
        """
        # Search for handler by connection_id (last part of instance_key)
        handler = None
        for key, h in self._active_connections.items():
            if key.endswith(f":{connection_id}"):
                handler = h
                break

        if not handler:
            cached_status = self._runtime_status_by_connection_id.get(
                connection_id,
                ConnectionStatus.DISCONNECTED,
            )
            return self._map_runtime_status(cached_status)

        try:
            status = handler.get_status()
            self._set_connection_runtime_status(connection_id, status)
            return self._map_runtime_status(status)
        except Exception:
            cached_status = self._runtime_status_by_connection_id.get(
                connection_id,
                ConnectionStatus.DISCONNECTED,
            )
            return self._map_runtime_status(cached_status)

    def list_active_connection_descriptors(self) -> list[dict[str, Any]]:
        """Return lightweight descriptors for all active connections."""
        items: list[dict[str, Any]] = []
        for key, handler in sorted(self._active_connections.items()):
            parts = key.split(":")
            if len(parts) != 3:
                continue
            user_id, channel_type, connection_id = parts
            items.append(
                {
                    "user_id": user_id,
                    "channel_type": channel_type,
                    "connection_id": connection_id,
                    "status": self.get_connection_runtime_status(connection_id),
                    "supports_long_connection": bool(getattr(handler, "supports_long_connection", False)),
                }
            )
        return items

    async def probe_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """Run a narrow health probe for an active connection."""
        instance_key = f"{user_id}:{channel_type}:{connection_id}"
        handler = self._active_connections.get(instance_key)
        if handler is None:
            return {"healthy": False, "status": "disconnected", "reconnected": False}

        healthy = await handler.health_check()
        return {
            "healthy": healthy,
            "status": self.get_connection_runtime_status(connection_id),
            "reconnected": False,
        }

    async def reconnect_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
    ) -> bool:
        """Attempt a best-effort reconnect for an active long connection."""
        instance_key = f"{user_id}:{channel_type}:{connection_id}"
        handler = self._active_connections.get(instance_key)
        if handler is None:
            return False
        if not getattr(handler, "supports_long_connection", False):
            return False
        try:
            return bool(await handler.reconnect())
        except Exception:
            logger.exception("Failed to reconnect channel connection: %s", instance_key)
            return False
