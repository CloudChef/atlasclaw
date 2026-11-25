# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Contract tests for the provider extension manifest block."""

from __future__ import annotations

import json
from pathlib import Path

from app.atlasclaw.api.service_provider_schemas import load_provider_schema_definition


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    manifest = tmp_path / "provider.schema.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def _base_manifest(**overrides) -> dict:
    manifest = {
        "schema_version": 1,
        "provider_type": "example",
        "catalog": {"display_name": "Example"},
        "config_schema": {"fields": []},
    }
    manifest.update(overrides)
    return manifest


def test_v1_extension_manifest_parses_nav_and_api(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest(
        catalog={"display_name": "Audit", "extension_only": True},
        runtime_capabilities=["documents"],
        extensions={
            "version": 1,
            "requires": {"host": ">=1.0 <2.0"},
            "ui": {
                "assets_root": "ui",
                "nav": [
                    {
                        "id": "file-audit",
                        "title": {"zh-CN": "文件审核", "en-US": "File Audit"},
                        "entry": "index.html",
                        "access": "user",
                        "layout": "full",
                        "order": 10,
                    },
                    {
                        "id": "admin-audit",
                        "title": {"en-US": "Audit Admin"},
                        "entry": "admin.html",
                        "access": "admin",
                        "order": 20,
                    },
                ],
            },
            "api": {"manifest": "runtime-api.json", "timeout_seconds": 45, "max_body_bytes": 1048576},
        },
    ))

    definition = load_provider_schema_definition(manifest)

    assert definition.extension_only is True
    assert definition.extensions_version == 1
    assert definition.requires_host == ">=1.0 <2.0"
    assert definition.ui_assets_root == "ui"
    assert [nav.id for nav in definition.ui_nav] == ["file-audit", "admin-audit"]
    assert definition.ui_nav[0].resolve_title("zh-CN") == "文件审核"
    assert definition.ui_nav[1].access == "admin"
    assert definition.api_manifest == "runtime-api.json"
    assert definition.api_timeout_seconds == 45
    assert definition.api_max_body_bytes == 1048576


def test_invalid_nav_entries_are_skipped_individually(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest(extensions={
        "version": 1,
        "ui": {
            "nav": [
                {"id": "Bad Id", "title": {"en-US": "Bad"}},
                {"id": "escape", "title": {"en-US": "Escape"}, "entry": "../index.html"},
                {"id": "ok", "title": {"en-US": "OK"}, "entry": "index.html"},
                {"id": "ok", "title": {"en-US": "Duplicate"}, "entry": "dup.html"},
            ]
        },
    }))

    definition = load_provider_schema_definition(manifest)

    assert [nav.id for nav in definition.ui_nav] == ["ok"]
    assert definition.ui_nav[0].title == {"en-US": "OK"}


def test_unsupported_version_disables_extensions_but_keeps_provider(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest(extensions={
        "version": 99,
        "ui": {"nav": [{"id": "x", "title": {"en-US": "X"}}]},
    }))

    definition = load_provider_schema_definition(manifest)

    assert definition.provider_type == "example"
    assert definition.extensions_version == 99
    assert definition.ui_nav == ()


def test_manifest_without_extensions_keeps_defaults(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest())

    definition = load_provider_schema_definition(manifest)

    assert definition.extensions_version == 0
    assert definition.ui_nav == ()
    assert definition.ui_assets_root == "ui"
    assert definition.api_manifest == "runtime-api.json"
    assert definition.extension_only is False


def test_nav_title_fallbacks(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest(extensions={
        "version": 1,
        "ui": {"nav": [{
            "id": "file-audit",
            "title": {"zh-CN": "文件审核", "en-US": "File Audit"},
            "entry": "index.html",
        }]},
    }))

    nav = load_provider_schema_definition(manifest).ui_nav[0]

    assert nav.resolve_title("zh-TW") == "文件审核"
    assert nav.resolve_title("fr-FR") == "File Audit"
    assert nav.resolve_title("") == "File Audit"


def test_layout_and_limits_are_normalized(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _base_manifest(extensions={
        "version": 1,
        "ui": {"nav": [{
            "id": "page",
            "title": {"en-US": "Page"},
            "entry": "page.html",
            "layout": "weird",
            "order": "not-a-number",
        }]},
        "api": {"timeout_seconds": -1, "max_body_bytes": "bad"},
    }))

    definition = load_provider_schema_definition(manifest)

    assert definition.ui_nav[0].layout == "full"
    assert definition.ui_nav[0].order == 100
    assert definition.api_timeout_seconds == 30.0
    assert definition.api_max_body_bytes == 32 * 1024 * 1024
