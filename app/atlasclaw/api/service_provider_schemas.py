# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider manifest schema loading and provider config normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


PROVIDER_SCHEMA_FILENAME = "provider.schema.json"


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


# Core owns the public auth mode vocabulary. Provider manifests own the
# provider-specific fields required for each mode.
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


def validate_provider_instance_auth_type(
    provider_type: str,
    instance_name: str,
    config: Mapping[str, Any],
) -> None:
    """Validate configured provider auth_type against core auth modes and manifests."""
    definition = get_provider_schema_definition(provider_type)
    if definition is None and _is_blank(config.get("auth_type")):
        return
    fallback = definition.default_auth_type if definition is not None else None
    try:
        normalize_provider_auth_type_chain(config.get("auth_type"), fallback=fallback)
    except ValueError as exc:
        raise ValueError(
            f"Skipping provider instance {provider_type}.{instance_name}: {exc}"
        ) from exc


def _normalize_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        items = value
    elif _is_blank(value):
        items = []
    else:
        items = [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            normalized.append(text)
            seen.add(text)
    return tuple(normalized)


@dataclass(frozen=True)
class ProviderSchemaField:
    """Manifest field used by provider-management UI and runtime validation."""

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
    scope: str = "instance"
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "type", str(self.type or "text").strip() or "text")
        object.__setattr__(self, "scope", str(self.scope or "instance").strip() or "instance")
        if not self.name:
            raise ValueError("provider schema field name must not be empty")

        if _has_auth_type_value(self.auth_types):
            object.__setattr__(
                self,
                "auth_types",
                normalize_provider_auth_type_chain(self.auth_types),
            )
        else:
            object.__setattr__(self, "auth_types", ())
        object.__setattr__(self, "aliases", _normalize_string_tuple(self.aliases))

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
        if self.scope:
            payload["scope"] = self.scope
        if self.aliases:
            payload["aliases"] = list(self.aliases)
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
            scope=self.scope,
            aliases=self.aliases,
        )


@dataclass(frozen=True)
class ProviderAuthModeDefinition:
    """Provider-declared required config fields for one standard auth mode."""

    required_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_fields",
            tuple(field.lower() for field in _normalize_string_tuple(self.required_fields)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"required_fields": list(self.required_fields)}


@dataclass(frozen=True)
class ProviderSchemaDefinition:
    """Machine-readable manifest for a manageable service provider type."""

    provider_type: str
    display_name: str
    fields: tuple[ProviderSchemaField, ...]
    name_i18n_key: str = ""
    description: str = ""
    description_i18n_key: str = ""
    badge: str = ""
    icon: str = ""
    icon_path: str = ""
    accent: str = ""
    default_auth_type: Any = ""
    auth_modes: dict[str, ProviderAuthModeDefinition] = field(default_factory=dict)
    redaction_sensitive_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_provider_type = str(self.provider_type or "").strip().lower()
        if not normalized_provider_type:
            raise ValueError("provider_type must not be empty")
        object.__setattr__(self, "provider_type", normalized_provider_type)

        normalized_default = self.default_auth_type
        if _has_auth_type_value(self.default_auth_type):
            normalized_default = serialize_provider_auth_type(
                normalize_provider_auth_type_chain(self.default_auth_type)
            )
            object.__setattr__(self, "default_auth_type", normalized_default)

        normalized_fields: list[ProviderSchemaField] = []
        for schema_field in self.fields:
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

        normalized_auth_modes: dict[str, ProviderAuthModeDefinition] = {}
        for raw_auth_type, auth_mode in self.auth_modes.items():
            auth_chain = normalize_provider_auth_type_chain(raw_auth_type)
            if len(auth_chain) != 1:
                raise ValueError("auth_modes keys must be single auth_type values")
            normalized_auth_modes[auth_chain[0]] = auth_mode
        object.__setattr__(self, "auth_modes", normalized_auth_modes)
        object.__setattr__(
            self,
            "redaction_sensitive_fields",
            tuple(
                field.lower()
                for field in _normalize_string_tuple(self.redaction_sensitive_fields)
            ),
        )

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
            resolved_fields = [
                field
                for field in resolved_fields
                if not field.auth_types or set(field.auth_types).intersection(auth_chain)
            ]

        return tuple(
            field.with_default(overrides[field.name])
            if field.name in overrides and not _is_blank(overrides[field.name])
            else field
            for field in resolved_fields
        )

    def required_fields_for_auth_type(self, auth_type: str) -> tuple[str, ...]:
        """Return provider-declared required fields for a standard auth mode."""
        normalized = normalize_provider_auth_type_chain(auth_type)[0]
        auth_mode = self.auth_modes.get(normalized)
        if auth_mode is not None:
            return auth_mode.required_fields

        return tuple(
            field.name.lower()
            for field in self.fields
            if field.required and normalized in field.auth_types
        )

    def auth_field_names(self) -> frozenset[str]:
        """Return all fields that belong to any auth mode for this provider."""
        names: set[str] = set()
        for field in self.fields:
            if field.auth_types:
                names.add(field.name.lower())
                names.update(alias.lower() for alias in field.aliases)
        for auth_mode in self.auth_modes.values():
            names.update(auth_mode.required_fields)
        return frozenset(names)

    def sensitive_field_names(self) -> frozenset[str]:
        """Return canonical and alias field names that must be redacted."""
        names: set[str] = set(self.redaction_sensitive_fields)
        for field in self.fields:
            if field.sensitive or field.type == "password":
                names.add(field.name.lower())
                names.update(alias.lower() for alias in field.aliases)
        return frozenset(names)

    def is_auth_mode_usable(
        self,
        auth_type: str,
        config: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> bool:
        """Return whether this provider has credentials for one auth mode."""
        if auth_type == "sso":
            return bool(runtime_context.get("provider_sso_available")) and not _is_blank(
                runtime_context.get("provider_sso_token")
            )
        if auth_type == "cookie" and bool(runtime_context.get("provider_cookie_available")):
            return not _is_blank(runtime_context.get("provider_cookie_token"))

        return all(
            not _is_blank(config.get(field_name))
            for field_name in self.required_fields_for_auth_type(auth_type)
        )

    def strip_auth_fields_for_runtime(
        self,
        config: Mapping[str, Any],
        selected_auth_type: str,
    ) -> dict[str, Any]:
        """Remove credentials for inactive auth modes before passing config to tools."""
        normalized_selected_auth_type = normalize_provider_auth_type_chain(selected_auth_type)[0]
        selected_fields = set(self.required_fields_for_auth_type(normalized_selected_auth_type))
        for field in self.fields:
            if normalized_selected_auth_type in field.auth_types:
                selected_fields.add(field.name.lower())
                selected_fields.update(alias.lower() for alias in field.aliases)
        auth_field_names = self.auth_field_names()
        runtime_config: dict[str, Any] = {"auth_type": normalized_selected_auth_type}

        for key, value in config.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key == "auth_type":
                continue
            if normalized_key in auth_field_names and normalized_key not in selected_fields:
                continue
            runtime_config[key] = value

        return runtime_config

    def to_dict(self, field_defaults: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        payload = {
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
        if self.icon_path:
            payload["icon_path"] = self.icon_path
        return payload


_PROVIDER_SCHEMA_DEFINITIONS: dict[str, ProviderSchemaDefinition] = {}
_CONTEXT_SCHEMA_MISSING = object()


def clear_provider_schema_definitions() -> None:
    """Clear globally registered provider schema definitions."""
    _PROVIDER_SCHEMA_DEFINITIONS.clear()


def register_provider_schema_definition(definition: ProviderSchemaDefinition) -> None:
    """Register a provider schema definition for process-wide fallback lookup."""
    _PROVIDER_SCHEMA_DEFINITIONS[definition.provider_type] = definition


def _safe_relative_path(value: Any, *, manifest_path: Path) -> str:
    raw_path = str(value or "").strip()
    if not raw_path:
        return ""
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(
            f"{manifest_path}: icon_path must be relative to provider root "
            "and must not contain '..'"
        )
    return raw_path


def _field_from_dict(raw_field: Mapping[str, Any], *, manifest_path: Path) -> ProviderSchemaField:
    if not isinstance(raw_field, Mapping):
        raise ValueError(f"{manifest_path}: config_schema.fields entries must be objects")
    return ProviderSchemaField(
        name=str(raw_field.get("name", "") or ""),
        type=str(raw_field.get("type", "text") or "text"),
        required=bool(raw_field.get("required", False)),
        sensitive=bool(raw_field.get("sensitive", False)),
        default=raw_field.get("default"),
        label=str(raw_field.get("label", "") or ""),
        label_i18n_key=str(raw_field.get("label_i18n_key", "") or ""),
        placeholder=str(raw_field.get("placeholder", "") or ""),
        placeholder_i18n_key=str(raw_field.get("placeholder_i18n_key", "") or ""),
        auth_types=tuple(_normalize_string_tuple(raw_field.get("auth_types"))),
        scope=str(raw_field.get("scope", "instance") or "instance"),
        aliases=tuple(_normalize_string_tuple(raw_field.get("aliases"))),
    )


def _auth_modes_from_dict(
    raw_auth_modes: Any,
    *,
    manifest_path: Path,
) -> dict[str, ProviderAuthModeDefinition]:
    if raw_auth_modes in (None, ""):
        return {}
    if not isinstance(raw_auth_modes, Mapping):
        raise ValueError(f"{manifest_path}: config_schema.auth_modes must be an object")
    auth_modes: dict[str, ProviderAuthModeDefinition] = {}
    for auth_type, raw_auth_mode in raw_auth_modes.items():
        if not isinstance(raw_auth_mode, Mapping):
            raise ValueError(f"{manifest_path}: auth_modes.{auth_type} must be an object")
        auth_modes[str(auth_type)] = ProviderAuthModeDefinition(
            required_fields=tuple(_normalize_string_tuple(raw_auth_mode.get("required_fields")))
        )
    return auth_modes


def load_provider_schema_definition(
    manifest_path: Path,
    *,
    fallback_provider_type: str = "",
    fallback_display_name: str = "",
    fallback_description: str = "",
) -> ProviderSchemaDefinition:
    """Load one provider schema manifest from provider.schema.json."""
    manifest_path = Path(manifest_path)
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest_path}: invalid JSON: {exc}") from exc

    if not isinstance(raw_manifest, Mapping):
        raise ValueError(f"{manifest_path}: provider manifest must be a JSON object")

    schema_version = raw_manifest.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"{manifest_path}: unsupported schema_version {schema_version!r}")

    provider_type = str(raw_manifest.get("provider_type") or fallback_provider_type or "").strip()
    catalog = raw_manifest.get("catalog") or {}
    if not isinstance(catalog, Mapping):
        raise ValueError(f"{manifest_path}: catalog must be an object")
    config_schema = raw_manifest.get("config_schema") or {}
    if not isinstance(config_schema, Mapping):
        raise ValueError(f"{manifest_path}: config_schema must be an object")

    raw_fields = config_schema.get("fields") or []
    if not isinstance(raw_fields, list):
        raise ValueError(f"{manifest_path}: config_schema.fields must be a list")
    fields = tuple(_field_from_dict(field, manifest_path=manifest_path) for field in raw_fields)

    redaction = raw_manifest.get("redaction") or {}
    if not isinstance(redaction, Mapping):
        raise ValueError(f"{manifest_path}: redaction must be an object")

    definition = ProviderSchemaDefinition(
        provider_type=provider_type,
        display_name=str(catalog.get("display_name") or fallback_display_name or provider_type),
        name_i18n_key=str(catalog.get("name_i18n_key", "") or ""),
        description=str(catalog.get("description") or fallback_description or ""),
        description_i18n_key=str(catalog.get("description_i18n_key", "") or ""),
        badge=str(catalog.get("badge", "") or ""),
        icon=str(catalog.get("icon", "") or ""),
        icon_path=_safe_relative_path(catalog.get("icon_path", ""), manifest_path=manifest_path),
        accent=str(catalog.get("accent", "") or ""),
        default_auth_type=config_schema.get("default_auth_type", ""),
        auth_modes=_auth_modes_from_dict(
            config_schema.get("auth_modes"),
            manifest_path=manifest_path,
        ),
        fields=fields,
        redaction_sensitive_fields=tuple(
            _normalize_string_tuple(redaction.get("sensitive_fields"))
        ),
    )

    if fallback_provider_type:
        normalized_fallback = str(fallback_provider_type or "").strip().lower()
        if normalized_fallback and definition.provider_type != normalized_fallback:
            raise ValueError(
                f"{manifest_path}: provider_type {definition.provider_type!r} "
                f"does not match PROVIDER.md {normalized_fallback!r}"
            )

    return definition


def load_provider_directory_schema(
    provider_dir: Path,
    *,
    context: Any = None,
) -> ProviderSchemaDefinition | None:
    """Load and register provider.schema.json from a provider directory when present."""
    manifest_path = Path(provider_dir) / PROVIDER_SCHEMA_FILENAME
    if not manifest_path.is_file():
        return None
    definition = load_provider_schema_definition(
        manifest_path,
        fallback_provider_type=str(getattr(context, "provider_type", "") or ""),
        fallback_display_name=str(getattr(context, "display_name", "") or ""),
        fallback_description=str(getattr(context, "description", "") or ""),
    )
    register_provider_schema_definition(definition)
    return definition


def _get_context_provider_schema_definitions() -> dict[str, ProviderSchemaDefinition] | object:
    try:
        from app.atlasclaw.api.deps_context import get_api_context
    except Exception:
        return _CONTEXT_SCHEMA_MISSING

    try:
        registry = getattr(get_api_context(), "service_provider_registry", None)
    except Exception:
        return _CONTEXT_SCHEMA_MISSING

    getter = getattr(registry, "get_all_provider_schema_definitions", None)
    if not callable(getter):
        return _CONTEXT_SCHEMA_MISSING
    try:
        definitions = getter()
    except Exception:
        return {}
    return definitions if isinstance(definitions, dict) else {}


def get_provider_schema_definition(provider_type: str) -> Optional[ProviderSchemaDefinition]:
    """Return a provider manifest definition when the runtime knows this type."""
    normalized = str(provider_type or "").strip().lower()
    if not normalized:
        return None
    context_definitions = _get_context_provider_schema_definitions()
    if isinstance(context_definitions, dict):
        definition = context_definitions.get(normalized)
        return definition if isinstance(definition, ProviderSchemaDefinition) else None
    return _PROVIDER_SCHEMA_DEFINITIONS.get(normalized)


def get_provider_schema_catalog(
    provider_types: Optional[Iterable[str]] = None,
    field_defaults: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Return provider schema catalog payload for API responses."""
    context_definitions = _get_context_provider_schema_definitions()
    definitions = (
        context_definitions
        if isinstance(context_definitions, dict)
        else _PROVIDER_SCHEMA_DEFINITIONS
    )
    if provider_types is None:
        selected = sorted(definitions.values(), key=lambda definition: definition.provider_type)
    else:
        seen: set[str] = set()
        selected = []
        for provider_type in provider_types:
            normalized = str(provider_type or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            definition = definitions.get(normalized)
            if definition is not None:
                selected.append(definition)

    return [
        definition.to_dict((field_defaults or {}).get(definition.provider_type))
        for definition in selected
    ]


def is_provider_config_field_sensitive(
    provider_type: str,
    field_name: str,
    *,
    field_defaults: Optional[Mapping[str, Any]] = None,
    key_fragments: Iterable[str] = (),
) -> bool:
    """Return whether a provider config field must be redacted."""
    normalized = str(field_name or "").strip().lower()
    if not normalized:
        return False
    compact = "".join(char for char in normalized if char.isalnum())
    if any(fragment in normalized or fragment in compact for fragment in key_fragments):
        return True

    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        return False
    if normalized in definition.sensitive_field_names():
        return True

    defaults = dict(field_defaults) if isinstance(field_defaults, Mapping) else None
    for field in definition.resolve_fields(field_defaults=defaults, filter_by_auth_type=False):
        if str(field.name or "").strip().lower() == normalized:
            return bool(field.sensitive or field.type == "password")
    return False


def _apply_aliases(
    merged: dict[str, Any],
    resolved_fields: Iterable[ProviderSchemaField],
) -> None:
    for field in resolved_fields:
        alias_to_apply = None
        if not _is_blank(merged.get(field.name)):
            alias_to_apply = None
        else:
            for alias in field.aliases:
                if not _is_blank(merged.get(alias)):
                    alias_to_apply = alias
                    merged[field.name] = merged[alias]
                    break
        for alias in field.aliases:
            if alias == alias_to_apply or alias in merged:
                merged.pop(alias, None)


def normalize_provider_config(
    provider_type: str,
    config: Optional[dict[str, Any]],
    existing_config: Optional[dict[str, Any]] = None,
    *,
    validate_auth_requirements: bool = True,
) -> dict[str, Any]:
    """Apply provider manifest defaults and validate required provider config fields."""
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
    _apply_aliases(merged, resolved_fields)

    for field in resolved_fields:
        if field.default is not None and _is_blank(merged.get(field.name)):
            merged[field.name] = field.default

    required_fields = [
        field
        for field in resolved_fields
        if field.required
        and (
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
