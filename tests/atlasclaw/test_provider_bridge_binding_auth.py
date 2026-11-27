# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Per-call authorization refresh for provider-to-model bridge calls."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.model_bindings import ProviderAdmission
from app.atlasclaw.core.provider_http_runtime import (
    ProviderHttpRuntimeError,
    ProviderVisualRuntime,
)
from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import RoleModel
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.skills.registry import SkillRegistry


@pytest_asyncio.fixture
async def db_manager(tmp_path: Path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "bridge-auth.db"))
    )
    await manager.create_tables()
    yield manager
    await manager.close()


def _skill_registry(tmp_path: Path) -> SkillRegistry:
    skill_root = tmp_path / "providers" / "crm" / "skills"
    skill_dir = skill_root / "audit"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit CRM records\n---\n# Audit\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.load_from_directory(str(skill_root), location="provider", provider="crm")
    return registry


def _provider_permissions(*, allowed: bool) -> dict[str, Any]:
    return RoleService.normalize_permissions(
        {
            "providers": {
                "allow_all": False,
                "provider_permissions": [
                    {
                        "provider_type": "crm",
                        "instance_name": "production",
                        "allowed": allowed,
                    }
                ],
            }
        }
    )


class _RecordingResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any, Any]] = []
        self.token = TokenEntry(
            token_id="test-token",
            provider="test-provider",
            model="test-model",
            base_url="https://model.example/v1",
            api_key="bridge-test-secret",
        )

    async def resolve(self, target, *, authz, required_capabilities, admission):
        self.calls.append((target, authz, admission))
        return SimpleNamespace(token=self.token)


class _FakeAgentPool:
    async def get_or_create(self, _agent_id, _token, _factory):
        return SimpleNamespace(
            agent=SimpleNamespace(model="resolved-model", model_settings={}),
            concurrency_sem=None,
        )


def _runtime(
    *,
    authz: AuthorizationContext,
    resolver: _RecordingResolver,
    skill_registry: SkillRegistry,
    admission: ProviderAdmission,
) -> ProviderVisualRuntime:
    runner = SimpleNamespace(
        agent=SimpleNamespace(model="default-model", model_settings={}),
        agent_pool=_FakeAgentPool(),
        agent_factory=lambda _token: None,
        agent_id="main",
    )
    return ProviderVisualRuntime(
        runner,
        binding_resolver=resolver,
        authz=authz,
        provider_type="crm",
        provider_instance="production",
        user_id="alice",
        skill_registry=skill_registry,
        admission=admission,
    )


@pytest.mark.asyncio
async def test_provider_skill_grant_revocation_blocks_next_bridge_model_request(
    db_manager: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.atlasclaw.core.provider_http_runtime as runtime_module

    async with db_manager.get_session() as session:
        session.add(
            RoleModel(
                name="Bridge Provider User",
                identifier="bridge-provider-user",
                description="",
                permissions=_provider_permissions(allowed=True),
                is_builtin=False,
                is_active=True,
            )
        )

    user = UserInfo(user_id="alice", roles=["bridge-provider-user"], auth_type="test")
    captured_authz = AuthorizationContext(
        user=user,
        permissions=_provider_permissions(allowed=True),
    )
    resolver = _RecordingResolver()
    admission = ProviderAdmission(
        provider_type="crm",
        instance_name="production",
        user_id="alice",
        run_id="admitted-run",
    )
    bridge = _runtime(
        authz=captured_authz,
        resolver=resolver,
        skill_registry=_skill_registry(tmp_path),
        admission=admission,
    )
    model_requests: list[str] = []

    class FakeTextAgent:
        def __init__(self, _model, **_kwargs):
            pass

        async def run(self, _prompt, *, deps=None):
            model_requests.append("request")
            return SimpleNamespace(output="ok", new_messages=lambda: [])

    monkeypatch.setattr(runtime_module, "Agent", FakeTextAgent)

    first_result = await bridge.run_text(
        prompt="Check the report",
        system_prompt="Return concise results",
        skill_id="crm:audit",
    )
    assert first_result == "ok"
    assert len(model_requests) == 1
    assert len(resolver.calls) == 1
    assert resolver.calls[0][2] is admission
    assert resolver.calls[0][1].permissions["providers"]["provider_permissions"][0]["allowed"] is True

    async with db_manager.get_session() as session:
        role = await RoleService.get_by_identifier(session, "bridge-provider-user")
        role.permissions = _provider_permissions(allowed=False)

    with pytest.raises(ProviderHttpRuntimeError) as exc_info:
        await bridge.run_text(
            prompt="Check the next report",
            system_prompt="Return concise results",
            skill_id="crm:audit",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "provider_skill_permission_denied"
    assert len(resolver.calls) == 1
    assert len(model_requests) == 1
    assert "bridge-test-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_bridge_keeps_captured_authz_without_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.atlasclaw.db as db_module

    class UninitializedManager:
        is_initialized = False

    monkeypatch.setattr(db_module, "get_db_manager", lambda: UninitializedManager())
    captured_authz = AuthorizationContext(
        user=UserInfo(user_id="alice", auth_type="test"),
        permissions=_provider_permissions(allowed=True),
    )
    resolver = _RecordingResolver()
    admission = ProviderAdmission("crm", "production", "alice", "offline-run")
    bridge = _runtime(
        authz=captured_authz,
        resolver=resolver,
        skill_registry=_skill_registry(tmp_path),
        admission=admission,
    )

    model, _, _, _, _ = await bridge._acquire_model(skill_id="crm:audit", required_capabilities={})

    assert model == "resolved-model"
    assert resolver.calls[0][1] is captured_authz
    assert resolver.calls[0][2] is admission


@pytest.mark.asyncio
async def test_provider_bridge_fails_closed_without_leaking_database_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.atlasclaw.db as db_module

    class BrokenManager:
        is_initialized = True

        @asynccontextmanager
        async def get_session(self):
            raise RuntimeError("database-password=do-not-leak")
            yield None

    monkeypatch.setattr(db_module, "get_db_manager", lambda: BrokenManager())
    captured_authz = AuthorizationContext(
        user=UserInfo(user_id="alice", auth_type="test"),
        permissions=_provider_permissions(allowed=True),
    )
    resolver = _RecordingResolver()
    admission = ProviderAdmission("crm", "production", "alice", "db-failure-run")
    bridge = _runtime(
        authz=captured_authz,
        resolver=resolver,
        skill_registry=_skill_registry(tmp_path),
        admission=admission,
    )

    with pytest.raises(ProviderHttpRuntimeError) as exc_info:
        await bridge._acquire_model(skill_id="crm:audit", required_capabilities={})

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "provider_model_authz_unavailable"
    assert "database-password" not in str(exc_info.value)
    assert resolver.calls == []
