# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Internal HA startup settings and workspace safety checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.atlasclaw.core.workspace import WorkspaceInitializer


@dataclass(frozen=True)
class HaRuntimeSettings:
    """HA settings supplied by the service manager, not ``atlasclaw.json``."""

    enabled: bool
    node_id: str | None
    runtime_dir: Path | None
    run_agent_heartbeat: bool

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        working_directory: str | Path | None = None,
    ) -> "HaRuntimeSettings":
        """Build settings from the service environment and working directory."""
        values = os.environ if environment is None else environment
        enabled = values.get("ATLASCLAW_ENABLE_HA", "").strip().lower() == "true"
        if not enabled:
            return cls(
                enabled=False,
                node_id=None,
                runtime_dir=None,
                run_agent_heartbeat=True,
            )

        node_id = values.get("ATLASCLAW_HA_NODE_ID", "").strip()
        if not node_id:
            raise ValueError("ATLASCLAW_HA_NODE_ID is required when HA is enabled")

        base_dir = Path.cwd() if working_directory is None else Path(working_directory)
        return cls(
            enabled=True,
            node_id=node_id,
            runtime_dir=(base_dir.resolve() / "runtime"),
            run_agent_heartbeat=(
                values.get("ATLASCLAW_RUN_AGENT_HEARTBEAT", "").strip().lower()
                == "true"
            ),
        )


def prepare_workspace_for_startup(
    workspace_initializer: WorkspaceInitializer,
    settings: HaRuntimeSettings,
) -> bool:
    """Initialize standalone workspaces or validate an HA shared workspace.

    Returns whether the workspace existed before the startup check. HA callers never
    invoke the initializer because the shared workspace is deployment-owned.
    """
    was_initialized = workspace_initializer.is_initialized()
    if settings.enabled:
        if not was_initialized:
            raise RuntimeError("HA workspace is incomplete; deployment initialization is required")
        return True

    if not workspace_initializer.initialize():
        raise RuntimeError("Workspace initialization failed")
    return was_initialized


def runtime_storage_path(
    workspace_path: str | Path,
    settings: HaRuntimeSettings,
) -> Path:
    """Return node-local HA runtime storage or standalone workspace storage."""
    if settings.enabled:
        assert settings.runtime_dir is not None
        return settings.runtime_dir
    return Path(workspace_path).resolve()


def validate_ha_channel_mode(
    handler_class: type[object],
    config: Mapping[str, object],
) -> None:
    """Require the registered Channel handler to select a long connection."""
    channel_type = str(getattr(handler_class, "channel_type", "") or "unknown")
    supports_long_connection = bool(
        getattr(handler_class, "supports_long_connection", False)
    )
    uses_long_connection = getattr(handler_class, "uses_long_connection", None)
    if (
        not supports_long_connection
        or not callable(uses_long_connection)
        or not bool(uses_long_connection(dict(config)))
    ):
        raise ValueError(f"HA requires {channel_type} long-connection mode")
