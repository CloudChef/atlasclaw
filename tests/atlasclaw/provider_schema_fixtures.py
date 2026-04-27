# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider schema fixtures for core tests.

These fixtures intentionally use provider-neutral names so core tests exercise
the manifest contract without depending on concrete provider packages.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.atlasclaw.api.service_provider_schemas import (
    ProviderAuthModeDefinition,
    ProviderSchemaDefinition,
    ProviderSchemaField,
    register_provider_schema_definition,
)

MANAGED_PROVIDER_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "provider_type": "managed",
    "catalog": {
        "display_name": "Managed Provider",
        "name_i18n_key": "provider.catalog.managed.name",
        "description": "Managed external service provider for contract tests.",
        "description_i18n_key": "provider.catalog.managed.description",
        "badge": "EXT",
        "icon": "MP",
        "icon_path": "assets/icon.svg",
        "accent": "#0f766e",
    },
    "config_schema": {
        "default_auth_type": "user_token",
        "auth_modes": {
            "provider_token": {"required_fields": ["provider_token"]},
            "user_token": {"required_fields": ["user_token"]},
            "cookie": {"required_fields": ["cookie"]},
            "credential": {"required_fields": ["username", "password"]},
        },
        "fields": [
            {
                "name": "base_url",
                "type": "url",
                "required": True,
                "scope": "instance",
                "default": "https://managed.example.com",
                "label": "Base URL",
                "placeholder": "https://managed.example.com",
            },
            {
                "name": "auth_type",
                "type": "hidden",
                "scope": "instance",
                "default": "user_token",
            },
            {
                "name": "user_token",
                "type": "password",
                "required": True,
                "sensitive": True,
                "scope": "user",
                "auth_types": ["user_token"],
                "label": "User Token",
                "placeholder": "Enter user token",
            },
            {
                "name": "provider_token",
                "type": "password",
                "required": True,
                "sensitive": True,
                "scope": "instance",
                "auth_types": ["provider_token"],
                "label": "Provider Token",
                "placeholder": "Enter shared provider token",
            },
            {
                "name": "username",
                "type": "text",
                "required": True,
                "scope": "instance",
                "auth_types": ["credential"],
                "label": "Username",
                "placeholder": "service-account",
            },
            {
                "name": "password",
                "type": "password",
                "required": True,
                "sensitive": True,
                "scope": "instance",
                "auth_types": ["credential"],
                "label": "Password",
                "placeholder": "Enter password",
            },
            {
                "name": "cookie",
                "type": "password",
                "required": True,
                "sensitive": True,
                "scope": "instance",
                "auth_types": ["cookie"],
                "label": "Cookie",
                "placeholder": "session=...",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "scope": "instance",
                "label": "Timeout Seconds",
            },
        ],
    },
    "redaction": {
        "sensitive_fields": ["session_secret"],
    },
}

TRACKER_PROVIDER_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "provider_type": "tracker",
    "catalog": {
        "display_name": "Tracker Provider",
        "description": "Issue tracker provider used for core contract tests.",
        "badge": "ISSUE",
        "icon": "TP",
        "icon_path": "assets/icon.svg",
        "accent": "#0052cc",
    },
    "config_schema": {
        "default_auth_type": "credential",
        "auth_modes": {
            "credential": {"required_fields": ["username", "password"]},
        },
        "fields": [
            {
                "name": "base_url",
                "type": "url",
                "required": True,
                "scope": "instance",
                "label": "Base URL",
                "placeholder": "https://tracker.example.com",
            },
            {
                "name": "auth_type",
                "type": "hidden",
                "scope": "instance",
                "default": "credential",
            },
            {
                "name": "username",
                "type": "text",
                "required": True,
                "scope": "instance",
                "auth_types": ["credential"],
                "label": "Username / Email",
                "placeholder": "admin@example.com",
            },
            {
                "name": "password",
                "type": "password",
                "required": True,
                "sensitive": True,
                "scope": "instance",
                "auth_types": ["credential"],
                "label": "Password / API Token",
                "placeholder": "Enter password or API token",
            },
            {
                "name": "api_version",
                "type": "text",
                "required": False,
                "scope": "instance",
                "default": "2",
                "label": "API Version",
                "placeholder": "2",
            },
        ],
    },
}


def write_provider_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def managed_provider_definition(
    *,
    provider_type: str = "managed",
    display_name: str = "Managed Provider",
    default_base_url: str = "https://managed.example.com",
) -> ProviderSchemaDefinition:
    return ProviderSchemaDefinition(
        provider_type=provider_type,
        display_name=display_name,
        name_i18n_key=f"provider.catalog.{provider_type}.name",
        description="Managed external service provider for contract tests.",
        description_i18n_key=f"provider.catalog.{provider_type}.description",
        badge="EXT",
        icon="MP",
        icon_path="assets/icon.svg",
        accent="#0f766e",
        default_auth_type="user_token",
        auth_modes={
            "provider_token": ProviderAuthModeDefinition(required_fields=("provider_token",)),
            "user_token": ProviderAuthModeDefinition(required_fields=("user_token",)),
            "cookie": ProviderAuthModeDefinition(required_fields=("cookie",)),
            "credential": ProviderAuthModeDefinition(required_fields=("username", "password")),
        },
        fields=(
            ProviderSchemaField(
                name="base_url",
                type="url",
                required=True,
                default=default_base_url,
                label="Base URL",
                placeholder=default_base_url,
            ),
            ProviderSchemaField(name="auth_type", type="hidden", default="user_token"),
            ProviderSchemaField(
                name="user_token",
                type="password",
                required=True,
                sensitive=True,
                scope="user",
                auth_types=("user_token",),
                label="User Token",
            ),
            ProviderSchemaField(
                name="provider_token",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("provider_token",),
                label="Provider Token",
            ),
            ProviderSchemaField(
                name="username",
                type="text",
                required=True,
                auth_types=("credential",),
                label="Username",
            ),
            ProviderSchemaField(
                name="password",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("credential",),
                label="Password",
            ),
            ProviderSchemaField(
                name="cookie",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("cookie",),
                label="Cookie",
            ),
            ProviderSchemaField(
                name="timeout",
                type="number",
                label="Timeout Seconds",
            ),
        ),
        redaction_sensitive_fields=("session_secret",),
    )


def tracker_provider_definition() -> ProviderSchemaDefinition:
    return ProviderSchemaDefinition(
        provider_type="tracker",
        display_name="Tracker Provider",
        description="Issue tracker provider used for core contract tests.",
        badge="ISSUE",
        icon="TP",
        icon_path="assets/icon.svg",
        accent="#0052cc",
        default_auth_type="credential",
        auth_modes={
            "credential": ProviderAuthModeDefinition(required_fields=("username", "password")),
        },
        fields=(
            ProviderSchemaField(
                name="base_url",
                type="url",
                required=True,
                label="Base URL",
                placeholder="https://tracker.example.com",
            ),
            ProviderSchemaField(name="auth_type", type="hidden", default="credential"),
            ProviderSchemaField(
                name="username",
                type="text",
                required=True,
                auth_types=("credential",),
                label="Username / Email",
                placeholder="admin@example.com",
            ),
            ProviderSchemaField(
                name="password",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("credential",),
                label="Password / API Token",
                placeholder="Enter password or API token",
            ),
            ProviderSchemaField(
                name="api_version",
                type="text",
                default="2",
                label="API Version",
                placeholder="2",
            ),
        ),
    )


def register_default_provider_schemas() -> None:
    register_provider_schema_definition(managed_provider_definition())
    register_provider_schema_definition(tracker_provider_definition())


def managed_manifest_copy() -> dict[str, Any]:
    return deepcopy(MANAGED_PROVIDER_MANIFEST)


def tracker_manifest_copy() -> dict[str, Any]:
    return deepcopy(TRACKER_PROVIDER_MANIFEST)
