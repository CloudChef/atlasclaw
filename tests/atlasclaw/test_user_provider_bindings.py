# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import pytest

from app.atlasclaw.core.user_provider_bindings import (
    build_resolved_provider_instances,
    resolve_provider_instance_config,
)


def test_resolve_provider_instance_config_prefers_first_usable_auth_mode() -> None:
    resolved = resolve_provider_instance_config(
        "smartcmp",
        "default",
        template_config={
            "base_url": "https://cmp.example.com",
            "auth_type": ["cookie", "user_token"],
            "cookie": "CloudChef-Authenticate=session-cookie",
        },
        user_config={"user_token": "user-token-123"},
    )

    assert resolved["auth_type"] == "cookie"
    assert resolved["cookie"] == "CloudChef-Authenticate=session-cookie"
    assert "user_token" not in resolved


def test_resolve_provider_instance_config_falls_back_to_user_token_when_sso_missing() -> None:
    resolved = resolve_provider_instance_config(
        "generic",
        "default",
        template_config={
            "base_url": "https://cmp.example.com",
            "auth_type": ["sso", "user_token"],
        },
        user_config={"user_token": "user-token-123"},
        runtime_context={
            "provider_sso_available": False,
            "provider_sso_token": "",
        },
    )

    assert resolved["auth_type"] == "user_token"
    assert resolved["user_token"] == "user-token-123"


def test_resolve_provider_instance_config_uses_shared_provider_token() -> None:
    resolved = resolve_provider_instance_config(
        "smartcmp",
        "default",
        template_config={
            "base_url": "https://cmp.example.com",
            "auth_type": ["provider_token", "user_token"],
            "provider_token": "shared-provider-token",
        },
        user_config={
            "provider_token": "ignored-user-provider-token",
            "user_token": "user-token-123",
        },
        runtime_context={
            "provider_sso_available": False,
            "provider_sso_token": "",
        },
    )

    assert resolved["auth_type"] == "provider_token"
    assert resolved["provider_token"] == "shared-provider-token"
    assert "user_token" not in resolved


def test_resolve_provider_instance_config_uses_request_scoped_cookie() -> None:
    resolved = resolve_provider_instance_config(
        "smartcmp",
        "default",
        template_config={
            "base_url": "https://cmp.example.com",
            "auth_type": ["cookie", "provider_token"],
            "provider_token": "shared-provider-token",
        },
        runtime_context={
            "provider_cookie_available": True,
            "provider_cookie_token": "request-cookie-token",
        },
    )

    assert resolved["auth_type"] == "cookie"
    assert resolved["cookie"] == "request-cookie-token"
    assert "provider_token" not in resolved


def test_resolve_provider_instance_config_uses_sso_and_strips_persisted_auth_fields() -> None:
    resolved = resolve_provider_instance_config(
        "generic",
        "default",
        template_config={
            "base_url": "https://cmp.example.com",
            "auth_type": ["sso", "cookie", "user_token"],
            "cookie": "CloudChef-Authenticate=session-cookie",
        },
        user_config={"user_token": "user-token-123"},
        runtime_context={
            "provider_sso_available": True,
            "provider_sso_token": "oidc-access-token",
        },
    )

    assert resolved["auth_type"] == "sso"
    assert resolved["base_url"] == "https://cmp.example.com"
    assert "cookie" not in resolved
    assert "user_token" not in resolved


def test_resolve_provider_instance_config_raises_when_chain_has_no_usable_auth() -> None:
    with pytest.raises(ValueError, match="no usable auth mode"):
        resolve_provider_instance_config(
            "generic",
            "default",
            template_config={
                "base_url": "https://cmp.example.com",
                "auth_type": ["sso", "cookie", "user_token"],
            },
            runtime_context={
                "provider_sso_available": False,
                "provider_sso_token": "",
            },
        )


def test_build_resolved_provider_instances_resolves_global_instances_with_runtime_context() -> None:
    resolved = build_resolved_provider_instances(
        {
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": ["cookie", "user_token"],
                    "user_token": "user-token-123",
                }
            }
        }
    )

    assert resolved["smartcmp"]["default"]["auth_type"] == "user_token"
    assert resolved["smartcmp"]["default"]["user_token"] == "user-token-123"
    assert "cookie" not in resolved["smartcmp"]["default"]
