# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider UI extension API: discovery and static serving."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes_ui_extensions import register_ui_extension_routes
from app.atlasclaw.api.service_provider_schemas import ProviderSchemaDefinition, ProviderUiNav
from app.atlasclaw.auth.guards import get_authorization_context
from app.atlasclaw.core.provider_extension_registry import get_provider_extension_registry


def _provider(root: Path) -> Path:
    provider = root / "audit-report"
    (provider / "ui" / "assets").mkdir(parents=True)
    (provider / "ui" / "index.html").write_text("<html>audit</html>", encoding="utf-8")
    (provider / "ui" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (provider / "assets").mkdir()
    (provider / "assets" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    return provider


def _definition(*navs: ProviderUiNav) -> ProviderSchemaDefinition:
    return ProviderSchemaDefinition(
        provider_type="audit-report",
        display_name="Audit",
        fields=(),
        ui_nav=navs,
        extensions_version=1,
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    registry = get_provider_extension_registry()
    registry.clear()
    registry.register(provider_root=_provider(tmp_path), definition=_definition(
        ProviderUiNav(id="file-audit", title={"zh-CN": "文件审核", "en-US": "File Audit"}, entry="index.html", order=10),
        ProviderUiNav(id="admin", title={"en-US": "Admin"}, entry="index.html", access="admin", order=20),
    ))

    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_ui_extension_routes(router)
    app.include_router(router)
    app.dependency_overrides[get_authorization_context] = lambda: SimpleNamespace(
        is_admin=False, user=SimpleNamespace(user_id="alice"),
    )
    yield TestClient(app)
    registry.clear()


def test_lists_authorized_navigation(client: TestClient) -> None:
    response = client.get("/api/ui/extensions", headers={"Accept-Language": "zh-CN,zh;q=0.9"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["host"]["version"] == 1
    assert payload["host"]["locale"] == "zh-CN"
    assert [item["nav_id"] for item in payload["items"]] == ["file-audit"]
    item = payload["items"][0]
    assert item["title"] == "文件审核"
    assert item["route"] == "/ext/audit-report/file-audit"
    assert item["entry_url"] == "/api/ui/extensions/audit-report/file-audit/"
    assert item["icon_url"] == ""


def test_admin_sees_admin_entries(client: TestClient) -> None:
    client.app.dependency_overrides[get_authorization_context] = lambda: SimpleNamespace(
        is_admin=True, user=SimpleNamespace(user_id="admin"),
    )

    payload = client.get("/api/ui/extensions").json()

    assert [item["nav_id"] for item in payload["items"]] == ["file-audit", "admin"]


def test_serves_entry_and_assets_with_safe_headers(client: TestClient) -> None:
    html = client.get("/api/ui/extensions/audit-report/file-audit/")
    assert html.status_code == 200
    assert html.text == "<html>audit</html>"
    assert html.headers["content-type"].startswith("text/html")
    assert html.headers["x-content-type-options"] == "nosniff"
    assert html.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'self'" in html.headers["content-security-policy"]

    script = client.get("/api/ui/extensions/audit-report/file-audit/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"].startswith("private")


def test_serves_provider_assets(client: TestClient) -> None:
    response = client.get("/api/ui/extensions/audit-report/_assets/icon.svg")

    assert response.status_code == 200
    assert response.text == "<svg/>"
    assert "svg" in response.headers["content-type"]


@pytest.mark.parametrize("path", [
    "/api/ui/extensions/audit-report/file-audit/../index.html",
    "/api/ui/extensions/audit-report/file-audit/%2e%2e/index.html",
    "/api/ui/extensions/audit-report/file-audit/missing.js",
    "/api/ui/extensions/audit-report/unknown/index.html",
])
def test_unknown_or_traversal_assets_are_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_admin_extension_requires_admin(client: TestClient) -> None:
    assert client.get("/api/ui/extensions/audit-report/admin/").status_code == 403
