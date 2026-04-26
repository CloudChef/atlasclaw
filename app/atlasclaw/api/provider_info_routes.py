# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Provider information and model-discovery routes."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel as PydanticBaseModel

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    filter_provider_instances_for_authz,
    has_permission,
    resolve_authorization_context,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.api.service_provider_schemas import (
    get_provider_schema_catalog,
    get_provider_schema_definition,
    normalize_provider_auth_type_chain,
    serialize_provider_auth_type,
)
from app.atlasclaw.core.provider_catalog import get_provider_catalog_instances
from app.atlasclaw.db.database import get_db_manager

router = APIRouter(tags=["Provider API"])
logger = logging.getLogger(__name__)

_SENSITIVE_CONFIG_KEY_FRAGMENTS = frozenset(
    (
        "cookie",
        "token",
        "password",
        "secret",
        "apikey",
        "credential",
    )
)


@router.get("/providers")
async def get_providers():
    """Return available providers and builtin model presets."""
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS, PROVIDER_MODELS

    result = {}
    for name, preset in BUILTIN_PROVIDERS.items():
        result[name] = {
            "base_url": preset.base_url,
            "api_type": preset.api_type,
            "models": PROVIDER_MODELS.get(name, []),
        }
    return result


class FetchModelsRequest(PydanticBaseModel):
    """Request body for fetching models from a provider."""

    provider: str
    base_url: str = ""
    api_key: str = ""


@router.post("/providers/fetch-models")
async def fetch_provider_models(body: FetchModelsRequest):
    """Fetch provider models from upstream API, fallback to builtin presets on failures."""
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS, PROVIDER_MODELS

    preset = BUILTIN_PROVIDERS.get(body.provider)
    if not preset:
        return {"models": PROVIDER_MODELS.get(body.provider, []), "source": "preset"}

    base_url = preset.base_url
    api_key = body.api_key
    api_type = preset.api_type

    if not base_url or not api_key:
        return {"models": PROVIDER_MODELS.get(body.provider, []), "source": "preset"}

    try:
        headers = {}
        url = ""

        if api_type == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            url = f"{base_url.rstrip('/')}/v1/models"
        elif api_type == "google":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        else:
            headers = {"Authorization": f"Bearer {api_key}"}
            clean_url = base_url.rstrip("/")
            if clean_url.endswith("/v1"):
                url = f"{clean_url}/models"
            else:
                url = f"{clean_url}/v1/models"

        async with httpx.AsyncClient(timeout=15.0, trust_env=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            return {
                "models": PROVIDER_MODELS.get(body.provider, []),
                "source": "preset",
                "error": f"HTTP {resp.status_code}",
            }

        data = resp.json()
        model_ids = []

        if api_type == "google":
            for model in data.get("models", []):
                name = model.get("name", "")
                if name.startswith("models/"):
                    name = name[7:]
                if name:
                    model_ids.append(name)
        else:
            for model in data.get("data", []):
                model_id = model.get("id", "")
                if model_id:
                    model_ids.append(model_id)

        model_ids.sort()
        return {"models": model_ids, "source": "api"}
    except Exception:
        return {
            "models": PROVIDER_MODELS.get(body.provider, []),
            "source": "preset",
            "error": "upstream_error",
        }


def _visible_config_keys(instance_config: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in instance_config.keys():
        normalized = str(key or "").strip()
        if not normalized:
            continue
        if _is_sensitive_config_key(normalized):
            continue
        if normalized in {"base_url", "auth_type"}:
            continue
        keys.append(normalized)
    return sorted(keys)


def _is_sensitive_config_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    compact = "".join(char for char in normalized if char.isalnum())
    # Match fragments instead of exact names so variants like accessToken,
    # provider_token, and api-key are redacted consistently.
    return any(
        fragment in normalized or fragment in compact
        for fragment in _SENSITIVE_CONFIG_KEY_FRAGMENTS
    )


def _collect_provider_field_defaults(
    service_providers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}

    for provider_type, instances in service_providers.items():
        if not isinstance(instances, dict):
            continue

        for instance_config in instances.values():
            if not isinstance(instance_config, dict):
                continue

            base_url = str(instance_config.get("base_url", "") or "").strip()
            if base_url and "base_url" not in defaults.setdefault(provider_type, {}):
                defaults[provider_type]["base_url"] = base_url
            raw_auth_type = instance_config.get("auth_type")
            if raw_auth_type and "auth_type" not in defaults.setdefault(provider_type, {}):
                try:
                    defaults[provider_type]["auth_type"] = serialize_provider_auth_type(
                        normalize_provider_auth_type_chain(
                            raw_auth_type,
                            fallback=_get_schema_default(provider_type, "auth_type"),
                        )
                    )
                except ValueError as exc:
                    logger.error(
                        "Skipping provider definition defaults for %s: %s",
                        provider_type,
                        exc,
                    )

            if (
                "base_url" in defaults.setdefault(provider_type, {})
                and "auth_type" in defaults.setdefault(provider_type, {})
            ):
                break

    return defaults


def _get_schema_default(provider_type: str, field_name: str) -> Any:
    """Return a provider schema default when the configured instance leaves a field blank."""
    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        return ""

    for field in definition.resolve_fields(filter_by_auth_type=False):
        if field.name == field_name and field.default is not None:
            return field.default
    return ""


def _normalize_instance_auth_type(
    provider_type: str,
    instance_name: str,
    instance_config: dict[str, Any],
) -> str | list[str] | None:
    """Return the public auth_type payload or None when the instance is invalid."""
    try:
        # The available-instances API is the first frontend-facing validation
        # boundary; bad auth_type values are logged and excluded from the UI.
        return serialize_provider_auth_type(
            normalize_provider_auth_type_chain(
                instance_config.get("auth_type"),
                fallback=_get_schema_default(provider_type, "auth_type"),
            )
        )
    except ValueError as exc:
        logger.error(
            "Skipping provider instance %s.%s: %s",
            provider_type,
            instance_name,
            exc,
        )
        return None


async def _get_optional_authorization_context(request: Request) -> AuthorizationContext | None:
    """Resolve authz opportunistically for provider catalog endpoints.

    The catalog is also used during bootstrap/no-DB flows, so missing auth
    middleware state or an uninitialized DB means "no RBAC context" rather than
    an authentication failure.
    """
    cached = getattr(request.state, "authorization_context", None)
    if isinstance(cached, AuthorizationContext):
        return cached

    user_info = getattr(request.state, "user_info", None)
    if not isinstance(user_info, UserInfo) or user_info.user_id == "anonymous":
        return None

    manager = get_db_manager()
    if manager is None or not manager.is_initialized:
        return None

    async with manager.get_session() as session:
        authz = await resolve_authorization_context(session, user_info)
    request.state.authorization_context = authz
    return authz


def _has_provider_catalog_governance(authz: AuthorizationContext) -> bool:
    """Return whether the caller may see denied instances for governance UI."""
    return (
        has_permission(authz, "roles.manage_permissions")
        or has_permission(authz, "providers.manage_permissions")
    )


def _get_provider_display_name(provider_type: str) -> str:
    """Prefer runtime provider metadata, then schema metadata, then raw type."""
    normalized_provider_type = str(provider_type or "").strip()
    if not normalized_provider_type:
        return ""

    try:
        from app.atlasclaw.api.deps_context import get_api_context

        registry = getattr(get_api_context(), "service_provider_registry", None)
        context_getter = getattr(registry, "get_provider_context", None)
        if callable(context_getter):
            provider_context = context_getter(normalized_provider_type)
            display_name = str(getattr(provider_context, "display_name", "") or "").strip()
            if display_name:
                return display_name
    except Exception:
        pass

    definition = get_provider_schema_definition(normalized_provider_type)
    schema_display_name = str(getattr(definition, "display_name", "") or "").strip()
    if schema_display_name:
        return schema_display_name

    return normalized_provider_type


@router.get("/service-providers/available-instances")
async def get_available_instances(
    request: Request,
    include_all: bool = Query(False, description="Return the full catalog for permission governance"),
) -> dict[str, Any]:
    """Return configured service provider instances.

    The response is intentionally safe for frontend display: it exposes provider
    identity, instance names, base URL, configured auth type when present, and
    non-sensitive config keys only.
    """
    service_providers = await get_provider_catalog_instances()
    authz = await _get_optional_authorization_context(request)
    # Role Management needs the full catalog to edit rules for denied
    # instances; ordinary callers receive the role-filtered catalog.
    if include_all:
        if authz is None or not _has_provider_catalog_governance(authz):
            raise HTTPException(
                status_code=403,
                detail="Missing permission to access full provider catalog",
            )
    elif authz is not None:
        service_providers = filter_provider_instances_for_authz(authz, service_providers)

    providers: list[dict[str, Any]] = []

    for provider_type, instances in service_providers.items():
        if not isinstance(instances, dict):
            continue

        for instance_name, instance_config in instances.items():
            if not isinstance(instance_config, dict):
                continue
            auth_type = _normalize_instance_auth_type(
                str(provider_type),
                str(instance_name),
                instance_config,
            )
            if auth_type is None:
                continue

            providers.append(
                {
                    "provider_type": provider_type,
                    "display_name": _get_provider_display_name(str(provider_type)),
                    "instance_name": instance_name,
                    "base_url": str(instance_config.get("base_url", "") or "").strip()
                    or _get_schema_default(provider_type, "base_url"),
                    "auth_type": auth_type,
                    "config_keys": _visible_config_keys(instance_config),
                }
            )

    providers.sort(key=lambda item: (item["provider_type"], item["instance_name"]))
    return {
        "count": len(providers),
        "providers": providers,
    }


@router.get("/service-providers/definitions")
async def get_service_provider_definitions() -> dict[str, Any]:
    """Return backend-managed provider definitions and form schemas."""
    service_providers = await get_provider_catalog_instances()
    providers = get_provider_schema_catalog(
        field_defaults=_collect_provider_field_defaults(service_providers)
    )
    return {
        "count": len(providers),
        "providers": providers,
    }
