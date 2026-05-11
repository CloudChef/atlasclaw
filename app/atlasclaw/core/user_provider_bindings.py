# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Helpers for resolving user-scoped provider bindings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.atlasclaw.api.service_provider_schemas import (
    get_provider_schema_definition,
    normalize_provider_config,
    normalize_provider_auth_type_chain,
    serialize_provider_auth_type,
)
from app.atlasclaw.core.config import get_config
from app.atlasclaw.core.user_paths import user_runtime_dir


_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "provider_token",
        "user_token",
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


def normalize_provider_runtime_context(
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Normalize request-scoped provider auth context."""
    source = runtime_context or {}
    # These credentials are runtime-only signals from the current request. They
    # must never be copied into user settings or provider templates.
    sso_token = str(source.get("provider_sso_token", "") or "").strip()
    sso_available = bool(source.get("provider_sso_available")) and bool(sso_token)
    cookie_token = str(source.get("provider_cookie_token", "") or "").strip()
    cookie_available = bool(source.get("provider_cookie_available")) and bool(cookie_token)
    return {
        "provider_sso_available": sso_available,
        "provider_sso_token": sso_token if sso_available else "",
        "provider_cookie_available": cookie_available,
        "provider_cookie_token": cookie_token if cookie_available else "",
    }


def _get_template_auth_chain(
    provider_type: str,
    template_config: dict[str, Any],
    user_config: Optional[dict[str, Any]] = None,
) -> tuple[str, ...]:
    """Return the provider-owned auth chain; user settings cannot override it."""
    definition = get_provider_schema_definition(provider_type)
    template_auth_source = template_config.get("auth_type")
    fallback = (
        definition.default_auth_type
        if definition is not None
        else (user_config or {}).get("auth_type")
    )
    return normalize_provider_auth_type_chain(template_auth_source, fallback=fallback)


def _filter_user_config(user_config: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(user_config, dict):
        return {}
    # Users may supply their own credentials, but provider-owned routing and
    # shared credentials remain controlled by the provider instance template.
    return {
        key: value
        for key, value in user_config.items()
        if key not in {"base_url", "auth_type", "provider_token"}
    }


def _is_auth_mode_usable(
    provider_type: str,
    auth_type: str,
    config: dict[str, Any],
    runtime_context: dict[str, Any],
) -> bool:
    definition = get_provider_schema_definition(provider_type)
    if definition is not None:
        return definition.is_auth_mode_usable(auth_type, config, runtime_context)

    if auth_type == "sso":
        return bool(runtime_context.get("provider_sso_available")) and not _is_blank(
            runtime_context.get("provider_sso_token")
        )
    if auth_type == "cookie" and bool(runtime_context.get("provider_cookie_available")):
        return not _is_blank(runtime_context.get("provider_cookie_token"))
    return True


def _strip_non_selected_auth_fields(
    provider_type: str,
    config: dict[str, Any],
    selected_auth_type: str,
) -> dict[str, Any]:
    definition = get_provider_schema_definition(provider_type)
    if definition is not None:
        return definition.strip_auth_fields_for_runtime(config, selected_auth_type)

    runtime_config: dict[str, Any] = {"auth_type": selected_auth_type}
    for key, value in config.items():
        if str(key or "").strip().lower() != "auth_type":
            runtime_config[key] = value
    return runtime_config


def _get_provider_template_from_instances(
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]],
    provider_type: str,
    instance_name: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(provider_instances, dict):
        return None

    provider_bucket = provider_instances.get(provider_type)
    if not isinstance(provider_bucket, dict):
        return None

    template_config = provider_bucket.get(instance_name)
    return dict(template_config) if isinstance(template_config, dict) else None


def _get_provider_template_bucket(
    provider_type: str,
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> dict[str, dict[str, Any]]:
    """Return provider template instances from explicit input or config."""
    normalized_provider_type = str(provider_type or "").strip()
    if not normalized_provider_type:
        return {}

    if isinstance(provider_instances, dict):
        provider_bucket = provider_instances.get(normalized_provider_type)
        if not isinstance(provider_bucket, dict):
            provider_bucket = provider_instances.get(normalized_provider_type.lower())
        if isinstance(provider_bucket, dict):
            return {
                str(instance_name): dict(instance_config)
                for instance_name, instance_config in provider_bucket.items()
                if str(instance_name or "").strip() and isinstance(instance_config, dict)
            }

    service_providers = get_config().service_providers or {}
    config_provider_instances = service_providers.get(normalized_provider_type)
    if not isinstance(config_provider_instances, dict):
        config_provider_instances = service_providers.get(normalized_provider_type.lower())
    if not isinstance(config_provider_instances, dict):
        return {}
    return {
        str(instance_name): dict(instance_config)
        for instance_name, instance_config in config_provider_instances.items()
        if str(instance_name or "").strip() and isinstance(instance_config, dict)
    }


def _resolve_template_instance_name(
    provider_type: str,
    instance_name: str,
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> str:
    """Resolve legacy user setting instance aliases to configured templates."""
    normalized_instance_name = str(instance_name or "").strip()
    if not normalized_instance_name:
        return ""

    templates = _get_provider_template_bucket(provider_type, provider_instances)
    if normalized_instance_name in templates:
        return normalized_instance_name

    lowered = normalized_instance_name.lower()
    for template_instance_name in templates.keys():
        if template_instance_name.lower() == lowered:
            return template_instance_name

    if lowered == "default" and len(templates) == 1:
        return next(iter(templates.keys()))

    return normalized_instance_name


def resolve_provider_instance_config(
    provider_type: str,
    instance_name: str,
    *,
    template_config: dict[str, Any],
    user_config: Optional[dict[str, Any]] = None,
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve a provider instance to the first usable auth mode in template order."""
    binding_value = f"{provider_type}/{instance_name}"
    normalized_runtime_context = normalize_provider_runtime_context(runtime_context)
    auth_chain = _get_template_auth_chain(provider_type, template_config, user_config)
    authoritative_auth_type = serialize_provider_auth_type(auth_chain)

    merged_config = dict(template_config)
    merged_config.update(_filter_user_config(user_config))
    # The selected chain always comes from the provider template. A user-level
    # saved auth_type is ignored so account settings cannot switch provider mode.
    merged_config["auth_type"] = authoritative_auth_type

    try:
        normalized_config = normalize_provider_config(
            provider_type,
            merged_config,
            validate_auth_requirements=False,
        )
    except ValueError as exc:
        raise ValueError(
            f"Provider binding '{binding_value}' is invalid: {exc}"
        ) from exc

    # Provider order is the fallback policy: the first auth mode with available
    # credentials wins for this request/user.
    selected_auth_type = next(
        (
            auth_type
            for auth_type in auth_chain
            if _is_auth_mode_usable(
                provider_type,
                auth_type,
                normalized_config,
                normalized_runtime_context,
            )
        ),
        "",
    )
    if not selected_auth_type:
        attempted_auth_chain = ", ".join(auth_chain)
        raise ValueError(
            f"Provider binding '{binding_value}' has no usable auth mode in chain [{attempted_auth_chain}]"
        )

    runtime_config = _strip_non_selected_auth_fields(
        provider_type,
        normalized_config,
        selected_auth_type,
    )
    if selected_auth_type == "cookie":
        runtime_cookie_token = normalized_runtime_context.get("provider_cookie_token")
        if not _is_blank(runtime_cookie_token):
            # Cookie auth can be selected purely from the current request. Put
            # that token into the runtime config so provider scripts receive
            # the same credential that made the mode usable.
            runtime_config["cookie"] = runtime_cookie_token
    return {
        "provider_type": provider_type,
        "instance_name": instance_name,
        **runtime_config,
    }


def build_resolved_provider_instances(
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]],
    *,
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Resolve system provider instances using request-scoped auth availability."""
    resolved_instances: dict[str, dict[str, dict[str, Any]]] = {}

    for provider_type, instances in (provider_instances or {}).items():
        if not isinstance(instances, dict):
            continue

        normalized_provider_type = str(provider_type or "").strip().lower()
        if not normalized_provider_type:
            continue

        for instance_name, instance_config in instances.items():
            normalized_instance_name = str(instance_name or "").strip()
            if not normalized_instance_name or not isinstance(instance_config, dict):
                continue

            try:
                resolved_entry = resolve_provider_instance_config(
                    normalized_provider_type,
                    normalized_instance_name,
                    template_config=instance_config,
                    runtime_context=runtime_context,
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
    return user_runtime_dir(_resolve_workspace_path(workspace_path), user_id) / "user_setting.json"


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


def get_provider_template_config(
    provider_type: str,
    instance_name: str,
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> Optional[dict[str, Any]]:
    """Return the configured system provider template for a binding."""
    resolved_instance_name = _resolve_template_instance_name(
        provider_type,
        instance_name,
        provider_instances,
    )
    template_config = _get_provider_template_from_instances(
        provider_instances,
        provider_type,
        resolved_instance_name,
    )
    if template_config is not None:
        return template_config

    templates = _get_provider_template_bucket(provider_type)
    template_config = templates.get(resolved_instance_name)
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
    runtime_context: Optional[dict[str, Any]] = None,
    provider_templates: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """Resolve a user-configured provider instance to a runtime-ready config."""
    binding_value = f"{provider_type}/{instance_name}"
    resolved_instance_name = _resolve_template_instance_name(
        provider_type,
        instance_name,
        provider_templates,
    )
    template_config = get_provider_template_config(
        provider_type,
        resolved_instance_name,
        provider_instances=provider_templates,
    )
    if template_config is None:
        raise ValueError(f"Provider binding '{binding_value}' was not found")

    user_entry = _get_user_provider_entry(user_id, provider_type, instance_name, workspace_path)
    if not isinstance(user_entry, dict) and resolved_instance_name != instance_name:
        user_entry = _get_user_provider_entry(
            user_id,
            provider_type,
            resolved_instance_name,
            workspace_path,
        )
    if not isinstance(user_entry, dict) or not bool(user_entry.get("configured")):
        raise ValueError(
            f"Provider binding '{binding_value}' is not configured for user '{user_id}'"
        )

    user_config = user_entry.get("config")
    if not isinstance(user_config, dict):
        raise ValueError(
            f"Provider binding '{binding_value}' is not configured for user '{user_id}'"
        )

    return resolve_provider_instance_config(
        provider_type,
        resolved_instance_name,
        template_config=template_config,
        user_config=user_config,
        runtime_context=runtime_context,
    )


def build_user_provider_instances(
    user_id: str,
    workspace_path: Optional[str] = None,
    runtime_context: Optional[dict[str, Any]] = None,
    provider_templates: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
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
                    runtime_context=runtime_context,
                    provider_templates=provider_templates,
                )
            except ValueError:
                continue

            resolved_instance_name = str(
                resolved_entry.get("instance_name") or normalized_instance_name
            ).strip()
            if not resolved_instance_name:
                continue
            resolved_instances.setdefault(normalized_provider_type, {})[
                resolved_instance_name
            ] = resolved_entry

    return {
        provider_type: {
            instance_name: dict(instance_config)
            for instance_name, instance_config in sorted(instances.items())
        }
        for provider_type, instances in sorted(resolved_instances.items())
    }


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
