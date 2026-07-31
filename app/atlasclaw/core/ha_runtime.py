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


def token_health_storage_path(
    workspace_path: str | Path,
    settings: HaRuntimeSettings,
) -> Path:
    """Return node-local token health storage in HA and workspace storage otherwise."""
    if settings.enabled:
        assert settings.runtime_dir is not None
        return settings.runtime_dir
    return Path(workspace_path).resolve()


def runtime_state_storage_path(
    workspace_path: str | Path,
    settings: HaRuntimeSettings,
) -> Path:
    """Keep process-owned HA state out of the shared workspace."""
    if settings.enabled:
        assert settings.runtime_dir is not None
        return settings.runtime_dir
    return Path(workspace_path).resolve()


_HA_LONG_CONNECTION_MODES = {
    "feishu": "longconnection",
    "dingtalk": "stream",
    "wecom": "websocket",
}


def validate_ha_channel_mode(
    channel_type: str,
    config: Mapping[str, object],
) -> None:
    """Reject Channel modes that cannot be routed by the minimal HA design."""
    expected_mode = _HA_LONG_CONNECTION_MODES.get(channel_type)
    configured_mode = str(config.get("connection_mode", "") or "").strip().lower()
    if expected_mode is None:
        if configured_mode == "webhook" or config.get("webhook_url"):
            raise ValueError(
                f"HA requires {channel_type} long-connection mode; webhook is not supported"
            )
        return

    if not configured_mode:
        configured_mode = "webhook" if config.get("webhook_url") else expected_mode
    if configured_mode != expected_mode:
        raise ValueError(
            f"HA requires {channel_type} long-connection mode "
            f"({expected_mode}); {configured_mode} is not supported"
        )
