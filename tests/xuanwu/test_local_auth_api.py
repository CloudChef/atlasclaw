# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.xuanwu.api.routes import APIContext, create_router, set_api_context
from app.xuanwu.auth.config import AuthConfig
from app.xuanwu.auth.jwt_token import issue_xuanwu_token
from app.xuanwu.auth.middleware import setup_auth_middleware
from app.xuanwu.db.database import DatabaseConfig, init_database
from app.xuanwu.db.orm.user import UserService
from app.xuanwu.db.schemas import UserCreate
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.queue import SessionQueue
from app.xuanwu.skills.registry import SkillRegistry


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
                "issuer": "xuanwu-test",
                "header_name": "Xuanwu-Authenticate",
                "cookie_name": "Xuanwu-Authenticate",
                "expires_minutes": 60,
            },
        )
    )
    return TestClient(app)


def _build_protected_client() -> TestClient:
    auth_config = AuthConfig(
        provider="local",
        jwt={
            "secret_key": "test-secret",
            "issuer": "xuanwu-test",
            "header_name": "Xuanwu-Authenticate",
            "cookie_name": "Xuanwu-Authenticate",
            "expires_minutes": 60,
        },
    )

    app = FastAPI()
    app.state.config = SimpleNamespace(auth=auth_config)
    setup_auth_middleware(app, auth_config)

    @app.get("/api/protected")
    async def protected_route(request: Request):
        return {"user_id": request.state.user_info.user_id}

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
    assert body["header_name"] == "Xuanwu-Authenticate"
    assert "xuanwu_session" in resp.cookies
    assert "Xuanwu-Authenticate" in resp.cookies


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
        headers={"Xuanwu-Authenticate": token},
    )
    assert me_ok.status_code == 200
    assert me_ok.json()["user_id"] == "admin"

    me_fail = client.get(
        "/api/auth/me",
        headers={"Xuanwu-Authenticate": "bad-token"},
    )
    assert me_fail.status_code == 401

    manager_cleanup(manager)


def test_auth_me_accepts_legacy_header_and_session_cookie(tmp_path):
    manager = init_database_sync(tmp_path)
    primary_client = _build_client(tmp_path)

    login_resp = primary_client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": "adminpass1"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]
    session_key = login_resp.json()["session"]["key"]

    legacy_client = _build_client(tmp_path)
    legacy_client.cookies.set("atlasclaw_session", session_key)

    me_resp = legacy_client.get(
        "/api/auth/me",
        headers={"AtlasClaw-Authenticate": token},
    )

    assert me_resp.status_code == 200
    assert me_resp.json()["user_id"] == "admin"

    manager_cleanup(manager)


def test_auth_middleware_accepts_legacy_header_and_cookie():
    client = _build_protected_client()
    token = issue_xuanwu_token(
        subject="admin",
        is_admin=True,
        roles=["admin"],
        auth_type="local",
        secret_key="test-secret",
        expires_minutes=60,
        issuer="xuanwu-test",
    )

    header_resp = client.get(
        "/api/protected",
        headers={"AtlasClaw-Authenticate": token},
    )
    assert header_resp.status_code == 200
    assert header_resp.json()["user_id"] == "admin"

    cookie_client = _build_protected_client()
    cookie_client.cookies.set("AtlasClaw-Authenticate", token)
    cookie_resp = cookie_client.get("/api/protected")

    assert cookie_resp.status_code == 200
    assert cookie_resp.json()["user_id"] == "admin"


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
                    is_admin=True,
                    is_active=True,
                ),
            )
        return manager

    return asyncio.run(_init())


def manager_cleanup(manager):
    import asyncio

    asyncio.run(manager.close())
