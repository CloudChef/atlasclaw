# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

from app.xuanwu.core.config_schema import ResetMode
from app.xuanwu.session.context import SessionKey
from app.xuanwu.session.manager import SessionManager


class SessionManagerRouter:
    """Resolve per-user session managers from users or session keys."""

    def __init__(
        self,
        workspace_path: str,
        *,
        reset_mode: ResetMode = ResetMode.DAILY,
        daily_reset_hour: int = 4,
        idle_reset_minutes: int = 60,
        agents_dir: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.reset_mode = reset_mode
        self.daily_reset_hour = daily_reset_hour
        self.idle_reset_minutes = idle_reset_minutes
        self.agents_dir = agents_dir
        self.agent_id = agent_id
        self._managers: dict[str, SessionManager] = {}

    @classmethod
    def from_manager(cls, manager: SessionManager) -> "SessionManagerRouter":
        """Build a router that mirrors an existing manager's storage settings."""
        return cls(
            workspace_path=str(manager.workspace_path),
            reset_mode=manager.reset_mode,
            daily_reset_hour=manager.daily_reset_hour,
            idle_reset_minutes=manager.idle_reset_minutes,
            agents_dir=str(manager.agents_dir) if getattr(manager, "_legacy_mode", False) else None,
            agent_id=getattr(manager, "agent_id", None) if getattr(manager, "_legacy_mode", False) else None,
        )

    def for_user(self, user_id: str) -> SessionManager:
        """Return the cached session manager for a user."""
        resolved_user_id = user_id or "default"
        manager = self._managers.get(resolved_user_id)
        if manager is None:
            if self.agents_dir is not None:
                manager = SessionManager(
                    agents_dir=self.agents_dir,
                    agent_id=self.agent_id,
                    user_id=resolved_user_id,
                    reset_mode=self.reset_mode,
                    daily_reset_hour=self.daily_reset_hour,
                    idle_reset_minutes=self.idle_reset_minutes,
                )
            else:
                manager = SessionManager(
                    workspace_path=str(self.workspace_path),
                    user_id=resolved_user_id,
                    reset_mode=self.reset_mode,
                    daily_reset_hour=self.daily_reset_hour,
                    idle_reset_minutes=self.idle_reset_minutes,
                )
            self._managers[resolved_user_id] = manager
        return manager

    def for_session_key(self, session_key: str) -> SessionManager:
        """Resolve a per-user manager from a serialized session key."""
        parsed = SessionKey.from_string(session_key)
        return self.for_user(parsed.user_id or "default")
