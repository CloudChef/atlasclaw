# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the internal HA startup boundary."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.handlers import (
    DingTalkHandler,
    FeishuHandler,
    RESTHandler,
    WeComHandler,
)
from app.atlasclaw.core.ha_runtime import (
    HaRuntimeSettings,
    prepare_workspace_for_startup,
    runtime_storage_path,
    validate_ha_channel_mode,
)
from app.atlasclaw.core.workspace import WorkspaceInitializer


def test_enabled_ha_uses_a_node_local_runtime_directory(tmp_path: Path) -> None:
    settings = HaRuntimeSettings.from_environment(
        {
            "ATLASCLAW_ENABLE_HA": "true",
            "ATLASCLAW_HA_NODE_ID": "node-a",
        },
        working_directory=tmp_path,
    )

    assert settings.enabled is True
    assert settings.node_id == "node-a"
    assert settings.runtime_dir == (tmp_path / "runtime").resolve()
    assert settings.run_agent_heartbeat is False


def test_primary_ha_node_may_run_agent_heartbeat(tmp_path: Path) -> None:
    settings = HaRuntimeSettings.from_environment(
        {
            "ATLASCLAW_ENABLE_HA": "true",
            "ATLASCLAW_HA_NODE_ID": "node-a",
            "ATLASCLAW_RUN_AGENT_HEARTBEAT": "true",
        },
        working_directory=tmp_path,
    )

    assert settings.run_agent_heartbeat is True


def test_enabled_ha_requires_a_stable_node_identifier(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ATLASCLAW_HA_NODE_ID"):
        HaRuntimeSettings.from_environment(
            {"ATLASCLAW_ENABLE_HA": "true"},
            working_directory=tmp_path,
        )


def test_disabled_ha_keeps_runtime_state_under_workspace(tmp_path: Path) -> None:
    settings = HaRuntimeSettings.from_environment({}, working_directory=tmp_path)

    assert settings.enabled is False
    assert settings.node_id is None
    assert settings.runtime_dir is None
    assert settings.run_agent_heartbeat is True


def test_ha_token_health_storage_is_not_under_shared_workspace(tmp_path: Path) -> None:
    settings = HaRuntimeSettings.from_environment(
        {
            "ATLASCLAW_ENABLE_HA": "true",
            "ATLASCLAW_HA_NODE_ID": "node-a",
        },
        working_directory=tmp_path / "node-a",
    )

    assert runtime_storage_path(tmp_path / "shared-workspace", settings) == (
        tmp_path / "node-a" / "runtime"
    ).resolve()


@pytest.mark.parametrize(
    ("handler_class", "config"),
    [
        (FeishuHandler, {"connection_mode": "webhook", "webhook_url": "https://example.test"}),
        (DingTalkHandler, {"connection_mode": "webhook", "webhook_url": "https://example.test"}),
        (WeComHandler, {"connection_mode": "webhook", "webhook_url": "https://example.test"}),
        (WeComHandler, {"connection_mode": "app", "corpid": "corp"}),
        (RESTHandler, {}),
    ],
)
def test_ha_rejects_non_long_connection_channel_modes(
    handler_class: type[ChannelHandler],
    config: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="long-connection mode"):
        validate_ha_channel_mode(handler_class, config)


@pytest.mark.parametrize(
    ("handler_class", "config"),
    [
        (FeishuHandler, {"connection_mode": "longconnection"}),
        (DingTalkHandler, {"connection_mode": "stream"}),
        (WeComHandler, {"connection_mode": "websocket"}),
    ],
)
def test_ha_accepts_supported_long_connection_channel_modes(
    handler_class: type[ChannelHandler],
    config: dict[str, str],
) -> None:
    validate_ha_channel_mode(handler_class, config)


def test_ha_startup_validates_an_initialized_workspace_without_writing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initializer = WorkspaceInitializer(str(workspace))
    assert initializer.initialize() is True
    marker = workspace / "runtime_state.json"
    before = marker.read_bytes()
    settings = HaRuntimeSettings.from_environment(
        {
            "ATLASCLAW_ENABLE_HA": "true",
            "ATLASCLAW_HA_NODE_ID": "node-a",
        },
        working_directory=tmp_path,
    )

    prepare_workspace_for_startup(initializer, settings)

    assert marker.read_bytes() == before


def test_ha_startup_rejects_an_incomplete_workspace_without_initializing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initializer = WorkspaceInitializer(str(workspace))
    settings = HaRuntimeSettings.from_environment(
        {
            "ATLASCLAW_ENABLE_HA": "true",
            "ATLASCLAW_HA_NODE_ID": "node-a",
        },
        working_directory=tmp_path,
    )

    with pytest.raises(RuntimeError, match="HA workspace is incomplete"):
        prepare_workspace_for_startup(initializer, settings)

    assert not workspace.exists()


def test_non_ha_startup_initializes_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initializer = WorkspaceInitializer(str(workspace))
    settings = HaRuntimeSettings.from_environment({}, working_directory=tmp_path)

    was_initialized = prepare_workspace_for_startup(initializer, settings)

    assert was_initialized is False
    assert initializer.is_initialized() is True


def test_non_ha_startup_reports_a_workspace_initialization_failure() -> None:
    initializer = MagicMock()
    initializer.is_initialized.return_value = False
    initializer.initialize.return_value = False

    with pytest.raises(RuntimeError, match="Workspace initialization failed"):
        prepare_workspace_for_startup(
            initializer,
            HaRuntimeSettings(
                enabled=False,
                node_id=None,
                runtime_dir=None,
                run_agent_heartbeat=True,
            ),
        )
