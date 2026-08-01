# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Loader for the configured embed Provider and its v1 route manifest."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.atlasclaw.core.config_schema import EmbedIntegrationConfig
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.skills.md_tool_runtime import (
    load_callable_from_file,
    parse_entrypoint,
)

from .models import RouteManifest


@dataclass(frozen=True)
class LoadedEmbedIntegration:
    """The single validated default Provider and its route manifest."""

    config: EmbedIntegrationConfig
    routes: RouteManifest
    provider_root: Path
    resolver_handler: Callable[..., Any]

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
        resolver_path, resolver_attr = self._resolve_provider_entrypoint(
            provider_root,
            raw_entrypoint=routes.context_resolver.entrypoint,
        )
        try:
            resolver_handler = load_callable_from_file(resolver_path, resolver_attr)
        except (AttributeError, ImportError, ValueError) as exc:
            raise ValueError(f"failed to load context resolver entrypoint: {exc}") from exc
        if not inspect.iscoroutinefunction(resolver_handler):
            raise ValueError("context resolver entrypoint must be an async callable")
        self._integration = LoadedEmbedIntegration(
            config=validated,
            routes=routes,
            provider_root=provider_root,
            resolver_handler=resolver_handler,
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
    def _resolve_provider_entrypoint(
        provider_root: Path,
        *,
        raw_entrypoint: str,
    ) -> tuple[Path, str]:
        """Resolve one explicit async Provider callable below its package root."""
        raw_entrypoint = str(raw_entrypoint or "").strip()
        module_path, attr_name, explicit_callable = parse_entrypoint(raw_entrypoint)
        if (
            not raw_entrypoint
            or not explicit_callable
            or "\\" in module_path
            or Path(module_path).is_absolute()
            or Path(module_path).suffix != ".py"
            or not attr_name.isidentifier()
        ):
            raise ValueError("context resolver entrypoint is invalid")
        path = (provider_root / module_path).resolve()
        if provider_root != path and provider_root not in path.parents:
            raise ValueError("context resolver entrypoint escapes provider root")
        if not path.is_file():
            raise ValueError("context resolver entrypoint does not exist")
        return path, attr_name
