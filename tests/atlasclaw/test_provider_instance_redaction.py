# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import asyncio
import logging

from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.tools.providers.instance_tools import list_provider_instances_tool


def _smartcmp_instance_config() -> dict[str, str]:
    return {
        "provider_type": "smartcmp",
        "instance_name": "default",
        "base_url": "https://cmp.example.com/platform-api",
        "auth_type": "user_token",
        "cookie": "CloudChef-Authenticate=session-cookie",
        "password": "super-secret-password",
        "user_token": "fake-smartcmp-user-token",
    }


def test_service_provider_registry_redacts_schema_sensitive_fields() -> None:
    registry = ServiceProviderRegistry()
    registry.load_instances_from_config({"smartcmp": {"default": _smartcmp_instance_config()}})

    redacted = registry.get_instance_config_redacted("smartcmp", "default")

    assert redacted is not None
    assert redacted["base_url"] == "https://cmp.example.com/platform-api"
    assert redacted["cookie"] == "***"
    assert redacted["password"] == "***"
    assert redacted["user_token"] == "***"


def test_service_provider_registry_skips_unknown_auth_type_and_logs_error(caplog) -> None:
    registry = ServiceProviderRegistry()

    with caplog.at_level(logging.ERROR):
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
    assert "super-secret-password" not in text
    assert "fake-smartcmp-user-token" not in text
    assert params["cookie"] == "***"
    assert params["password"] == "***"
    assert params["user_token"] == "***"
