"""Serialized execution queue for session-scoped runs.

This queue ensures that each session key is processed serially while still
allowing bounded concurrency across independent sessions.

Queue modes:
- `collect`: batch queued messages into the next run
- `steer`: inject steering messages into an active run
- `followup`: schedule a follow-up run after the current run finishes
- `steer-backlog`: preserve steering messages for later injection
- `interrupt`: interrupt the current run

Key settings:
- `debounce_ms`: debounce delay before starting the next run
- `cap`: maximum number of queued messages per session
- `drop`: overflow strategy (`old`, `new`, or `summarize`)
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class QueueMode(Enum):
    """Queue behavior for messages that arrive during active processing."""

    COLLECT = "collect"           # Buffer messages for the next combined run.
    STEER = "steer"               # Inject steering content into the active run.
    FOLLOWUP = "followup"         # Trigger a follow-up run after completion.
    STEER_BACKLOG = "steer-backlog"  # Keep steering messages in a backlog.
    INTERRUPT = "interrupt"       # Interrupt the active run.


class DropStrategy(Enum):
    """Overflow handling strategy for full queues."""

    OLD = "old"         # Drop the oldest queued message.
    NEW = "new"         # Reject the incoming message.
    SUMMARIZE = "summarize"  # Placeholder for future summarization behavior.


@dataclass
class QueuedMessage:
    """A message waiting to be processed for a session."""
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class SessionQueue:
    """Coordinate serialized execution for session runs.

    The queue enforces one active run per session while supporting multiple
    queueing behaviors for concurrent incoming messages.

    Example:
        ```python
        queue = SessionQueue(max_concurrent=10, debounce_ms=1000)

        await queue.acquire(session_key)
        try:
            ...
        finally:
            queue.release(session_key)

        queue.enqueue(session_key, "new message")
        messages = queue.get_queued_messages(session_key)
        ```
    """
    
    def __init__(
        self,
        max_concurrent: int = 10,
        debounce_ms: int = 1000,
        cap: int = 20,
        mode: QueueMode = QueueMode.COLLECT,
        drop: DropStrategy = DropStrategy.OLD,
    ):
        """Initialize the session queue.

        Args:
            max_concurrent: Maximum number of sessions that may run concurrently.
            debounce_ms: Debounce delay before processing queued messages.
            cap: Maximum queued message count per session.
            mode: Default queue mode.
            drop: Overflow strategy when the queue reaches `cap`.
        """
        self.max_concurrent = max_concurrent
        self.debounce_ms = debounce_ms
        self.cap = cap
        self.mode = mode
        self.drop = drop
        
        # Per-session serialization locks.
        self._locks: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))
        # Global concurrency limit across sessions.
        self._global_semaphore = asyncio.Semaphore(max_concurrent)
        # Queued messages by session key.
        self._queued: dict[str, list[QueuedMessage]] = defaultdict(list)
        # Whether a session currently has an active run.
        self._active: dict[str, bool] = {}
        # Timestamp of the most recent queued message per session.
        self._last_message_time: dict[str, float] = {}
        # Optional mode overrides scoped to individual sessions.
        self._session_modes: dict[str, QueueMode] = {}
        # Optional mode overrides scoped to channels.
        self._channel_modes: dict[str, QueueMode] = {}
    
    async def acquire(self, session_key: str) -> bool:
        """Acquire execution slots for a session run."""
        # Acquire the global slot first.
        await self._global_semaphore.acquire()
        # Then acquire the session-local serialization slot.
        await self._locks[session_key].acquire()
        self._active[session_key] = True
        return True
    
    def release(self, session_key: str) -> None:
        """Release execution slots after a session run completes."""
        self._active[session_key] = False
        self._locks[session_key].release()
        self._global_semaphore.release()
    
    def is_active(self, session_key: str) -> bool:
        """Return whether a session currently has an active run."""
        return self._active.get(session_key, False)
    
    def enqueue(
        self,
        session_key: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Enqueue a message for later processing.

        Args:
            session_key: Serialized session key.
            content: Message content to queue.
            metadata: Optional message metadata.

        Returns:
            `True` if the message was queued, or `False` if it was dropped.
        """
        queue = self._queued[session_key]
        
        # Apply the configured overflow strategy when the queue is full.
        if len(queue) >= self.cap:
            match self.drop:
                case DropStrategy.OLD:
                    queue.pop(0)  # Drop the oldest queued message.
                case DropStrategy.NEW:
                    return False  # Reject the incoming message.
                case DropStrategy.SUMMARIZE:
                    # TODO: Replace the oldest entries with a summary message.
                    queue.pop(0)
        
        message = QueuedMessage(
            content=content,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        queue.append(message)
        self._last_message_time[session_key] = message.timestamp
        return True
    
    def get_queued_messages(self, session_key: str, clear: bool = True) -> list[str]:
        """Return queued message contents for a session.

        Args:
            session_key: Serialized session key.
            clear: Whether to clear the queue after reading it.

        Returns:
            Queued message contents in FIFO order.
        """
        queue = self._queued.get(session_key, [])
        contents = [msg.content for msg in queue]
        if clear:
            self._queued[session_key] = []
        return contents
    
    def get_steer_messages(self, session_key: str) -> list[str]:
        """Return queued steering messages for active-run injection."""
        return self.get_queued_messages(session_key, clear=True)
    
    def get_mode(self, session_key: str, channel: Optional[str] = None) -> QueueMode:
        """Resolve the effective queue mode for a session.

        Session overrides take precedence over channel overrides, which take
        precedence over the default queue mode.
        """
        # Priority: session override > channel override > default mode.
        if session_key in self._session_modes:
            return self._session_modes[session_key]
        if channel and channel in self._channel_modes:
            return self._channel_modes[channel]
        return self.mode
    
    def set_session_mode(self, session_key: str, mode: QueueMode) -> None:
        """Set a queue mode override for a specific session."""
        self._session_modes[session_key] = mode
    
    def set_channel_mode(self, channel: str, mode: QueueMode) -> None:
        """Set a queue mode override for all sessions in a channel."""
        self._channel_modes[channel] = mode
    
    def clear_session_mode(self, session_key: str) -> None:
        """Remove a session-specific queue mode override."""
        self._session_modes.pop(session_key, None)
    
    async def wait_debounce(self, session_key: str) -> bool:
        """Wait for the debounce window and detect newer arrivals.

        Returns:
            `True` when no newer message arrived during the debounce period.
        """
        if self.debounce_ms <= 0:
            return True
        
        start_time = self._last_message_time.get(session_key, 0)
        await asyncio.sleep(self.debounce_ms / 1000.0)
        
        # If a newer timestamp exists, another message arrived during debounce.
        current_time = self._last_message_time.get(session_key, 0)
        return current_time <= start_time
    
    def queue_size(self, session_key: str) -> int:
        """Return the queued message count for a session."""
        return len(self._queued.get(session_key, []))
    
    def clear_queue(self, session_key: str) -> None:
        """Remove all queued messages for a session."""
        self._queued[session_key] = []
    
    def get_stats(self) -> dict:
        """Return aggregate queue statistics."""
        active_count = sum(1 for v in self._active.values() if v)
        total_queued = sum(len(q) for q in self._queued.values())
        return {
            "active_sessions": active_count,
            "total_queued_messages": total_queued,
            "max_concurrent": self.max_concurrent,
            "sessions_with_queue": len([k for k, v in self._queued.items() if v]),
        }
