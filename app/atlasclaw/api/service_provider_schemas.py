# -*- coding: utf-8 -*-
"""Backend-managed service provider form schema definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class ProviderSchemaField:
    """Schema field used by provider-management UI."""

    name: str
    type: str = "text"
    required: bool = False
    sensitive: bool = False
    default: Optional[str] = None
    label: str = ""
    label_i18n_key: str = ""
    placeholder: str = ""
    placeholder_i18n_key: str = ""
    auth_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "sensitive": self.sensitive,
        }
        if self.default is not None:
            payload["default"] = self.default
        if self.label:
            payload["label"] = self.label
        if self.label_i18n_key:
            payload["label_i18n_key"] = self.label_i18n_key
        if self.placeholder:
            payload["placeholder"] = self.placeholder
        if self.placeholder_i18n_key:
            payload["placeholder_i18n_key"] = self.placeholder_i18n_key
        if self.auth_types:
            payload["auth_types"] = list(self.auth_types)
        return payload

    def with_default(self, default: Optional[str]) -> "ProviderSchemaField":
        """Return a copy with an overridden default value."""
        return ProviderSchemaField(
            name=self.name,
            type=self.type,
            required=self.required,
            sensitive=self.sensitive,
            default=default,
            label=self.label,
            label_i18n_key=self.label_i18n_key,
            placeholder=self.placeholder,
            placeholder_i18n_key=self.placeholder_i18n_key,
            auth_types=self.auth_types,
        )


@dataclass(frozen=True)
class ProviderSchemaDefinition:
    """Backend definition for a manageable service provider type."""

    provider_type: str
    display_name: str
    name_i18n_key: str
    description: str
    description_i18n_key: str
    badge: str
    icon: str
    accent: str
    fields: tuple[ProviderSchemaField, ...]
    default_auth_type: str = ""
    auth_type_variants: dict[str, tuple[ProviderSchemaField, ...]] = field(default_factory=dict)

    def resolve_fields(
        self,
        field_defaults: Optional[dict[str, str]] = None,
        filter_by_auth_type: bool = True,
    ) -> tuple[ProviderSchemaField, ...]:
        """Resolve active schema fields with injected defaults."""
        overrides = field_defaults or {}
        auth_type = str(
            overrides.get("auth_type")
            or self.default_auth_type
            or next(
                (
                    field.default
                    for field in self.fields
                    if field.name == "auth_type" and field.default
                ),
                "",
            )
        ).strip().lower()

        resolved_fields = list(self.fields)
        if filter_by_auth_type:
            resolved_fields = [
                field
                for field in resolved_fields
                if not field.auth_types or auth_type in field.auth_types
            ]
        resolved_fields.extend(self.auth_type_variants.get(auth_type, ()))

        return tuple(
            field.with_default(str(overrides[field.name]))
            if field.name in overrides and overrides[field.name] is not None
            else field
            for field in resolved_fields
        )

    def to_dict(self, field_defaults: Optional[dict[str, str]] = None) -> dict[str, Any]:
        return {
            "provider_type": self.provider_type,
            "display_name": self.display_name,
            "name_i18n_key": self.name_i18n_key,
            "description": self.description,
            "description_i18n_key": self.description_i18n_key,
            "badge": self.badge,
            "icon": self.icon,
            "accent": self.accent,
            "schema": {
                "fields": [
                    field.to_dict()
                    for field in self.resolve_fields(
                        field_defaults,
                        filter_by_auth_type=False,
                    )
                ],
            },
        }


_PROVIDER_SCHEMA_DEFINITIONS: dict[str, ProviderSchemaDefinition] = {
    "smartcmp": ProviderSchemaDefinition(
        provider_type="smartcmp",
        display_name="SmartCMP",
        name_i18n_key="provider.catalog.smartcmp.name",
        description=(
            "Enterprise CMP workflow provider for approvals, service catalog queries, "
            "and fulfillment actions."
        ),
        description_i18n_key="provider.catalog.smartcmp.description",
        badge="CMP",
        icon="SC",
        accent="#0f766e",
        default_auth_type="user_token",
        fields=(
            ProviderSchemaField(
                name="base_url",
                label="Base URL",
                label_i18n_key="provider.baseUrl",
                default="https://console.smartcmp.cloud",
                placeholder="https://console.smartcmp.cloud",
                placeholder_i18n_key="provider.baseUrlPlaceholder",
                required=True,
            ),
            ProviderSchemaField(
                name="auth_type",
                type="hidden",
                default="user_token",
            ),
            ProviderSchemaField(
                name="user_token",
                type="password",
                label="User Token",
                label_i18n_key="provider.userToken",
                placeholder="Enter user token",
                placeholder_i18n_key="provider.userTokenPlaceholder",
                required=True,
                sensitive=True,
                auth_types=("user_token",),
            ),
            ProviderSchemaField(
                name="username",
                label="Username",
                label_i18n_key="provider.username",
                placeholder="cmp-robot",
                placeholder_i18n_key="provider.usernamePlaceholder",
                required=True,
                auth_types=("credential",),
            ),
            ProviderSchemaField(
                name="password",
                type="password",
                label="Password",
                label_i18n_key="provider.password",
                placeholder="Enter password",
                placeholder_i18n_key="provider.passwordPlaceholder",
                required=True,
                sensitive=True,
                auth_types=("credential",),
            ),
            ProviderSchemaField(
                name="cookie",
                type="password",
                label="Cookie",
                label_i18n_key="provider.cookie",
                placeholder="session=...",
                placeholder_i18n_key="provider.cookiePlaceholder",
                required=True,
                sensitive=True,
                auth_types=("cookie",),
            ),
        ),
    ),
    "dingtalk": ProviderSchemaDefinition(
        provider_type="dingtalk",
        display_name="DingTalk",
        name_i18n_key="provider.catalog.dingtalk.name",
        description=(
            "Enterprise messaging provider for org bots, app credentials, "
            "and downstream work notifications."
        ),
        description_i18n_key="provider.catalog.dingtalk.description",
        badge="COLLAB",
        icon="DT",
        accent="#2952cc",
        default_auth_type="app_credentials",
        fields=(
            ProviderSchemaField(
                name="base_url",
                label="Base URL",
                label_i18n_key="provider.baseUrl",
                default="https://oapi.dingtalk.com",
                placeholder="https://oapi.dingtalk.com",
                placeholder_i18n_key="provider.baseUrlPlaceholder",
                required=True,
            ),
            ProviderSchemaField(
                name="auth_type",
                type="hidden",
                default="app_credentials",
            ),
            ProviderSchemaField(
                name="app_key",
                label="App Key",
                label_i18n_key="provider.appKey",
                placeholder="dingxxxx",
                placeholder_i18n_key="provider.appKeyPlaceholder",
                required=True,
            ),
            ProviderSchemaField(
                name="app_secret",
                type="password",
                label="App Secret",
                label_i18n_key="provider.appSecret",
                placeholder="Enter app secret",
                placeholder_i18n_key="provider.appSecretPlaceholder",
                required=True,
                sensitive=True,
            ),
            ProviderSchemaField(
                name="agent_id",
                label="Agent ID",
                label_i18n_key="provider.agentId",
                placeholder="1000001",
                placeholder_i18n_key="provider.agentIdPlaceholder",
                required=True,
            ),
        ),
    ),
}


def get_provider_schema_definition(provider_type: str) -> Optional[ProviderSchemaDefinition]:
    """Return a provider schema definition when the backend knows this type."""
    return _PROVIDER_SCHEMA_DEFINITIONS.get(str(provider_type or "").strip().lower())


def get_provider_schema_catalog(
    provider_types: Optional[Iterable[str]] = None,
    field_defaults: Optional[dict[str, dict[str, str]]] = None,
) -> list[dict[str, Any]]:
    """Return provider schema catalog payload for API responses."""
    if provider_types is None:
        selected = list(_PROVIDER_SCHEMA_DEFINITIONS.values())
    else:
        seen: set[str] = set()
        selected = []
        for provider_type in provider_types:
            normalized = str(provider_type or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            definition = _PROVIDER_SCHEMA_DEFINITIONS.get(normalized)
            if definition is not None:
                selected.append(definition)

    return [
        definition.to_dict((field_defaults or {}).get(definition.provider_type))
        for definition in selected
    ]


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_provider_config(
    provider_type: str,
    config: Optional[dict[str, Any]],
    existing_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Apply backend schema defaults and validate required provider config fields."""
    merged: dict[str, Any] = dict(existing_config or {})
    merged.update(dict(config or {}))

    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        return merged

    resolved_fields = definition.resolve_fields(merged)

    for field in resolved_fields:
        if field.default is not None and _is_blank(merged.get(field.name)):
            merged[field.name] = field.default

    missing = [
        field.name
        for field in resolved_fields
        if field.required and _is_blank(merged.get(field.name))
    ]
    if missing:
        raise ValueError(
            "Missing required config fields: " + ", ".join(missing)
        )

    return merged
