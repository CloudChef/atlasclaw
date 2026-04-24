# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

from starlette.applications import Starlette

from app.atlasclaw.auth.middleware import AuthMiddleware


class _Provider:
    @staticmethod
    def provider_name() -> str:
        return "oidc"


class _Strategy:
    primary_provider = _Provider()


def test_build_user_info_from_payload_exposes_provider_sso_context() -> None:
    middleware = AuthMiddleware(Starlette(), strategy=_Strategy())

    user = middleware._build_user_info_from_payload(
        {
            "sub": "user-1",
            "display_name": "User One",
            "roles": ["admin"],
            "auth_type": "oidc:test",
            "external_subject": "ext-user-1",
            "is_admin": True,
        },
        "atlas-jwt-token",
        provider_sso_token="oidc-access-token",
    )

    assert user.raw_token == "atlas-jwt-token"
    assert user.extra["provider_sso_available"] is True
    assert user.extra["provider_sso_token"] == "oidc-access-token"
