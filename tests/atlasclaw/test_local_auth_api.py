# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.deps_context import get_api_context
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.jwt_token import issue_atlas_token
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import UserCreate, UserUpdate
from app.atlasclaw.session.context import ChatType as SessionChatType
from app.atlasclaw.session.context import SessionKey, SessionScope
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path: Path) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    app.state.config = SimpleNamespace(
        auth=AuthConfig(
            provider="local",
            jwt={
                "secret_key": "test-secret",
                "issuer": "atlasclaw-test",
                "header_name": "AtlasClaw-Authenticate",
                "cookie_name": "AtlasClaw-Authenticate",
                "expires_minutes": 60,
            },
        )
    )
    return TestClient(app)



def test_local_login_success(tmp_path):
    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    resp = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "adminpass1"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["user"]["username"] == "admin"
    assert body["user"]["auth_type"] == "local"
    assert body["token"]
    assert body["header_name"] == "AtlasClaw-Authenticate"
    assert "atlasclaw_session" in resp.cookies
    assert "AtlasClaw-Authenticate" in resp.cookies


    manager_cleanup(manager)


def test_local_login_failure(tmp_path):
    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    resp = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert resp.status_code == 401
    assert "failed" in resp.json()["detail"]

    manager_cleanup(manager)


def test_local_login_sets_cookie_path_for_base_path(tmp_path):
    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)
    client.app.state.config.base_path = "/atlasclaw"

    resp = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "adminpass1"},
    )

    assert resp.status_code == 200
    set_cookie_headers = resp.headers.get_list("set-cookie")
    assert any("atlasclaw_session=" in header and "Path=/atlasclaw" in header for header in set_cookie_headers)
    assert any(
        "AtlasClaw-Authenticate=" in header and "Path=/atlasclaw" in header
        for header in set_cookie_headers
    )

    manager_cleanup(manager)


def test_auth_me_requires_valid_jwt(tmp_path):
    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    login_resp = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "adminpass1"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]

    me_ok = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )
    assert me_ok.status_code == 200
    assert me_ok.json()["user_id"] == "admin"
    assert me_ok.json()["permissions"]["roles"]["view"] is True
    assert me_ok.json()["role_identifiers"] == ["admin"]

    me_fail = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": "bad-token"},
    )
    assert me_fail.status_code == 401

    manager_cleanup(manager)


def test_auth_me_includes_avatar_from_profile(tmp_path):
    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    async def _update_profile():
        async with manager.get_session() as session:
            user = await UserService.get_by_username(session, "admin")
            await UserService.update(
                session,
                user.id,
                UserUpdate(
                    display_name="Atlas Admin",
                    avatar_url="/user-content/avatars/admin-profile.png",
                ),
            )

    import asyncio

    asyncio.run(_update_profile())

    login_resp = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "adminpass1"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]

    me_resp = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["display_name"] == "Atlas Admin"
    assert body["avatar_url"] == "/user-content/avatars/admin-profile.png"
    assert body["username"] == "admin"

    manager_cleanup(manager)


def test_password_enabled_external_user_local_login_uses_db_user_id(tmp_path):
    import asyncio

    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    async def _create_password_enabled_external_user() -> str:
        async with manager.get_session() as session:
            user = await UserService.create(
                session,
                UserCreate(
                    username="cmp_user",
                    password="password",
                    display_name="CMP User",
                    roles={"user": True},
                    auth_type="cookie",
                    is_active=True,
                ),
            )
            return user.id

    db_user_id = asyncio.run(_create_password_enabled_external_user())

    login_resp = client.post(
        "/api/auth/local/login",
        json={"username": "cmp_user", "password": "password"},
    )
    assert login_resp.status_code == 200
    login_body = login_resp.json()
    assert login_body["user"]["id"] == db_user_id
    assert login_body["user"]["username"] == "cmp_user"
    assert login_body["user"]["auth_type"] == "cookie"

    token = login_body["token"]
    me_resp = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["user_id"] == db_user_id
    assert body["username"] == "cmp_user"
    assert body["auth_type"] == "cookie"
    assert body["role_identifiers"] == ["user"]

    manager_cleanup(manager)


def test_auth_me_rejects_inactive_db_user_token(tmp_path):
    import asyncio

    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    async def _prepare_inactive_federated_session() -> tuple[str, str]:
        async with manager.get_session() as session:
            user = await UserService.create(
                session,
                UserCreate(
                    username="disabled-sso@example.com",
                    password=None,
                    display_name="Disabled SSO User",
                    roles={"user": True},
                    auth_type="oidc:test",
                    is_active=False,
                ),
            )

        ctx = get_api_context()
        key = SessionKey(
            agent_id="main",
            channel="web",
            chat_type=SessionChatType.DM,
            user_id=user.id,
        )
        session_key = key.to_string(scope=SessionScope.MAIN)
        session = await ctx.session_manager.get_or_create(session_key)
        session.display_name = "Disabled SSO User"
        return user.id, session_key

    user_id, session_key = asyncio.run(_prepare_inactive_federated_session())

    token = issue_atlas_token(
        subject=user_id,
        is_admin=False,
        roles=["user"],
        auth_type="oidc:test",
        secret_key="test-secret",
        expires_minutes=60,
        issuer="atlasclaw-test",
        additional_claims={"external_subject": "disabled-sso@example.com"},
    )
    client.cookies.set("atlasclaw_session", session_key)

    me_resp = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )

    assert me_resp.status_code == 403
    assert "inactive" in me_resp.json()["detail"]

    manager_cleanup(manager)


def test_auth_me_uses_external_subject_for_federated_rbac(tmp_path):
    import asyncio

    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    client.app.state.config.workspace = SimpleNamespace(path=str(workspace_path))

    async def _prepare_federated_session() -> str:
        async with manager.get_session() as session:
            await UserService.create(
                session,
                UserCreate(
                    username="sso-user@example.com",
                    password=None,
                    display_name="Workspace SSO User",
                    email="sso-user@example.com",
                    roles={"viewer": True},
                    auth_type="oidc:test",
                    is_active=True,
                ),
            )

        ctx = get_api_context()
        key = SessionKey(
            agent_id="main",
            channel="web",
            chat_type=SessionChatType.DM,
            user_id="shadow-user-1",
        )
        session_key = key.to_string(scope=SessionScope.MAIN)
        session = await ctx.session_manager.get_or_create(session_key)
        session.display_name = "SSO User"
        return session_key

    session_key = asyncio.run(_prepare_federated_session())

    token = issue_atlas_token(
        subject="shadow-user-1",
        is_admin=False,
        roles=[],
        auth_type="oidc:test",
        secret_key="test-secret",
        expires_minutes=60,
        issuer="atlasclaw-test",
        additional_claims={"external_subject": "sso-user@example.com"},
    )
    client.cookies.set("atlasclaw_session", session_key)

    me_resp = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )

    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["user_id"] == "shadow-user-1"
    assert body["username"] == "sso-user@example.com"
    assert body["display_name"] == "Workspace SSO User"
    assert body["role_identifiers"] == ["viewer"]
    assert body["permissions"]["roles"]["view"] is True

    manager_cleanup(manager)


def test_auth_me_does_not_trust_unprovisioned_federated_token_roles(tmp_path):
    import asyncio

    manager = init_database_sync(tmp_path)
    client = _build_client(tmp_path)

    async def _prepare_federated_session() -> str:
        ctx = get_api_context()
        key = SessionKey(
            agent_id="main",
            channel="web",
            chat_type=SessionChatType.DM,
            user_id="shadow-user-1",
        )
        session_key = key.to_string(scope=SessionScope.MAIN)
        session = await ctx.session_manager.get_or_create(session_key)
        session.display_name = "Unprovisioned SSO User"
        return session_key

    session_key = asyncio.run(_prepare_federated_session())
    token = issue_atlas_token(
        subject="shadow-user-1",
        is_admin=True,
        roles=["admin"],
        auth_type="oidc:test",
        secret_key="test-secret",
        expires_minutes=60,
        issuer="atlasclaw-test",
        additional_claims={"external_subject": "unknown-user@example.com"},
    )
    client.cookies.set("atlasclaw_session", session_key)

    me_resp = client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )

    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["user_id"] == "shadow-user-1"
    assert body["role_identifiers"] == []
    assert body["roles"] == []
    assert body["is_admin"] is False
    assert body["permissions"]["roles"]["view"] is False
    assert body["permissions"]["users"]["view"] is False

    manager_cleanup(manager)



def init_database_sync(tmp_path: Path):
    import asyncio

    async def _init():
        db_path = tmp_path / "local_auth_api_test.db"
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
                    is_active=True,
                ),
            )
        return manager

    return asyncio.run(_init())


def manager_cleanup(manager):
    import asyncio

    asyncio.run(manager.close())
