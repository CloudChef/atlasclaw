# -*- coding: utf-8 -*-
"""Provider information and model-discovery routes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel as PydanticBaseModel

from app.atlasclaw.api.service_provider_schemas import get_provider_schema_catalog

router = APIRouter(tags=["Provider API"])

_SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "cookie",
        "password",
        "secret",
        "app_secret",
        "api_key",
        "access_token",
        "token",
        "credential",
    }
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
        if normalized.lower() in _SENSITIVE_CONFIG_KEYS:
            continue
        if normalized in {"base_url", "auth_type"}:
            continue
        keys.append(normalized)
    return sorted(keys)


def _collect_provider_field_defaults(
    service_providers: dict[str, Any],
) -> dict[str, dict[str, str]]:
    defaults: dict[str, dict[str, str]] = {}

    for provider_type, instances in service_providers.items():
        if not isinstance(instances, dict):
            continue

        for instance_config in instances.values():
            if not isinstance(instance_config, dict):
                continue

            base_url = str(instance_config.get("base_url", "") or "").strip()
            if base_url and "base_url" not in defaults.setdefault(provider_type, {}):
                defaults[provider_type]["base_url"] = base_url
            auth_type = str(instance_config.get("auth_type", "") or "").strip()
            if auth_type and "auth_type" not in defaults.setdefault(provider_type, {}):
                defaults[provider_type]["auth_type"] = auth_type

            if (
                "base_url" in defaults.setdefault(provider_type, {})
                and "auth_type" in defaults.setdefault(provider_type, {})
            ):
                break

    return defaults


@router.get("/service-providers/available-instances")
async def get_available_instances() -> dict[str, Any]:
    """Return configured service provider instances from atlasclaw.json.

    The response is intentionally safe for frontend display: it exposes provider
    identity, instance names, base URL, configured auth type when present, and
    non-sensitive config keys only.
    """
    from app.atlasclaw.core.config import get_config

    config = get_config()
    service_providers = config.service_providers or {}

    providers: list[dict[str, Any]] = []

    for provider_type, instances in service_providers.items():
        if not isinstance(instances, dict):
            continue

        for instance_name, instance_config in instances.items():
            if not isinstance(instance_config, dict):
                continue

            providers.append(
                {
                    "provider_type": provider_type,
                    "instance_name": instance_name,
                    "base_url": str(instance_config.get("base_url", "") or ""),
                    "auth_type": str(instance_config.get("auth_type", "") or ""),
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
    from app.atlasclaw.core.config import get_config

    config = get_config()
    service_providers = config.service_providers or {}
    providers = get_provider_schema_catalog(
        field_defaults=_collect_provider_field_defaults(service_providers)
    )
    return {
        "count": len(providers),
        "providers": providers,
    }
