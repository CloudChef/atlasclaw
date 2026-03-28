# -*- coding: utf-8 -*-
"""Agent instance pool keyed by `(agent_id, token_id)`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.xuanwu.core.token_pool import TokenEntry


@dataclass
class AgentInstance:
    """Runtime agent instance bound to one token."""

    agent_id: str
    token_id: str
    agent: Any
    concurrency_sem: asyncio.Semaphore
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def cache_key(self) -> str:
        return f"{self.agent_id}:{self.token_id}"


class AgentInstancePool:
    """Manage cached agent instances with per-instance concurrency control."""

    def __init__(self, max_concurrent_per_instance: int = 4) -> None:
        self._instances: dict[str, AgentInstance] = {}
        self._lock = asyncio.Lock()
        self._max_concurrent = max_concurrent_per_instance

    async def get_or_create(
        self,
        agent_id: str,
        token: TokenEntry,
        agent_factory: Callable[[str, TokenEntry], Awaitable[Any] | Any],
    ) -> AgentInstance:
        cache_key = f"{agent_id}:{token.token_id}"
        async with self._lock:
            existing = self._instances.get(cache_key)
            if existing is not None:
                return existing

            maybe_agent = agent_factory(agent_id, token)
            agent = await maybe_agent if asyncio.iscoroutine(maybe_agent) else maybe_agent
            instance = AgentInstance(
                agent_id=agent_id,
                token_id=token.token_id,
                agent=agent,
                concurrency_sem=asyncio.Semaphore(self._max_concurrent),
            )
            self._instances[cache_key] = instance
            return instance

    def get(self, agent_id: str, token_id: str) -> AgentInstance | None:
        return self._instances.get(f"{agent_id}:{token_id}")
