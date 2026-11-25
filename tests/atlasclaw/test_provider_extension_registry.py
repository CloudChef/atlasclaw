# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider extension registry: navigation filtering and asset jails."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.atlasclaw.api.service_provider_schemas import ProviderSchemaDefinition, ProviderUiNav
from app.atlasclaw.core.provider_extension_registry import (
    ExtensionAssetError,
    ProviderExtensionRegistry,
)


def _definition(*navs: ProviderUiNav, provider_type: str = "audit-report") -> ProviderSchemaDefinition:
    return ProviderSchemaDefinition(
        provider_type=provider_type,
        display_name="Audit",
        fields=(),
        ui_assets_root="ui",
        ui_nav=navs,
        extensions_version=1,
    )


def _provider(tmp_path: Path) -> Path:
    root = tmp_path / "audit-report"
    (root / "ui" / "assets").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (root / "ui" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "ui" / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    return root


def test_registers_nav_and_filters_admin_entries(tmp_path: Path) -> None:
    root = _provider(tmp_path)
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="index.html", order=10),
        ProviderUiNav(id="admin", title={"en-US": "Admin"}, entry="index.html", access="admin", order=20),
    ))

    user_items = registry.list_nav(is_admin=False)
    admin_items = registry.list_nav(is_admin=True)

    assert [item["nav_id"] for item in user_items] == ["file-audit"]
    assert [item["nav_id"] for item in admin_items] == ["file-audit", "admin"]
    assert user_items[0]["route"] == "/ext/audit-report/file-audit"
    assert user_items[0]["entry_url"] == "/api/ui/extensions/audit-report/file-audit/"


def test_orders_by_order_then_provider(tmp_path: Path) -> None:
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=_provider(tmp_path), definition=_definition(
        ProviderUiNav(id="second", title={"en-US": "Second"}, entry="index.html", order=20),
        ProviderUiNav(id="first", title={"en-US": "First"}, entry="index.html", order=10),
    ))
    other_root = _provider(tmp_path / "other")
    registry.register(provider_root=other_root, definition=_definition(
        ProviderUiNav(id="same", title={"en-US": "Same"}, entry="index.html", order=10),
        provider_type="other",
    ))

    items = registry.list_nav(is_admin=False)

    assert [item["id"] for item in items] == ["audit-report.first", "other.same", "audit-report.second"]


def test_resolves_assets_with_default_entry(tmp_path: Path) -> None:
    root = _provider(tmp_path)
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="index.html"),
    ))
    entry = registry.resolve_nav("audit-report", "file-audit")
    assert entry is not None

    assert registry.resolve_asset(entry, "") == (root / "ui" / "index.html").resolve()
    assert registry.resolve_asset(entry, "assets/app.css") == (root / "ui" / "assets" / "app.css").resolve()
    assert registry.resolve_provider_asset("audit-report", "icon.svg") == (root / "assets" / "icon.svg").resolve()


@pytest.mark.parametrize("bad_path", ["../index.html", "..\\index.html", "/etc/passwd", "assets/../../x.html"])
def test_asset_traversal_is_rejected(tmp_path: Path, bad_path: str) -> None:
    root = _provider(tmp_path)
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="index.html"),
    ))
    entry = registry.resolve_nav("audit-report", "file-audit")
    assert entry is not None

    with pytest.raises(ExtensionAssetError):
        registry.resolve_asset(entry, bad_path)


def test_asset_symlink_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    root = _provider(tmp_path)
    outside = tmp_path / "outside.html"
    outside.write_text("secret", encoding="utf-8")
    link = root / "ui" / "linked.html"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation requires privileges")

    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="index.html"),
    ))
    entry = registry.resolve_nav("audit-report", "file-audit")
    assert entry is not None

    with pytest.raises(ExtensionAssetError):
        registry.resolve_asset(entry, "linked.html")


def test_disallowed_asset_type_is_rejected(tmp_path: Path) -> None:
    root = _provider(tmp_path)
    (root / "ui" / "data.py").write_text("print(1)", encoding="utf-8")
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="index.html"),
    ))
    entry = registry.resolve_nav("audit-report", "file-audit")
    assert entry is not None

    with pytest.raises(ExtensionAssetError):
        registry.resolve_asset(entry, "data.py")


def test_missing_entry_is_not_found(tmp_path: Path) -> None:
    root = _provider(tmp_path)
    registry = ProviderExtensionRegistry()
    registry.register(provider_root=root, definition=_definition(
        ProviderUiNav(id="file-audit", title={"en-US": "File Audit"}, entry="missing.html"),
    ))
    entry = registry.resolve_nav("audit-report", "file-audit")
    assert entry is not None

    with pytest.raises(ExtensionAssetError) as exc:
        registry.resolve_asset(entry, "")

    assert exc.value.reason == "not_found"
