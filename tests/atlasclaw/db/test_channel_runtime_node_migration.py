# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Alembic contract for the one-column minimal HA migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "versions" / "010_add_channel_runtime_node_id.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("channel_owner_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_and_indexes_the_runtime_node_column() -> None:
    module = _load_migration()
    with patch.object(module.op, "add_column") as add_column, patch.object(
        module.op,
        "create_index",
    ) as create_index:
        module.upgrade()

    table_name, column = add_column.call_args.args
    assert table_name == "channels"
    assert column.name == "runtime_node_id"
    assert column.nullable is True
    create_index.assert_called_once_with(
        "ix_channels_runtime_node_id",
        "channels",
        ["runtime_node_id"],
        unique=False,
    )


def test_migration_downgrade_removes_the_runtime_node_column_and_index() -> None:
    module = _load_migration()
    with patch.object(module.op, "drop_index") as drop_index, patch.object(
        module.op,
        "drop_column",
    ) as drop_column:
        module.downgrade()

    drop_index.assert_called_once_with(
        "ix_channels_runtime_node_id",
        table_name="channels",
    )
    drop_column.assert_called_once_with("channels", "runtime_node_id")


def test_migration_revision_follows_channel_provisioning_sessions() -> None:
    module = _load_migration()

    assert module.revision == "010"
    assert module.down_revision == "009"
