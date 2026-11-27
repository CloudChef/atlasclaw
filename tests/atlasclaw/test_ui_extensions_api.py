# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider UI extension API: discovery and static serving."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.deps_context import APIContext, get_api_context
from app.atlasclaw.api.provider_http_routes import router as provider_http_router
from app.atlasclaw.api.routes_ui_extensions import register_ui_extension_routes
from app.atlasclaw.api.service_provider_schemas import ProviderSchemaDefinition, ProviderUiNav
from app.atlasclaw.auth.guards import AuthorizationContext, get_authorization_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.model_bindings import ModelBindingError
from app.atlasclaw.core.provider_extension_registry import get_provider_extension_registry
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


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


class _SyntheticBindingResolver:
    """Model the committed activation overlay without sharing global DB state."""

    def __init__(
        self,
        visible_instances: dict[str, dict[str, dict[str, Any]]],
        enabled_instances: set[tuple[str, str]],
    ) -> None:
        self.visible_instances = deepcopy(visible_instances)
        self.enabled_instances = set(enabled_instances)

    async def effective_provider_instances(self, _instances):
        return deepcopy(self.visible_instances)

    async def provider_is_enabled(self, provider_type: str, instance_name: str) -> bool:
        return (provider_type, instance_name) in self.enabled_instances

    async def ensure_provider_enabled(self, provider_type: str, instance_name: str) -> None:
        if not await self.provider_is_enabled(provider_type, instance_name):
            raise ModelBindingError("Provider is not registered or disabled")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    registry = get_provider_extension_registry()
    registry.clear()
    registry.register(provider_root=_provider(tmp_path), definition=_definition(
        ProviderUiNav(id="file-audit", title={"zh-CN": "文件审核", "en-US": "File Audit"}, entry="index.html", order=10),
        ProviderUiNav(id="admin", title={"en-US": "Admin"}, entry="index.html", access="admin", order=20),
    ))

    provider_registry = ServiceProviderRegistry()
    provider_registry.load_instances_from_config(
        {"audit-report": {"default": {"endpoint": "https://audit.example"}}}
    )
    provider_instances = provider_registry.get_all_instance_configs()
    api_context = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path / "workspace")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        service_provider_registry=provider_registry,
        model_binding_resolver=_SyntheticBindingResolver(
            provider_instances,
            {("audit-report", "default")},
        ),
        provider_instances=provider_instances,
    )

    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_ui_extension_routes(router)
    app.include_router(router)
    app.include_router(provider_http_router, prefix="/api")
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice", roles=["user"]),
        permissions=RoleService.normalize_permissions({"providers": {"allow_all": True}}),
        is_admin=False,
    )
    app.state.test_api_context = api_context
    app.state.test_authz = authz
    app.dependency_overrides[get_authorization_context] = lambda: app.state.test_authz
    app.dependency_overrides[get_api_context] = lambda: app.state.test_api_context
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
    client.app.state.test_authz = AuthorizationContext(
        user=UserInfo(user_id="admin", roles=["admin"]),
        permissions=RoleService.normalize_permissions({"providers": {"allow_all": True}}),
        is_admin=True,
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


def test_disabled_provider_instances_are_omitted_from_navigation(client: TestClient) -> None:
    client.app.state.test_api_context.model_binding_resolver = _SyntheticBindingResolver(
        {},
        set(),
    )

    response = client.get("/api/ui/extensions")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_reenabled_inactive_database_instance_can_restore_navigation(client: TestClient) -> None:
    # The registry has no production instance (it models an inactive DB row),
    # but the committed resolver view can re-enable and supply its fresh config.
    client.app.state.test_api_context.model_binding_resolver = _SyntheticBindingResolver(
        {"audit-report": {"production": {"endpoint": "https://db.example"}}},
        {("audit-report", "production")},
    )

    response = client.get("/api/ui/extensions")

    assert response.status_code == 200, response.text
    assert [item["nav_id"] for item in response.json()["items"]] == ["file-audit"]


def test_disabled_provider_http_request_returns_safe_forbidden_envelope(
    client: TestClient,
    monkeypatch,
) -> None:
    import app.atlasclaw.api.provider_http_routes as provider_http_module

    client.app.state.test_api_context.model_binding_resolver = _SyntheticBindingResolver({}, set())
    monkeypatch.setattr(provider_http_module, "get_api_context", lambda: client.app.state.test_api_context)

    no_raise_client = TestClient(client.app, raise_server_exceptions=False)
    response = no_raise_client.get("/api/providers/audit-report/default/not-configured")

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "provider_disabled",
        "message": "Provider is disabled",
    }


def test_provider_http_request_uses_fresh_reenabled_instance_config(
    client: TestClient,
    monkeypatch,
) -> None:
    import app.atlasclaw.api.provider_http_routes as provider_http_module

    api_context = client.app.state.test_api_context
    api_context.model_binding_resolver = _SyntheticBindingResolver(
        {
            "audit-report": {
                "production": {
                    "endpoint": "https://fresh-db.example",
                    "api_key": "fresh-db-secret",
                }
            }
        },
        {("audit-report", "production")},
    )
    route = SimpleNamespace(
        path="records",
        access="user",
        max_body_bytes=1024,
        timeout_seconds=5,
        success_status=200,
        response="json",
    )
    monkeypatch.setattr(
        api_context.service_provider_registry,
        "match_provider_http_route",
        lambda *_args: (route, {}),
    )
    monkeypatch.setattr(
        provider_http_module,
        "get_api_context",
        lambda: api_context,
    )

    async def stale_catalog():
        return {
            "audit-report": {
                "production": {
                    "endpoint": "https://stale-json.example",
                    "api_key": "stale-json-secret",
                }
            }
        }

    captured_configs = []

    async def invoke(_route, _request, context):
        captured_configs.append(dict(context.provider_config))
        return {"endpoint": context.provider_config["endpoint"]}

    monkeypatch.setattr(provider_http_module, "get_provider_catalog_instances", stale_catalog)
    monkeypatch.setattr(provider_http_module, "invoke_provider_http_handler", invoke)

    response = TestClient(client.app, raise_server_exceptions=False).get(
        "/api/providers/audit-report/production/records"
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"endpoint": "https://fresh-db.example"}
    assert captured_configs == [{
        "endpoint": "https://fresh-db.example",
        "api_key": "fresh-db-secret",
        "instance_name": "production",
    }]
    assert "fresh-db-secret" not in response.text
    assert "stale-json-secret" not in response.text


def test_extension_only_default_keeps_empty_instance_fallback(
    client: TestClient,
    monkeypatch,
) -> None:
    import app.atlasclaw.api.provider_http_routes as provider_http_module

    api_context = client.app.state.test_api_context
    api_context.model_binding_resolver = _SyntheticBindingResolver(
        {},
        {("audit-report", "default")},
    )
    route = SimpleNamespace(
        path="records",
        access="user",
        max_body_bytes=1024,
        timeout_seconds=5,
        success_status=200,
        response="json",
    )
    registry = api_context.service_provider_registry
    monkeypatch.setattr(
        registry,
        "get_provider_schema_definition",
        lambda _provider_type: SimpleNamespace(extension_only=True),
    )
    monkeypatch.setattr(registry, "match_provider_http_route", lambda *_args: (route, {}))
    monkeypatch.setattr(provider_http_module, "get_api_context", lambda: api_context)

    async def empty_catalog():
        return {}

    async def invoke(_route, _request, context):
        return {"instance_name": context.provider_instance, "config": context.provider_config}

    monkeypatch.setattr(provider_http_module, "get_provider_catalog_instances", empty_catalog)
    monkeypatch.setattr(provider_http_module, "invoke_provider_http_handler", invoke)

    response = TestClient(client.app, raise_server_exceptions=False).get(
        "/api/providers/audit-report/default/records"
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "instance_name": "default",
        "config": {"instance_name": "default"},
    }
