# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Backend-managed service provider form schema definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        if not value:
            return True
        return all(_is_blank(item) for item in value)
    return False


# Core owns the auth type vocabulary. Provider definitions may choose a subset,
# but unknown auth types are rejected before a provider instance becomes active.
SUPPORTED_PROVIDER_AUTH_TYPES = frozenset(
    {
        "sso",
        "provider_token",
        "user_token",
        "cookie",
        "credential",
        "app_credentials",
    }
)

# Fields required only when a provider instance resolves to that auth mode.
PROVIDER_AUTH_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sso": (),
    "provider_token": ("provider_token",),
    "user_token": ("user_token",),
    "cookie": ("cookie",),
    "credential": ("username", "password"),
    "app_credentials": ("app_key", "app_secret", "agent_id"),
}

PROVIDER_AUTH_FIELD_NAMES = frozenset(
    field_name
    for field_names in PROVIDER_AUTH_REQUIRED_FIELDS.values()
    for field_name in field_names
)


def _has_auth_type_value(value: Any) -> bool:
    return not _is_blank(value)


def normalize_provider_auth_type_chain(
    value: Any,
    *,
    fallback: Any = None,
) -> tuple[str, ...]:
    """Normalize a public auth_type value into the ordered runtime fallback chain."""
    raw_value = value if _has_auth_type_value(value) else fallback

    if isinstance(raw_value, (list, tuple, set)):
        items = list(raw_value)
    elif _is_blank(raw_value):
        items = []
    else:
        items = [raw_value]

    chain: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        if normalized not in SUPPORTED_PROVIDER_AUTH_TYPES:
            raise ValueError(f"Unsupported auth_type: {normalized}")
        chain.append(normalized)
        seen.add(normalized)

    if not chain:
        raise ValueError("auth_type must not be empty")
    return tuple(chain)


def serialize_provider_auth_type(chain: Iterable[str]) -> str | list[str]:
    """Serialize a normalized auth chain using the public string-or-list contract."""
    normalized_chain = tuple(chain)
    if len(normalized_chain) == 1:
        return normalized_chain[0]
    return list(normalized_chain)


@dataclass(frozen=True)
class ProviderSchemaField:
    """Schema field used by provider-management UI."""

    name: str
    type: str = "text"
    required: bool = False
    sensitive: bool = False
    default: Any = None
    label: str = ""
    label_i18n_key: str = ""
    placeholder: str = ""
    placeholder_i18n_key: str = ""
    auth_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Field-level auth filters use the same strong auth type validation as
        # provider defaults so typos fail during schema construction.
        if _has_auth_type_value(self.auth_types):
            object.__setattr__(
                self,
                "auth_types",
                normalize_provider_auth_type_chain(self.auth_types),
            )
        else:
            object.__setattr__(self, "auth_types", ())

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

    def with_default(self, default: Any) -> "ProviderSchemaField":
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
    default_auth_type: Any = ""
    auth_type_variants: dict[str, tuple[ProviderSchemaField, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep default auth values in the same public shape that API clients
        # already understand: a string for one auth mode, or a list for a chain.
        normalized_default = self.default_auth_type
        if _has_auth_type_value(self.default_auth_type):
            normalized_default = serialize_provider_auth_type(
                normalize_provider_auth_type_chain(self.default_auth_type)
            )
            object.__setattr__(self, "default_auth_type", normalized_default)

        normalized_fields: list[ProviderSchemaField] = []
        for schema_field in self.fields:
            # The auth_type field itself can define the provider's supported
            # chain. Normalize it once here so UI/API responses stay canonical.
            if schema_field.name == "auth_type" and _has_auth_type_value(schema_field.default):
                normalized_auth_type_default = serialize_provider_auth_type(
                    normalize_provider_auth_type_chain(
                        schema_field.default,
                        fallback=normalized_default,
                    )
                )
                schema_field = schema_field.with_default(normalized_auth_type_default)
            normalized_fields.append(schema_field)
        object.__setattr__(self, "fields", tuple(normalized_fields))

        normalized_variants: dict[str, tuple[ProviderSchemaField, ...]] = {}
        for raw_auth_type, fields in self.auth_type_variants.items():
            # Variant buckets are keyed by one concrete auth mode; multi-auth
            # ordering belongs on the provider default, not on variant keys.
            auth_chain = normalize_provider_auth_type_chain(raw_auth_type)
            if len(auth_chain) != 1:
                raise ValueError("auth_type_variants keys must be single auth_type values")
            normalized_variants[auth_chain[0]] = tuple(fields)
        object.__setattr__(self, "auth_type_variants", normalized_variants)

    def resolve_fields(
        self,
        field_defaults: Optional[dict[str, Any]] = None,
        filter_by_auth_type: bool = True,
    ) -> tuple[ProviderSchemaField, ...]:
        """Resolve schema fields for the active auth chain with injected defaults."""
        overrides = field_defaults or {}
        auth_chain = normalize_provider_auth_type_chain(
            overrides.get("auth_type"),
            fallback=(
                self.default_auth_type
                if _has_auth_type_value(self.default_auth_type)
                else next(
                    (
                        field.default
                        for field in self.fields
                        if field.name == "auth_type" and field.default is not None
                    ),
                    "",
                )
            ),
        )

        resolved_fields = list(self.fields)
        if filter_by_auth_type:
            # Multi-auth providers expose the union of fields for every mode in
            # the chain; runtime resolution later picks one usable mode.
            resolved_fields = [
                field
                for field in resolved_fields
                if not field.auth_types or set(field.auth_types).intersection(auth_chain)
            ]
        for auth_type in auth_chain:
            resolved_fields.extend(self.auth_type_variants.get(auth_type, ()))

        return tuple(
            field.with_default(overrides[field.name])
            if field.name in overrides and not _is_blank(overrides[field.name])
            else field
            for field in resolved_fields
        )

    def to_dict(self, field_defaults: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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
                name="provider_token",
                type="password",
                label="Provider Token",
                label_i18n_key="provider.providerToken",
                placeholder="Enter shared provider token",
                placeholder_i18n_key="provider.providerTokenPlaceholder",
                required=True,
                sensitive=True,
                auth_types=("provider_token",),
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
    field_defaults: Optional[dict[str, dict[str, Any]]] = None,
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

def normalize_provider_config(
    provider_type: str,
    config: Optional[dict[str, Any]],
    existing_config: Optional[dict[str, Any]] = None,
    *,
    validate_auth_requirements: bool = True,
) -> dict[str, Any]:
    """Apply backend schema defaults and validate required provider config fields."""
    merged: dict[str, Any] = dict(existing_config or {})
    merged.update(dict(config or {}))

    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        return merged

    auth_chain = normalize_provider_auth_type_chain(
        merged.get("auth_type"),
        fallback=definition.default_auth_type,
    )
    merged["auth_type"] = serialize_provider_auth_type(auth_chain)
    resolved_fields = definition.resolve_fields(merged)

    for field in resolved_fields:
        if field.default is not None and _is_blank(merged.get(field.name)):
            merged[field.name] = field.default

    required_fields = [
        field
        for field in resolved_fields
        if field.required
        and (
            # Multi-auth provider templates are allowed to omit per-mode
            # secrets. The runtime resolver later chooses the first mode whose
            # own credentials are available for the current request/user.
            (validate_auth_requirements and len(auth_chain) == 1)
            or not field.auth_types
        )
    ]
    missing = [
        field.name
        for field in required_fields
        if _is_blank(merged.get(field.name))
    ]
    if missing:
        raise ValueError(
            "Missing required config fields: " + ", ".join(missing)
        )

    return merged
