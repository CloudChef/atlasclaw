# -*- coding: utf-8 -*-
"""Token pool and token health management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Optional


@dataclass
class TokenEntry:
    """Single token endpoint entry."""

    token_id: str
    provider: str
    model: str
    base_url: str
    api_key: str
    api_type: str = "openai"
    priority: int = 0
    weight: int = 100
    context_window: Optional[int] = None


@dataclass
class TokenHealth:
    """Health snapshot for one token."""

    remaining_tokens: int = 100000
    remaining_requests: int = 100
    reset_tokens_seconds: int = 0
    reset_requests_seconds: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def health_score(self) -> float:
        """Compute health score in range 0~100."""
        req_score = min(100.0, float(self.remaining_requests) * 10.0)
        tok_score = min(100.0, float(self.remaining_tokens) / 1000.0)
        return req_score * 0.6 + tok_score * 0.4

    @property
    def is_healthy(self) -> bool:
        return self.remaining_requests > 0 and self.remaining_tokens > 100


class TokenPool:
    """Thread-safe token pool with health-aware selection."""

    def __init__(self) -> None:
        self.tokens: dict[str, TokenEntry] = {}
        self.health_status: dict[str, TokenHealth] = {}
        self._lock = RLock()

    def register_token(self, token: TokenEntry) -> None:
        with self._lock:
            self.tokens[token.token_id] = token
            if token.token_id not in self.health_status:
                self.health_status[token.token_id] = TokenHealth()

    def restore_health(self, token_id: str, health: TokenHealth) -> None:
        with self._lock:
            if token_id in self.tokens:
                self.health_status[token_id] = health

    def update_token_health(self, token_id: str, headers: dict[str, str]) -> None:
        with self._lock:
            if token_id not in self.tokens:
                return

            def _to_int(name: str, default: int) -> int:
                raw = headers.get(name) or headers.get(name.lower())
                if raw is None:
                    return default
                try:
                    return int(str(raw).strip())
                except (TypeError, ValueError):
                    return default

            prev = self.health_status.get(token_id, TokenHealth())
            self.health_status[token_id] = TokenHealth(
                remaining_tokens=_to_int("x-ratelimit-remaining-tokens", prev.remaining_tokens),
                remaining_requests=_to_int("x-ratelimit-remaining-requests", prev.remaining_requests),
                reset_tokens_seconds=_to_int("x-ratelimit-reset-tokens", prev.reset_tokens_seconds),
                reset_requests_seconds=_to_int("x-ratelimit-reset-requests", prev.reset_requests_seconds),
            )

    def select_token(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        *,
        strategy: str = "health",
    ) -> Optional[TokenEntry]:
        with self._lock:
            candidates: list[tuple[TokenEntry, TokenHealth]] = []
            for token_id, token in self.tokens.items():
                if provider and token.provider != provider:
                    continue
                if model and token.model != model:
                    continue
                health = self.health_status.get(token_id, TokenHealth())
                candidates.append((token, health))

            if not candidates:
                return None

            if strategy == "random":
                candidates.sort(key=lambda item: (item[0].priority, item[0].weight), reverse=True)
                return candidates[0][0]

            if strategy == "round_robin":
                candidates.sort(key=lambda item: (item[0].priority, item[0].weight, item[1].updated_at.timestamp()), reverse=True)
                return candidates[0][0]

            # default: health
            candidates.sort(
                key=lambda item: (
                    item[1].is_healthy,
                    item[1].health_score,
                    item[0].priority,
                    item[0].weight,
                ),
                reverse=True,
            )
            return candidates[0][0]

    def get_token_health(self, token_id: str) -> Optional[TokenHealth]:
        with self._lock:
            return self.health_status.get(token_id)

    def export_health_status(self) -> dict[str, TokenHealth]:
        with self._lock:
            return dict(self.health_status)
