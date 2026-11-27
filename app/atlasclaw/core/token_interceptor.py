# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Token health interceptor for response headers."""

from __future__ import annotations

from app.atlasclaw.core.token_health_store import TokenHealthStore
from app.atlasclaw.core.token_pool import TokenPool


class TokenHealthInterceptor:
    """Extract rate-limit headers and update token health state."""

    def __init__(self, token_pool: TokenPool, health_store: TokenHealthStore) -> None:
        self.token_pool = token_pool
        self.health_store = health_store

    def on_response(self, token_id: str, headers: dict[str, str], *, expected_fingerprint: str | None = None) -> None:
        lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if not any(k.startswith("x-ratelimit-") for k in lowered.keys()):
            return
        if self.token_pool.update_token_health(token_id, lowered, expected_fingerprint=expected_fingerprint):
            self.health_store.save(self.token_pool.export_health_status())

    def on_hard_failure(self, token_id: str, error: str, *, expected_fingerprint: str | None = None) -> None:
        """Persist a provider-level hard failure so selection can fail over quickly."""
        if self.token_pool.mark_token_unhealthy(token_id, reason=error, expected_fingerprint=expected_fingerprint):
            self.health_store.save(self.token_pool.export_health_status())
