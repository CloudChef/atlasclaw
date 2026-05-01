# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
FastAPI dependency guards for authentication and authorization.

Provides reusable dependency functions for:
- Extracting authenticated user from request state
- Resolving effective workspace authorization state
- Requiring admin identity for legacy protected endpoints
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db import get_db_manager, get_db_session_dependency as get_db_session
from app.atlasclaw.db.models import UserModel
from app.atlasclaw.db.orm.role import RoleService, build_default_permissions
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.skills.permission_service import skill_permission_service


SKILL_MODULE_PERMISSION_KEYS = {"view", "enable_disable", "manage_permissions"}
PROVIDER_MODULE_PERMISSION_KEYS = {"manage_permissions"}
CHANNEL_MODULE_PERMISSION_KEYS = {"manage_permissions"}


@dataclass
class AuthorizationContext:
    """Resolved authorization state for the current request."""

    user: UserInfo
    db_user: Optional[UserModel] = None
    role_identifiers: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=build_default_permissions)
    is_admin: bool = False


async def get_current_user(request: Request) -> UserInfo:
    """
    Extract authenticated user from request state.

    This dependency retrieves the UserInfo object injected by AuthMiddleware
    and validates that the user is properly authenticated (not anonymous).

    Args:
        request: The FastAPI request object

    Returns:
        UserInfo: The authenticated user's information

    Raises:
        HTTPException: 401 if no user info found or user is anonymous
    """
    user_info = getattr(request.state, "user_info", None)
    if not user_info or user_info.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_info


async def require_admin(
    request: Request,
    user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserInfo:
    """
    Require admin identity for the current user.

    This legacy dependency checks whether the authenticated user currently
    holds the built-in ``admin`` workspace role.

    Args:
        request: The incoming request
        user: The authenticated user
        session: Database session

    Returns:
        UserInfo: The authenticated admin user's information

    Raises:
        HTTPException: 403 if user is not an admin
    """
    authz = await resolve_authorization_context(session, user)
    request.state.authorization_context = authz
    if not authz.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return authz.user


def _extract_role_identifiers(raw_roles: Any) -> list[str]:
    """Normalize assigned role identifiers from either dict or list storage."""
    if isinstance(raw_roles, dict):
        return [str(identifier) for identifier, enabled in raw_roles.items() if bool(enabled)]
    if isinstance(raw_roles, list):
        return [str(identifier) for identifier in raw_roles if str(identifier).strip()]
    return []


def _merge_skill_permissions(
    current_entries: list[dict[str, Any]],
    incoming_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge per-skill permissions across roles using OR semantics."""
    merged: dict[str, dict[str, Any]] = {}
    for entry in current_entries + incoming_entries:
        if not isinstance(entry, dict):
            continue

        skill_id = str(entry.get("skill_id") or entry.get("skill_name") or "").strip()
        if not skill_id:
            continue

        existing = merged.get(skill_id)
        if not existing:
            merged[skill_id] = {
                "skill_id": skill_id,
                "skill_name": str(entry.get("skill_name") or skill_id),
                "description": str(entry.get("description") or ""),
                "authorized": bool(entry.get("authorized", False)),
                "enabled": bool(entry.get("enabled", False)),
            }
            continue

        existing["authorized"] = existing["authorized"] or bool(entry.get("authorized", False))
        existing["enabled"] = existing["enabled"] or bool(entry.get("enabled", False))
        if not existing["description"] and entry.get("description"):
            existing["description"] = str(entry.get("description"))
        if not existing["skill_name"] and entry.get("skill_name"):
            existing["skill_name"] = str(entry.get("skill_name"))

    return list(merged.values())


def _provider_rule_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("provider_type") or "").strip(),
        str(entry.get("instance_name") or "").strip(),
    )


def _merge_effective_provider_permissions(
    role_permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge provider-instance permissions using additive allowlist semantics."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for permissions in role_permissions:
        entries = (
            permissions.get("providers", {}).get("provider_permissions", [])
            if isinstance(permissions, dict)
            else []
        )
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = _provider_rule_key(entry)
            if not key[0] or not key[1]:
                continue
            allowed = entry.get("allowed") is True
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "provider_type": key[0],
                    "instance_name": key[1],
                    "allowed": allowed,
                }
                continue
            existing["allowed"] = bool(existing.get("allowed", False)) or allowed

    return list(merged.values())


def _channel_rule_key(entry: dict[str, Any]) -> str:
    return str(entry.get("channel_type") or "").strip()


def _merge_effective_channel_permissions(
    role_permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge channel-type permissions using additive allowlist semantics."""
    merged: dict[str, dict[str, Any]] = {}
    for permissions in role_permissions:
        entries = (
            permissions.get("channels", {}).get("channel_permissions", [])
            if isinstance(permissions, dict)
            else []
        )
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            channel_type = _channel_rule_key(entry)
            if not channel_type:
                continue
            existing = merged.get(channel_type)
            allowed = entry.get("allowed") is True
            if existing is None:
                merged[channel_type] = {
                    "channel_type": channel_type,
                    "channel_name": str(entry.get("channel_name") or channel_type),
                    "allowed": allowed,
                }
                continue
            existing["allowed"] = bool(existing.get("allowed", False)) or allowed
            if not existing.get("channel_name") and entry.get("channel_name"):
                existing["channel_name"] = str(entry.get("channel_name"))

    return list(merged.values())


def _merge_permissions(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge role permissions with recursive OR semantics."""
    merged = dict(current)
    for key, value in incoming.items():
        existing = merged.get(key)
        if key == "skill_permissions" and isinstance(existing, list) and isinstance(value, list):
            merged[key] = _merge_skill_permissions(existing, value)
            continue

        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_permissions(existing, value)
            continue

        if isinstance(existing, bool) and isinstance(value, bool):
            merged[key] = existing or value
            continue

        merged[key] = value

    return merged


def _normalize_permission_path(permission_path: str) -> list[str]:
    parts = [segment.strip() for segment in permission_path.split(".") if segment.strip()]
    if len(parts) == 2 and parts[0] == "skills" and parts[1] in SKILL_MODULE_PERMISSION_KEYS:
        return ["skills", "module_permissions", parts[1]]
    if len(parts) == 2 and parts[0] == "providers" and parts[1] in PROVIDER_MODULE_PERMISSION_KEYS:
        return ["providers", "module_permissions", parts[1]]
    if len(parts) == 2 and parts[0] == "channels" and parts[1] in CHANNEL_MODULE_PERMISSION_KEYS:
        return ["channels", "module_permissions", parts[1]]
    return parts


def _extract_external_subject(user: UserInfo) -> str:
    extra = user.extra if isinstance(user.extra, dict) else {}
    external_subject = str(extra.get("external_subject", "")).strip()
    if external_subject:
        return external_subject

    provider_subject = str(user.provider_subject or "").strip()
    if ":" not in provider_subject:
        return ""

    return provider_subject.split(":", 1)[1].strip()


async def _lookup_workspace_user(session: AsyncSession, user: UserInfo) -> Optional[UserModel]:
    runtime_user_id = user.user_id or ""
    if runtime_user_id:
        db_user = await UserService.get_by_id(session, runtime_user_id)
        if db_user is not None:
            return db_user

    candidates: list[str] = []

    def _append_candidate(value: str) -> None:
        normalized_value = str(value or "").strip()
        if normalized_value and normalized_value not in candidates:
            candidates.append(normalized_value)

    _append_candidate(runtime_user_id)
    _append_candidate(_extract_external_subject(user))

    auth_type = str(user.auth_type or "").strip() or None
    for candidate in candidates:
        db_user = await UserService.get_by_username(
            session,
            candidate,
            auth_type=auth_type,
        )
        if db_user is not None:
            return db_user

    return None


def has_permission(authz: AuthorizationContext, permission_path: str) -> bool:
    """Check whether the current user has a specific effective permission."""
    value: Any = authz.permissions
    for segment in _normalize_permission_path(permission_path):
        if not isinstance(value, dict):
            return False
        value = value.get(segment)

    return value is True


def has_skill_access(authz: AuthorizationContext, skill_name: str) -> bool:
    """Check whether the current user may execute a specific skill."""
    skill_permissions = authz.permissions.get("skills", {}).get("skill_permissions", [])
    if not isinstance(skill_permissions, list) or not skill_permissions:
        return False

    return skill_permission_service.is_skill_enabled(skill_permissions, skill_name)


def has_provider_instance_access(
    authz: AuthorizationContext,
    provider_type: str,
    instance_name: str,
) -> bool:
    """Return whether the user may use a provider instance at runtime."""
    normalized_provider_type = str(provider_type or "").strip()
    normalized_instance_name = str(instance_name or "").strip()
    if not normalized_provider_type or not normalized_instance_name:
        return False

    provider_permissions = (
        authz.permissions.get("providers", {}).get("provider_permissions", [])
        if isinstance(authz.permissions, dict)
        else []
    )
    if not isinstance(provider_permissions, list) or not provider_permissions:
        return False

    for entry in provider_permissions:
        if not isinstance(entry, dict):
            continue
        if (
            str(entry.get("provider_type") or "").strip() == normalized_provider_type
            and str(entry.get("instance_name") or "").strip() == normalized_instance_name
        ):
            return entry.get("allowed") is True
    return False


def has_channel_type_access(authz: AuthorizationContext, channel_type: str) -> bool:
    """Return whether the user may manage own connections for a channel type."""
    normalized_channel_type = str(channel_type or "").strip()
    if not normalized_channel_type:
        return False

    channel_permissions = (
        authz.permissions.get("channels", {}).get("channel_permissions", [])
        if isinstance(authz.permissions, dict)
        else []
    )
    if not isinstance(channel_permissions, list) or not channel_permissions:
        return False

    for entry in channel_permissions:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("channel_type") or "").strip() == normalized_channel_type:
            return entry.get("allowed") is True
    return False


def filter_channel_types_for_authz(
    authz: AuthorizationContext,
    channels: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return channel catalog entries allowed for the current authorization context."""
    return [
        dict(channel)
        for channel in channels
        if isinstance(channel, dict)
        and has_channel_type_access(authz, str(channel.get("type") or ""))
    ]


def filter_provider_instances_for_authz(
    authz: AuthorizationContext,
    provider_instances: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return provider instances visible to the current authorization context."""
    filtered: dict[str, dict[str, dict[str, Any]]] = {}
    for provider_type, instances in (provider_instances or {}).items():
        if not isinstance(instances, dict):
            continue
        visible_instances: dict[str, dict[str, Any]] = {}
        for instance_name, instance_config in instances.items():
            if not isinstance(instance_config, dict):
                continue
            if has_provider_instance_access(authz, str(provider_type), str(instance_name)):
                visible_instances[str(instance_name)] = dict(instance_config)
        if visible_instances:
            filtered[str(provider_type)] = visible_instances
    return filtered


def ensure_permission(
    authz: AuthorizationContext,
    permission_path: str,
    *,
    detail: Optional[str] = None,
) -> None:
    """Raise 403 if the current user lacks a required permission."""
    if has_permission(authz, permission_path):
        return
    raise HTTPException(status_code=403, detail=detail or f"Missing permission: {permission_path}")


def ensure_any_permission(
    authz: AuthorizationContext,
    permission_paths: Sequence[str],
    *,
    detail: str,
) -> None:
    """Raise 403 unless one of the requested permissions is granted."""
    if any(has_permission(authz, permission_path) for permission_path in permission_paths):
        return
    raise HTTPException(status_code=403, detail=detail)


def ensure_skill_access(
    authz: AuthorizationContext,
    skill_name: str,
    *,
    detail: Optional[str] = None,
) -> None:
    """Raise 403 if the current user cannot execute the requested skill."""
    if has_skill_access(authz, skill_name):
        return
    raise HTTPException(
        status_code=403,
        detail=detail or f"Missing permission to execute skill: {skill_name}",
    )


def ensure_provider_instance_access(
    authz: AuthorizationContext,
    provider_type: str,
    instance_name: str,
    *,
    detail: Optional[str] = None,
) -> None:
    """Raise 403 if the user cannot access a provider instance."""
    if has_provider_instance_access(authz, provider_type, instance_name):
        return
    raise HTTPException(
        status_code=403,
        detail=detail or f"Missing permission to access provider instance: {provider_type}.{instance_name}",
    )


def ensure_channel_type_access(
    authz: AuthorizationContext,
    channel_type: str,
    *,
    detail: Optional[str] = None,
) -> None:
    """Raise 403 if the user cannot manage own connections for a channel type."""
    if has_channel_type_access(authz, channel_type):
        return
    raise HTTPException(
        status_code=403,
        detail=detail or f"Missing permission to access channel type: {channel_type}",
    )


def is_same_workspace_user(authz: AuthorizationContext, candidate_user: UserModel) -> bool:
    """Return whether the candidate user record represents the current actor."""
    candidate_id = str(getattr(candidate_user, "id", "") or "").strip()
    if authz.db_user is not None:
        current_db_user_id = str(getattr(authz.db_user, "id", "") or "").strip()
        if current_db_user_id and current_db_user_id == candidate_id:
            return True

    candidate_username = str(getattr(candidate_user, "username", "") or "").strip()
    if not candidate_username:
        return False

    current_user_id = str(authz.user.user_id or "").strip()
    if current_user_id and current_user_id == candidate_username:
        return True

    external_subject = _extract_external_subject(authz.user)
    if external_subject and external_subject == candidate_username:
        return True

    return False


def can_manage_permission_module(authz: AuthorizationContext, module_id: str) -> bool:
    """Check whether the current user can govern a permission module."""
    if has_permission(authz, "roles.manage_permissions"):
        return True
    if module_id == "roles":
        return False
    return has_permission(authz, f"{module_id}.manage_permissions")


def ensure_can_manage_permission_modules(
    authz: AuthorizationContext,
    requested_permissions: Optional[dict[str, Any]],
    *,
    existing_permissions: Optional[dict[str, Any]] = None,
) -> None:
    """Validate permission-matrix edits against module governance permissions."""
    normalized_existing = RoleService.normalize_permissions(existing_permissions)
    normalized_requested = RoleService.normalize_permissions(requested_permissions)
    changed_modules = sorted({
        module_id
        for module_id in set(normalized_existing.keys()) | set(normalized_requested.keys())
        if normalized_existing.get(module_id) != normalized_requested.get(module_id)
    })

    if not changed_modules:
        return

    if has_permission(authz, "roles.manage_permissions"):
        return

    unauthorized_modules = [
        module_id for module_id in changed_modules if not can_manage_permission_module(authz, module_id)
    ]
    if unauthorized_modules:
        raise HTTPException(
            status_code=403,
            detail=(
                "Missing permission governance access for module(s): "
                + ", ".join(unauthorized_modules)
            ),
        )


async def get_authorization_context(
    request: Request,
    user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AuthorizationContext:
    """Resolve effective request permissions from assigned roles."""
    cached = getattr(request.state, "authorization_context", None)
    if isinstance(cached, AuthorizationContext) and cached.user.user_id == user.user_id:
        return cached

    authz = await resolve_authorization_context(session, user)
    request.state.authorization_context = authz
    return authz


async def get_optional_authorization_context(request: Request) -> AuthorizationContext | None:
    """Resolve request permissions when RBAC storage is available.

    Catalog-style endpoints also run during bootstrap/no-DB flows, where missing
    auth state or an uninitialized database means "no RBAC context".
    """
    user_info = getattr(request.state, "user_info", None)
    cached = getattr(request.state, "authorization_context", None)
    if (
        isinstance(user_info, UserInfo)
        and isinstance(cached, AuthorizationContext)
        and cached.user.user_id == user_info.user_id
    ):
        return cached

    if not isinstance(user_info, UserInfo) or user_info.user_id == "anonymous":
        return None

    db_mgr = get_db_manager()
    if db_mgr is None or not db_mgr.is_initialized:
        return None

    async with db_mgr.get_session() as session:
        authz = await resolve_authorization_context(session, user_info)
    request.state.authorization_context = authz
    return authz


async def resolve_authorization_context(
    session: AsyncSession,
    user: UserInfo,
) -> AuthorizationContext:
    """Resolve effective permissions for a user without requiring a request object."""
    await RoleService.ensure_builtin_roles(session)

    db_user = await _lookup_workspace_user(session, user)
    if db_user and not db_user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    normalized_auth_type = str(user.auth_type or "").strip().lower()
    allow_transient_user_roles = normalized_auth_type in {"", "local", "test"}
    role_identifiers = (
        _extract_role_identifiers(db_user.roles)
        if db_user is not None
        else (_extract_role_identifiers(user.roles) if allow_transient_user_roles else [])
    )

    effective_permissions = build_default_permissions()
    if role_identifiers:
        roles = await RoleService.list_by_identifiers(session, role_identifiers, is_active=True)
        normalized_role_permissions: list[dict[str, Any]] = []
        for role in roles:
            normalized_permissions = RoleService.normalize_permissions(role.permissions)
            normalized_role_permissions.append(normalized_permissions)
            effective_permissions = _merge_permissions(
                effective_permissions,
                normalized_permissions,
            )
        effective_permissions.setdefault("providers", {})
        effective_permissions["providers"]["provider_permissions"] = (
            _merge_effective_provider_permissions(normalized_role_permissions)
        )
        effective_permissions.setdefault("channels", {})
        effective_permissions["channels"]["channel_permissions"] = (
            _merge_effective_channel_permissions(normalized_role_permissions)
        )

    is_admin = any(identifier.lower() == "admin" for identifier in role_identifiers)

    authz = AuthorizationContext(
        user=user,
        db_user=db_user,
        role_identifiers=role_identifiers,
        permissions=effective_permissions,
        is_admin=is_admin,
    )
    return authz
