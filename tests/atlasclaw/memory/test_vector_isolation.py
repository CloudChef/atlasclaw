# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
HybridSearcher 向量索引隔离单元测试

涵盖：不同 user_id 使用独立 HybridSearcher 实例、index_path 路径正确、
sqlite-vec 不可用时内存降级正常工作。
"""

from __future__ import annotations

import pytest
from pathlib import Path

from app.atlasclaw.core.user_paths import normalize_runtime_user_id
from app.atlasclaw.memory.search import HybridSearcher
from app.atlasclaw.memory.manager import MemoryEntry, MemoryType
from datetime import datetime, timezone


def _make_entry(content: str, entry_id: str | None = None) -> MemoryEntry:
    ts = datetime.now(timezone.utc)
    eid = entry_id or MemoryEntry.generate_id(content, ts)
    return MemoryEntry(id=eid, content=content, memory_type=MemoryType.LONG_TERM, timestamp=ts)


class TestVectorIsolation:

    def test_index_path_contains_user_id(self, tmp_path):
        searcher = HybridSearcher(user_id="u-alice", workspace=str(tmp_path))
        assert searcher._index_path is not None
        assert "u-alice" in searcher._index_path
        assert "index.sqlite" in searcher._index_path

    def test_index_path_in_memory_subdir(self, tmp_path):
        searcher = HybridSearcher(user_id="u-bob", workspace=str(tmp_path))
        expected = str(tmp_path / "users" / "u-bob" / "memory" / "index.sqlite")
        assert searcher._index_path == expected

    def test_dot_only_user_id_uses_default_index_path(self, tmp_path):
        searcher = HybridSearcher(user_id="..", workspace=str(tmp_path))
        expected = str(tmp_path / "users" / "default" / "memory" / "index.sqlite")
        assert searcher._index_path == expected
        assert normalize_runtime_user_id("..") == "default"

    def test_different_users_have_different_index_paths(self, tmp_path):
        s1 = HybridSearcher(user_id="u-alice", workspace=str(tmp_path))
        s2 = HybridSearcher(user_id="u-bob", workspace=str(tmp_path))
        assert s1._index_path != s2._index_path

    def test_no_workspace_gives_none_index_path(self):
        searcher = HybridSearcher(user_id="u-alice")
        assert searcher._index_path is None

    @pytest.mark.asyncio
    async def test_in_memory_search_works_without_sqlite_vec(self, tmp_path):
        """sqlite-vec is not available in test env; searcher must still work in-memory."""
        searcher = HybridSearcher(user_id="u-test", workspace=str(tmp_path))

        entry1 = _make_entry("cloud resource management VM")
        entry2 = _make_entry("database backup restore procedure")
        searcher.index_sync(entry1)
        searcher.index_sync(entry2)

        results = await searcher.search("cloud VM", top_k=5)
        assert len(results) >= 1
        assert results[0].entry.id == entry1.id

    @pytest.mark.asyncio
    async def test_independent_instances_do_not_share_data(self, tmp_path):
        """Two HybridSearcher instances must have isolated in-memory indices."""
        s1 = HybridSearcher(user_id="u-alice", workspace=str(tmp_path))
        s2 = HybridSearcher(user_id="u-bob", workspace=str(tmp_path))

        entry = _make_entry("alice exclusive data", "alice-entry-1")
        s1.index_sync(entry)

        # s2 should not find alice's entry
        results = await s2.search("alice exclusive data", top_k=5)
        assert not any(r.entry.id == "alice-entry-1" for r in results)

    def test_user_id_stored_on_instance(self):
        searcher = HybridSearcher(user_id="u-xyz")
        assert searcher._user_id == "u-xyz"
