# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from .schemas import LocalLoginRequest
from .services.auth_service import (
    begin_sso_login,
    complete_sso_login,
    get_current_user_payload,
    logout_user,
    perform_local_login,
)


def register_auth_routes(router: APIRouter) -> None:
    @router.post("/auth/local/login")
    async def local_login(request: Request, body: LocalLoginRequest) -> Response:
        return await perform_local_login(request, body)

    @router.get("/auth/login")
    async def sso_login(request: Request):
        return await begin_sso_login(request)

    @router.get("/auth/callback")
    async def sso_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> Response:
        return await complete_sso_login(
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )

    @router.get("/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        return await get_current_user_payload(request)

    @router.get("/auth/logout")
    async def auth_logout(request: Request, redirect: bool = True) -> Response:
        return await logout_user(request, redirect=redirect)
