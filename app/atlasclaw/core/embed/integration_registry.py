# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Loader for configured embed profiles and provider-owned v1 manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.atlasclaw.core.config_schema import EmbedIntegrationConfig
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry

from .models import RouteManifest


@dataclass(frozen=True)
class LoadedEmbedIntegration:
    """A validated profile and its provider-owned route manifest."""

    integration_id: str
    config: EmbedIntegrationConfig
    routes: RouteManifest
    provider_root: Path


class EmbedIntegrationRegistry:
    """Resolve configured integrations without embedding provider-specific rules in Core."""

    def __init__(
        self,
        profiles: dict[str, EmbedIntegrationConfig | dict[str, Any]],
        provider_registry: ServiceProviderRegistry,
    ) -> None:
        """Load enabled profiles and fail fast on invalid provider manifests."""
        self._integrations: dict[str, LoadedEmbedIntegration] = {}
        for integration_id, raw_profile in profiles.items():
            profile = (
                raw_profile
                if isinstance(raw_profile, EmbedIntegrationConfig)
                else EmbedIntegrationConfig.model_validate(raw_profile)
            )
            if not profile.enabled:
                continue
            normalized_id = str(integration_id or "").strip()
            if not normalized_id:
                raise ValueError("embed integration id must not be empty")
            template = provider_registry.get_template_for_provider_type(profile.provider_type)
            if template is None:
                raise ValueError(
                    f"embed integration '{normalized_id}' provider is not loaded: "
                    f"{profile.provider_type}"
                )
            provider_root = template.path.resolve()
            routes = RouteManifest.model_validate(
                self._load_manifest(provider_root, profile.route_manifest)
            )
            if routes.provider_type != profile.provider_type:
                raise ValueError(
                    f"route manifest provider_type must equal profile provider_type: {normalized_id}"
                )
            self._validate_resolver_entrypoints(provider_root, routes)
            self._integrations[normalized_id] = LoadedEmbedIntegration(
                integration_id=normalized_id,
                config=profile,
                routes=routes,
                provider_root=provider_root,
            )

    @staticmethod
    def _load_manifest(provider_root: Path, relative_path: str) -> dict[str, Any]:
        path = (provider_root / relative_path).resolve()
        if provider_root != path and provider_root not in path.parents:
            raise ValueError(f"embed manifest escapes provider root: {relative_path}")
        if not path.is_file():
            raise ValueError(f"embed manifest does not exist: {relative_path}")
        try:
            raw = path.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ValueError(f"embed manifest exceeds 1 MiB: {relative_path}")
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to read embed manifest {relative_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"embed manifest must contain a JSON object: {relative_path}")
        return payload

    def get(self, integration_id: str) -> LoadedEmbedIntegration | None:
        """Return an enabled loaded integration by exact identifier."""
        return self._integrations.get(str(integration_id or "").strip())

    @staticmethod
    def _validate_resolver_entrypoints(
        provider_root: Path,
        routes: RouteManifest,
    ) -> None:
        """Require the Provider-level resolver script to stay inside its package root."""
        EmbedIntegrationRegistry._validate_provider_entrypoint(
            provider_root,
            raw_entrypoint=routes.context_resolver.entrypoint,
        )

    @staticmethod
    def _validate_provider_entrypoint(
        provider_root: Path,
        *,
        raw_entrypoint: str,
    ) -> None:
        """Require one Provider executable to be a Python file below its root."""
        raw_entrypoint = str(raw_entrypoint or "").strip()
        if (
            not raw_entrypoint
            or "\\" in raw_entrypoint
            or Path(raw_entrypoint).is_absolute()
            or Path(raw_entrypoint).suffix != ".py"
        ):
            raise ValueError("context resolver entrypoint is invalid")
        path = (provider_root / raw_entrypoint).resolve()
        if provider_root != path and provider_root not in path.parents:
            raise ValueError("context resolver entrypoint escapes provider root")
        if not path.is_file():
            raise ValueError("context resolver entrypoint does not exist")

    def list_ids(self) -> list[str]:
        """Return stable enabled integration identifiers."""
        return sorted(self._integrations)
