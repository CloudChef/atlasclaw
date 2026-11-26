# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.atlasclaw.auth import jwt_token
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.jwt_token import issue_atlas_token, verify_atlas_token
from app.atlasclaw.auth.middleware import AuthMiddleware


class _Provider:
    def __init__(self, name: str) -> None:
        self.name = name

    def provider_name(self) -> str:
        return self.name


class _Strategy:
    def __init__(self, name: str = "local") -> None:
        self.primary_provider = _Provider(name)

    def ensure_user_workspace(self, user_id: str) -> None:
        pass


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"user_id": request.state.user_info.user_id})


async def _denied(request: Request) -> JSONResponse:
    return JSONResponse({"detail": "denied"}, status_code=403)


async def _logout(request: Request) -> JSONResponse:
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("Custom-Atlas-Cookie")
    return response


def _client(*, provider: str = "local", base_path: str = "") -> TestClient:
    app = Starlette(
        routes=[
            Route("/api/test", _ok),
            Route("/api/denied", _denied),
            Route("/api/auth/me", _ok),
            Route("/api/auth/logout", _logout, methods=["GET"]),
        ]
    )
    app.state.config = SimpleNamespace(base_path=base_path)
    auth_config = AuthConfig(
        provider=provider,
        jwt={
            "secret_key": "test-secret",
            "issuer": "atlasclaw-test",
            "header_name": "Custom-Atlas-Auth",
            "cookie_name": "Custom-Atlas-Cookie",
            "expires_minutes": 480,
        },
    )
    app.add_middleware(AuthMiddleware, strategy=_Strategy(provider), auth_config=auth_config)
    return TestClient(app)


def _token(*, expires_minutes: int = 480, auth_type: str = "local") -> str:
    return issue_atlas_token(
        subject="user-1",
        is_admin=True,
        roles=["admin", "auditor"],
        auth_type=auth_type,
        secret_key="test-secret",
        expires_minutes=expires_minutes,
        issuer="atlasclaw-test",
        login_time="2024-01-02T03:04:05+00:00",
        additional_claims={"external_subject": "external-1", "custom_claim": "preserve-me"},
    )


def test_recent_authenticated_request_renews_jwt_and_preserves_identity_claims(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token(expires_minutes=480)
    old_payload = verify_atlas_token(
        token=token, secret_key="test-secret", issuer="atlasclaw-test"
    )
    now += 479 * 60

    with _client() as client:
        response = client.get("/api/test", headers={"Custom-Atlas-Auth": token})

    assert response.status_code == 200
    renewed = response.headers["AtlasClaw-Authenticate"]
    payload = verify_atlas_token(
        token=renewed, secret_key="test-secret", issuer="atlasclaw-test"
    )
    assert payload["exp"] == now + 480 * 60
    assert payload["iat"] == now
    assert payload["login_time"] == old_payload["login_time"]
    assert payload["roles"] == old_payload["roles"]
    assert payload["sub"] == old_payload["sub"]
    assert payload["is_admin"] == old_payload["is_admin"]
    assert payload["external_subject"] == old_payload["external_subject"]
    assert payload["custom_claim"] == old_payload["custom_claim"]
    assert response.headers["Custom-Atlas-Auth"] == renewed
    assert "Custom-Atlas-Cookie=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]
    assert response.headers["Cache-Control"] == "no-store"

    now += 2 * 60
    with _client() as client:
        after_original_expiry = client.get(
            "/api/test", headers={"AtlasClaw-Authenticate": renewed}
        )
    assert after_original_expiry.status_code == 200


def test_expired_jwt_is_rejected_without_renewal(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token(expires_minutes=1)
    now += 60

    with _client() as client:
        response = client.get("/api/test", headers={"AtlasClaw-Authenticate": token})

    assert response.status_code == 401
    assert "AtlasClaw-Authenticate" not in response.headers
    assert "set-cookie" not in response.headers


def test_denied_request_does_not_renew_jwt(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token()
    with _client() as client:
        response = client.get("/api/denied", headers={"AtlasClaw-Authenticate": token})
    assert response.status_code == 403
    assert "AtlasClaw-Authenticate" not in response.headers
    assert "set-cookie" not in response.headers


def test_cookie_only_request_renews_session_cookie_for_https_base_path(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token()
    now += 60

    with _client(base_path="/atlasclaw") as client:
        response = client.get(
            "https://testserver/api/test",
            cookies={"Custom-Atlas-Cookie": token},
        )

    assert response.status_code == 200
    renewed = response.headers["AtlasClaw-Authenticate"]
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "Custom-Atlas-Cookie=" in item
        and "Path=/atlasclaw" in item
        and "HttpOnly" in item
        and "SameSite=lax" in item
        and "Secure" in item
        for item in cookies
    )
    assert response.headers["Custom-Atlas-Auth"] == renewed


def test_auth_me_activity_renews_jwt(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token()
    now += 60

    with _client() as client:
        response = client.get("/api/auth/me", headers={"AtlasClaw-Authenticate": token})

    assert response.status_code == 200
    assert "AtlasClaw-Authenticate" in response.headers


def test_host_cookie_mode_renews_local_atlas_jwt(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token()
    now += 60

    with _client(provider="host_cookie") as client:
        response = client.get("/api/test", cookies={"AtlasClaw-Authenticate": token})

    assert response.status_code == 200
    assert "AtlasClaw-Authenticate" in response.headers
    assert response.headers["AtlasClaw-Authenticate"] != token


def test_host_cookie_mode_logout_does_not_reissue_session(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token()
    with _client(provider="host_cookie") as client:
        response = client.get(
            "/api/auth/logout",
            cookies={"AtlasClaw-Authenticate": token},
        )
    assert response.status_code == 200
    assert "AtlasClaw-Authenticate" not in response.headers
    assert "set-cookie" in response.headers


def test_nonlocal_atlas_jwt_is_not_renewed(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    token = _token(auth_type="oidc")
    now += 60
    with _client() as client:
        response = client.get("/api/test", headers={"AtlasClaw-Authenticate": token})
    assert response.status_code == 200
    assert "AtlasClaw-Authenticate" not in response.headers
    assert "set-cookie" not in response.headers


def test_anonymous_and_opaque_api_token_requests_are_not_renewed(monkeypatch) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(jwt_token.time, "time", lambda: now)
    with _client() as client:
        anonymous = client.get("/api/test")

    assert anonymous.status_code == 401
    assert "AtlasClaw-Authenticate" not in anonymous.headers
    assert "set-cookie" not in anonymous.headers


def test_opaque_api_token_is_not_renewed(monkeypatch) -> None:
    async def authenticate_api_token(self, credential: str):
        return SimpleNamespace(user_id="api-user", is_anonymous=False)

    monkeypatch.setattr(AuthMiddleware, "_authenticate_user_access_token", authenticate_api_token)
    with _client() as client:
        response = client.get("/api/test", headers={"Authorization": "Bearer ac_pat_v1_secret"})

    assert response.status_code == 200
    assert "AtlasClaw-Authenticate" not in response.headers
    assert "set-cookie" not in response.headers
