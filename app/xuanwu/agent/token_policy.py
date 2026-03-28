# -*- coding: utf-8 -*-
"""Session-level dynamic token policy."""

from __future__ import annotations

from threading import RLock
from typing import Optional

from app.xuanwu.core.token_pool import TokenEntry, TokenPool


class DynamicTokenPolicy:
    """Select and pin token per session."""

    def __init__(
        self,
        token_pool: TokenPool,
        strategy: str = "health",
        primary_token_id: Optional[str] = None,
    ) -> None:
        self.token_pool = token_pool
        self.strategy = strategy
        self.primary_token_id = primary_token_id
        self._session_token_map: dict[str, str] = {}
        self._lock = RLock()

    def select_for_session(
        self,
        session_key: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[TokenEntry]:
        # Try primary token first if configured and healthy
        if self.primary_token_id:
            primary_token = self.token_pool.tokens.get(self.primary_token_id)
            if primary_token:
                health = self.token_pool.get_token_health(self.primary_token_id)
                if health and health.is_healthy:
                    with self._lock:
                        self._session_token_map[session_key] = primary_token.token_id
                    return primary_token

        token = self.token_pool.select_token(provider=provider, model=model, strategy=self.strategy)
        if token is None:
            return None
        with self._lock:
            self._session_token_map[session_key] = token.token_id
        return token

    def get_session_token(self, session_key: str) -> Optional[TokenEntry]:
        with self._lock:
            token_id = self._session_token_map.get(session_key)
        if not token_id:
            return None
        return self.token_pool.tokens.get(token_id)

    def get_or_select_session_token(
        self,
        session_key: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[TokenEntry]:
        token = self.get_session_token(session_key)
        if token:
            return token
        return self.select_for_session(session_key, provider=provider, model=model)

    def refresh_session_token(self, session_key: str) -> Optional[TokenEntry]:
        current = self.get_session_token(session_key)
        if current:
            health = self.token_pool.get_token_health(current.token_id)
            if health and health.is_healthy:
                return current
        with self._lock:
            self._session_token_map.pop(session_key, None)
        return self.select_for_session(session_key)

    def release_session_token(self, session_key: str) -> None:
        with self._lock:
            self._session_token_map.pop(session_key, None)
