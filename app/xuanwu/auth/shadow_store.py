"""
ShadowUserStore — persists the external-identity → internal-user mapping.

Storage: <workspace>/users.json
Concurrency: asyncio.Lock guarantees idempotent writes under concurrent load.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles

from app.xuanwu.auth.models import AuthResult, ShadowUser

logger = logging.getLogger(__name__)


class ShadowUserStore:
    """
    Thread-safe (asyncio) store for ShadowUser records.

    Index key format: ``"{provider}:{subject}"``
    """

    def __init__(
        self,
        store_path: Optional[str] = None,
        workspace_path: str = ".",
    ) -> None:
        self._workspace_path = Path(workspace_path).resolve()
        default_store_path = self._workspace_path / "users.json"
        self._path = Path(store_path).expanduser().resolve() if store_path else default_store_path
        self._lock = asyncio.Lock()
        self._users: dict[str, ShadowUser] = {}    # user_id -> ShadowUser
        self._index: dict[str, str] = {}           # "provider:subject" -> user_id
        self._loaded = False

    @property
    def workspace_path(self) -> Path:
        return self._workspace_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create(
        self,
        provider: str,
        result: AuthResult,
    ) -> ShadowUser:
        """
        Look up or create a ShadowUser for the given ``provider:subject`` pair.

        - Hit: updates ``last_seen_at`` and returns the existing record.
        - Miss: creates a new record with a fresh UUID and persists it.
        """
        await self._ensure_loaded()

        index_key = f"{provider}:{result.subject}"

        # --- fast path (no lock needed for read) -----------------------
        if index_key in self._index:
            user = self._users[self._index[index_key]]
            user.last_seen_at = datetime.now(timezone.utc)
            await self._save()
            return user

        # --- slow path (must hold lock to avoid duplicate creation) ----
        async with self._lock:
            # Double-check after acquiring the lock
            if index_key in self._index:
                return self._users[self._index[index_key]]

            user = ShadowUser.create(
                provider=provider,
                subject=result.subject,
                result=result,
            )
            self._users[user.user_id] = user
            self._index[index_key] = user.user_id

            await self._save_locked()
            logger.info(
                "ShadowUserStore: created user %s (%s:%s)",
                user.user_id,
                provider,
                result.subject,
            )
            return user

    async def get_by_id(self, user_id: str) -> Optional[ShadowUser]:
        """Return an existing ShadowUser by its internal UUID, or None."""
        await self._ensure_loaded()
        return self._users.get(user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            await self._load_locked()
            self._loaded = True

    async def _load_locked(self) -> None:
        """Load persisted data from disk. Called with self._lock held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            return
        try:
            async with aiofiles.open(self._path, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
            for entry in data.get("users", []):
                user = ShadowUser.from_dict(entry)
                self._users[user.user_id] = user
                self._index[f"{user.provider}:{user.subject}"] = user.user_id
            logger.debug(
                "ShadowUserStore: loaded %d users from %s",
                len(self._users),
                self._path,
            )
        except Exception as exc:
            logger.warning("ShadowUserStore: failed to load %s: %s", self._path, exc)

    async def _save(self) -> None:
        async with self._lock:
            await self._save_locked()

    async def _save_locked(self) -> None:
        """Persist all users to disk. Must be called with self._lock held."""
        data = {"users": [u.to_dict() for u in self._users.values()]}
        try:
            async with aiofiles.open(self._path, "w", encoding="utf-8") as f:
                await f.write(
                    json.dumps(data, ensure_ascii=False, indent=2, default=str)
                )
        except Exception as exc:
            logger.error(
                "ShadowUserStore: failed to persist %s: %s", self._path, exc
            )
