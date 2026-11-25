# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Alembic contract for generic chat attachment metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "versions" / "013_add_chat_document_attachments.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("chat_document_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Capture batch operations without requiring a database connection."""

    def __init__(self) -> None:
        self.added: list = []
        self.altered: list = []
        self.dropped: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def add_column(self, column) -> None:
        self.added.append(column)

    def alter_column(self, name, **kwargs) -> None:
        self.altered.append((name, kwargs))

    def drop_column(self, name) -> None:
        self.dropped.append(name)


def test_migration_follows_chat_attachments():
    module = _load_migration()

    assert module.revision == "013"
    assert module.down_revision == "012"


def test_upgrade_adds_kind_extension_and_metadata_columns():
    module = _load_migration()
    recorder = _Recorder()

    with patch.object(module.op, "batch_alter_table", return_value=recorder):
        module.upgrade()

    added = {column.name: column for column in recorder.added}
    assert set(added) == {"kind", "extension", "metadata_json"}
    assert added["kind"].nullable is False
    assert added["extension"].nullable is False
    assert added["metadata_json"].nullable is True
    assert {name for name, _ in recorder.altered} == {"width", "height"}
    assert all(kwargs.get("nullable") is True for _, kwargs in recorder.altered)


def test_downgrade_drops_generic_attachment_columns():
    module = _load_migration()
    recorder = _Recorder()

    with patch.object(module.op, "batch_alter_table", return_value=recorder):
        module.downgrade()

    assert set(recorder.dropped) == {"kind", "extension", "metadata_json"}
    assert {name for name, _ in recorder.altered} == {"width", "height"}
