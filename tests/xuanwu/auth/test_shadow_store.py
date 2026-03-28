# -*- coding: utf-8 -*-
"""
ShadowUserStore 单元测试

涵盖：命中缓存更新 last_seen_at、首次创建 ShadowUser、并发创建幂等。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.xuanwu.auth.models import AuthResult
from app.xuanwu.auth.shadow_store import ShadowUserStore


def _make_result(subject: str, display_name: str = "Test", tenant_id: str = "t1") -> AuthResult:
    return AuthResult(subject=subject, display_name=display_name, tenant_id=tenant_id)


class TestShadowUserStore:
    def test_workspace_path_is_used_for_default_store_path(self, tmp_path):
        workspace = tmp_path / "custom-workspace"
        store = ShadowUserStore(workspace_path=str(workspace))

        assert store.workspace_path == workspace.resolve()
        assert store._path == (workspace / "users.json").resolve()

    @pytest.mark.asyncio
    async def test_create_new_user(self, tmp_path):
        store = ShadowUserStore(store_path=str(tmp_path / "users.json"))
        result = _make_result("user@corp.com")
        shadow = await store.get_or_create("smartcmp", result)

        assert shadow.provider == "smartcmp"
        assert shadow.subject == "user@corp.com"
        assert shadow.display_name == "Test"
        assert shadow.tenant_id == "t1"
        assert shadow.user_id  # non-empty UUID

    @pytest.mark.asyncio
    async def test_existing_user_returned_on_hit(self, tmp_path):
        store = ShadowUserStore(store_path=str(tmp_path / "users.json"))
        result = _make_result("alice@corp.com")

        shadow1 = await store.get_or_create("smartcmp", result)
        shadow2 = await store.get_or_create("smartcmp", result)

        assert shadow1.user_id == shadow2.user_id

    @pytest.mark.asyncio
    async def test_last_seen_at_updated(self, tmp_path):
        store = ShadowUserStore(store_path=str(tmp_path / "users.json"))
        result = _make_result("bob@corp.com")

        shadow1 = await store.get_or_create("smartcmp", result)
        ts1 = shadow1.last_seen_at

        await asyncio.sleep(0.01)
        shadow2 = await store.get_or_create("smartcmp", result)

        assert shadow2.last_seen_at >= ts1

    @pytest.mark.asyncio
    async def test_concurrent_creation_is_idempotent(self, tmp_path):
        """Concurrent get_or_create for the same user must yield the same user_id."""
        store = ShadowUserStore(store_path=str(tmp_path / "users.json"))
        result = _make_result("concurrent@corp.com")

        shadows = await asyncio.gather(
            *[store.get_or_create("smartcmp", result) for _ in range(10)]
        )

        user_ids = {s.user_id for s in shadows}
        assert len(user_ids) == 1, f"Expected 1 unique user_id, got {user_ids}"

    @pytest.mark.asyncio
    async def test_different_providers_create_separate_users(self, tmp_path):
        store = ShadowUserStore(store_path=str(tmp_path / "users.json"))
        result = _make_result("same-subject")

        s1 = await store.get_or_create("oidc", result)
        s2 = await store.get_or_create("smartcmp", result)

        # Same subject but different provider → different internal users
        assert s1.user_id != s2.user_id

    @pytest.mark.asyncio
    async def test_persistence_across_instances(self, tmp_path):
        """User created in one store instance should load in a new instance."""
        path = str(tmp_path / "users.json")
        result = _make_result("persist@corp.com")

        store1 = ShadowUserStore(store_path=path)
        shadow1 = await store1.get_or_create("smartcmp", result)

        store2 = ShadowUserStore(store_path=path)
        shadow2 = await store2.get_or_create("smartcmp", result)

        assert shadow1.user_id == shadow2.user_id
