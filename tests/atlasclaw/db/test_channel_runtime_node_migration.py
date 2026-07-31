# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Alembic contract for the one-column minimal HA migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "versions" / "010_add_channel_runtime_node_id.py"


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_only_adds_runtime_node_id_to_channels() -> None:
    text = _migration_text()

    assert "op.add_column(" in text
    assert '"channels",' in text
    assert '"runtime_node_id"' in text
    assert 'op.create_index("ix_channels_runtime_node_id"' in text
    assert "op.create_table" not in text
    assert "ha_coordination" not in text
    assert "inbox" not in text.lower()
    assert "outbox" not in text.lower()


def test_migration_has_upgrade_and_downgrade_entrypoints() -> None:
    spec = importlib.util.spec_from_file_location("channel_owner_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "010"
    assert module.down_revision == "009"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
