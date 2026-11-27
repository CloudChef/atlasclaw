# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.

"""Regression tests for global Provider and Skill model bindings."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.config_schema import ModelConfig, TokenConfig
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import RoleModel
from app.atlasclaw.db.orm.role import BUILTIN_ROLE_DEFINITIONS, RoleService
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.schemas import ServiceProviderConfigCreate
from app.atlasclaw.skills.registry import SkillRegistry

from app.atlasclaw.core.model_bindings import (
    BindingTarget,
    ModelBindingError,
    ModelBindingResolver,
)


@pytest_asyncio.fixture
async def db_manager(tmp_path: Path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "bindings.db"))
    )
    await manager.create_tables()
    yield manager
    await manager.close()


def _build_config() -> SimpleNamespace:
    return SimpleNamespace(
        model=ModelConfig(
            primary="primary-token",
            tokens=[
                TokenConfig(
                    id="primary-token",
                    provider="openai",
                    model="gpt-file",
                    base_url="https://file.example/v1",
                    api_key="file-secret-key",
                    capabilities={"vision": True},
                )
            ],
        )
    )


def _build_registries(tmp_path: Path):
    provider_registry = ServiceProviderRegistry()
    provider_registry.load_instances_from_config(
        {
            "crm": {
                "production": {"display_name": "Production CRM"},
                "staging": {"display_name": "Staging CRM"},
            }
        }
    )

    skill_registry = SkillRegistry()
    provider_skills = tmp_path / "providers" / "crm" / "skills"
    provider_skills.mkdir(parents=True)
    (provider_skills / "audit" / "SKILL.md").parent.mkdir(parents=True)
    (provider_skills / "audit" / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit CRM records\nrequires_vision: true\n---\n# Audit\n",
        encoding="utf-8",
    )
    skill_registry.load_from_directory(str(provider_skills), location="provider", provider="crm")

    standalone_skills = tmp_path / "skills"
    standalone_skills.mkdir()
    (standalone_skills / "independent" / "SKILL.md").parent.mkdir(parents=True)
    (standalone_skills / "independent" / "SKILL.md").write_text(
        "---\nname: independent\ndescription: Independent workflow\n---\n# Independent\n",
        encoding="utf-8",
    )
    skill_registry.load_from_directory(str(standalone_skills), location="workspace")
    return skill_registry, provider_registry


def _build_token_policy(config: SimpleNamespace) -> DynamicTokenPolicy:
    pool = TokenPool()
    for entry in config.model.tokens:
        pool.register_token(
            TokenEntry(
                token_id=entry.id,
                provider=entry.provider,
                model=entry.model,
                base_url=entry.base_url,
                api_key=entry.api_key,
                capabilities=entry.capabilities,
            )
        )
    return DynamicTokenPolicy(pool, primary_token_id="primary-token")


@pytest.mark.asyncio
async def test_explicit_file_reference_resolves_when_database_models_exist(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    """A refreshed DB pool must not hide an explicitly bound file credential."""
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService
    from app.atlasclaw.db.schemas import ModelConfigCreate
    from app.atlasclaw.bootstrap.startup_helpers import build_token_entries, build_token_entries_from_model_configs

    async with db_manager.get_session() as session:
        await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="managed",
                provider="anthropic",
                model_id="claude-db",
                base_url="https://db.example/v1",
                api_key="database-secret-key",
                capabilities={"vision": False},
            ),
        )
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="standalone_skill", skill_id="independent"),
                    "revision": 0,
                    "enabled": None,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": ["file-token:primary-token"],
                        "default_model_ref": "file-token:primary-token",
                    },
                }
            ],
            updated_by="admin",
        )

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    pool = TokenPool()

    async def load_tokens():
        async with db_manager.get_session() as session:
            managed = await build_token_entries_from_model_configs(session)
        file_tokens, _ = build_token_entries(config)
        return [*managed, *file_tokens]

    token_policy = DynamicTokenPolicy(pool, primary_token_id="managed", token_loader=load_tokens)
    resolver = ModelBindingResolver(config, skills, providers, token_policy)

    selection = await resolver.resolve(
        BindingTarget(kind="standalone_skill", skill_id="independent")
    )

    assert selection.token.token_id == "file-token:primary-token"
    assert selection.token.model == "gpt-file"
    assert selection.eligible_token_ids == {"file-token:primary-token"}
    assert selection.eligible_tokens[0].token_id == "file-token:primary-token"
    assert selection.source == "explicit_skill"
    assert "file-secret-key" not in repr(selection)


@pytest.mark.asyncio
async def test_no_policy_keeps_platform_primary_selection(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))

    selection = await resolver.resolve(
        BindingTarget(kind="standalone_skill", skill_id="independent")
    )

    assert selection.token.token_id == "primary-token"
    assert selection.source == "platform_default"


@pytest.mark.asyncio
async def test_provider_skill_explicit_binding_is_scoped_and_checks_vision(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    """Provider Skill overrides use only allowed references with required capabilities."""
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.schemas import ModelConfigCreate

    async with db_manager.get_session() as session:
        model = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="vision-managed",
                provider="openai",
                model_id="gpt-db-vision",
                base_url="https://db.example/v1",
                api_key="database-secret-key",
                capabilities={"vision": True},
            ),
        )
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
                    "revision": 0,
                    "enabled": True,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [f"db-model:{model.id}", "file-token:primary-token"],
                        "default_model_ref": "file-token:primary-token",
                    },
                },
                {
                    "target": BindingTarget(
                        kind="provider_skill",
                        provider_type="crm",
                        instance_name="production",
                        skill_id="crm:audit",
                    ),
                    "revision": 0,
                    "enabled": None,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [f"db-model:{model.id}"],
                        "default_model_ref": f"db-model:{model.id}",
                    },
                },
            ],
            updated_by="admin",
        )

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))

    selection = await resolver.resolve(
        BindingTarget(
            kind="provider_skill",
            provider_type="crm",
            instance_name="production",
            skill_id="crm:audit",
        ),
        required_capabilities={"vision": True},
    )

    assert selection.token.model == "gpt-db-vision"
    assert selection.token.source_model_config_id == model.id
    assert selection.eligible_token_ids == {f"db-model:{model.id}"}
    assert selection.source == "explicit_skill"


@pytest.mark.asyncio
async def test_explicit_policy_rejects_empty_set_bad_default_and_vision_mismatch(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    """Binding policies cannot encode an unusable explicit range."""
    from app.atlasclaw.db.orm.capability_model_policy import (
        CapabilityModelPolicyService,
        ModelPolicyValidationError,
    )

    config = _build_config()
    config.model.tokens[0].capabilities = {"vision": False}
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))
    file_ref = "file-token:primary-token"

    with pytest.raises(ModelPolicyValidationError, match="at least one"):
        await resolver.validate_policy(
            BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
            {"mode": "explicit", "allowed_model_refs": [], "default_model_ref": None},
        )

    with pytest.raises(ModelPolicyValidationError, match="default"):
        await resolver.validate_policy(
            BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
            {"mode": "explicit", "allowed_model_refs": [file_ref], "default_model_ref": "missing"},
        )

    with pytest.raises(ModelPolicyValidationError, match="vision"):
        await resolver.validate_policy(
            BindingTarget(
                kind="provider_skill",
                provider_type="crm",
                instance_name="production",
                skill_id="crm:audit",
            ),
            {"mode": "explicit", "allowed_model_refs": [file_ref], "default_model_ref": file_ref},
        )


@pytest.mark.asyncio
async def test_stale_policy_references_fail_closed(db_manager: DatabaseManager, tmp_path: Path) -> None:
    """A deleted credential does not silently fall back outside the saved range."""
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.schemas import ModelConfigCreate

    async with db_manager.get_session() as session:
        model = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="temporary",
                provider="anthropic",
                model_id="claude-test",
                base_url="https://db.example/v1",
                api_key="temporary-secret-key",
                capabilities={"vision": True},
            ),
        )
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
                    "revision": 0,
                    "enabled": True,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [f"db-model:{model.id}"],
                        "default_model_ref": f"db-model:{model.id}",
                    },
                }
            ],
            updated_by="admin",
        )
        await ModelConfigService.delete(session, model.id)

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))

    with pytest.raises(ModelBindingError, match="[Nn]o available model"):
        await resolver.resolve(
            BindingTarget(kind="provider", provider_type="crm", instance_name="production")
        )


@pytest.mark.asyncio
async def test_provider_disabled_state_is_enforced(db_manager: DatabaseManager, tmp_path: Path) -> None:
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService

    async with db_manager.get_session() as session:
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
                    "revision": 0,
                    "enabled": False,
                    "model_policy": {"mode": "inherit", "allowed_model_refs": [], "default_model_ref": None},
                }
            ],
            updated_by="admin",
        )

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))

    with pytest.raises(ModelBindingError, match="disabled"):
        await resolver.ensure_provider_enabled("crm", "production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_active", "policy_enabled", "expected_enabled"),
    [
        (False, True, True),
        (False, False, False),
        (False, None, False),
        (True, None, True),
    ],
)
async def test_effective_provider_instances_uses_committed_activation_overlay(
    db_manager: DatabaseManager,
    database_active: bool,
    policy_enabled: bool | None,
    expected_enabled: bool,
) -> None:
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService

    config = _build_config()
    config.service_providers = {
        "crm": {"production": {"api_key": "json-config-secret", "source": "json"}}
    }
    async with db_manager.get_session() as session:
        await ServiceProviderConfigService.create(
            session,
            ServiceProviderConfigCreate(
                provider_type="crm",
                instance_name="production",
                config={"api_key": "database-config-secret", "source": "database"},
                is_active=database_active,
            ),
        )
        if policy_enabled is not None:
            await CapabilityModelPolicyService.save_many(
                session,
                [{
                    "target": BindingTarget(
                        kind="provider", provider_type="crm", instance_name="production"
                    ),
                    "revision": 0,
                    "enabled": policy_enabled,
                    "model_policy": {
                        "mode": "inherit",
                        "allowed_model_refs": [],
                        "default_model_ref": None,
                    },
                }],
                updated_by="admin",
            )

    resolver = ModelBindingResolver(config, None, ServiceProviderRegistry(), None)
    result = await resolver.effective_provider_instances(
        {"crm": {"production": {"api_key": "stale-cache-secret", "source": "cache"}}}
    )

    assert ("production" in result.get("crm", {})) is expected_enabled
    if expected_enabled:
        assert result["crm"]["production"] == {
            "api_key": "database-config-secret",
            "source": "database",
        }


@pytest.mark.asyncio
async def test_json_only_provider_respects_committed_disabled_overlay(
    db_manager: DatabaseManager,
) -> None:
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService

    config = _build_config()
    config.service_providers = {
        "crm": {"json-only": {"api_key": "json-only-secret", "enabled": True}}
    }
    async with db_manager.get_session() as session:
        await CapabilityModelPolicyService.save_many(
            session,
            [{
                "target": BindingTarget(
                    kind="provider", provider_type="crm", instance_name="json-only"
                ),
                "revision": 0,
                "enabled": False,
                "model_policy": {
                    "mode": "inherit",
                    "allowed_model_refs": [],
                    "default_model_ref": None,
                },
            }],
            updated_by="admin",
        )

    resolver = ModelBindingResolver(config, None, ServiceProviderRegistry(), None)
    result = await resolver.effective_provider_instances(
        {"crm": {"json-only": {"api_key": "json-only-secret", "enabled": True}}}
    )

    assert "json-only" not in result.get("crm", {})


def test_provider_management_permissions_default_to_admin_only() -> None:
    definitions = {entry["identifier"]: entry["permissions"] for entry in BUILTIN_ROLE_DEFINITIONS}
    admin = RoleService.normalize_permissions(definitions["admin"])
    standard_user = RoleService.normalize_permissions(definitions["user"])

    assert admin["provider_management"]["view"] is True
    assert admin["provider_management"]["enable_disable"] is True
    assert admin["provider_management"]["configure_models"] is True
    assert admin["provider_management"]["manage_permissions"] is True
    assert not any(standard_user["provider_management"].values())


def test_permission_governance_maps_provider_management_module() -> None:
    from fastapi import HTTPException

    from app.atlasclaw.auth.guards import ensure_can_manage_permission_modules

    unprivileged = AuthorizationContext(
        user=UserInfo(user_id="reader", auth_type="test"),
        permissions=RoleService.normalize_permissions({}),
    )
    before = RoleService.normalize_permissions({})
    after = RoleService.normalize_permissions(
        {"provider_management": {"view": True}}
    )
    with pytest.raises(HTTPException) as exc_info:
        ensure_can_manage_permission_modules(
            unprivileged, after, existing_permissions=before
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_builtin_user_role_keeps_delegated_provider_management_flags(
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.get_session() as session:
        await RoleService.ensure_builtin_roles(session)
        user_role = await RoleService.get_by_identifier(session, "user")
        user_role.permissions = RoleService.normalize_permissions(
            {
                **user_role.permissions,
                "provider_management": {"view": True, "configure_models": True},
            }
        )
        await session.flush()
        await RoleService.ensure_builtin_roles(session)
        refreshed_role = await RoleService.get_by_identifier(session, "user")

    assert refreshed_role.permissions["provider_management"]["view"] is True
    assert refreshed_role.permissions["provider_management"]["configure_models"] is True
    assert refreshed_role.permissions["provider_management"]["enable_disable"] is False


@pytest.mark.asyncio
async def test_builtin_role_upgrade_adds_admin_permissions_and_preserves_user_delegation(
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.get_session() as session:
        for identifier in ("admin", "user"):
            definition = next(
                item for item in BUILTIN_ROLE_DEFINITIONS if item["identifier"] == identifier
            )
            legacy_permissions = deepcopy(definition["permissions"])
            legacy_permissions.pop("provider_management", None)
            session.add(
                RoleModel(
                    name=definition["name"],
                    identifier=identifier,
                    description=definition["description"],
                    permissions=legacy_permissions,
                    is_builtin=True,
                    is_active=True,
                )
            )
        await session.flush()

        await RoleService.ensure_builtin_roles(session)
        admin = await RoleService.get_by_identifier(session, "admin")
        user = await RoleService.get_by_identifier(session, "user")
        assert all(admin.permissions["provider_management"].values())
        assert not any(user.permissions["provider_management"].values())

        user.permissions = RoleService.normalize_permissions(
            {
                **user.permissions,
                "provider_management": {"view": True, "configure_models": True},
            }
        )
        await session.flush()
        await RoleService.ensure_builtin_roles(session)
        await RoleService.ensure_builtin_roles(session)
        refreshed_user = await RoleService.get_by_identifier(session, "user")

    assert refreshed_user.permissions["provider_management"]["view"] is True
    assert refreshed_user.permissions["provider_management"]["configure_models"] is True
    assert refreshed_user.permissions["provider_management"]["enable_disable"] is False
    assert refreshed_user.permissions["provider_management"]["manage_permissions"] is False


@pytest.mark.asyncio
async def test_provider_skill_uses_provider_grant_without_standalone_skill_grant(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    from app.atlasclaw.auth.guards import has_provider_instance_access

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))
    authz = AuthorizationContext(
        user=UserInfo(user_id="provider-user", auth_type="test"),
        permissions=RoleService.normalize_permissions(
            {
                "providers": {
                    "allow_all": False,
                    "provider_permissions": [
                        {
                            "provider_type": "crm",
                            "instance_name": "production",
                            "allowed": True,
                        }
                    ],
                },
                "skills": {"allow_all": False, "skill_permissions": []},
            }
        ),
    )
    assert has_provider_instance_access(authz, "crm", "production") is True

    selection = await resolver.resolve(
        BindingTarget(
            kind="provider_skill",
            provider_type="crm",
            instance_name="production",
            skill_id="crm:audit",
        ),
        authz=authz,
    )

    assert selection.token.token_id == "primary-token"


@pytest.mark.asyncio
async def test_binding_authz_rejects_wrong_provider_instance_and_ungranted_standalone_skill(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))
    authz = AuthorizationContext(
        user=UserInfo(user_id="provider-user", auth_type="test"),
        permissions=RoleService.normalize_permissions(
            {
                "providers": {
                    "allow_all": False,
                    "provider_permissions": [
                        {
                            "provider_type": "crm",
                            "instance_name": "production",
                            "allowed": True,
                        }
                    ],
                },
                "skills": {"allow_all": False, "skill_permissions": []},
            }
        ),
    )

    with pytest.raises(HTTPException) as wrong_instance:
        await resolver.resolve(
            BindingTarget(
                kind="provider_skill",
                provider_type="crm",
                instance_name="staging",
                skill_id="crm:audit",
            ),
            authz=authz,
        )
    assert wrong_instance.value.status_code == 403

    with pytest.raises(HTTPException) as standalone_denied:
        await resolver.resolve(
            BindingTarget(kind="standalone_skill", skill_id="independent"),
            authz=authz,
        )
    assert standalone_denied.value.status_code == 403


def test_binding_target_constructor_keeps_shared_runtime_contract() -> None:
    target = BindingTarget(
        kind="provider_skill",
        provider_type="crm",
        instance_name="production",
        skill_id="crm:audit",
    )
    assert target.kind == "provider_skill"
    assert target.provider_type == "crm"
    assert target.instance_name == "production"
    assert target.skill_id == "crm:audit"


@pytest.mark.asyncio
async def test_same_runtime_token_id_keeps_file_and_database_credentials_distinct(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    from app.atlasclaw.bootstrap.startup_helpers import build_token_entries, load_runtime_token_entries
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.schemas import ModelConfigCreate

    config = _build_config()
    async with db_manager.get_session() as session:
        model = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="primary-token",
                provider="openai",
                model_id="gpt-db",
                base_url="https://db.example/v1",
                api_key="database-secret-key",
                capabilities={"vision": True},
            ),
        )
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="standalone_skill", skill_id="independent"),
                    "revision": 0,
                    "enabled": None,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [
                            "file-token:primary-token",
                            f"db-model:{model.id}",
                        ],
                        "default_model_ref": "file-token:primary-token",
                    },
                }
            ],
            updated_by="admin",
        )

    file_tokens, _ = build_token_entries(config)

    async def load_tokens():
        return await load_runtime_token_entries(file_tokens)

    token_policy = DynamicTokenPolicy(TokenPool(), primary_token_id="primary-token", token_loader=load_tokens)
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, token_policy)
    target = BindingTarget(kind="standalone_skill", skill_id="independent")

    file_selection = await resolver.resolve(target)

    assert file_selection.token.token_id == "file-token:primary-token"
    assert file_selection.token.api_key == "file-secret-key"
    assert file_selection.eligible_token_ids == {
        "file-token:primary-token",
        f"db-model:{model.id}",
    }
    assert {token.api_key for token in file_selection.eligible_tokens} == {
        "file-secret-key",
        "database-secret-key",
    }
    assert "file-secret-key" not in repr(file_selection)
    assert "database-secret-key" not in repr(file_selection)

    async with db_manager.get_session() as session:
        record = await CapabilityModelPolicyService.get_by_target(session, target)
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": target,
                    "revision": record.revision,
                    "enabled": None,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [
                            "file-token:primary-token",
                            f"db-model:{model.id}",
                        ],
                        "default_model_ref": f"db-model:{model.id}",
                    },
                }
            ],
            updated_by="admin",
        )

    database_selection = await resolver.resolve(target)
    assert database_selection.token.token_id == f"db-model:{model.id}"
    assert database_selection.token.api_key == "database-secret-key"


@pytest.mark.asyncio
async def test_no_database_resolution_uses_configured_file_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.atlasclaw.core import model_bindings

    config = _build_config()
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))
    monkeypatch.setattr(model_bindings, "get_db_manager", lambda: SimpleNamespace(is_initialized=False))

    selection = await resolver.resolve(
        BindingTarget(kind="standalone_skill", skill_id="independent")
    )

    assert selection.token.token_id == "primary-token"
    assert selection.token.api_key == "file-secret-key"
    assert selection.source == "platform_default"


@pytest.mark.asyncio
async def test_registered_executable_skill_vision_requirement_is_enforced(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.schemas import ModelConfigCreate
    from app.atlasclaw.db.orm.capability_model_policy import ModelPolicyValidationError
    from app.atlasclaw.skills.registry import SkillMetadata

    async with db_manager.get_session() as session:
        model = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="text-only",
                provider="anthropic",
                model_id="claude-text",
                base_url="https://db.example/v1",
                api_key="database-secret-key",
                capabilities={"vision": False},
            ),
        )
    config = _build_config()
    skills, providers = _build_registries(tmp_path)

    async def executable_handler():
        return {"ok": True}

    skills.register(
        SkillMetadata(
            name="crm_vision_audit",
            description="Vision-based CRM audit",
            source="provider",
            provider_type="crm",
            requires_vision=True,
        ),
        executable_handler,
    )
    resolver = ModelBindingResolver(config, skills, providers, _build_token_policy(config))

    with pytest.raises(ModelPolicyValidationError, match="required capabilities"):
        await resolver.validate_policy(
            BindingTarget(
                kind="provider_skill",
                provider_type="crm",
                instance_name="production",
                skill_id="crm_vision_audit",
            ),
            {
                "mode": "explicit",
                "allowed_model_refs": [f"db-model:{model.id}"],
                "default_model_ref": f"db-model:{model.id}",
            },
        )


@pytest.mark.asyncio
async def test_unhealthy_policy_default_fails_over_only_within_allowed_refs(
    db_manager: DatabaseManager, tmp_path: Path
) -> None:
    from app.atlasclaw.bootstrap.startup_helpers import build_token_entries, load_runtime_token_entries
    from app.atlasclaw.db.orm.capability_model_policy import CapabilityModelPolicyService
    from app.atlasclaw.db.orm.model_config import ModelConfigService
    from app.atlasclaw.db.schemas import ModelConfigCreate

    async with db_manager.get_session() as session:
        primary = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="policy-primary",
                provider="openai",
                model_id="primary-model",
                base_url="https://db.example/v1",
                api_key="primary-secret-key",
                priority=10,
                capabilities={"vision": True},
            ),
        )
        backup = await ModelConfigService.create(
            session,
            ModelConfigCreate(
                name="policy-backup",
                provider="openai",
                model_id="backup-model",
                base_url="https://db.example/v1",
                api_key="backup-secret-key",
                priority=1,
                capabilities={"vision": True},
            ),
        )
        await CapabilityModelPolicyService.save_many(
            session,
            [
                {
                    "target": BindingTarget(kind="provider", provider_type="crm", instance_name="production"),
                    "revision": 0,
                    "enabled": True,
                    "model_policy": {
                        "mode": "explicit",
                        "allowed_model_refs": [f"db-model:{primary.id}", f"db-model:{backup.id}"],
                        "default_model_ref": f"db-model:{primary.id}",
                    },
                }
            ],
            updated_by="admin",
        )

    config = _build_config()
    file_tokens, _ = build_token_entries(config)

    async def load_tokens():
        return await load_runtime_token_entries(file_tokens)

    pool = TokenPool()
    token_policy = DynamicTokenPolicy(
        pool,
        primary_token_id="policy-primary",
        token_loader=load_tokens,
    )
    await token_policy.refresh_tokens()
    pool.mark_token_unhealthy("policy-primary", reason="simulated failure")
    skills, providers = _build_registries(tmp_path)
    resolver = ModelBindingResolver(config, skills, providers, token_policy)

    selection = await resolver.resolve(
        BindingTarget(kind="provider", provider_type="crm", instance_name="production")
    )

    assert selection.token.token_id == f"db-model:{backup.id}"
    assert selection.token.model == "backup-model"
    assert selection.eligible_token_ids == {f"db-model:{primary.id}", f"db-model:{backup.id}"}
