# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.atlasclaw.core.user_paths import user_runtime_dir
from app.atlasclaw.hooks.runtime_models import HookContextInjection, HookWriteMemoryRequest
from app.atlasclaw.hooks.runtime_store import HookStateStore


@dataclass
class HookMemoryWriteResult:
    """Result of promoting a confirmed hook item into long-term memory."""

    path: Path
    content: str


class MemorySink:
    """Generic sink that writes confirmed hook content into timestamped memory files."""

    def __init__(self, workspace_path: str):
        self.workspace_path = Path(workspace_path).resolve()

    async def write_confirmed(self, request: HookWriteMemoryRequest) -> HookMemoryWriteResult:
        """Persist a confirmed item into `workspace/users/<user_id>/memory/memory_<timestamp>.md`."""
        user_memory_dir = user_runtime_dir(self.workspace_path, request.user_id) / "memory"
        timestamp = datetime.now(timezone.utc)
        path = user_memory_dir / f"memory_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.md"
        lines = [
            "# Hook Memory Promotion",
            "",
            f"- timestamp_utc: {timestamp.isoformat()}",
            f"- module_name: {request.module_name}",
            f"- user_id: {request.user_id}",
        ]
        if request.source_event_ids:
            lines.append(f"- source_event_ids: {', '.join(request.source_event_ids)}")
        if request.metadata:
            for key, value in sorted(request.metadata.items()):
                lines.append(f"- {key}: {value}")
        lines.extend(
            [
                "",
                f"## {request.title.strip() or 'Memory Entry'}",
                "",
                request.body.strip(),
                "",
            ]
        )
        payload = "\n".join(lines)

        def _write() -> None:
            user_memory_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write)
        return HookMemoryWriteResult(path=path, content=payload)


class ContextSink:
    """Expose only confirmed hook items for prompt/context injection consumers."""

    def __init__(self, store: HookStateStore):
        self.store = store

    async def add_injection(
        self,
        *,
        module_name: str,
        user_id: str,
        summary: str,
        payload: Optional[dict] = None,
        source_event_ids: Optional[list[str]] = None,
    ) -> HookContextInjection:
        """Persist a generic context injection for later recall."""
        item = HookContextInjection(
            module_name=module_name,
            user_id=user_id,
            summary=summary,
            payload=dict(payload or {}),
            source_event_ids=list(source_event_ids or []),
            confirmed_at=datetime.now(timezone.utc),
        )
        await self.store.append_context_item(module_name, item)
        return item

    async def list_confirmed(
        self,
        module_name: str,
        user_id: str,
        *,
        limit: Optional[int] = None,
    ) -> list[HookContextInjection]:
        """Return confirmed items as generic context injections."""
        confirmed = [
            HookContextInjection(
                module_name=item.module_name,
                user_id=item.user_id,
                summary=item.summary,
                payload=dict(item.payload),
                source_event_ids=list(item.source_event_ids),
                confirmed_at=item.updated_at,
            )
            for item in await self.store.list_confirmed(module_name, user_id)
        ]
        explicit_context = await self.store.list_context_items(module_name, user_id)
        combined = confirmed + explicit_context
        combined.sort(key=lambda item: item.confirmed_at or datetime.min.replace(tzinfo=timezone.utc))
        if limit is not None and limit >= 0:
            combined = combined[-limit:]
        return combined
