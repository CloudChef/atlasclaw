# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.

"""Authorization and persistence regressions for Provider Management APIs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.api.deps_context import APIContext, get_api_context, set_api_context
from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.config_schema import ModelConfig, TokenConfig
from app.atlasclaw.core.model_bindings import BindingTarget, ModelBindingResolver
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.db import get_db_session_dependency
from app.atlasclaw.db.database import DatabaseManager, DatabaseConfig, init_database
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.schemas import ModelConfigCreate
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


_test_db_manager: DatabaseManager | None = None
_auth_permissions: dict[str, bool] = {}


async def _test_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    assert _test_db_manager is not None
    async with _test_db_manager.get_session() as session:
        yield session


async def _test_authorization_context() -> AuthorizationContext:
    return AuthorizationContext(
        user=UserInfo(user_id="test-admin", auth_type="test"),
        permissions=RoleService.normalize_permissions(
            {"provider_management": dict(_auth_permissions)}
        ),
    )


def _build_test_app(tmp_path: Path) -> TestClient:
    skill_registry = SkillRegistry()
    provider_skill_root = tmp_path / "providers" / "crm" / "skills"
    (provider_skill_root / "audit").mkdir(parents=True)
    (provider_skill_root / "audit" / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit CRM records\nrequires_vision: true\n---\n# Audit\n",
        encoding="utf-8",
    )
    skill_registry.load_from_directory(
        str(provider_skill_root), location="provider", provider="crm"
    )
    standalone_skill_root = tmp_path / "standalone-skills"
    (standalone_skill_root / "independent").mkdir(parents=True)
    (standalone_skill_root / "independent" / "SKILL.md").write_text(
        "---\nname: independent\ndescription: Independent workflow\n---\n# Independent\n",
        encoding="utf-8",
    )
    skill_registry.load_from_directory(str(standalone_skill_root), location="workspace")
    provider_registry = ServiceProviderRegistry()
    provider_registry.load_instances_from_config(
        {"crm": {"production": {"display_name": "Production CRM"}}}
    )
    config = SimpleNamespace(
        model=ModelConfig(
            primary="primary-token",
            tokens=[
                TokenConfig(
                    id="primary-token",
                    provider="openai",
                    model="gpt-file",
                    base_url="https://file.example/v1",
                    api_key="never-return-this-file-secret",
                    capabilities={"vision": True},
                )
            ],
        )
    )
    set_api_context(
        APIContext(
            session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
            session_queue=SessionQueue(),
            skill_registry=skill_registry,
            service_provider_registry=provider_registry,
            provider_instances=provider_registry.get_all_instance_configs(),
        )
    )
    app = FastAPI()
    app.state.config = config
    app.include_router(api_router)
    app.dependency_overrides[get_db_session_dependency] = _test_get_db_session
    from app.atlasclaw.auth.guards import get_authorization_context

    app.dependency_overrides[get_authorization_context] = _test_authorization_context
    return TestClient(app)


def _init_test_db(tmp_path: Path) -> DatabaseManager:
    async def initialize() -> DatabaseManager:
        global _test_db_manager
        _test_db_manager = await init_database(
            DatabaseConfig(
                db_type="sqlite",
                sqlite_path=str(tmp_path / "provider-management.db"),
            )
        )
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
            await ModelConfigService.create(
                session,
                ModelConfigCreate(
                    name="managed-vision",
                    provider="anthropic",
                    model_id="claude-vision",
                    base_url="https://db.example/v1",
                    api_key="never-return-this-db-secret",
                    capabilities={"vision": True},
                ),
            )
        return _test_db_manager

    return asyncio.run(initialize())


@pytest.fixture
def provider_management_client(tmp_path: Path):
    global _auth_permissions
    manager = _init_test_db(tmp_path)
    _auth_permissions = {
        "view": True,
        "enable_disable": True,
        "configure_models": True,
        "manage_permissions": True,
    }
    client = _build_test_app(tmp_path)
    yield client
    asyncio.run(manager.close())


def _inherit_policy() -> dict[str, object]:
    return {"mode": "inherit", "allowed_model_refs": [], "default_model_ref": None}


def _explicit_file_policy() -> dict[str, object]:
    return {
        "mode": "explicit",
        "allowed_model_refs": ["file-token:primary-token"],
        "default_model_ref": "file-token:primary-token",
    }


def _provider_payload(*, revision: int = 0, enabled: bool = True, policy=None):
    return {
        "revision": revision,
        "enabled": enabled,
        "model_policy": policy if policy is not None else _explicit_file_policy(),
        "skills": [],
    }


def test_model_options_returns_file_and_db_models_without_credentials(
    provider_management_client: TestClient,
) -> None:
    response = provider_management_client.get(
        "/api/provider-management/model-options",
        params={
            "target_kind": "provider",
            "provider_type": "crm",
            "instance_name": "production",
            "search": "",
            "page": 1,
            "page_size": 20,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    refs = {model["ref"] for model in payload["models"]}
    assert "file-token:primary-token" in refs
    assert any(ref.startswith("db-model:") for ref in refs)
    assert all("api_key" not in model and "secret" not in str(model).lower() for model in payload["models"])
    assert "never-return-this-file-secret" not in response.text
    assert "never-return-this-db-secret" not in response.text


def test_skill_model_options_filter_capabilities_before_pagination(
    provider_management_client: TestClient,
) -> None:
    assert _test_db_manager is not None

    async def add_text_only_catalog_entries() -> None:
        async with _test_db_manager.get_session() as session:
            for index in range(24):
                await ModelConfigService.create(
                    session,
                    ModelConfigCreate(
                        name=f"text-only-{index:02d}",
                        provider="aaa-text",
                        model_id=f"text-model-{index:02d}",
                        base_url="https://text.example/v1",
                        api_key=f"text-secret-{index:02d}",
                        capabilities={"vision": False},
                    ),
                )

    asyncio.run(add_text_only_catalog_entries())
    response = provider_management_client.get(
        "/api/provider-management/model-options",
        params={
            "target_kind": "provider_skill",
            "provider_type": "crm",
            "instance_name": "production",
            "skill_id": "crm:audit",
            "page": 1,
            "page_size": 20,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["required_capabilities"] == {"vision": True}
    assert payload["total"] == 2
    assert len(payload["models"]) == 2
    assert all(model["capabilities"].get("vision") is True for model in payload["models"])
    assert all("api_key" not in model for model in payload["models"])
    assert "text-secret" not in response.text


def test_provider_owned_standard_skill_keeps_identity_for_management_and_binding(
    provider_management_client: TestClient,
    tmp_path: Path,
) -> None:
    ctx = get_api_context()
    skill_root = tmp_path / "providers" / "audit-report" / "skills"
    skill_dir = skill_root / "audit-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: audit-report\n"
        "description: Audit reports\n"
        "execution: standard\n"
        "requires_vision: true\n"
        "---\n"
        "# Audit report\n",
        encoding="utf-8",
    )
    assert ctx.skill_registry.load_from_directory(
        str(skill_root), location="provider", provider="audit-report"
    ) == 1
    ctx.service_provider_registry.load_instances_from_config(
        {"audit-report": {"default": {"display_name": "Audit Reports"}}}
    )
    ctx.provider_instances = ctx.service_provider_registry.get_all_instance_configs()

    entry = ctx.skill_registry.get_md_skill("audit-report:audit-report")
    assert entry is not None
    assert entry.provider == "audit-report"
    assert entry.qualified_name == "audit-report:audit-report"

    providers_response = provider_management_client.get(
        "/api/provider-management/providers"
    )
    assert providers_response.status_code == 200, providers_response.text
    provider = next(
        item
        for item in providers_response.json()["providers"]
        if item["provider_type"] == "audit-report"
    )
    assert [skill["skill_id"] for skill in provider["skills"]] == [
        "audit-report:audit-report"
    ]
    assert provider["skills"][0]["requires_vision"] is True

    standalone_response = provider_management_client.get(
        "/api/provider-management/standalone-skills"
    )
    assert standalone_response.status_code == 200, standalone_response.text
    assert "audit-report:audit-report" not in {
        skill["skill_id"] for skill in standalone_response.json()["skills"]
    }

    options_response = provider_management_client.get(
        "/api/provider-management/model-options",
        params={
            "target_kind": "provider_skill",
            "provider_type": "audit-report",
            "instance_name": "default",
            "skill_id": "audit-report:audit-report",
        },
    )
    assert options_response.status_code == 200, options_response.text
    assert options_response.json()["required_capabilities"] == {"vision": True}

    update_response = provider_management_client.put(
        "/api/provider-management/providers/audit-report/default",
        json=_provider_payload(policy=_explicit_file_policy()),
    )
    assert update_response.status_code == 200, update_response.text

    resolver = ModelBindingResolver(
        provider_management_client.app.state.config,
        ctx.skill_registry,
        ctx.service_provider_registry,
        None,
    )
    selection = asyncio.run(
        resolver.resolve(
            BindingTarget(
                kind="provider_skill",
                provider_type="audit-report",
                instance_name="default",
                skill_id="audit-report:audit-report",
            )
        )
    )
    assert selection.source == "inherited_provider"
    assert selection.token.token_id == "file-token:primary-token"


def test_denied_user_cannot_view_or_write_provider_management(
    provider_management_client: TestClient,
) -> None:
    global _auth_permissions
    _auth_permissions = {}

    read_response = provider_management_client.get("/api/provider-management/providers")
    write_response = provider_management_client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(),
    )

    assert read_response.status_code == 403
    assert write_response.status_code == 403


def test_unprivileged_no_op_put_is_still_denied(provider_management_client: TestClient) -> None:
    global _auth_permissions
    _auth_permissions = {}
    response = provider_management_client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(policy=_inherit_policy()),
    )
    assert response.status_code == 403


def test_activation_permission_cannot_change_model_policy(
    provider_management_client: TestClient,
) -> None:
    global _auth_permissions
    _auth_permissions = {"enable_disable": True}

    response = provider_management_client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(policy=_explicit_file_policy()),
    )

    assert response.status_code == 403


def test_model_permission_cannot_change_provider_activation(
    provider_management_client: TestClient,
) -> None:
    global _auth_permissions
    _auth_permissions = {"configure_models": True}

    response = provider_management_client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(enabled=False),
    )

    assert response.status_code == 403


def test_activation_patch_changes_only_enabled_state_and_preserves_revisioned_policy(
    provider_management_client: TestClient,
) -> None:
    client = provider_management_client
    initial = client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(),
    )
    assert initial.status_code == 200, initial.text
    saved = initial.json()

    activated = client.patch(
        "/api/provider-management/providers/crm/production/activation",
        json={"revision": saved["revision"], "enabled": False},
    )

    assert activated.status_code == 200, activated.text
    result = activated.json()
    assert result["enabled"] is False
    assert result["model_policy"] == saved["model_policy"]


def test_stale_revision_returns_conflict(provider_management_client: TestClient) -> None:
    client = provider_management_client
    first = client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(),
    )
    assert first.status_code == 200, first.text

    stale = client.put(
        "/api/provider-management/providers/crm/production",
        json=_provider_payload(revision=0, policy=_inherit_policy()),
    )

    assert stale.status_code == 409


def test_provider_skill_model_must_be_in_provider_allowlist(
    provider_management_client: TestClient,
) -> None:
    client = provider_management_client
    response = client.get(
        "/api/provider-management/model-options",
        params={"target_kind": "provider", "provider_type": "crm", "instance_name": "production"},
    )
    managed_ref = next(
        model["ref"] for model in response.json()["models"] if model["ref"].startswith("db-model:")
    )

    invalid = _provider_payload()
    invalid["skills"] = [
        {
            "skill_id": "crm:audit",
            "model_policy": {
                "mode": "explicit",
                "allowed_model_refs": [managed_ref],
                "default_model_ref": managed_ref,
            },
        }
    ]
    response = client.put(
        "/api/provider-management/providers/crm/production",
        json=invalid,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "explicit", "allowed_model_refs": [], "default_model_ref": None},
        {
            "mode": "explicit",
            "allowed_model_refs": ["file-token:primary-token", "file-token:primary-token"],
            "default_model_ref": "file-token:primary-token",
        },
        {
            "mode": "explicit",
            "allowed_model_refs": ["file-token:primary-token"],
            "default_model_ref": "db-model:not-allowed",
        },
    ],
    ids=["empty-allowlist", "duplicate-ref", "default-outside-allowlist"],
)
@pytest.mark.parametrize("target", ["provider", "standalone_skill"])
def test_invalid_policy_shapes_return_422(
    provider_management_client: TestClient,
    policy: dict[str, object],
    target: str,
) -> None:
    # Exercise the actual HTTP response for uncaught route errors instead of
    # allowing Starlette's test client to re-raise the server exception.
    client = TestClient(provider_management_client.app, raise_server_exceptions=False)
    if target == "provider":
        response = client.put(
            "/api/provider-management/providers/crm/production",
            json=_provider_payload(policy=policy),
        )
    else:
        response = client.put(
            "/api/provider-management/standalone-skills/independent/model-policy",
            json={"revision": 0, "model_policy": policy},
        )

    assert response.status_code == 422, response.text


def test_provider_put_can_atomically_repair_child_when_parent_model_is_removed(
    provider_management_client: TestClient,
) -> None:
    client = provider_management_client
    options = client.get(
        "/api/provider-management/model-options",
        params={"target_kind": "provider", "provider_type": "crm", "instance_name": "production"},
    )
    assert options.status_code == 200, options.text
    managed_ref = next(
        model["ref"] for model in options.json()["models"] if model["ref"].startswith("db-model:")
    )
    file_ref = "file-token:primary-token"
    provider_path = "/api/provider-management/providers/crm/production"
    initial = _provider_payload(
        policy={
            "mode": "explicit",
            "allowed_model_refs": [file_ref, managed_ref],
            "default_model_ref": file_ref,
        }
    )
    initial["skills"] = [
        {
            "skill_id": "crm:audit",
            "model_policy": {
                "mode": "explicit",
                "allowed_model_refs": [managed_ref],
                "default_model_ref": managed_ref,
            },
        }
    ]
    saved = client.put(provider_path, json=initial)
    assert saved.status_code == 200, saved.text

    repaired = {
        "revision": saved.json()["revision"],
        "enabled": True,
        "model_policy": {
            "mode": "explicit",
            "allowed_model_refs": [file_ref],
            "default_model_ref": file_ref,
        },
        "skills": [
            {
                "skill_id": "crm:audit",
                "model_policy": _inherit_policy(),
            }
        ],
    }
    response = client.put(provider_path, json=repaired)

    assert response.status_code == 200, response.text
    skill = response.json()["skills"][0]
    assert skill["model_policy"]["mode"] == "inherit"
    assert skill["effective_model_policy"]["allowed_model_refs"] == [file_ref]
