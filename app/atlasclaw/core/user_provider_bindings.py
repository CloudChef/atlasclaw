# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Helpers for resolving user-scoped provider bindings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.atlasclaw.api.service_provider_schemas import (
    get_provider_schema_definition,
    normalize_provider_config,
)
from app.atlasclaw.core.config import get_config


_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "private_key",
        "credential",
        "cookie",
        "app_secret",
    }
)


def _default_user_setting_document() -> dict[str, object]:
    """Return the default user settings structure."""
    return {
        "channels": {},
        "providers": {},
        "preferences": {},
    }


def _resolve_workspace_path(workspace_path: Optional[str] = None) -> Path:
    """Return the effective workspace root."""
    if workspace_path:
        return Path(workspace_path).resolve()
    return Path(get_config().workspace.path).resolve()


def get_user_setting_path(user_id: str, workspace_path: Optional[str] = None) -> Path:
    """Return the user settings file path."""
    return _resolve_workspace_path(workspace_path) / "users" / user_id / "user_setting.json"


def load_user_setting_document(
    user_id: str,
    workspace_path: Optional[str] = None,
) -> dict[str, object]:
    """Load a user's settings document."""
    config_path = get_user_setting_path(user_id, workspace_path)
    if not config_path.exists():
        return _default_user_setting_document()

    try:
        raw_document = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw_document = {}

    document = _default_user_setting_document()
    if isinstance(raw_document, dict):
        for section_name in document.keys():
            section_value = raw_document.get(section_name)
            if isinstance(section_value, dict):
                document[section_name] = section_value
    return document


def parse_provider_binding(binding_value: Any) -> Optional[tuple[str, str]]:
    """Parse a `provider_type/instance_name` binding string."""
    raw_value = str(binding_value or "").strip()
    if not raw_value:
        return None

    provider_type, separator, instance_name = raw_value.partition("/")
    provider_type = provider_type.strip().lower()
    instance_name = instance_name.strip()

    if not separator or not provider_type or not instance_name:
        raise ValueError(
            "Provider binding must use the format '<provider_type>/<instance_name>'"
        )

    return provider_type, instance_name


def get_provider_template_config(
    provider_type: str,
    instance_name: str,
) -> Optional[dict[str, Any]]:
    """Return the configured system provider template for a binding."""
    service_providers = get_config().service_providers or {}
    provider_instances = service_providers.get(provider_type)
    if not isinstance(provider_instances, dict):
        return None

    template_config = provider_instances.get(instance_name)
    return dict(template_config) if isinstance(template_config, dict) else None


def _get_user_provider_entry(
    user_id: str,
    provider_type: str,
    instance_name: str,
    workspace_path: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    document = load_user_setting_document(user_id, workspace_path)
    providers = document.get("providers", {})
    if not isinstance(providers, dict):
        return None

    provider_bucket = providers.get(provider_type)
    if not isinstance(provider_bucket, dict):
        return None

    entry = provider_bucket.get(instance_name)
    return entry if isinstance(entry, dict) else None


def resolve_user_provider_instance(
    user_id: str,
    provider_type: str,
    instance_name: str,
    workspace_path: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve a user-configured provider instance to a runtime-ready config."""
    binding_value = f"{provider_type}/{instance_name}"
    template_config = get_provider_template_config(provider_type, instance_name)
    if template_config is None:
        raise ValueError(f"Provider binding '{binding_value}' was not found")

    user_entry = _get_user_provider_entry(user_id, provider_type, instance_name, workspace_path)
    if not isinstance(user_entry, dict) or not bool(user_entry.get("configured")):
        raise ValueError(
            f"Provider binding '{binding_value}' is not configured for user '{user_id}'"
        )

    user_config = user_entry.get("config")
    if not isinstance(user_config, dict):
        raise ValueError(
            f"Provider binding '{binding_value}' is not configured for user '{user_id}'"
        )

    try:
        resolved_config = normalize_provider_config(
            provider_type,
            user_config,
            existing_config=template_config,
        )
    except ValueError as exc:
        raise ValueError(
            f"Provider binding '{binding_value}' is invalid: {exc}"
        ) from exc

    return {
        "provider_type": provider_type,
        "instance_name": instance_name,
        **resolved_config,
    }


def build_user_provider_instances(
    user_id: str,
    workspace_path: Optional[str] = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return resolved provider instances configured for a user."""
    document = load_user_setting_document(user_id, workspace_path)
    providers = document.get("providers", {})
    if not isinstance(providers, dict):
        return {}

    resolved_instances: dict[str, dict[str, dict[str, Any]]] = {}
    for provider_type, provider_bucket in providers.items():
        if not isinstance(provider_bucket, dict):
            continue

        normalized_provider_type = str(provider_type or "").strip().lower()
        if not normalized_provider_type:
            continue

        for instance_name, entry in provider_bucket.items():
            if not isinstance(entry, dict) or not bool(entry.get("configured")):
                continue

            normalized_instance_name = str(instance_name or "").strip()
            if not normalized_instance_name:
                continue

            try:
                resolved_entry = resolve_user_provider_instance(
                    user_id,
                    normalized_provider_type,
                    normalized_instance_name,
                    workspace_path,
                )
            except ValueError:
                continue

            resolved_instances.setdefault(normalized_provider_type, {})[
                normalized_instance_name
            ] = resolved_entry

    return {
        provider_type: {
            instance_name: dict(instance_config)
            for instance_name, instance_config in sorted(instances.items())
        }
        for provider_type, instances in sorted(resolved_instances.items())
    }


def build_provider_binding_options(
    user_id: str,
    workspace_path: Optional[str] = None,
) -> list[dict[str, str]]:
    """Build select options for channel provider bindings."""
    resolved_instances = build_user_provider_instances(user_id, workspace_path)
    options: list[dict[str, str]] = []

    for provider_type, instances in resolved_instances.items():
        provider_definition = get_provider_schema_definition(provider_type)
        provider_label = (
            provider_definition.display_name
            if provider_definition is not None and provider_definition.display_name
            else provider_type
        )

        for instance_name, config in instances.items():
            value = f"{provider_type}/{instance_name}"
            base_url = str(config.get("base_url", "") or "").strip()
            label = f"{provider_label} / {instance_name}"
            if base_url:
                label = f"{label} ({base_url})"
            options.append(
                {
                    "value": value,
                    "label": label,
                    "provider_type": provider_type,
                    "instance_name": instance_name,
                    "base_url": base_url,
                }
            )

    return options


class ResolvedProviderInstanceRegistry:
    """Registry adapter backed by resolved user-scoped provider instances."""

    def __init__(self, provider_instances: dict[str, dict[str, dict[str, Any]]]):
        self._provider_instances = {
            provider_type: {
                instance_name: dict(instance_config)
                for instance_name, instance_config in instances.items()
            }
            for provider_type, instances in (provider_instances or {}).items()
        }

    def list_instances(self, provider_type: str) -> list[str]:
        """List available instance names for a provider type."""
        return sorted(self._provider_instances.get(provider_type, {}).keys())

    def get_instance_config(
        self,
        provider_type: str,
        instance_name: str,
    ) -> Optional[dict[str, Any]]:
        """Return a resolved provider instance config."""
        instances = self._provider_instances.get(provider_type)
        if not isinstance(instances, dict):
            return None

        config = instances.get(instance_name)
        return dict(config) if isinstance(config, dict) else None

    def get_instance_config_redacted(
        self,
        provider_type: str,
        instance_name: str,
    ) -> Optional[dict[str, Any]]:
        """Return a redacted resolved provider instance config."""
        config = self.get_instance_config(provider_type, instance_name)
        if config is None:
            return None

        from app.atlasclaw.core.trace import sanitize_log_value

        redacted = sanitize_log_value(
            config,
            redacted_text="***",
            provider_type=provider_type,
            field_defaults=config,
        )
        if not isinstance(redacted, dict):
            return {}

        for key in config.keys():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in _SENSITIVE_KEYS:
                redacted[key] = "***"
        return redacted

    def get_available_providers_summary(self) -> dict[str, list[str]]:
        """Return a provider-to-instance summary."""
        return {
            provider_type: self.list_instances(provider_type)
            for provider_type in sorted(self._provider_instances.keys())
        }

    def get_all_instance_configs(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return all resolved instance configs."""
        return {
            provider_type: {
                instance_name: dict(instance_config)
                for instance_name, instance_config in instances.items()
            }
            for provider_type, instances in self._provider_instances.items()
        }


def build_provider_binding_runtime_context(
    user_id: str,
    binding_value: Any = "",
    workspace_path: Optional[str] = None,
) -> dict[str, Any]:
    """Build runtime context payload for a user's provider bindings."""
    provider_instances = build_user_provider_instances(user_id, workspace_path)
    registry = ResolvedProviderInstanceRegistry(provider_instances)
    context: dict[str, Any] = {
        "available_providers": registry.get_available_providers_summary(),
        "provider_instances": registry.get_all_instance_configs(),
        "_service_provider_registry": registry,
    }

    parsed_binding = parse_provider_binding(binding_value)
    if not parsed_binding:
        return context

    provider_type, instance_name = parsed_binding
    resolved_instance = resolve_user_provider_instance(
        user_id,
        provider_type,
        instance_name,
        workspace_path,
    )
    context.update(
        {
            "provider_type": provider_type,
            "provider_instance_name": instance_name,
            "provider_instance": resolved_instance,
        }
    )
    return context
