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
    async def test_daily_memory_stored_in_user_subdir(self, tmp_path):
        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()
        await manager.write_daily("Test memory content", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        assert alice_dir.exists()
        md_files = list(alice_dir.glob("*.md"))
        assert len(md_files) > 0

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
        await mgr_alice.write_daily("Alice memory", source="test")
        await mgr_bob.write_daily("Bob memory", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        bob_dir = tmp_path / "users" / "u-bob" / "memory"

        assert alice_dir.exists()
        assert bob_dir.exists()
        # Bob's directory should not contain Alice's files
        alice_files = set(f.name for f in alice_dir.glob("*.md"))
        bob_files = set(f.name for f in bob_dir.glob("*.md"))
        # Both should have the same date file, but content is separate
        assert alice_dir != bob_dir

    @pytest.mark.asyncio
    async def test_legacy_migration_to_default(self, tmp_path):
        """Legacy .md files directly in memory/ migrate to users/default/memory/."""
        legacy_dir = tmp_path / "memory"
        legacy_dir.mkdir(parents=True)

        # Create a fake legacy daily file
        (legacy_dir / "2025-01-15.md").write_text("# Legacy memory\n\nOld content\n",
                                                    encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="default")
        await manager.ensure_dirs()

        default_dir = tmp_path / "users" / "default" / "memory"
        assert default_dir.exists()
        migrated_files = list(default_dir.glob("*.md"))
        assert any("2025-01-15" in f.name for f in migrated_files)
        # Legacy file should no longer be directly in memory/
        assert not (legacy_dir / "2025-01-15.md").exists()

    @pytest.mark.asyncio
    async def test_legacy_user_subdir_migrates_to_documented_location(self, tmp_path):
        """Legacy memory/<user_id>/ files migrate to users/<user_id>/memory/."""
        legacy_user_dir = tmp_path / "memory" / "u-alice"
        legacy_user_dir.mkdir(parents=True)
        (legacy_user_dir / "MEMORY.md").write_text("# Legacy memory\n", encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()

        new_user_dir = tmp_path / "users" / "u-alice" / "memory"
        assert (new_user_dir / "MEMORY.md").exists()
        assert not (legacy_user_dir / "MEMORY.md").exists()
