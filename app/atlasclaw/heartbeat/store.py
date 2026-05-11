# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from pathlib import Path

from app.atlasclaw.core.user_paths import user_runtime_dir
from app.atlasclaw.heartbeat.models import (
    HeartbeatEventEnvelope,
    HeartbeatJobDefinition,
    HeartbeatJobStateSnapshot,
)


class HeartbeatStateStore:
    """Persist heartbeat jobs, state snapshots, and emitted events per user."""

    JOBS_FILE = "jobs.json"
    STATE_FILE = "state.json"
    EVENTS_FILE = "events.jsonl"

    def __init__(self, workspace_path: str):
        self.workspace_path = Path(workspace_path).resolve()

    def save_jobs(self, user_id: str, jobs: list[HeartbeatJobDefinition]) -> None:
        path = self._user_dir(user_id) / self.JOBS_FILE
        self._write_json(path, [job.to_dict() for job in jobs])

    def load_jobs(self, user_id: str) -> list[HeartbeatJobDefinition]:
        path = self._user_dir(user_id) / self.JOBS_FILE
        rows = self._read_json(path, default=[])
        return [HeartbeatJobDefinition.from_dict(row) for row in rows]

    def save_state(self, user_id: str, snapshots: list[HeartbeatJobStateSnapshot]) -> None:
        path = self._user_dir(user_id) / self.STATE_FILE
        self._write_json(path, [item.to_dict() for item in snapshots])

    def load_state(self, user_id: str) -> list[HeartbeatJobStateSnapshot]:
        path = self._user_dir(user_id) / self.STATE_FILE
        rows = self._read_json(path, default=[])
        return [HeartbeatJobStateSnapshot.from_dict(row) for row in rows]

    def append_event(self, user_id: str, event: HeartbeatEventEnvelope) -> None:
        path = self._user_dir(user_id) / self.EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def load_events(self, user_id: str) -> list[HeartbeatEventEnvelope]:
        path = self._user_dir(user_id) / self.EVENTS_FILE
        if not path.exists():
            return []
        events: list[HeartbeatEventEnvelope] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(HeartbeatEventEnvelope.from_dict(json.loads(line)))
        return events

    def _user_dir(self, user_id: str) -> Path:
        path = user_runtime_dir(self.workspace_path, user_id) / "heartbeat"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _read_json(self, path: Path, *, default: object) -> object:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
