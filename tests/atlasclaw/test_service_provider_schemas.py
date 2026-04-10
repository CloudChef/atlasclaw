# -*- coding: utf-8 -*-

from __future__ import annotations

import pytest

from app.atlasclaw.api.service_provider_schemas import (
    get_provider_schema_catalog,
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


def test_normalize_provider_config_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="user_token"):
        normalize_provider_config(
            "smartcmp",
            {
                "base_url": "https://cmp.team-a.local",
            },
        )
