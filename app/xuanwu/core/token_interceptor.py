# -*- coding: utf-8 -*-
"""Token health interceptor for response headers."""

from __future__ import annotations

from app.xuanwu.core.token_health_store import TokenHealthStore
from app.xuanwu.core.token_pool import TokenPool


class TokenHealthInterceptor:
    """Extract rate-limit headers and update token health state."""

    def __init__(self, token_pool: TokenPool, health_store: TokenHealthStore) -> None:
        self.token_pool = token_pool
        self.health_store = health_store

    def on_response(self, token_id: str, headers: dict[str, str]) -> None:
        lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if not any(k.startswith("x-ratelimit-") for k in lowered.keys()):
            return
        self.token_pool.update_token_health(token_id, lowered)
        self.health_store.save(self.token_pool.export_health_status())
