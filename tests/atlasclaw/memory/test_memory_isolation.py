# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
MemoryManager 路径隔离单元测试

涵盖：不同 user_id 存储在不同子目录、旧数据迁移到 memory/default/。
"""

from __future__ import annotations

import pytest
from pathlib import Path

from app.atlasclaw.memory.manager import MemoryManager


class TestMemoryIsolation:

    @pytest.mark.asyncio
    async def test_long_term_memory_stored_in_user_subdir(self, tmp_path):
        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()
        await manager.write_long_term("Test memory content", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        assert alice_dir.exists()
        assert (alice_dir / "MEMORY.md").exists()

    @pytest.mark.asyncio
    async def test_long_term_path_in_user_subdir(self, tmp_path):
        manager = MemoryManager(workspace=str(tmp_path), user_id="u-bob")
        expected_path = tmp_path / "users" / "u-bob" / "memory" / "MEMORY.md"
        assert manager.long_term_path == expected_path

    @pytest.mark.asyncio
    async def test_different_users_use_separate_directories(self, tmp_path):
        mgr_alice = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        mgr_bob = MemoryManager(workspace=str(tmp_path), user_id="u-bob")

        await mgr_alice.ensure_dirs()
        await mgr_bob.ensure_dirs()
        await mgr_alice.write_long_term("Alice memory", source="test")
        await mgr_bob.write_long_term("Bob memory", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        bob_dir = tmp_path / "users" / "u-bob" / "memory"

        assert alice_dir.exists()
        assert bob_dir.exists()
        assert alice_dir != bob_dir
        assert "Alice memory" in (alice_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "Alice memory" not in (bob_dir / "MEMORY.md").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_legacy_date_scoped_files_are_not_migrated(self, tmp_path):
        """Date-scoped legacy files are not part of the long-term memory contract."""
        legacy_dir = tmp_path / "memory"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "2025-01-15.md").write_text("# Legacy memory\n\nOld content\n",
                                                    encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="default")
        await manager.ensure_dirs()

        default_dir = tmp_path / "users" / "default" / "memory"
        assert default_dir.exists()
        assert not (default_dir / "2025-01-15.md").exists()
        assert (legacy_dir / "2025-01-15.md").exists()

    @pytest.mark.asyncio
    async def test_legacy_user_subdir_is_not_migrated(self, tmp_path):
        """Legacy user directories are not read or moved by the long-term manager."""
        legacy_user_dir = tmp_path / "memory" / "u-alice"
        legacy_user_dir.mkdir(parents=True)
        (legacy_user_dir / "MEMORY.md").write_text("# Legacy memory\n", encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()

        new_user_dir = tmp_path / "users" / "u-alice" / "memory"
        assert not (new_user_dir / "MEMORY.md").exists()
        assert (legacy_user_dir / "MEMORY.md").exists()
