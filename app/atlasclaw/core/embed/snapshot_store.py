# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Bounded in-process store for immutable embed context snapshots."""

from __future__ import annotations

import secrets
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass

from .models import ContextSnapshot


_LATEST_STATE_CAPACITY_MULTIPLIER = 4
_LATEST_STATE_MIN_CAPACITY = 32
_LATEST_STATE_DEFAULT_MAX_CONTEXTS = 128


@dataclass(frozen=True)
class _LatestSurfaceState:
    """Track one surface generation independently from snapshot retention."""

    generation: int
    context_id: str | None


class SnapshotNotFoundError(LookupError):
    """Raised when a snapshot is missing or belongs to another user."""


class SnapshotGenerationError(ValueError):
    """Raised when a request tries to bind a snapshot to another generation."""


class EmbedContextSnapshotStore:
    """Keep snapshots in one process with per-user capacity limits.

    This store deliberately has no distributed fallback. Deployments must prove
    single-process execution or sticky routing before enabling embedded Context.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._order: dict[str, deque[str]] = defaultdict(deque)
        self._latest: dict[tuple[str, str], _LatestSurfaceState] = {}
        self._latest_order: dict[str, OrderedDict[str, None]] = defaultdict(OrderedDict)

    @staticmethod
    def new_context_id() -> str:
        """Return an unpredictable identifier suitable for an untrusted client."""
        return f"ctx_{secrets.token_urlsafe(24)}"

    def put(self, snapshot: ContextSnapshot, *, max_contexts_per_user: int) -> bool:
        """Store a current completion, returning false after state loss or drift."""
        bucket_key = snapshot.owner_user_id
        latest_key = (bucket_key, snapshot.surface_id)
        current = self._latest.get(latest_key)
        if (
            current is None
            or snapshot.generation != current.generation
            or current.context_id is not None
        ):
            return False
        # put() is a single transition from the empty latest-generation marker
        # to its resolved snapshot. A late or duplicate completion cannot replace it.
        order = self._order[bucket_key]
        self._snapshots[snapshot.context_id] = snapshot.model_copy(deep=True)
        order.append(snapshot.context_id)
        self._latest[latest_key] = _LatestSurfaceState(
            generation=snapshot.generation,
            context_id=snapshot.context_id,
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
        generation: int,
    ) -> ContextSnapshot:
        """Return a matching immutable snapshot or a non-enumerating lookup error."""
        snapshot = self._snapshots.get(str(context_id or ""))
        if (
            snapshot is None
            or snapshot.owner_user_id != owner_user_id
        ):
            raise SnapshotNotFoundError("embed context snapshot not found")
        if snapshot.generation != generation:
            raise SnapshotGenerationError("embed context generation does not match snapshot")
        return snapshot.model_copy(deep=True)

    def mark_latest(
        self,
        *,
        owner_user_id: str,
        surface_id: str,
        generation: int,
        context_id: str | None,
        max_contexts_per_user: int = _LATEST_STATE_DEFAULT_MAX_CONTEXTS,
    ) -> bool:
        """Record one surface generation independently from any resolved snapshot.

        Repeating the current generation explicitly re-arms that surface with an
        empty marker. The first matching completion may then fill it; lower
        generations and completions whose state was evicted fail closed.
        """
        bucket_key = str(owner_user_id)
        latest_key = (bucket_key, str(surface_id))
        current = self._latest.get(latest_key)
        candidate_generation = int(generation)
        if current is not None and candidate_generation < current.generation:
            return False
        self._latest[latest_key] = _LatestSurfaceState(
            generation=candidate_generation,
            context_id=str(context_id) if context_id else None,
        )
        self._touch_latest(bucket_key, str(surface_id))
        self._trim_latest(bucket_key, max_contexts_per_user=max_contexts_per_user)
        return True

    def is_latest(
        self,
        context_id: str,
        *,
        owner_user_id: str,
        surface_id: str,
        generation: int,
    ) -> bool:
        """Return whether a context is latest on its originating Host surface."""
        latest_key = (
            str(owner_user_id),
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
        bucket_key = latest_key[0]
        self._touch_latest(bucket_key, latest_key[1])
        return True

    def _touch_latest(self, bucket_key: str, surface_id: str) -> None:
        """Move one surface to the most-recent end of its owner bucket."""
        order = self._latest_order[bucket_key]
        order.pop(surface_id, None)
        order[surface_id] = None

    def _trim_latest(
        self,
        bucket_key: str,
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
            self._latest.pop((bucket_key, surface_id), None)
        if not order:
            self._latest_order.pop(bucket_key, None)

    def _remove_latest(self, latest_key: tuple[str, str]) -> None:
        """Remove one latest state and compact its LRU bucket."""
        self._latest.pop(latest_key, None)
        bucket_key = latest_key[0]
        order = self._latest_order.get(bucket_key)
        if order is None:
            return
        order.pop(latest_key[1], None)
        if not order:
            self._latest_order.pop(bucket_key, None)
