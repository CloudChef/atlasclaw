# -*- coding: utf-8 -*-
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

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

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


def _is_local_auth_type(auth_type: str) -> bool:
    return str(auth_type or "").strip().lower() == "local"


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

    item = await ServiceProviderConfigService.create(session, provider_data)
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

    target_provider_type = update_payload.get("provider_type")
    target_instance_name = update_payload.get("instance_name")
    if target_provider_type or target_instance_name:
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

    item = await ServiceProviderConfigService.update(session, config_id, provider_data)
    if item is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
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
            raise HTTPException(
                status_code=400,
                detail="Built-in admin permissions are managed by the system and cannot be modified",
            )
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
