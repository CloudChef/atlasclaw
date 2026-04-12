# -*- coding: utf-8 -*-
"""
SessionManager 路径隔离单元测试

涵盖：不同 user_id 使用不同子目录、旧数据迁移到 sessions/default/。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path

from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.core.config_schema import ResetMode


class TestSessionManagerIsolation:

    @pytest.mark.asyncio
    async def test_user_sessions_stored_in_user_subdir(self, tmp_path):
        agents_dir = str(tmp_path / "agents")
        manager = SessionManager(
            agents_dir=agents_dir,
            agent_id="main",
            user_id="u-alice",
            reset_mode=ResetMode.MANUAL,
        )

        session = await manager.get_or_create("agent:main:user:u-alice:api:dm:bob")

        expected_dir = Path(agents_dir) / "main" / "sessions" / "u-alice"
        assert expected_dir.exists()
        assert (expected_dir / "sessions.json").exists()

    @pytest.mark.asyncio
    async def test_different_users_use_separate_directories(self, tmp_path):
        agents_dir = str(tmp_path / "agents")

        mgr_alice = SessionManager(
            agents_dir=agents_dir, agent_id="main", user_id="u-alice",
            reset_mode=ResetMode.MANUAL,
        )
        mgr_bob = SessionManager(
            agents_dir=agents_dir, agent_id="main", user_id="u-bob",
            reset_mode=ResetMode.MANUAL,
        )

        await mgr_alice.get_or_create("session-alice")
        await mgr_bob.get_or_create("session-bob")

        alice_dir = Path(agents_dir) / "main" / "sessions" / "u-alice"
        bob_dir = Path(agents_dir) / "main" / "sessions" / "u-bob"
        assert alice_dir.exists()
        assert bob_dir.exists()
        assert alice_dir != bob_dir

    @pytest.mark.asyncio
    async def test_legacy_migration_to_default(self, tmp_path):
        """Legacy sessions.json in sessions/ (no user sub-dir) migrates to sessions/default/."""
        legacy_sessions_dir = tmp_path / "agents" / "main" / "sessions"
        legacy_sessions_dir.mkdir(parents=True)

        # Create a fake legacy sessions.json
        legacy_data = {"old-key": {"session_id": "s001", "session_key": "old-key",
                                    "created_at": "2025-01-01T00:00:00",
                                    "updated_at": "2025-01-01T00:00:00"}}
        (legacy_sessions_dir / "sessions.json").write_text(
            json.dumps(legacy_data), encoding="utf-8"
        )

        # Create a new manager with user_id="default" which triggers migration
        manager = SessionManager(
            agents_dir=str(tmp_path / "agents"),
            agent_id="main",
            user_id="default",
            reset_mode=ResetMode.MANUAL,
        )
        await manager._ensure_dir()

        # Legacy file should be gone; default/ should have the metadata
        default_metadata = legacy_sessions_dir / "default" / "sessions.json"
        assert default_metadata.exists()
        assert not (legacy_sessions_dir / "sessions.json").exists()

    @pytest.mark.asyncio
    async def test_blank_metadata_file_is_treated_as_empty(self, tmp_path):
        agents_dir = tmp_path / "agents"
        sessions_dir = agents_dir / "main" / "sessions" / "default"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "sessions.json").write_text("", encoding="utf-8")

        manager = SessionManager(
            agents_dir=str(agents_dir),
            agent_id="main",
            user_id="default",
            reset_mode=ResetMode.MANUAL,
        )

        await manager._load_metadata()

        assert manager._metadata_cache == {}

    @pytest.mark.asyncio
    async def test_save_metadata_uses_atomic_replace(self, tmp_path):
        agents_dir = tmp_path / "agents"
        manager = SessionManager(
            agents_dir=str(agents_dir),
            agent_id="main",
            user_id="default",
            reset_mode=ResetMode.MANUAL,
        )

        await manager.get_or_create("agent:main:user:alice:api:dm:bob")

        metadata_path = agents_dir / "main" / "sessions" / "default" / "sessions.json"
        tmp_path_file = metadata_path.with_suffix(".json.tmp")

        assert metadata_path.exists()
        assert not tmp_path_file.exists()
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert "agent:main:user:alice:api:dm:bob" in data

    @pytest.mark.asyncio
    async def test_concurrent_save_metadata_uses_distinct_temp_files(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        manager = SessionManager(
            agents_dir=str(agents_dir),
            agent_id="main",
            user_id="default",
            reset_mode=ResetMode.MANUAL,
        )
        await manager._ensure_dir()
        manager._metadata_cache = {
            "s-1": manager._create_new_session("s-1"),
            "s-2": manager._create_new_session("s-2"),
        }
        manager._loaded = True

        replace_sources = []

        async def _fake_replace(src, dst):
            replace_sources.append(src.name)
            await asyncio.sleep(0.01)
            Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
            Path(src).unlink(missing_ok=True)

        monkeypatch.setattr(manager, "_replace_file_with_retry", _fake_replace)

        await asyncio.gather(manager._save_metadata(), manager._save_metadata())

        assert len(replace_sources) == 2
        assert len(set(replace_sources)) == 2
        assert not list((agents_dir / "main" / "sessions" / "default").glob("*.tmp"))

    @pytest.mark.asyncio
    async def test_session_manager_router_routes_by_session_user(self, tmp_path):
        from app.atlasclaw.session.router import SessionManagerRouter

        router = SessionManagerRouter(workspace_path=str(tmp_path))
        session_key = "agent:main:user:u-alice:web:dm:u-alice"
        manager = router.for_session_key(session_key)

        await manager.get_or_create(session_key)

        alice_metadata = tmp_path / "users" / "u-alice" / "sessions" / "sessions.json"
        default_metadata = tmp_path / "users" / "default" / "sessions" / "sessions.json"

        assert alice_metadata.exists()
        assert not default_metadata.exists()
