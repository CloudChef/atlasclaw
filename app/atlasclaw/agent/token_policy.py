# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Session-level dynamic token policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from threading import RLock
from typing import Optional

from app.atlasclaw.core.token_pool import TokenEntry, TokenPool


class DynamicTokenPolicy:
    """Select and pin token per session."""

    def __init__(
        self,
        token_pool: TokenPool,
        strategy: str = "health",
        primary_token_id: Optional[str] = None,
        token_loader: Optional[Callable[[], Awaitable[list[TokenEntry]]]] = None,
    ) -> None:
        self.token_pool = token_pool
        self.strategy = strategy
        self.primary_token_id = primary_token_id
        self._configured_primary_token_id = primary_token_id
        self._session_token_map: dict[str, str] = {}
        self._lock = RLock()
        self._token_loader = token_loader
        self._refresh_lock = asyncio.Lock()
        self._managed_credentials: dict[str, str] = {}

    async def refresh_tokens(self) -> None:
        """Reload committed configuration before selecting the next runtime agent."""
        if self._token_loader is None:
            return
        async with self._refresh_lock:
            entries = await self._token_loader()
            self.token_pool.replace_tokens(entries)
            if not entries:
                raise RuntimeError("No usable model credentials are configured")
            managed = [entry for entry in entries if entry.source_model_config_id]
            credentials = {entry.source_model_config_id: entry.credential_fingerprint for entry in managed}
            current = self.token_pool.tokens.get(self.primary_token_id)
            with self._lock:
                if managed:
                    if current is None or not current.source_model_config_id:
                        self.primary_token_id = managed[0].token_id
                    if credentials != self._managed_credentials:
                        # New credentials supersede defaults; preserve deliberate failover pins otherwise.
                        self._session_token_map = {
                            key: token_id for key, token_id in self._session_token_map.items()
                            if token_id in self.token_pool.tokens
                            and self.token_pool.tokens[token_id].source_model_config_id
                        }
                else:
                    self.primary_token_id = self._configured_primary_token_id
                self._managed_credentials = credentials

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
        candidate = self.select_for_session(session_key)
        if candidate is None:
            return None
        health = self.token_pool.get_token_health(candidate.token_id)
        if health and health.is_healthy:
            return candidate
        with self._lock:
            self._session_token_map.pop(session_key, None)
        return None

    def mark_session_token_unhealthy(
        self,
        session_key: str,
        *,
        reason: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        eligible_token_ids: Optional[set[str]] = None,
        expected_fingerprint: Optional[str] = None,
    ) -> Optional[TokenEntry]:
        """Mark the current session token unhealthy and rotate to the next candidate."""
        current = self.get_session_token(session_key)
        excluded_ids: set[str] = set()
        if current is not None:
            marked = self.token_pool.mark_token_unhealthy(
                current.token_id, reason=reason, expected_fingerprint=expected_fingerprint,
            )
            if marked:
                excluded_ids.add(current.token_id)
        with self._lock:
            self._session_token_map.pop(session_key, None)

        candidate = self.token_pool.select_token(
            provider=provider,
            model=model,
            strategy=self.strategy,
            exclude_token_ids=excluded_ids,
            eligible_token_ids=eligible_token_ids,
        )
        if candidate is None and provider:
            candidate = self.token_pool.select_token(
                provider=provider,
                model=None,
                strategy=self.strategy,
                exclude_token_ids=excluded_ids,
                eligible_token_ids=eligible_token_ids,
            )
        if candidate is None:
            candidate = self.token_pool.select_token(
                provider=None,
                model=None,
                strategy=self.strategy,
                exclude_token_ids=excluded_ids,
                eligible_token_ids=eligible_token_ids,
            )
        if candidate is None:
            return None
        with self._lock:
            self._session_token_map[session_key] = candidate.token_id
        return candidate

    def release_session_token(self, session_key: str) -> None:
        with self._lock:
            self._session_token_map.pop(session_key, None)
