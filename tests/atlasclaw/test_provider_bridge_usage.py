# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Usage accounting for selected Provider bridge models."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select

from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.model_bindings import ProviderAdmission
from app.atlasclaw.core.provider_http_runtime import ProviderVisualRuntime
from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import ModelUsageModel, RoleModel
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.skills.registry import SkillRegistry


@pytest_asyncio.fixture
async def usage_db(tmp_path: Path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "provider-bridge-usage.db"))
    )
    await manager.create_tables()
    yield manager
    await manager.close()


def _provider_skill_registry(tmp_path: Path) -> SkillRegistry:
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


def _authz() -> AuthorizationContext:
    return AuthorizationContext(
        user=UserInfo(user_id="alice", auth_type="test"),
        permissions=RoleService.normalize_permissions(
            {
                "providers": {
                    "provider_permissions": [
                        {
                            "provider_type": "crm",
                            "instance_name": "production",
                            "allowed": True,
                        }
                    ]
                }
            }
        ),
    )


def _runtime(
    model,
    *,
    token: TokenEntry | None,
    with_resolver: bool,
    tmp_path: Path,
    authz: AuthorizationContext | None = None,
):
    class Resolver:
        async def resolve(self, _target, *, authz, required_capabilities, admission):
            assert authz.user.user_id == "alice"
            assert admission is not None
            return SimpleNamespace(token=token)

    class AgentPool:
        async def get_or_create(self, _agent_id, _token, _factory):
            return SimpleNamespace(
                agent=SimpleNamespace(model=model, model_settings={}),
                concurrency_sem=None,
            )

    runner = SimpleNamespace(
        agent=SimpleNamespace(model=model, model_settings={}),
        agent_pool=AgentPool(),
        agent_factory=lambda _token: None,
        agent_id="main",
    )
    return ProviderVisualRuntime(
        runner,
        binding_resolver=Resolver() if with_resolver else None,
        authz=(authz or _authz()) if with_resolver else None,
        provider_type="crm",
        provider_instance="production",
        user_id="alice",
        skill_registry=_provider_skill_registry(tmp_path) if with_resolver else None,
        admission=(
            ProviderAdmission("crm", "production", "alice", "parent-run-42")
            if with_resolver
            else None
        ),
    )


@pytest.mark.asyncio
async def test_bound_bridge_request_records_selected_model_once_without_private_content(
    usage_db: DatabaseManager,
    tmp_path: Path,
) -> None:
    async def respond(_messages, _info):
        return ModelResponse(
            parts=[TextPart("bridge result")],
            usage=RequestUsage(input_tokens=17, output_tokens=6),
            model_name="selected-bridge-model",
        )

    selected_model = FunctionModel(respond, model_name="selected-bridge-model")
    token = TokenEntry(
        token_id="selected-model-token",
        provider="anthropic",
        model="selected-bridge-model",
        base_url="https://model.example/v1",
        api_key="selected-bridge-secret-key-1234",
        source_model_config_id="selected-model-config-id",
    )
    authz = _authz()
    async with usage_db.get_session() as session:
        session.add(
            RoleModel(
                name="Bridge Provider User",
                identifier="bridge-provider-user",
                description="",
                permissions=authz.permissions,
                is_builtin=False,
                is_active=True,
            )
        )
    user = UserInfo(user_id="alice", roles=["bridge-provider-user"], auth_type="test")
    authz = AuthorizationContext(user=user, permissions=authz.permissions)
    bridge = _runtime(
        selected_model,
        token=token,
        with_resolver=True,
        tmp_path=tmp_path,
        authz=authz,
    )

    result = await bridge.run_text(
        prompt="private-provider-prompt",
        system_prompt="private-provider-system-prompt",
        skill_id="crm:audit",
    )

    assert result == "bridge result"
    async with usage_db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == "alice"
    assert row.run_id == "parent-run-42"
    assert "parent-run-42" in row.session_key
    assert row.model == "selected-bridge-model"
    assert row.model_config_id == "selected-model-config-id"
    assert row.provider == "anthropic"
    assert row.token_id == "selected-model-token"
    assert row.key_label == "•••• 1234"
    assert row.action == "provider_bridge_text"
    assert row.input_tokens == 17
    assert row.output_tokens == 6
    assert row.total_tokens == 23
    row_repr = repr(row.__dict__)
    assert "private-provider-prompt" not in row_repr
    assert "private-provider-system-prompt" not in row_repr
    assert token.api_key not in row_repr


@pytest.mark.asyncio
async def test_no_resolver_bridge_keeps_legacy_unmetered_agent_behavior(
    usage_db: DatabaseManager,
    tmp_path: Path,
) -> None:
    async def respond(_messages, _info):
        return ModelResponse(
            parts=[TextPart("legacy bridge result")],
            usage=RequestUsage(input_tokens=1, output_tokens=1),
            model_name="legacy-model",
        )

    bridge = _runtime(
        FunctionModel(respond, model_name="legacy-model"),
        token=None,
        with_resolver=False,
        tmp_path=tmp_path,
    )

    result = await bridge.run_text(
        prompt="legacy prompt",
        system_prompt="legacy system",
    )

    assert result == "legacy bridge result"
    async with usage_db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_bound_visual_bridge_request_uses_selected_token_usage_hook(
    usage_db: DatabaseManager,
    tmp_path: Path,
) -> None:
    async def respond(_messages, _info):
        return ModelResponse(
            parts=[TextPart("visual bridge result")],
            usage=RequestUsage(input_tokens=29, output_tokens=8),
            model_name="selected-vision-model",
        )

    token = TokenEntry(
        token_id="selected-vision-token",
        provider="openai",
        model="selected-vision-model",
        base_url="https://vision.example/v1",
        api_key="selected-vision-secret-7890",
        source_model_config_id="selected-vision-config-id",
    )
    permissions = _authz().permissions
    async with usage_db.get_session() as session:
        session.add(
            RoleModel(
                name="Bridge Provider User",
                identifier="bridge-provider-user",
                description="",
                permissions=permissions,
                is_builtin=False,
                is_active=True,
            )
        )
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice", roles=["bridge-provider-user"], auth_type="test"),
        permissions=permissions,
    )
    bridge = _runtime(
        FunctionModel(respond, model_name="selected-vision-model"),
        token=token,
        with_resolver=True,
        tmp_path=tmp_path,
        authz=authz,
    )

    result = await bridge.run_visual(
        prompt="private-visual-prompt",
        system_prompt="private-visual-system-prompt",
        timeout_seconds=5,
        skill_id="crm:audit",
        data=b"private-image-bytes",
        media_type="image/png",
    )

    assert result == "visual bridge result"
    async with usage_db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == "alice"
    assert row.run_id == "parent-run-42"
    assert row.model == "selected-vision-model"
    assert row.model_config_id == "selected-vision-config-id"
    assert row.action == "provider_bridge_visual"
    assert row.input_tokens == 29
    assert row.output_tokens == 8
    row_repr = repr(row.__dict__)
    assert "private-visual-prompt" not in row_repr
    assert "private-visual-system-prompt" not in row_repr
    assert "private-image-bytes" not in row_repr
    assert token.api_key not in row_repr
