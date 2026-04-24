# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Database API routes for configuration management.

Provides REST endpoints for:
- /api/agent-configs - Agent configuration management
- /api/token-configs - Token configuration management
- /api/provider-configs - Service provider configuration management
- /api/model-configs - Model configuration management
- /api/users - User management

Note: Channel configuration is managed via /api/channels routes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.core.config import get_config
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.schemas import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentListResponse,
    TokenCreate,
    TokenUpdate,
    TokenResponse,
    TokenListResponse,
    ServiceProviderConfigCreate,
    ServiceProviderConfigUpdate,
    ServiceProviderConfigResponse,
    ServiceProviderConfigListResponse,
    UserCreate,
    UserUpdate,
    UserResponse,
    UserListResponse,
    RoleCreate,
    RoleUpdate,
    RoleResponse,
    RoleListResponse,
    ProfileUpdate,
    PasswordChange,
    UserProviderSettingUpdate,
    UserProviderSettingsResponse,
)
from app.atlasclaw.db.orm.agent_config import AgentConfigService
from app.atlasclaw.db.orm.audit import AuditService
from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService
from app.atlasclaw.db.orm.role import RoleService, is_system_managed_builtin_role
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.orm.user import UserService, verify_password
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_any_permission,
    ensure_can_manage_permission_modules,
    ensure_permission,
    get_current_user,
    get_authorization_context,
    is_same_workspace_user,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.api.service_provider_schemas import (
    get_provider_schema_definition,
    normalize_provider_config,
    normalize_provider_auth_type_chain,
    serialize_provider_auth_type,
)
from app.atlasclaw.bootstrap.startup_helpers import (
    build_provider_instances_from_db,
    merge_provider_instances,
)
from .deps_context import get_api_context
from .services.auth_service import load_profile_snapshot
from .model_config_routes import router as model_config_router
from .provider_info_routes import router as provider_info_router

ALLOWED_AVATAR_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ROLE_MANAGEMENT_ACCESS_PERMISSIONS = (
    "roles.view",
    "roles.create",
    "roles.edit",
    "roles.delete",
    "rbac.manage_permissions",
    "skills.manage_permissions",
    "channels.manage_permissions",
    "tokens.manage_permissions",
    "agent_configs.manage_permissions",
    "provider_configs.manage_permissions",
    "model_configs.manage_permissions",
    "users.manage_permissions",
)
ROLE_CATALOG_ACCESS_PERMISSIONS = ROLE_MANAGEMENT_ACCESS_PERMISSIONS + (
    "users.assign_roles",
)
USER_MANAGEMENT_ACCESS_PERMISSIONS = (
    "users.view",
    "users.create",
    "users.edit",
    "users.delete",
    "users.reset_password",
    "users.assign_roles",
)
SENSITIVE_ROLE_IDENTIFIERS = frozenset({"admin"})
NON_ADMIN_ASSIGNABLE_PERMISSION_PATHS = frozenset({
    "skills.module_permissions.view",
    "channels.view",
    "tokens.view",
    "agent_configs.view",
    "provider_configs.view",
    "model_configs.view",
    "users.view",
    "roles.view",
})
SENSITIVE_PROVIDER_CONFIG_KEY_FRAGMENTS = frozenset(
    (
        "cookie",
        "token",
        "password",
        "secret",
        "apikey",
        "credential",
    )
)


def _is_local_auth_type(auth_type: str) -> bool:
    return str(auth_type or "").strip().lower() == "local"


def _is_blank_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return not value or all(_is_blank_value(item) for item in value)
    return False


def _serialize_role_for_audit(role: object) -> dict[str, object]:
    """Build a compact role payload for audit logging."""
    return AuditService.sanitize_user_data({
        "id": getattr(role, "id", None),
        "name": getattr(role, "name", None),
        "identifier": getattr(role, "identifier", None),
        "description": getattr(role, "description", None),
        "permissions": getattr(role, "permissions", None),
        "is_builtin": getattr(role, "is_builtin", None),
        "is_active": getattr(role, "is_active", None),
    })


def _has_truthy_role_assignments(raw_roles: Optional[dict[str, object]]) -> bool:
    return any(bool(enabled) for enabled in (raw_roles or {}).values())


def _extract_enabled_role_identifiers(raw_roles: object) -> set[str]:
    identifiers: set[str] = set()
    if isinstance(raw_roles, dict):
        for identifier, enabled in raw_roles.items():
            normalized_identifier = str(identifier or "").strip().lower()
            if normalized_identifier and bool(enabled):
                identifiers.add(normalized_identifier)
        return identifiers

    if isinstance(raw_roles, list):
        for identifier in raw_roles:
            normalized_identifier = str(identifier or "").strip().lower()
            if normalized_identifier:
                identifiers.add(normalized_identifier)
    return identifiers


def _iter_enabled_permission_paths(
    value: object,
    *,
    prefix: str = "",
) -> list[str]:
    if isinstance(value, bool):
        return [prefix] if value and prefix else []

    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_iter_enabled_permission_paths(child, prefix=child_prefix))
        return paths

    if isinstance(value, list):
        if prefix == "skills.skill_permissions" and any(
            isinstance(entry, dict)
            and (bool(entry.get("authorized", False)) or bool(entry.get("enabled", False)))
            for entry in value
        ):
            return [prefix]
        return []

    return []


def _is_non_admin_assignable_role(role: object) -> bool:
    normalized_identifier = str(getattr(role, "identifier", "") or "").strip().lower()
    if normalized_identifier in SENSITIVE_ROLE_IDENTIFIERS:
        return False

    normalized_permissions = RoleService.normalize_permissions(getattr(role, "permissions", None))
    enabled_paths = set(_iter_enabled_permission_paths(normalized_permissions))
    return enabled_paths.issubset(NON_ADMIN_ASSIGNABLE_PERMISSION_PATHS)


async def _ensure_role_identifiers_exist(
    session: AsyncSession,
    role_identifiers: set[str],
) -> None:
    if not role_identifiers:
        return

    existing_roles = await RoleService.list_by_identifiers(session, sorted(role_identifiers))
    existing_identifiers = {
        str(getattr(role, "identifier", "") or "").strip().lower()
        for role in existing_roles
        if str(getattr(role, "identifier", "") or "").strip()
    }
    unknown_identifiers = sorted(role_identifiers - existing_identifiers)
    if unknown_identifiers:
        raise HTTPException(
            status_code=400,
            detail="Unknown role identifier(s): " + ", ".join(unknown_identifiers),
        )


async def _ensure_can_assign_roles(
    session: AsyncSession,
    authz: AuthorizationContext,
    requested_roles: object,
    *,
    existing_roles: object = None,
) -> None:
    requested_identifiers = _extract_enabled_role_identifiers(requested_roles)
    existing_identifiers = _extract_enabled_role_identifiers(existing_roles)

    if requested_identifiers == existing_identifiers:
        return

    ensure_permission(
        authz,
        "users.assign_roles",
        detail="Missing permission: users.assign_roles",
    )

    await _ensure_role_identifiers_exist(session, requested_identifiers)

    if authz.is_admin:
        return

    changed_identifiers = sorted(requested_identifiers.symmetric_difference(existing_identifiers))
    changed_sensitive_roles = [
        identifier for identifier in changed_identifiers if identifier in SENSITIVE_ROLE_IDENTIFIERS
    ]
    if changed_sensitive_roles:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only administrators may grant or revoke role(s): "
                + ", ".join(changed_sensitive_roles)
            ),
        )

    changed_roles = await RoleService.list_by_identifiers(session, changed_identifiers)
    changed_protected_roles = sorted(
        str(role.identifier)
        for role in changed_roles
        if not _is_non_admin_assignable_role(role)
    )
    if changed_protected_roles:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only administrators may grant or revoke protected role(s): "
                + ", ".join(changed_protected_roles)
            ),
        )


def _changed_role_metadata_fields(existing_role: object, role_data: RoleUpdate) -> set[str]:
    changed_fields: set[str] = set()
    for field_name in {"name", "identifier", "description", "is_active"}:
        if field_name not in role_data.model_fields_set:
            continue
        if getattr(role_data, field_name) != getattr(existing_role, field_name, None):
            changed_fields.add(field_name)
    return changed_fields


def _user_profile_fields_present(user_data: UserUpdate) -> bool:
    editable_fields = {"email", "display_name", "auth_type", "is_active", "avatar_url"}
    return bool(editable_fields & user_data.model_fields_set)


def _provider_config_to_response(item: object) -> ServiceProviderConfigResponse:
    """Convert provider config ORM model to decrypted API response."""
    return ServiceProviderConfigResponse(
        id=getattr(item, "id"),
        provider_type=getattr(item, "provider_type"),
        instance_name=getattr(item, "instance_name"),
        config=ServiceProviderConfigService.get_config(item),
        is_active=getattr(item, "is_active"),
        created_at=getattr(item, "created_at"),
        updated_at=getattr(item, "updated_at"),
    )


def _default_user_setting_document() -> dict[str, object]:
    """Return the default user_setting.json payload."""
    return {
        "channels": {},
        "providers": {},
        "preferences": {},
    }


def _get_config_provider_instances() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        provider_type: {
            instance_name: dict(instance_config)
            for instance_name, instance_config in instances.items()
            if isinstance(instance_config, dict)
        }
        for provider_type, instances in (get_config().service_providers or {}).items()
        if isinstance(instances, dict)
    }


async def _get_merged_provider_instances(
    session: AsyncSession,
) -> dict[str, dict[str, dict[str, Any]]]:
    config_provider_instances = _get_config_provider_instances()
    db_provider_instances = await build_provider_instances_from_db(session)
    if not db_provider_instances:
        return config_provider_instances
    return merge_provider_instances(db_provider_instances, config_provider_instances)


async def _refresh_provider_instances_in_api_context(session: AsyncSession) -> None:
    """Refresh in-memory provider instances after config CRUD operations."""
    ctx = get_api_context()
    merged_provider_instances = await _get_merged_provider_instances(session)

    if ctx.service_provider_registry is not None:
        ctx.service_provider_registry.load_instances_from_config(merged_provider_instances)
        ctx.provider_instances = ctx.service_provider_registry.get_all_instance_configs()
        ctx.available_providers = ctx.service_provider_registry.get_available_providers_summary()
    else:
        ctx.provider_instances = merged_provider_instances
        ctx.available_providers = {
            provider_type: sorted(instances.keys())
            for provider_type, instances in merged_provider_instances.items()
            if isinstance(instances, dict)
        }


def _get_user_setting_path(workspace_path: str, user_id: str) -> Path:
    """Return the path to a user's user_setting.json file."""
    return Path(workspace_path).resolve() / "users" / user_id / "user_setting.json"


def _load_user_setting_document(workspace_path: str, user_id: str) -> dict[str, object]:
    """Load the user's settings document, creating a default one when absent."""
    config_path = _get_user_setting_path(workspace_path, user_id)
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        document = _default_user_setting_document()
        config_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return document

    try:
        raw_document = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw_document = {}

    document = _default_user_setting_document()
    if isinstance(raw_document, dict):
        for section_name in document.keys():
            section_value = raw_document.get(section_name)
            if isinstance(section_value, dict):
                document[section_name] = section_value
    return document


def _save_user_setting_document(workspace_path: str, user_id: str, document: dict[str, object]) -> None:
    """Persist the user's settings document."""
    config_path = _get_user_setting_path(workspace_path, user_id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _get_provider_template_config(
    provider_type: str,
    instance_name: str,
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> Optional[dict[str, object]]:
    """Resolve a configured system provider template instance."""
    service_providers = provider_instances or _get_config_provider_instances()
    provider_bucket = service_providers.get(provider_type)
    if not isinstance(provider_bucket, dict):
        return None
    template_config = provider_bucket.get(instance_name)
    return dict(template_config) if isinstance(template_config, dict) else None


def _normalize_user_provider_config(
    provider_type: str,
    instance_name: str,
    config: dict[str, object],
    existing_config: Optional[dict[str, object]] = None,
    provider_instances: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
) -> dict[str, object]:
    """Validate user-owned provider config against a system template instance."""
    template_config = _get_provider_template_config(
        provider_type,
        instance_name,
        provider_instances=provider_instances,
    )
    if template_config is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider template '{provider_type}.{instance_name}' not found",
        )

    definition = get_provider_schema_definition(provider_type)
    template_auth_source: object = template_config.get("auth_type")
    if template_auth_source in (None, "") and definition is not None:
        template_auth_source = definition.default_auth_type
    if template_auth_source in (None, ""):
        template_auth_source = config.get("auth_type")

    try:
        # Account settings must follow the provider instance template. The
        # request body cannot switch the provider to another auth mode.
        authoritative_auth_chain = normalize_provider_auth_type_chain(
            template_auth_source,
            fallback=definition.default_auth_type if definition is not None else None,
        )
        authoritative_auth_type = serialize_provider_auth_type(authoritative_auth_chain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "user_token" not in authoritative_auth_chain:
        raise HTTPException(
            status_code=422,
            detail="Provider template does not support user-owned user_token settings",
        )

    # Only user_token is user-owned. Platform fields such as base_url,
    # provider_token, cookie, and credentials stay in the provider template.
    merged_config = dict(template_config)
    if isinstance(existing_config, dict):
        merged_config.update(
            {
                key: value
                for key, value in existing_config.items()
                if key == "user_token"
            }
        )
    merged_config.update(
        {
            key: value
            for key, value in dict(config).items()
            if key == "user_token"
        }
    )
    merged_config["auth_type"] = authoritative_auth_type
    existing_user_token = (
        existing_config.get("user_token")
        if isinstance(existing_config, dict)
        else None
    )
    incoming_user_token = dict(config).get("user_token")
    if _is_blank_value(existing_user_token) and _is_blank_value(incoming_user_token):
        raise HTTPException(
            status_code=422,
            detail="user_token is required for user-owned provider settings",
        )

    try:
        normalized_config = normalize_provider_config(
            provider_type,
            merged_config,
            validate_auth_requirements=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    allowed_fields = None
    if definition is not None:
        allowed_fields = {
            field.name
            for field in definition.resolve_fields(
                {"auth_type": authoritative_auth_type}
            )
        }

    persisted_keys = {"auth_type"}
    # Blank updates preserve the existing token but never cause template fields
    # to be copied into the user's settings document.
    if isinstance(existing_config, dict):
        persisted_keys.update(
            key
            for key in existing_config.keys()
            if key == "user_token"
        )
    persisted_keys.update(
        key
        for key in dict(config).keys()
        if key == "user_token"
    )

    return {
        key: value
        for key, value in normalized_config.items()
        if key in persisted_keys
        and key not in {"base_url", "provider_token"}
        and (allowed_fields is None or key in allowed_fields)
    }


def _is_sensitive_provider_config_field(provider_type: str, field_name: str) -> bool:
    """Return True when a provider config field should never be echoed back to the UI."""
    normalized_field_name = str(field_name or "").strip().lower()
    if not normalized_field_name:
        return False
    compact_field_name = "".join(
        char for char in normalized_field_name if char.isalnum()
    )
    if any(
        fragment in normalized_field_name or fragment in compact_field_name
        for fragment in SENSITIVE_PROVIDER_CONFIG_KEY_FRAGMENTS
    ):
        return True

    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        return False

    for field in definition.resolve_fields(filter_by_auth_type=False):
        if field.name == normalized_field_name:
            return bool(field.sensitive or field.type == "password")
    return False


def _redact_user_provider_config(
    provider_type: str,
    config: object,
) -> dict[str, object]:
    """Remove sensitive provider config values from user-facing responses."""
    if not isinstance(config, dict):
        return {}

    return {
        key: value
        for key, value in config.items()
        if not _is_sensitive_provider_config_field(provider_type, str(key))
    }


def _redact_user_provider_settings(
    providers: object,
) -> dict[str, dict[str, dict[str, object]]]:
    """Build a response-safe provider settings payload without sensitive config values."""
    if not isinstance(providers, dict):
        return {}

    redacted: dict[str, dict[str, dict[str, object]]] = {}
    for provider_type, instances in providers.items():
        if not isinstance(instances, dict):
            continue

        provider_bucket: dict[str, dict[str, object]] = {}
        for instance_name, entry in instances.items():
            if not isinstance(entry, dict):
                continue

            provider_bucket[str(instance_name)] = {
                "configured": bool(entry.get("configured", False)),
                "config": _redact_user_provider_config(
                    str(provider_type),
                    entry.get("config", {}),
                ),
                "updated_at": entry.get("updated_at"),
            }

        redacted[str(provider_type)] = provider_bucket

    return redacted


router = APIRouter(prefix="/api", tags=["Database API"])
router.include_router(model_config_router)
router.include_router(provider_info_router)


# ============== Agent Config Routes ==============


@router.post("/agent-configs", response_model=AgentResponse, status_code=201)
async def create_agent_config(
    agent_data: AgentCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> AgentResponse:
    """Create a new Agent configuration."""
    ensure_permission(authz, "agent_configs.create", detail="Missing permission: agent_configs.create")
    existing = await AgentConfigService.get_by_name(session, agent_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent '{agent_data.name}' already exists")
    
    agent = await AgentConfigService.create(session, agent_data)
    return AgentResponse.model_validate(agent)


@router.get("/agent-configs", response_model=AgentListResponse)
async def list_agent_configs(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> AgentListResponse:
    """List all Agent configurations with optional filtering."""
    ensure_permission(authz, "agent_configs.view", detail="Missing permission: agent_configs.view")
    agents, total = await AgentConfigService.list_all(session, is_active=is_active, page=page, page_size=page_size)
    return AgentListResponse(
        agents=[AgentResponse.model_validate(a) for a in agents],
        total=total,
    )


@router.get("/agent-configs/{agent_id}", response_model=AgentResponse)
async def get_agent_config(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> AgentResponse:
    """Get Agent configuration by ID."""
    ensure_permission(authz, "agent_configs.view", detail="Missing permission: agent_configs.view")
    agent = await AgentConfigService.get_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return AgentResponse.model_validate(agent)


@router.put("/agent-configs/{agent_id}", response_model=AgentResponse)
async def update_agent_config(
    agent_id: str,
    agent_data: AgentUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> AgentResponse:
    """Update an Agent configuration."""
    ensure_permission(authz, "agent_configs.edit", detail="Missing permission: agent_configs.edit")
    agent = await AgentConfigService.update(session, agent_id, agent_data)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return AgentResponse.model_validate(agent)


@router.delete("/agent-configs/{agent_id}", status_code=204)
async def delete_agent_config(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete an Agent configuration."""
    ensure_permission(authz, "agent_configs.delete", detail="Missing permission: agent_configs.delete")
    deleted = await AgentConfigService.delete(session, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent config not found")


# ============== Token Config Routes ==============


@router.post("/token-configs", response_model=TokenResponse, status_code=201)
async def create_token_config(
    token_data: TokenCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> TokenResponse:
    """Create a new Token configuration."""
    ensure_permission(authz, "tokens.create", detail="Missing permission: tokens.create")
    existing = await ModelTokenConfigService.get_by_name(session, token_data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Token '{token_data.name}' already exists")
    
    token = await ModelTokenConfigService.create(session, token_data)
    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.get("/token-configs", response_model=TokenListResponse)
async def list_token_configs(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> TokenListResponse:
    """List all Token configurations with optional filtering."""
    ensure_permission(authz, "tokens.view", detail="Missing permission: tokens.view")
    tokens, total = await ModelTokenConfigService.list_all(session, provider=provider, is_active=is_active, page=page, page_size=page_size)
    
    responses = []
    for token in tokens:
        r = TokenResponse.model_validate(token)
        r.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
        responses.append(r)
    
    return TokenListResponse(tokens=responses, total=total)


@router.get("/token-configs/{token_id}", response_model=TokenResponse)
async def get_token_config(
    token_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> TokenResponse:
    """Get Token configuration by ID."""
    ensure_permission(authz, "tokens.view", detail="Missing permission: tokens.view")
    token = await ModelTokenConfigService.get_by_id(session, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token config not found")

    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.put("/token-configs/{token_id}", response_model=TokenResponse)
async def update_token_config(
    token_id: str,
    token_data: TokenUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> TokenResponse:
    """Update a Token configuration."""
    ensure_permission(authz, "tokens.edit", detail="Missing permission: tokens.edit")
    token = await ModelTokenConfigService.update(session, token_id, token_data)
    if token is None:
        raise HTTPException(status_code=404, detail="Token config not found")

    response = TokenResponse.model_validate(token)
    response.api_key_masked = ModelTokenConfigService.get_masked_api_key(token)
    return response


@router.delete("/token-configs/{token_id}", status_code=204)
async def delete_token_config(
    token_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete a Token configuration."""
    ensure_permission(authz, "tokens.delete", detail="Missing permission: tokens.delete")
    deleted = await ModelTokenConfigService.delete(session, token_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Token config not found")


# ============== Service Provider Config Routes ==============


@router.post("/provider-configs", response_model=ServiceProviderConfigResponse, status_code=201)
async def create_provider_config(
    provider_data: ServiceProviderConfigCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ServiceProviderConfigResponse:
    """Create a new service provider instance configuration."""
    ensure_permission(
        authz,
        "provider_configs.create",
        detail="Missing permission: provider_configs.create",
    )
    existing = await ServiceProviderConfigService.get_by_provider_instance(
        session,
        provider_data.provider_type,
        provider_data.instance_name,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider config '{provider_data.provider_type}.{provider_data.instance_name}' already exists"
            ),
        )

    try:
        normalized_config = normalize_provider_config(
            provider_data.provider_type,
            provider_data.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    provider_data = provider_data.model_copy(update={"config": normalized_config})
    item = await ServiceProviderConfigService.create(session, provider_data)
    await _refresh_provider_instances_in_api_context(session)
    return _provider_config_to_response(item)


@router.get("/provider-configs", response_model=ServiceProviderConfigListResponse)
async def list_provider_configs(
    provider_type: Optional[str] = Query(None, description="Filter by provider type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ServiceProviderConfigListResponse:
    """List service provider instance configurations with optional filtering."""
    ensure_permission(
        authz,
        "provider_configs.view",
        detail="Missing permission: provider_configs.view",
    )
    items, total = await ServiceProviderConfigService.list_all(
        session,
        provider_type=provider_type,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return ServiceProviderConfigListResponse(
        provider_configs=[_provider_config_to_response(i) for i in items],
        total=total,
    )


@router.get("/provider-configs/{config_id}", response_model=ServiceProviderConfigResponse)
async def get_provider_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ServiceProviderConfigResponse:
    """Get service provider instance config by ID."""
    ensure_permission(
        authz,
        "provider_configs.view",
        detail="Missing permission: provider_configs.view",
    )
    item = await ServiceProviderConfigService.get_by_id(session, config_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return _provider_config_to_response(item)


@router.put("/provider-configs/{config_id}", response_model=ServiceProviderConfigResponse)
async def update_provider_config(
    config_id: str,
    provider_data: ServiceProviderConfigUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> ServiceProviderConfigResponse:
    """Update a service provider instance config."""
    ensure_permission(
        authz,
        "provider_configs.edit",
        detail="Missing permission: provider_configs.edit",
    )
    update_payload = provider_data.model_dump(exclude_unset=True)
    current = None

    target_provider_type = update_payload.get("provider_type")
    target_instance_name = update_payload.get("instance_name")
    if target_provider_type or target_instance_name or provider_data.config is not None:
        current = await ServiceProviderConfigService.get_by_id(session, config_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Provider config not found")

        check_provider_type = target_provider_type or current.provider_type
        check_instance_name = target_instance_name or current.instance_name
        duplicate = await ServiceProviderConfigService.get_by_provider_instance(
            session,
            check_provider_type,
            check_instance_name,
        )
        if duplicate and duplicate.id != config_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Provider config '{check_provider_type}.{check_instance_name}' already exists"
                ),
            )

    if provider_data.config is not None:
        effective_provider_type = target_provider_type or current.provider_type
        try:
            normalized_config = normalize_provider_config(
                effective_provider_type,
                provider_data.config,
                existing_config=ServiceProviderConfigService.get_config(current),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        provider_data = provider_data.model_copy(update={"config": normalized_config})

    item = await ServiceProviderConfigService.update(session, config_id, provider_data)
    if item is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    await _refresh_provider_instances_in_api_context(session)
    return _provider_config_to_response(item)


@router.delete("/provider-configs/{config_id}", status_code=204)
async def delete_provider_config(
    config_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete a service provider instance config."""
    ensure_permission(
        authz,
        "provider_configs.delete",
        detail="Missing permission: provider_configs.delete",
    )
    deleted = await ServiceProviderConfigService.delete(session, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider config not found")
    await _refresh_provider_instances_in_api_context(session)


# ============== User Routes ==============



@router.post("/roles", response_model=RoleResponse, status_code=201)
async def create_role(
    role_data: RoleCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> RoleResponse:
    """Create a new Role."""
    ensure_permission(authz, "roles.create", detail="Missing permission: roles.create")
    ensure_can_manage_permission_modules(
        authz,
        role_data.permissions,
        existing_permissions=None,
    )
    await RoleService.ensure_builtin_roles(session)

    existing_name = await RoleService.get_by_name(session, role_data.name)
    if existing_name:
        raise HTTPException(status_code=409, detail=f"Role '{role_data.name}' already exists")

    existing_identifier = await RoleService.get_by_identifier(session, role_data.identifier)
    if existing_identifier:
        raise HTTPException(
            status_code=409,
            detail=f"Role identifier '{role_data.identifier}' already exists",
        )

    role = await RoleService.create(session, role_data)
    await AuditService.log_audit(
        session=session,
        entity_type="role",
        entity_id=role.id,
        action="CREATE",
        user_id=authz.user.user_id,
        new_value=_serialize_role_for_audit(role),
    )
    return RoleResponse.model_validate(role)


@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    search: Optional[str] = Query(None, description="Search by role name, identifier, or description"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> RoleListResponse:
    """List all Roles with optional filtering."""
    ensure_any_permission(
        authz,
        ROLE_CATALOG_ACCESS_PERMISSIONS,
        detail="Missing permission to access role catalog",
    )
    roles, total = await RoleService.list_all(
        session,
        search=search,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return RoleListResponse(
        roles=[RoleResponse.model_validate(role) for role in roles],
        total=total,
    )


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> RoleResponse:
    """Get Role by ID."""
    ensure_any_permission(
        authz,
        ROLE_CATALOG_ACCESS_PERMISSIONS,
        detail="Missing permission to access role catalog",
    )
    await RoleService.ensure_builtin_roles(session)
    role = await RoleService.get_by_id(session, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return RoleResponse.model_validate(role)


@router.put("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    role_data: RoleUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> RoleResponse:
    """Update a Role."""
    await RoleService.ensure_builtin_roles(session)
    old_role = await RoleService.get_by_id(session, role_id)
    if old_role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    changed_metadata_fields = _changed_role_metadata_fields(old_role, role_data)
    if old_role.is_builtin and changed_metadata_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                "Built-in role metadata is read-only: "
                + ", ".join(sorted(changed_metadata_fields))
            ),
        )

    if changed_metadata_fields:
        ensure_permission(authz, "roles.edit", detail="Missing permission: roles.edit")

    if "permissions" in role_data.model_fields_set:
        if old_role.is_builtin and is_system_managed_builtin_role(old_role.identifier):
            # System-managed admin role: allow skills permission changes.
            # Strip non-skills changes so only the skills module is persisted.
            old_perms = RoleService.normalize_permissions(old_role.permissions)
            new_perms = RoleService.normalize_permissions(role_data.permissions)
            # Preserve existing non-skills modules unchanged
            for module_id in list(new_perms.keys()):
                if module_id != "skills":
                    new_perms[module_id] = old_perms.get(module_id, new_perms[module_id])
            role_data.permissions = new_perms
        ensure_can_manage_permission_modules(
            authz,
            role_data.permissions,
            existing_permissions=old_role.permissions,
        )

    if role_data.name and role_data.name != old_role.name:
        existing_name = await RoleService.get_by_name(session, role_data.name)
        if existing_name and existing_name.id != role_id:
            raise HTTPException(status_code=409, detail=f"Role '{role_data.name}' already exists")

    if "identifier" in role_data.model_fields_set and role_data.identifier != old_role.identifier:
        raise HTTPException(
            status_code=400,
            detail="Role identifiers cannot be changed after creation",
        )

    if role_data.identifier:
        existing_identifier = await RoleService.get_by_identifier(session, role_data.identifier)
        if existing_identifier and existing_identifier.id != role_id:
            raise HTTPException(
                status_code=409,
                detail=f"Role identifier '{role_data.identifier}' already exists",
            )

    old_value = _serialize_role_for_audit(old_role)
    role = await RoleService.update(session, role_id, role_data)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    await AuditService.log_audit(
        session=session,
        entity_type="role",
        entity_id=role_id,
        action="UPDATE",
        user_id=authz.user.user_id,
        old_value=old_value,
        new_value=_serialize_role_for_audit(role),
    )
    return RoleResponse.model_validate(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete a Role."""
    ensure_permission(authz, "roles.delete", detail="Missing permission: roles.delete")
    await RoleService.ensure_builtin_roles(session)
    role = await RoleService.get_by_id(session, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    if role.is_builtin:
        raise HTTPException(status_code=400, detail="Built-in roles cannot be deleted")

    assigned_count = await UserService.count_users_with_role(session, role.identifier)
    if assigned_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Role is currently assigned to {assigned_count} user(s)",
        )

    await RoleService.delete(session, role_id)
    await AuditService.log_audit(
        session=session,
        entity_type="role",
        entity_id=role_id,
        action="DELETE",
        user_id=authz.user.user_id,
        old_value=_serialize_role_for_audit(role),
    )


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserResponse:
    """Create a new User."""
    ensure_permission(authz, "users.create", detail="Missing permission: users.create")
    await _ensure_can_assign_roles(session, authz, user_data.roles)

    existing = await UserService.get_by_username(session, user_data.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"User '{user_data.username}' already exists")

    if user_data.email:
        existing_email = await UserService.get_by_email(session, user_data.email)
        if existing_email:
            raise HTTPException(status_code=409, detail=f"Email '{user_data.email}' already exists")

    user = await UserService.create(session, user_data)

    # Audit log for user creation
    try:
        new_value = AuditService.sanitize_user_data({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "auth_type": user.auth_type,
            "roles": user.roles,
        })
        await AuditService.log_audit(
            session=session,
            entity_type="user",
            entity_id=user.id,
            action="CREATE",
            user_id=authz.user.user_id,
            new_value=new_value,
        )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to write audit log for user creation (user=%s): %s",
            user.username,
            e,
        )

    return UserResponse.model_validate(user)


@router.get("/users", response_model=UserListResponse)
async def list_users(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search by username, email, or display name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserListResponse:
    """List all Users with optional filtering."""
    ensure_any_permission(
        authz,
        USER_MANAGEMENT_ACCESS_PERMISSIONS,
        detail="Missing permission to access user management",
    )
    users, total = await UserService.list_all(session, is_active=is_active, search=search, page=page, page_size=page_size)
    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
    )


# ============== User Self-Service Profile Routes ==============


@router.get("/users/me/profile", response_model=UserResponse, status_code=200)
async def get_my_profile(
    current_user: UserInfo = Depends(get_current_user),
) -> UserResponse:
    """Get the authenticated user's own profile."""
    profile = await load_profile_snapshot(
        user_id=current_user.user_id,
        auth_type=current_user.auth_type,
        workspace_path=str(Path(get_config().workspace.path).resolve()),
        external_subject=str(current_user.extra.get("external_subject", "")),
    )
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(profile)


@router.put("/users/me/profile", response_model=UserResponse, status_code=200)
async def update_my_profile(
    profile_data: ProfileUpdate,
    current_user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Update the authenticated user's own profile."""
    # Build update dict from non-None fields only
    update_fields = profile_data.model_dump(exclude_unset=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Get user by username first to get the database ID
    user = await UserService.get_by_username(session, current_user.user_id)
    if not user:
        if not _is_local_auth_type(current_user.auth_type):
            raise HTTPException(
                status_code=400,
                detail="Profile editing is not available for federated accounts",
            )
        raise HTTPException(status_code=404, detail="User not found")

    # If email is being changed, check uniqueness
    if "email" in update_fields and update_fields["email"]:
        existing = await UserService.get_by_email(session, update_fields["email"])
        if existing and existing.id != user.id:
            raise HTTPException(status_code=409, detail="Email already in use")

    # Use UserService.update with database ID
    updated = await UserService.update(session, user.id, UserUpdate(**update_fields))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(updated)


@router.get("/users/me/provider-settings", response_model=UserProviderSettingsResponse, status_code=200)
async def get_my_provider_settings(
    current_user: UserInfo = Depends(get_current_user),
) -> UserProviderSettingsResponse:
    """Get the authenticated user's provider credentials bound to system templates."""
    workspace_path = str(Path(get_config().workspace.path).resolve())
    document = _load_user_setting_document(workspace_path, current_user.user_id)
    providers = document.get("providers", {})
    return UserProviderSettingsResponse(
        providers=_redact_user_provider_settings(providers)
    )


@router.put("/users/me/provider-settings", response_model=UserProviderSettingsResponse, status_code=200)
async def update_my_provider_settings(
    provider_data: UserProviderSettingUpdate,
    current_user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserProviderSettingsResponse:
    """Create or update the authenticated user's provider credentials."""
    workspace_path = str(Path(get_config().workspace.path).resolve())
    document = _load_user_setting_document(workspace_path, current_user.user_id)
    providers = document.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        document["providers"] = providers

    provider_bucket = providers.setdefault(provider_data.provider_type, {})
    if not isinstance(provider_bucket, dict):
        provider_bucket = {}
        providers[provider_data.provider_type] = provider_bucket

    existing_entry = provider_bucket.get(provider_data.instance_name)
    existing_config = (
        existing_entry.get("config", {})
        if isinstance(existing_entry, dict)
        else {}
    )

    provider_instances = await _get_merged_provider_instances(session)
    normalized_config = _normalize_user_provider_config(
        provider_data.provider_type,
        provider_data.instance_name,
        provider_data.config,
        existing_config=existing_config if isinstance(existing_config, dict) else None,
        provider_instances=provider_instances,
    )

    provider_bucket[provider_data.instance_name] = {
        "configured": True,
        "config": normalized_config,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }

    _save_user_setting_document(workspace_path, current_user.user_id, document)
    return UserProviderSettingsResponse(
        providers=_redact_user_provider_settings(providers)
    )


@router.post("/users/me/avatar", response_model=UserResponse, status_code=200)
async def upload_my_avatar(
    avatar: UploadFile = File(...),
    current_user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Upload avatar image for the authenticated user."""
    user = await UserService.get_by_username(session, current_user.user_id)
    if not user:
        if not _is_local_auth_type(current_user.auth_type):
            raise HTTPException(
                status_code=400,
                detail="Avatar upload is not available for federated accounts",
            )
        raise HTTPException(status_code=404, detail="User not found")

    content_type = (avatar.content_type or "").lower()
    extension = ALLOWED_AVATAR_CONTENT_TYPES.get(content_type)
    if not extension:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, WEBP, and GIF avatars are supported")

    avatar_bytes = await avatar.read()
    if not avatar_bytes:
        raise HTTPException(status_code=400, detail="Avatar file is empty")

    if len(avatar_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Avatar image must be 2 MB or smaller")

    workspace_path = Path(get_config().workspace.path).resolve()
    avatar_dir = workspace_path / "public" / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    safe_username = re.sub(r"[^a-zA-Z0-9_-]", "_", user.username or user.id)
    for existing_file in avatar_dir.glob(f"{safe_username}-*"):
        if existing_file.is_file():
            existing_file.unlink(missing_ok=True)

    filename = f"{safe_username}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{extension}"
    avatar_path = avatar_dir / filename
    avatar_path.write_bytes(avatar_bytes)

    avatar_url = f"/user-content/avatars/{filename}"
    updated = await UserService.update(session, user.id, UserUpdate(avatar_url=avatar_url))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(updated)


@router.put("/users/me/password", status_code=200)
async def change_my_password(
    password_data: PasswordChange,
    current_user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Change the authenticated user's own password."""
    # Get user record first by username
    user = await UserService.get_by_username(session, current_user.user_id)
    if not user:
        if not _is_local_auth_type(current_user.auth_type):
            raise HTTPException(
                status_code=400,
                detail="Password authentication not available for this account",
            )
        raise HTTPException(status_code=404, detail="User not found")

    # Verify current password
    if not user.password:
        raise HTTPException(status_code=400, detail="Password authentication not available for this account")

    if not verify_password(password_data.current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Update with new password using database ID
    await UserService.update(session, user.id, UserUpdate(password=password_data.new_password))
    return {"success": True, "message": "Password changed successfully"}


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserResponse:
    """Get User by ID."""
    ensure_any_permission(
        authz,
        USER_MANAGEMENT_ACCESS_PERMISSIONS,
        detail="Missing permission to access user management",
    )
    user = await UserService.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserResponse:
    """Update a User."""
    # Fetch old user data for audit log
    old_user = await UserService.get_by_id(session, user_id)
    if old_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if _user_profile_fields_present(user_data):
        ensure_permission(authz, "users.edit", detail="Missing permission: users.edit")

    if "password" in user_data.model_fields_set:
        ensure_permission(
            authz,
            "users.reset_password",
            detail="Missing permission: users.reset_password",
        )

    if "roles" in user_data.model_fields_set and (user_data.roles or {}) != (old_user.roles or {}):
        await _ensure_can_assign_roles(
            session,
            authz,
            user_data.roles,
            existing_roles=old_user.roles,
        )

    if "email" in user_data.model_fields_set and user_data.email and user_data.email != old_user.email:
        existing_user = await UserService.get_by_email(session, user_data.email)
        if existing_user and existing_user.id != old_user.id:
            raise HTTPException(status_code=409, detail="Email already in use")

    old_value = AuditService.sanitize_user_data({
        "id": old_user.id,
        "username": old_user.username,
        "email": old_user.email,
        "display_name": old_user.display_name,
        "is_active": old_user.is_active,
        "is_admin": old_user.is_admin,
        "auth_type": old_user.auth_type,
        "roles": old_user.roles,
    })

    user = await UserService.update(session, user_id, user_data)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Audit log for user update
    new_value = AuditService.sanitize_user_data({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "auth_type": user.auth_type,
        "roles": user.roles,
    })
    await AuditService.log_audit(
        session=session,
        entity_type="user",
        entity_id=user_id,
        action="UPDATE",
        user_id=authz.user.user_id,
        old_value=old_value,
        new_value=new_value,
    )

    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> None:
    """Delete a User. Cannot delete own account."""
    ensure_permission(authz, "users.delete", detail="Missing permission: users.delete")
    # Fetch user data for audit log before deletion
    user = await UserService.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-deletion for both local and federated identities.
    if is_same_workspace_user(authz, user):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    old_value = AuditService.sanitize_user_data({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "auth_type": user.auth_type,
        "roles": user.roles,
    })

    deleted = await UserService.delete(session, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    # Audit log for user deletion
    await AuditService.log_audit(
        session=session,
        entity_type="user",
        entity_id=user_id,
        action="DELETE",
        user_id=authz.user.user_id,
        old_value=old_value,
    )
