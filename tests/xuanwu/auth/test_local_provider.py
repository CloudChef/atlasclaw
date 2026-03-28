# -*- coding: utf-8 -*-
"""Unit tests for LocalAuthProvider."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.xuanwu.auth.models import AuthenticationError
from app.xuanwu.auth.providers.local import LocalAuthProvider
from app.xuanwu.db.database import DatabaseConfig, init_database
from app.xuanwu.db.orm.user import UserService
from app.xuanwu.db.schemas import UserCreate


@pytest_asyncio.fixture
async def local_db(tmp_path: Path):
    db_path = tmp_path / "local_auth_test.db"
    manager = await init_database(DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path)))
    await manager.create_tables()

    async with manager.get_session() as session:
        await UserService.create(
            session,
            UserCreate(
                username="admin",
                password="adminpass1",
                display_name="Administrator",
                roles={"admin": True},
                auth_type="local",
                is_admin=True,
                is_active=True,
            ),
        )

    yield manager
    await manager.close()


class TestLocalAuthProvider:
    @pytest.mark.asyncio
    async def test_authenticate_success(self, local_db):
        provider = LocalAuthProvider()

        result = await provider.authenticate("admin:adminpass1")

        assert result.subject == "admin"
        assert result.display_name == "Administrator"
        assert "admin" in result.roles
        assert result.extra.get("auth_type") == "local"

    @pytest.mark.asyncio
    async def test_authenticate_invalid_format(self, local_db):
        provider = LocalAuthProvider()

        with pytest.raises(AuthenticationError, match="format"):
            await provider.authenticate("admin")

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, local_db):
        provider = LocalAuthProvider()

        with pytest.raises(AuthenticationError, match="Invalid username or password"):
            await provider.authenticate("admin:wrong")
