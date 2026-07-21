# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Loader for the configured embed Provider and its v1 route manifest."""

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
    """The single validated default Provider and its route manifest."""

    config: EmbedIntegrationConfig
    routes: RouteManifest
    provider_root: Path

    @property
    def agent_id(self) -> str:
        """Return the fixed Agent used by the embedded UI."""
        return "main"

    @property
    def session_scope(self) -> str:
        """Derive the Chat scope from the selected default Provider."""
        return self.config.provider_type

    @property
    def context_ttl_seconds(self) -> int:
        """Return the fixed in-memory Context lifetime."""
        return 1800

    @property
    def max_contexts_per_user(self) -> int:
        """Return the fixed per-user Context capacity."""
        return 128


class EmbedIntegrationRegistry:
    """Load the single default Provider without embedding Provider rules in Core."""

    def __init__(
        self,
        config: EmbedIntegrationConfig | dict[str, Any] | None,
        provider_registry: ServiceProviderRegistry,
    ) -> None:
        """Load the configured Provider and its conventional manifest, if present."""
        self._integration: LoadedEmbedIntegration | None = None
        if config is None:
            return
        validated = (
            config
            if isinstance(config, EmbedIntegrationConfig)
            else EmbedIntegrationConfig.model_validate(config)
        )
        template = provider_registry.get_template_for_provider_type(validated.provider_type)
        if template is None:
            raise ValueError(
                f"embed provider is not loaded: {validated.provider_type}"
            )
        provider_root = template.path.resolve()
        manifest_path = "assistant_context/routes.json"
        routes = RouteManifest.model_validate(
            self._load_manifest(provider_root, manifest_path)
        )
        if routes.provider_type != validated.provider_type:
            raise ValueError(
                "route manifest provider_type must equal the default embed provider_type"
            )
        self._validate_resolver_entrypoints(provider_root, routes)
        self._integration = LoadedEmbedIntegration(
            config=validated,
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

    def get(self) -> LoadedEmbedIntegration | None:
        """Return the configured default embedded Provider, if enabled."""
        return self._integration

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
