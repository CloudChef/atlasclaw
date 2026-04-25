# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import pytest

from app.atlasclaw.api.service_provider_schemas import (
    ProviderSchemaDefinition,
    ProviderSchemaField,
    get_provider_schema_catalog,
    normalize_provider_auth_type_chain,
    normalize_provider_config,
)


def test_provider_schema_catalog_exposes_hidden_auth_type_defaults():
    catalog = {
        item["provider_type"]: item
        for item in get_provider_schema_catalog()
    }

    smartcmp = catalog["smartcmp"]
    fields = {
        field["name"]: field
        for field in smartcmp["schema"]["fields"]
    }

    assert smartcmp["name_i18n_key"] == "provider.catalog.smartcmp.name"
    assert fields["base_url"]["default"] == "https://console.smartcmp.cloud"
    assert fields["auth_type"]["type"] == "hidden"
    assert fields["auth_type"]["default"] == "user_token"
    assert fields["user_token"]["type"] == "password"
    assert fields["user_token"]["sensitive"] is True
    assert fields["user_token"]["auth_types"] == ["user_token"]
    assert fields["provider_token"]["type"] == "password"
    assert fields["provider_token"]["sensitive"] is True
    assert fields["provider_token"]["auth_types"] == ["provider_token"]
    assert fields["username"]["auth_types"] == ["credential"]


def test_provider_schema_catalog_accepts_backend_field_defaults():
    catalog = {
        item["provider_type"]: item
        for item in get_provider_schema_catalog(
            field_defaults={
                "smartcmp": {
                    "base_url": "https://console.smartcmp.cloud",
                }
            }
        )
    }

    smartcmp = catalog["smartcmp"]
    fields = {
        field["name"]: field
        for field in smartcmp["schema"]["fields"]
    }

    assert fields["base_url"]["default"] == "https://console.smartcmp.cloud"


def test_normalize_provider_config_applies_hidden_schema_defaults():
    normalized = normalize_provider_config(
        "smartcmp",
        {
            "base_url": "https://cmp.team-a.local",
            "user_token": "token-123",
        },
    )

    assert normalized == {
        "base_url": "https://cmp.team-a.local",
        "auth_type": "user_token",
        "user_token": "token-123",
    }


def test_normalize_provider_config_uses_schema_default_when_base_url_is_blank():
    normalized = normalize_provider_config(
        "smartcmp",
        {
            "base_url": "",
            "auth_type": "user_token",
            "user_token": "token-123",
        },
    )

    assert normalized == {
        "base_url": "https://console.smartcmp.cloud",
        "auth_type": "user_token",
        "user_token": "token-123",
    }


def test_normalize_provider_config_preserves_ordered_multi_auth_chain():
    normalized = normalize_provider_config(
        "smartcmp",
        {
            "base_url": "https://cmp.team-a.local",
            "auth_type": ["cookie", "user_token", "cookie", "  "],
        },
    )

    assert normalized == {
        "base_url": "https://cmp.team-a.local",
        "auth_type": ["cookie", "user_token"],
    }


def test_normalize_provider_config_accepts_multi_auth_without_requiring_all_auth_fields():
    normalized = normalize_provider_config(
        "smartcmp",
        {
            "base_url": "https://cmp.team-a.local",
            "auth_type": ["provider_token", "cookie", "user_token"],
        },
    )

    assert normalized == {
        "base_url": "https://cmp.team-a.local",
        "auth_type": ["provider_token", "cookie", "user_token"],
    }


def test_normalize_provider_config_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="user_token"):
        normalize_provider_config(
            "smartcmp",
            {
                "base_url": "https://cmp.team-a.local",
            },
        )


def test_normalize_provider_auth_type_chain_rejects_unknown_auth_type():
    with pytest.raises(ValueError, match="Unsupported auth_type: cmp"):
        normalize_provider_auth_type_chain(["cookie", "cmp", "user_token"])


def test_provider_schema_field_rejects_unknown_auth_type_scope():
    with pytest.raises(ValueError, match="Unsupported auth_type: cmp"):
        ProviderSchemaField(name="session", auth_types=("cmp",))


def test_provider_schema_definition_rejects_unknown_default_auth_type():
    with pytest.raises(ValueError, match="Unsupported auth_type: cmp"):
        ProviderSchemaDefinition(
            provider_type="legacy",
            display_name="Legacy",
            name_i18n_key="provider.catalog.legacy.name",
            description="Legacy provider",
            description_i18n_key="provider.catalog.legacy.description",
            badge="LEG",
            icon="LG",
            accent="#111827",
            default_auth_type="cmp",
            fields=(
                ProviderSchemaField(name="base_url"),
                ProviderSchemaField(name="auth_type", type="hidden", default="cmp"),
            ),
        )


def test_provider_schema_definition_rejects_unknown_hidden_auth_type_default():
    with pytest.raises(ValueError, match="Unsupported auth_type: cmp"):
        ProviderSchemaDefinition(
            provider_type="legacy",
            display_name="Legacy",
            name_i18n_key="provider.catalog.legacy.name",
            description="Legacy provider",
            description_i18n_key="provider.catalog.legacy.description",
            badge="LEG",
            icon="LG",
            accent="#111827",
            default_auth_type="user_token",
            fields=(
                ProviderSchemaField(name="base_url"),
                ProviderSchemaField(name="auth_type", type="hidden", default="cmp"),
            ),
        )
