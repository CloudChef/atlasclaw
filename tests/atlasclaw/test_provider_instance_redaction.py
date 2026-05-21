# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import logging

import pytest

from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.tools.providers.instance_tools import (
    PROVIDER_INSTANCE_SELECTIONS_KEY,
    list_provider_instances_tool,
    select_provider_instance_tool,
)


@pytest.fixture(autouse=True)
def _enable_provider_registry_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep caplog tests isolated from Alembic fileConfig logger disabling."""
    monkeypatch.setattr(
        logging.getLogger("app.atlasclaw.core.provider_registry"),
        "disabled",
        False,
    )


def _smartcmp_instance_config() -> dict[str, str]:
    return {
        "provider_type": "smartcmp",
        "instance_name": "default",
        "base_url": "https://cmp.example.com/platform-api",
        "auth_type": "user_token",
        "usage_hint": "Use for SmartCMP production approval workflows.",
        "cookie": "AtlasClaw-Host-Authenticate=session-cookie",
        "password": "super-secret-password",
        "user_token": "fake-smartcmp-user-token",
        "robot_auth": {
            "preapproval_bot": {
                "auth_type": "provider_token",
                "provider_token": "cmp_tk_robot_secret",
                "allowed_skills": ["smartcmp:preapproval-agent"],
            }
        },
    }


def test_service_provider_registry_redacts_schema_sensitive_fields() -> None:
    registry = ServiceProviderRegistry()
    registry.load_instances_from_config({"smartcmp": {"default": _smartcmp_instance_config()}})

    redacted = registry.get_instance_config_redacted("smartcmp", "default")

    assert redacted is not None
    assert redacted["base_url"] == "https://cmp.example.com/platform-api"
    assert redacted["usage_hint"] == "Use for SmartCMP production approval workflows."
    assert redacted["cookie"] == "***"
    assert redacted["password"] == "***"
    assert redacted["user_token"] == "***"
    assert redacted["robot_auth"]["preapproval_bot"]["provider_token"] == "***"
    assert redacted["robot_auth"]["preapproval_bot"]["allowed_skills"] == [
        "smartcmp:preapproval-agent"
    ]


def test_service_provider_registry_skips_unknown_auth_type_and_logs_error(caplog) -> None:
    registry = ServiceProviderRegistry()

    with caplog.at_level(logging.ERROR, logger="app.atlasclaw.core.provider_registry"):
        registry.load_instances_from_config(
            {
                "smartcmp": {
                    "legacy": {
                        "base_url": "https://legacy.smartcmp.cloud",
                        "auth_type": "cmp",
                    },
                    "default": _smartcmp_instance_config(),
                }
            }
        )

    assert registry.list_instances("smartcmp") == ["default"]
    assert registry.get_instance_config("smartcmp", "legacy") is None
    assert "Skipping provider instance smartcmp.legacy" in caplog.text
    assert "Unsupported auth_type: cmp" in caplog.text


def test_resolved_provider_instance_registry_redacts_schema_sensitive_fields() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry

    registry = ResolvedProviderInstanceRegistry(
        {"smartcmp": {"default": _smartcmp_instance_config()}}
    )

    redacted = registry.get_instance_config_redacted("smartcmp", "default")

    assert redacted is not None
    assert redacted["base_url"] == "https://cmp.example.com/platform-api"
    assert redacted["cookie"] == "***"
    assert redacted["password"] == "***"
    assert redacted["user_token"] == "***"


def test_resolved_provider_instance_registry_keeps_sensitive_key_fallback_without_schema() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry

    registry = ResolvedProviderInstanceRegistry(
        {
            "custom": {
                "default": {
                    "provider_type": "custom",
                    "instance_name": "default",
                    "base_url": "https://custom.example.com",
                    "credential": "plain-credential-value",
                    "display_name": "Custom Provider",
                }
            }
        }
    )

    redacted = registry.get_instance_config_redacted("custom", "default")

    assert redacted is not None
    assert redacted["base_url"] == "https://custom.example.com"
    assert redacted["display_name"] == "Custom Provider"
    assert redacted["credential"] == "***"


def test_list_provider_instances_tool_masks_schema_sensitive_params() -> None:
    registry = ServiceProviderRegistry()
    registry.load_instances_from_config({"smartcmp": {"default": _smartcmp_instance_config()}})

    class _Deps:
        extra = {
            "available_providers": registry.get_available_providers_summary(),
            "_service_provider_registry": registry,
        }

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(list_provider_instances_tool(_Ctx(), "smartcmp"))
    text = result["content"][0]["text"]
    params = result["details"]["instances"][0]["params"]

    assert "session-cookie" not in text
    assert "Use for SmartCMP production approval workflows." in text
    assert "super-secret-password" not in text
    assert "fake-smartcmp-user-token" not in text
    assert result["details"]["instances"][0]["usage_hint"] == (
        "Use for SmartCMP production approval workflows."
    )
    assert params["cookie"] == "***"
    assert params["password"] == "***"
    assert params["user_token"] == "***"


def test_select_provider_instance_persists_session_sticky_selection() -> None:
    registry = ServiceProviderRegistry()
    registry.load_instances_from_config({"smartcmp": {"default": _smartcmp_instance_config()}})

    class _SessionManager:
        def __init__(self) -> None:
            self.updates = []

        async def update_extra(self, session_key, updates):
            self.updates.append((session_key, updates))

    session_manager = _SessionManager()

    class _Deps:
        session_key = "agent:main:user:u-1:main"
        extra = {
            "available_providers": registry.get_available_providers_summary(),
            "_service_provider_registry": registry,
        }
        session_manager = None

    _Deps.session_manager = session_manager

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(select_provider_instance_tool(_Ctx(), "smartcmp", "default"))

    assert result["is_error"] is False
    assert _Ctx.deps.extra[PROVIDER_INSTANCE_SELECTIONS_KEY] == {"smartcmp": "default"}
    assert session_manager.updates == [
        (
            "agent:main:user:u-1:main",
            {PROVIDER_INSTANCE_SELECTIONS_KEY: {"smartcmp": "default"}},
        )
    ]


def test_provider_skill_wrapper_uses_request_scoped_filtered_registry() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry

    registry = ServiceProviderRegistry()
    registry.load_instances_from_config({"smartcmp": {"default": _smartcmp_instance_config()}})

    async def handler(ctx, **kwargs):
        return {
            "provider_type": ctx.deps.extra.get("provider_type"),
            "provider_instance_name": ctx.deps.extra.get("provider_instance_name"),
        }

    wrapped = registry._make_handler_wrapper(handler=handler, provider_type="smartcmp")

    class _Deps:
        extra = {
            "provider_instances": {},
            "available_providers": {},
            "_service_provider_registry": ResolvedProviderInstanceRegistry({}),
        }

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(wrapped(_Ctx()))

    assert result["is_error"] is True
    assert "no configured instances" in result["content"][0]["text"]


def test_provider_skill_wrapper_requires_selection_for_multiple_instances() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry

    provider_instances = {
        "smartcmp": {
            "prod": {
                **_smartcmp_instance_config(),
                "usage_hint": "Use for production CMP approvals.",
            },
            "dev": {
                **_smartcmp_instance_config(),
                "base_url": "https://dev-cmp.example.com/platform-api",
                "usage_hint": "Use for development CMP testing.",
            },
        }
    }
    registry = ServiceProviderRegistry()

    async def handler(ctx, **kwargs):
        return {
            "provider_type": ctx.deps.extra.get("provider_type"),
            "provider_instance_name": ctx.deps.extra.get("provider_instance_name"),
        }

    wrapped = registry._make_handler_wrapper(handler=handler, provider_type="smartcmp")

    class _Deps:
        extra = {
            "provider_instances": provider_instances,
            "available_providers": {"smartcmp": ["prod", "dev"]},
            "_service_provider_registry": ResolvedProviderInstanceRegistry(provider_instances),
        }

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(wrapped(_Ctx()))

    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "Provider 'smartcmp' has 2 instances" in text
    assert "Use for production CMP approvals." in text
    assert "Use for development CMP testing." in text
    assert "select_provider_instance" in text


def test_provider_skill_wrapper_uses_session_sticky_selection() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry
    from app.atlasclaw.tools.providers.instance_tools import PROVIDER_INSTANCE_SELECTIONS_KEY

    provider_instances = {
        "smartcmp": {
            "prod": _smartcmp_instance_config(),
            "dev": {
                **_smartcmp_instance_config(),
                "base_url": "https://dev-cmp.example.com/platform-api",
            },
        }
    }
    registry = ServiceProviderRegistry()

    async def handler(ctx, **kwargs):
        return {
            "provider_type": ctx.deps.extra.get("provider_type"),
            "provider_instance_name": ctx.deps.extra.get("provider_instance_name"),
            "base_url": ctx.deps.extra.get("provider_instance", {}).get("base_url"),
        }

    wrapped = registry._make_handler_wrapper(handler=handler, provider_type="smartcmp")

    class _Deps:
        extra = {
            PROVIDER_INSTANCE_SELECTIONS_KEY: {"smartcmp": "dev"},
            "provider_instances": provider_instances,
            "available_providers": {"smartcmp": ["prod", "dev"]},
            "_service_provider_registry": ResolvedProviderInstanceRegistry(provider_instances),
        }

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(wrapped(_Ctx()))

    assert result == {
        "provider_type": "smartcmp",
        "provider_instance_name": "dev",
        "base_url": "https://dev-cmp.example.com/platform-api",
    }


def test_provider_skill_wrapper_explicit_selection_overrides_session_sticky_selection() -> None:
    from app.atlasclaw.core.user_provider_bindings import ResolvedProviderInstanceRegistry
    from app.atlasclaw.tools.providers.instance_tools import PROVIDER_INSTANCE_SELECTIONS_KEY

    provider_instances = {
        "smartcmp": {
            "prod": _smartcmp_instance_config(),
            "dev": {
                **_smartcmp_instance_config(),
                "base_url": "https://dev-cmp.example.com/platform-api",
            },
        }
    }
    registry = ServiceProviderRegistry()

    async def handler(ctx, **kwargs):
        return {
            "provider_instance_name": ctx.deps.extra.get("provider_instance_name"),
            "base_url": ctx.deps.extra.get("provider_instance", {}).get("base_url"),
        }

    wrapped = registry._make_handler_wrapper(handler=handler, provider_type="smartcmp")

    class _Deps:
        extra = {
            PROVIDER_INSTANCE_SELECTIONS_KEY: {"smartcmp": "dev"},
            "provider_type": "smartcmp",
            "provider_instance_name": "prod",
            "provider_instances": provider_instances,
            "available_providers": {"smartcmp": ["prod", "dev"]},
            "_service_provider_registry": ResolvedProviderInstanceRegistry(provider_instances),
        }

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(wrapped(_Ctx()))

    assert result == {
        "provider_instance_name": "prod",
        "base_url": "https://cmp.example.com/platform-api",
    }
