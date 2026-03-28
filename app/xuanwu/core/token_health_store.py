# -*- coding: utf-8 -*-
"""Persistence for token health snapshots."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from app.xuanwu.core.token_pool import TokenHealth

logger = logging.getLogger(__name__)


class TokenHealthStore:
    """Persist token health snapshots to `<workspace>/token_health.json`."""

    def __init__(self, workspace_path: str) -> None:
        self.file_path = Path(workspace_path) / "token_health.json"

    def save(self, health_status: dict[str, TokenHealth]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            token_id: {
                "remaining_tokens": health.remaining_tokens,
                "remaining_requests": health.remaining_requests,
                "reset_tokens_seconds": health.reset_tokens_seconds,
                "reset_requests_seconds": health.reset_requests_seconds,
                "updated_at": health.updated_at.isoformat(),
            }
            for token_id, health in health_status.items()
        }
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict[str, TokenHealth]:
        if not self.file_path.exists():
            return {}
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load token health file %s: %s", self.file_path, exc)
            return {}

        result: dict[str, TokenHealth] = {}
        for token_id, value in raw.items():
            try:
                result[token_id] = TokenHealth(
                    remaining_tokens=int(value.get("remaining_tokens", 100000)),
                    remaining_requests=int(value.get("remaining_requests", 100)),
                    reset_tokens_seconds=int(value.get("reset_tokens_seconds", 0)),
                    reset_requests_seconds=int(value.get("reset_requests_seconds", 0)),
                    updated_at=datetime.fromisoformat(value.get("updated_at"))
                    if value.get("updated_at")
                    else datetime.now(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skip invalid token health record for %s: %s", token_id, exc)
        return result
