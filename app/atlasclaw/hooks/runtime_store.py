# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiofiles

from app.atlasclaw.core.user_paths import user_runtime_dir
from app.atlasclaw.hooks.runtime_models import (
    HookDecision,
    HookContextInjection,
    HookDecisionRecord,
    HookEventEnvelope,
    PendingHookItem,
    PendingHookStatus,
)


class HookStateStore:
    """Persist generic hook state under per-user hook module directories."""

    EVENTS_FILE = "events.jsonl"
    PENDING_FILE = "pending.jsonl"
    DECISIONS_FILE = "decisions.jsonl"
    CONTEXT_FILE = "context.jsonl"

    def __init__(self, workspace_path: str):
        self.workspace_path = Path(workspace_path).resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    async def append_event(self, module_name: str, event: HookEventEnvelope) -> None:
        """Append a runtime event for a hook module and user."""
        path = self._module_dir(module_name, event.user_id) / self.EVENTS_FILE
        async with await self._get_lock(module_name, event.user_id):
            await self._append_jsonl(path, event.to_dict())

    async def list_events(
        self,
        module_name: str,
        user_id: str,
        *,
        limit: Optional[int] = None,
    ) -> list[HookEventEnvelope]:
        """Return stored events for a module and user, oldest-first."""
        path = self._module_dir(module_name, user_id) / self.EVENTS_FILE
        items = [HookEventEnvelope.from_dict(record) for record in await self._read_jsonl(path)]
        if limit is not None and limit >= 0:
            return items[-limit:]
        return items

    async def append_pending(self, module_name: str, item: PendingHookItem) -> None:
        """Append a pending item snapshot for a hook module."""
        path = self._module_dir(module_name, item.user_id) / self.PENDING_FILE
        snapshot = PendingHookItem(
            id=item.id,
            module_name=module_name,
            user_id=item.user_id,
            source_event_ids=list(item.source_event_ids),
            summary=item.summary,
            payload=dict(item.payload),
            status=item.status,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        async with await self._get_lock(module_name, item.user_id):
            await self._append_jsonl(path, snapshot.to_dict())

    async def list_pending(self, module_name: str, user_id: str) -> list[PendingHookItem]:
        """Return active pending items for a module and user."""
        latest_items = await self.list_items(module_name, user_id)
        active = [
            item for item in latest_items
            if item.status == PendingHookStatus.PENDING
        ]
        active.sort(key=lambda item: item.updated_at)
        return active

    async def list_items(self, module_name: str, user_id: str) -> list[PendingHookItem]:
        """Return latest snapshots for all tracked pending items."""
        latest_by_id = await self._load_pending_index(module_name, user_id)
        items = list(latest_by_id.values())
        items.sort(key=lambda item: item.updated_at)
        return items

    async def list_confirmed(self, module_name: str, user_id: str) -> list[PendingHookItem]:
        """Return latest snapshots that have been confirmed."""
        items = await self.list_items(module_name, user_id)
        return [item for item in items if item.status == PendingHookStatus.CONFIRMED]

    async def resolve_pending(
        self,
        *,
        module_name: str,
        user_id: str,
        pending_id: str,
        decision: str,
        decided_by: str,
        note: str = "",
    ) -> PendingHookItem:
        """Resolve a pending item and append a matching decision record."""
        hook_decision = HookDecision(decision)
        latest_by_id = await self._load_pending_index(module_name, user_id)
        if pending_id not in latest_by_id:
            raise KeyError(f"Pending item not found: {pending_id}")

        current = latest_by_id[pending_id]
        if current.status != PendingHookStatus.PENDING:
            raise ValueError(f"Pending item already resolved: {pending_id}")

        updated = PendingHookItem(
            id=current.id,
            module_name=current.module_name,
            user_id=current.user_id,
            source_event_ids=list(current.source_event_ids),
            summary=current.summary,
            payload=dict(current.payload),
            status=(
                PendingHookStatus.CONFIRMED
                if hook_decision == HookDecision.CONFIRM
                else PendingHookStatus.REJECTED
            ),
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        record = HookDecisionRecord(
            id=f"decision-{uuid4().hex}",
            module_name=module_name,
            user_id=user_id,
            pending_id=pending_id,
            decision=hook_decision,
            decided_by=decided_by,
            decided_at=updated.updated_at,
            note=note,
            payload={"source_event_ids": list(current.source_event_ids)},
        )
        module_dir = self._module_dir(module_name, user_id)
        async with await self._get_lock(module_name, user_id):
            await self._append_jsonl(module_dir / self.PENDING_FILE, updated.to_dict())
            await self._append_jsonl(module_dir / self.DECISIONS_FILE, record.to_dict())
        return updated

    async def list_decisions(self, module_name: str, user_id: str) -> list[HookDecisionRecord]:
        """Return decision audit records for a module and user."""
        path = self._module_dir(module_name, user_id) / self.DECISIONS_FILE
        items = [HookDecisionRecord.from_dict(record) for record in await self._read_jsonl(path)]
        items.sort(key=lambda item: item.decided_at)
        return items

    async def append_context_item(self, module_name: str, item: HookContextInjection) -> None:
        """Append a context injection record for a module and user."""
        path = self._module_dir(module_name, item.user_id) / self.CONTEXT_FILE
        async with await self._get_lock(module_name, item.user_id):
            await self._append_jsonl(path, item.to_dict())

    async def list_context_items(
        self,
        module_name: str,
        user_id: str,
        *,
        limit: Optional[int] = None,
    ) -> list[HookContextInjection]:
        """Return stored context injections for a module and user."""
        path = self._module_dir(module_name, user_id) / self.CONTEXT_FILE
        items = [HookContextInjection.from_dict(item) for item in await self._read_jsonl(path)]
        items.sort(key=lambda item: item.confirmed_at or datetime.min.replace(tzinfo=timezone.utc))
        if limit is not None and limit >= 0:
            return items[-limit:]
        return items

    async def get_pending(self, module_name: str, user_id: str, pending_id: str) -> Optional[PendingHookItem]:
        """Return the latest snapshot for a pending item regardless of state."""
        latest_by_id = await self._load_pending_index(module_name, user_id)
        return latest_by_id.get(pending_id)

    def _module_dir(self, module_name: str, user_id: str) -> Path:
        module_dir = (
            user_runtime_dir(self.workspace_path, user_id)
            / "hooks"
            / module_name
        )
        module_dir.mkdir(parents=True, exist_ok=True)
        return module_dir

    async def _get_lock(self, module_name: str, user_id: str) -> asyncio.Lock:
        key = f"{user_id}:{module_name}"
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _load_pending_index(
        self,
        module_name: str,
        user_id: str,
    ) -> dict[str, PendingHookItem]:
        path = self._module_dir(module_name, user_id) / self.PENDING_FILE
        records = [PendingHookItem.from_dict(item) for item in await self._read_jsonl(path)]
        latest_by_id: dict[str, PendingHookItem] = {}
        for record in records:
            previous = latest_by_id.get(record.id)
            if previous is None or previous.updated_at <= record.updated_at:
                latest_by_id[record.id] = record
        return latest_by_id

    async def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        async with aiofiles.open(path, "r", encoding="utf-8") as handle:
            async for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    async def _append_jsonl(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "a", encoding="utf-8") as handle:
            await handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
