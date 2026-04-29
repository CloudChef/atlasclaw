# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.atlasclaw.auth.user_store import (
    build_user_info_from_db_user,
    ensure_db_user_for_auth_result,
    resolve_user_info_for_auth_result,
)
from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate


@pytest.mark.asyncio
async def test_external_auth_creates_standard_db_user(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        user = await ensure_db_user_for_auth_result(
            session,
            provider="host_cookie",
            result=AuthResult(
                subject="cmp-user",
                display_name="CMP User",
                raw_token="cookie-token",
                extra={"auth_type": "cookie"},
            ),
        )

    assert user.username == "cmp-user"
    assert user.display_name == "CMP User"
    assert user.auth_type == "cookie"
    assert user.roles == {"user": True}
    assert user.password is None

    user_info = build_user_info_from_db_user(
        user,
        provider="host_cookie",
        result=AuthResult(subject="cmp-user", extra={"auth_type": "cookie"}),
        raw_token="cookie-token",
        auth_type="cookie",
    )
    assert user_info.user_id == user.id
    assert user_info.roles == ["user"]
    assert user_info.extra["external_subject"] == "cmp-user"

    async with manager.get_session() as session:
        assert await UserService.authenticate(session, "cmp-user", "anything") is None

    await manager.close()


@pytest.mark.asyncio
async def test_resolve_user_info_creates_passwordless_standard_user(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    user_info = await resolve_user_info_for_auth_result(
        provider="host_cookie",
        result=AuthResult(
            subject="cmp_user",
            display_name="CMP User",
            raw_token="cookie-token",
            extra={"auth_type": "cookie"},
        ),
        raw_token="cookie-token",
        auth_type="cookie",
    )

    assert user_info.display_name == "CMP User"
    assert user_info.auth_type == "cookie"
    assert user_info.roles == ["user"]
    assert user_info.extra["external_subject"] == "cmp_user"

    async with manager.get_session() as session:
        user = await UserService.get_by_username(session, "cmp_user", auth_type="cookie")
        assert user is not None
        assert user.id == user_info.user_id
        assert user.roles == {"user": True}
        assert user.password is None
        assert await UserService.authenticate(session, "cmp_user", "password") is None

    await manager.close()


@pytest.mark.asyncio
async def test_user_service_normalizes_identity_fields_before_persist(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        created = await UserService.create(
            session,
            UserCreate(
                username=" cmp_user ",
                email=" cmp_user@example.com ",
                password="password",
                display_name=" CMP User ",
                roles={"user": True},
                auth_type=" cookie ",
                is_active=True,
            ),
        )

    assert created.username == "cmp_user"
    assert created.email == "cmp_user@example.com"
    assert created.display_name == "CMP User"
    assert created.auth_type == "cookie"

    async with manager.get_session() as session:
        user_by_username = await UserService.get_by_username(
            session,
            " cmp_user ",
            auth_type=" cookie ",
        )
        user_by_email = await UserService.get_by_email(session, " cmp_user@example.com ")
        assert user_by_username is not None
        assert user_by_username.id == created.id
        assert user_by_email is not None
        assert user_by_email.id == created.id
        assert await UserService.authenticate(session, " cmp_user ", "password") is None

    await manager.close()


@pytest.mark.asyncio
async def test_external_auth_keeps_existing_db_roles(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        existing = await UserService.create(
            session,
            UserCreate(
                username="cmp-admin",
                password=None,
                display_name="Existing Admin",
                roles={"admin": True},
                auth_type="cookie",
                is_active=True,
            ),
        )
        user = await ensure_db_user_for_auth_result(
            session,
            provider="host_cookie",
            result=AuthResult(
                subject="cmp-admin",
                display_name="CMP Admin",
                raw_token="cookie-token",
                extra={"auth_type": "cookie"},
            ),
        )

    assert user.id == existing.id
    assert user.roles == {"admin": True}
    assert user.auth_type == "cookie"
    assert user.display_name == "CMP Admin"

    await manager.close()


@pytest.mark.asyncio
async def test_usernames_are_unique_per_auth_type(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        local_user = await UserService.create(
            session,
            UserCreate(
                username="admin",
                password="local-password",
                display_name="Local Admin",
                roles={"admin": True},
                auth_type="local",
                is_active=True,
            ),
        )
        cookie_user = await UserService.create(
            session,
            UserCreate(
                username="admin",
                password="cookie-password",
                display_name="Cookie Admin",
                roles={"user": True},
                auth_type="cookie",
                is_active=True,
            ),
        )

    assert local_user.id != cookie_user.id

    async with manager.get_session() as session:
        assert (
            await UserService.get_by_username(session, "admin", auth_type="local")
        ).id == local_user.id
        assert (
            await UserService.get_by_username(session, "admin", auth_type="cookie")
        ).id == cookie_user.id
        assert (
            await UserService.authenticate(session, "admin", "local-password")
        ).id == local_user.id
        assert await UserService.authenticate(session, "admin", "cookie-password") is None

    await manager.close()


@pytest.mark.asyncio
async def test_duplicate_username_same_auth_type_is_rejected(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        await UserService.create(
            session,
            UserCreate(
                username="admin",
                password="first-password",
                roles={"user": True},
                auth_type="cookie",
                is_active=True,
            ),
        )
        with pytest.raises(IntegrityError):
            await UserService.create(
                session,
                UserCreate(
                    username="admin",
                    password="second-password",
                    roles={"user": True},
                    auth_type="cookie",
                    is_active=True,
                ),
            )
        await session.rollback()

    await manager.close()


@pytest.mark.asyncio
async def test_external_auth_uses_auth_type_scope_when_subject_matches_local_user(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        local_user = await UserService.create(
            session,
            UserCreate(
                username="admin",
                password="local-password",
                display_name="Administrator",
                roles={"admin": True},
                auth_type="local",
                is_active=True,
            ),
        )
        cookie_user = await ensure_db_user_for_auth_result(
            session,
            provider="host_cookie",
            result=AuthResult(
                subject="admin",
                display_name="Platform Administrator",
                raw_token="cookie-token",
                extra={"auth_type": "cookie"},
            ),
        )

    assert cookie_user.id != local_user.id
    assert cookie_user.username == "admin"
    assert cookie_user.auth_type == "cookie"
    assert cookie_user.roles == {"user": True}
    assert cookie_user.display_name == "Platform Administrator"

    await manager.close()


@pytest.mark.asyncio
async def test_external_auth_rejects_inactive_existing_user(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
    )
    await manager.create_tables()

    async with manager.get_session() as session:
        await UserService.create(
            session,
            UserCreate(
                username="disabled-cmp-user",
                password=None,
                display_name="Disabled CMP User",
                roles={"user": True},
                auth_type="cookie",
                is_active=False,
            ),
        )

    with pytest.raises(AuthenticationError, match="inactive"):
        await resolve_user_info_for_auth_result(
            provider="host_cookie",
            result=AuthResult(
                subject="disabled-cmp-user",
                display_name="Disabled CMP User",
                raw_token="cookie-token",
                extra={"auth_type": "cookie"},
            ),
        )

    async with manager.get_session() as session:
        user = await UserService.get_by_username(
            session,
            "disabled-cmp-user",
            auth_type="cookie",
        )
        assert user is not None
        assert user.last_login_at is None

    await manager.close()
