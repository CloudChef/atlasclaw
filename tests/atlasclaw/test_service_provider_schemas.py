# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import app.atlasclaw.api.deps_context as deps_context
from app.atlasclaw.api.service_provider_schemas import (
    ProviderAuthModeDefinition,
    ProviderSchemaDefinition,
    ProviderSchemaField,
    clear_provider_schema_definitions,
    get_provider_schema_catalog,
    load_provider_schema_definition,
    normalize_provider_auth_type_chain,
    normalize_provider_config,
    register_provider_schema_definition,
)
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.skills.registry import SkillRegistry
from tests.atlasclaw.provider_schema_fixtures import (
    managed_manifest_copy,
    managed_provider_definition,
    register_default_provider_schemas,
    tracker_provider_definition,
    write_provider_manifest,
)


@pytest.fixture(autouse=True)
def provider_manifests():
    clear_provider_schema_definitions()
    register_default_provider_schemas()
    yield
    clear_provider_schema_definitions()


def test_load_provider_schema_definition_parses_manifest_shape(tmp_path: Path):
    manifest_path = write_provider_manifest(
        tmp_path / "provider.schema.json",
        managed_manifest_copy(),
    )

    definition = load_provider_schema_definition(manifest_path)

    assert definition.provider_type == "managed"
    assert definition.display_name == "Managed Provider"
    assert definition.icon_path == "assets/icon.svg"
    assert definition.default_auth_type == "user_token"
    assert definition.required_fields_for_auth_type("credential") == ("username", "password")
    assert "session_secret" in definition.sensitive_field_names()


def test_provider_schema_catalog_exposes_manifest_metadata_and_excludes_dingtalk():
    catalog = {
        item["provider_type"]: item
        for item in get_provider_schema_catalog()
    }

    assert set(catalog.keys()) == {"managed", "tracker"}
    assert "dingtalk" not in catalog

    managed = catalog["managed"]
    fields = {
        field["name"]: field
        for field in managed["schema"]["fields"]
    }

    assert managed["name_i18n_key"] == "provider.catalog.managed.name"
    assert managed["icon_path"] == "assets/icon.svg"
    assert fields["base_url"]["default"] == "https://managed.example.com"
    assert fields["auth_type"]["type"] == "hidden"
    assert fields["auth_type"]["default"] == "user_token"
    assert fields["user_token"]["type"] == "password"
    assert fields["user_token"]["sensitive"] is True
    assert fields["user_token"]["scope"] == "user"
    assert fields["user_token"]["auth_types"] == ["user_token"]
    assert fields["provider_token"]["auth_types"] == ["provider_token"]
    assert fields["cookie"]["auth_types"] == ["cookie"]
    assert fields["username"]["auth_types"] == ["credential"]
    assert "default_business_group" not in fields


def test_tracker_manifest_uses_password_canonical_field_without_token_alias():
    catalog = {
        item["provider_type"]: item
        for item in get_provider_schema_catalog()
    }
    tracker_fields = {
        field["name"]: field
        for field in catalog["tracker"]["schema"]["fields"]
    }

    assert tracker_fields["auth_type"]["default"] == "credential"
    assert tracker_fields["password"]["label"] == "Password / API Token"
    assert "aliases" not in tracker_fields["password"]
    assert tracker_fields["password"]["sensitive"] is True


def test_runtime_empty_schema_registry_is_authoritative(monkeypatch):
    class EmptyProviderRegistry:
        def get_all_provider_schema_definitions(self):
            return {}

    monkeypatch.setattr(
        deps_context,
        "_api_context",
        SimpleNamespace(service_provider_registry=EmptyProviderRegistry()),
    )

    assert get_provider_schema_catalog() == []
    config = {"base_url": "https://managed.example.com", "token": "plain"}
    assert normalize_provider_config("managed", config) == config


def test_provider_schema_catalog_accepts_backend_field_defaults():
    catalog = {
        item["provider_type"]: item
        for item in get_provider_schema_catalog(
            field_defaults={
                "managed": {
                    "base_url": "https://cmp.team-a.local",
                }
            }
        )
    }

    managed = catalog["managed"]
    fields = {
        field["name"]: field
        for field in managed["schema"]["fields"]
    }

    assert fields["base_url"]["default"] == "https://cmp.team-a.local"


def test_normalize_provider_config_applies_hidden_schema_defaults():
    normalized = normalize_provider_config(
        "managed",
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
        "managed",
        {
            "base_url": "",
            "auth_type": "user_token",
            "user_token": "token-123",
        },
    )

    assert normalized == {
        "base_url": "https://managed.example.com",
        "auth_type": "user_token",
        "user_token": "token-123",
    }


def test_normalize_provider_config_preserves_ordered_multi_auth_chain():
    normalized = normalize_provider_config(
        "managed",
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
        "managed",
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
            "managed",
            {
                "base_url": "https://cmp.team-a.local",
            },
        )


def test_normalize_provider_config_rejects_tracker_token_without_password():
    with pytest.raises(ValueError, match="password"):
        normalize_provider_config(
            "tracker",
            {
                "base_url": "https://company.atlassian.net",
                "username": "admin@example.com",
                "token": "legacy-token-value",
            },
        )


def test_normalize_provider_config_drops_declared_alias_after_canonicalization():
    register_provider_schema_definition(
        ProviderSchemaDefinition(
            provider_type="aliasdemo",
            display_name="Alias Demo",
            default_auth_type="credential",
            fields=(
                ProviderSchemaField(name="base_url", required=True),
                ProviderSchemaField(name="auth_type", type="hidden", default="credential"),
                ProviderSchemaField(
                    name="username",
                    required=True,
                    auth_types=("credential",),
                ),
                ProviderSchemaField(
                    name="password",
                    type="password",
                    required=True,
                    sensitive=True,
                    auth_types=("credential",),
                    aliases=("secret_alias",),
                ),
            ),
        )
    )

    normalized = normalize_provider_config(
        "aliasdemo",
        {
            "base_url": "https://alias.example.test",
            "username": "user",
            "secret_alias": "alias-secret",
        },
    )

    assert normalized["password"] == "alias-secret"
    assert "secret_alias" not in normalized


def test_strip_auth_fields_for_runtime_keeps_optional_fields_for_selected_auth_mode():
    definition = ProviderSchemaDefinition(
        provider_type="optional-auth",
        display_name="Optional Auth",
        default_auth_type="credential",
        auth_modes={
            "credential": ProviderAuthModeDefinition(required_fields=("username", "password")),
            "user_token": ProviderAuthModeDefinition(required_fields=("user_token",)),
        },
        fields=(
            ProviderSchemaField(name="base_url", required=True),
            ProviderSchemaField(name="auth_type", type="hidden", default="credential"),
            ProviderSchemaField(name="username", required=True, auth_types=("credential",)),
            ProviderSchemaField(
                name="password",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("credential",),
            ),
            ProviderSchemaField(name="auth_url", auth_types=("credential",)),
            ProviderSchemaField(
                name="user_token",
                type="password",
                required=True,
                sensitive=True,
                auth_types=("user_token",),
            ),
        ),
    )

    runtime_config = definition.strip_auth_fields_for_runtime(
        {
            "base_url": "https://optional.example.com",
            "auth_type": "credential",
            "username": "svc",
            "password": "secret",
            "auth_url": "https://optional.example.com/login",
            "user_token": "inactive-token",
        },
        "credential",
    )

    assert runtime_config == {
        "auth_type": "credential",
        "base_url": "https://optional.example.com",
        "username": "svc",
        "password": "secret",
        "auth_url": "https://optional.example.com/login",
    }


def test_tracker_password_is_redacted():
    definition = tracker_provider_definition()

    assert "password" in definition.sensitive_field_names()
    assert "token" not in definition.sensitive_field_names()


def test_managed_auth_modes_cover_supported_modes():
    definition = managed_provider_definition()

    assert definition.required_fields_for_auth_type("provider_token") == ("provider_token",)
    assert definition.required_fields_for_auth_type("user_token") == ("user_token",)
    assert definition.required_fields_for_auth_type("cookie") == ("cookie",)
    assert definition.required_fields_for_auth_type("credential") == ("username", "password")


def test_missing_provider_manifest_leaves_catalog_and_normalization_unavailable(tmp_path: Path):
    clear_provider_schema_definitions()
    provider_dir = tmp_path / "provider"
    provider_dir.mkdir()
    (provider_dir / "PROVIDER.md").write_text(
        "---\nprovider_type: custom\ndisplay_name: Custom\n---\n\n# Custom\n",
        encoding="utf-8",
    )

    assert get_provider_schema_catalog() == []
    config = {"base_url": "https://custom.example.com", "token": "plain"}
    assert normalize_provider_config("custom", config) == config


def test_missing_provider_manifest_does_not_block_provider_skill_loading(tmp_path: Path):
    clear_provider_schema_definitions()
    provider_dir = tmp_path / "custom"
    skills_dir = provider_dir / "skills"
    skills_dir.mkdir(parents=True)
    (provider_dir / "PROVIDER.md").write_text(
        "---\nprovider_type: custom\ndisplay_name: Custom\n---\n\n# Custom\n",
        encoding="utf-8",
    )
    (skills_dir / "ping.py").write_text(
        "\n".join(
            [
                "from app.atlasclaw.skills.registry import SkillMetadata",
                "SKILL_METADATA = SkillMetadata(name='ping', description='Ping')",
                "async def handler(ctx, **kwargs):",
                "    return {'ok': True}",
            ]
        ),
        encoding="utf-8",
    )

    provider_registry = ServiceProviderRegistry()
    skill_registry = SkillRegistry()

    assert provider_registry.load_from_directory(tmp_path) == 1
    assert provider_registry.get_all_provider_schema_definitions() == {}
    assert provider_registry.register_skills_to(skill_registry) == 1
    assert skill_registry.get("custom__ping") is not None


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
