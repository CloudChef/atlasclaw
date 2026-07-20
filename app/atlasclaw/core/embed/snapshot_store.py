# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Bounded in-process store for immutable embed context snapshots."""

from __future__ import annotations

import secrets
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import ContextSnapshot


_LATEST_STATE_CAPACITY_MULTIPLIER = 4
_LATEST_STATE_MIN_CAPACITY = 32
_LATEST_STATE_DEFAULT_TTL_SECONDS = 1800
_LATEST_STATE_DEFAULT_MAX_CONTEXTS = 128


@dataclass(frozen=True)
class _LatestSurfaceState:
    """Track one surface generation independently from snapshot retention."""

    generation: int
    context_id: str | None
    expires_at: datetime
    touched_at: datetime


class SnapshotNotFoundError(LookupError):
    """Raised when a snapshot is missing or belongs to another user/integration."""


class SnapshotExpiredError(LookupError):
    """Raised when a matching snapshot has exceeded its configured TTL."""


class SnapshotGenerationError(ValueError):
    """Raised when a request tries to bind a snapshot to another generation."""


class EmbedContextSnapshotStore:
    """Keep short-lived snapshots in one process with per-user/profile capacity limits.

    This store deliberately has no distributed fallback. Deployments must prove
    single-process execution or sticky routing before enabling an integration.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._order: dict[tuple[str, str], deque[str]] = defaultdict(deque)
        self._latest: dict[tuple[str, str, str], _LatestSurfaceState] = {}
        self._latest_order: dict[
            tuple[str, str], OrderedDict[str, None]
        ] = defaultdict(OrderedDict)

    @staticmethod
    def new_context_id() -> str:
        """Return an unpredictable identifier suitable for an untrusted client."""
        return f"ctx_{secrets.token_urlsafe(24)}"

    def put(self, snapshot: ContextSnapshot, *, max_contexts_per_user: int) -> bool:
        """Store a current completion, returning false after state loss or drift."""
        now = datetime.now(timezone.utc)
        self._purge_expired(now)
        bucket_key = (snapshot.owner_user_id, snapshot.integration_id)
        latest_key = (*bucket_key, snapshot.surface_id)
        current = self._latest.get(latest_key)
        if (
            current is None
            or snapshot.generation != current.generation
            or current.context_id is not None
        ):
            return False
        order = self._order[bucket_key]
        self._snapshots[snapshot.context_id] = snapshot.model_copy(deep=True)
        order.append(snapshot.context_id)
        self._latest[latest_key] = _LatestSurfaceState(
            generation=snapshot.generation,
            context_id=snapshot.context_id,
            expires_at=snapshot.expires_at,
            touched_at=now,
        )
        self._touch_latest(bucket_key, snapshot.surface_id)
        self._trim_latest(bucket_key, max_contexts_per_user=max_contexts_per_user)
        while len(order) > max_contexts_per_user:
            self._snapshots.pop(order.popleft(), None)
        return True

    def get(
        self,
        context_id: str,
        *,
        owner_user_id: str,
        integration_id: str,
        generation: int,
    ) -> ContextSnapshot:
        """Return a matching immutable snapshot or a non-enumerating lookup error."""
        snapshot = self._snapshots.get(str(context_id or ""))
        if (
            snapshot is None
            or snapshot.owner_user_id != owner_user_id
            or snapshot.integration_id != integration_id
        ):
            raise SnapshotNotFoundError("embed context snapshot not found")
        now = datetime.now(timezone.utc)
        if snapshot.expires_at <= now:
            self._remove(snapshot)
            self._purge_expired(now)
            raise SnapshotExpiredError("embed context snapshot expired")
        self._purge_expired(now)
        if snapshot.generation != generation:
            raise SnapshotGenerationError("embed context generation does not match snapshot")
        return snapshot.model_copy(deep=True)

    def mark_latest(
        self,
        *,
        owner_user_id: str,
        integration_id: str,
        surface_id: str,
        generation: int,
        context_id: str | None,
        max_contexts_per_user: int = _LATEST_STATE_DEFAULT_MAX_CONTEXTS,
        state_ttl_seconds: int = _LATEST_STATE_DEFAULT_TTL_SECONDS,
    ) -> bool:
        """Record one surface generation independently from any resolved snapshot.

        Repeating the current generation explicitly re-arms that surface with an
        empty marker. The first matching completion may then fill it; lower
        generations and completions whose state was expired or evicted fail closed.
        """
        now = datetime.now(timezone.utc)
        self._purge_expired(now)
        bucket_key = (str(owner_user_id), str(integration_id))
        latest_key = (*bucket_key, str(surface_id))
        current = self._latest.get(latest_key)
        candidate_generation = int(generation)
        if current is not None and candidate_generation < current.generation:
            return False
        ttl_seconds = max(1, int(state_ttl_seconds))
        self._latest[latest_key] = _LatestSurfaceState(
            generation=candidate_generation,
            context_id=str(context_id) if context_id else None,
            expires_at=now + timedelta(seconds=ttl_seconds),
            touched_at=now,
        )
        self._touch_latest(bucket_key, str(surface_id))
        self._trim_latest(bucket_key, max_contexts_per_user=max_contexts_per_user)
        return True

    def is_latest(
        self,
        context_id: str,
        *,
        owner_user_id: str,
        integration_id: str,
        surface_id: str,
        generation: int,
    ) -> bool:
        """Return whether a context is latest on its originating Host surface."""
        self._purge_expired(datetime.now(timezone.utc))
        latest_key = (
            str(owner_user_id),
            str(integration_id),
            str(surface_id),
        )
        state = self._latest.get(latest_key)
        if (
            state is None
            or state.generation != int(generation)
            or state.context_id != str(context_id)
            or str(context_id) not in self._snapshots
        ):
            return False
        bucket_key = latest_key[:2]
        self._latest[latest_key] = _LatestSurfaceState(
            generation=state.generation,
            context_id=state.context_id,
            expires_at=state.expires_at,
            touched_at=datetime.now(timezone.utc),
        )
        self._touch_latest(bucket_key, latest_key[2])
        return True

    def _purge_expired(self, now: datetime) -> None:
        """Remove expired snapshots and latest states across all buckets."""
        for snapshot in list(self._snapshots.values()):
            if snapshot.expires_at <= now:
                self._remove(snapshot)
        for latest_key, state in list(self._latest.items()):
            if state.expires_at <= now:
                self._remove_latest(latest_key)

    def _remove(self, snapshot: ContextSnapshot) -> None:
        """Remove one snapshot and compact its per-user ordering bucket."""
        self._snapshots.pop(snapshot.context_id, None)
        bucket_key = (snapshot.owner_user_id, snapshot.integration_id)
        order = self._order.get(bucket_key)
        if order is None:
            return
        self._order[bucket_key] = deque(
            item for item in order if item != snapshot.context_id and item in self._snapshots
        )
        if not self._order[bucket_key]:
            self._order.pop(bucket_key, None)

    def _touch_latest(self, bucket_key: tuple[str, str], surface_id: str) -> None:
        """Move one surface to the most-recent end of its owner bucket."""
        order = self._latest_order[bucket_key]
        order.pop(surface_id, None)
        order[surface_id] = None

    def _trim_latest(
        self,
        bucket_key: tuple[str, str],
        *,
        max_contexts_per_user: int,
    ) -> None:
        """Bound per-owner surface states without changing snapshot capacity."""
        capacity = max(
            _LATEST_STATE_MIN_CAPACITY,
            int(max_contexts_per_user) * _LATEST_STATE_CAPACITY_MULTIPLIER,
        )
        order = self._latest_order[bucket_key]
        while len(order) > capacity:
            surface_id, _ = order.popitem(last=False)
            self._latest.pop((*bucket_key, surface_id), None)
        if not order:
            self._latest_order.pop(bucket_key, None)

    def _remove_latest(self, latest_key: tuple[str, str, str]) -> None:
        """Remove one latest state and compact its LRU bucket."""
        self._latest.pop(latest_key, None)
        bucket_key = latest_key[:2]
        order = self._latest_order.get(bucket_key)
        if order is None:
            return
        order.pop(latest_key[2], None)
        if not order:
            self._latest_order.pop(bucket_key, None)
