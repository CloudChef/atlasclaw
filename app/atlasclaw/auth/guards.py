# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

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
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.models import UserModel
from app.atlasclaw.db.orm.role import RoleService, build_default_permissions
from app.atlasclaw.db.orm.user import UserService


SKILL_MODULE_PERMISSION_KEYS = {"view", "enable_disable", "manage_permissions"}


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
    return parts


def _normalize_skill_identifier(skill_name: str) -> str:
    return str(skill_name or "").strip()


def _extract_external_subject(user: UserInfo) -> str:
    extra = user.extra if isinstance(user.extra, dict) else {}
    external_subject = str(extra.get("external_subject", "")).strip()
    if external_subject:
        return external_subject

    provider_subject = str(user.provider_subject or "").strip()
    if ":" not in provider_subject:
        return ""

    return provider_subject.split(":", 1)[1].strip()


async def _resolve_shadow_subject(user: UserInfo) -> str:
    normalized_auth_type = str(user.auth_type or "").strip().lower()
    if normalized_auth_type == "local":
        return ""

    normalized_user_id = str(user.user_id or "").strip()
    if not normalized_user_id:
        return ""

    try:
        from pathlib import Path

        from app.atlasclaw.auth.shadow_store import ShadowUserStore
        from app.atlasclaw.core.config import get_config

        workspace_path = Path(get_config().workspace.path).resolve()
        shadow_store = ShadowUserStore(workspace_path=str(workspace_path))
        shadow_user = await shadow_store.get_by_id(normalized_user_id)
    except Exception:
        return ""

    if not shadow_user:
        return ""

    return str(shadow_user.subject or "").strip()


async def _lookup_workspace_user(session: AsyncSession, user: UserInfo) -> Optional[UserModel]:
    candidates: list[str] = []

    def _append_candidate(value: str) -> None:
        normalized_value = str(value or "").strip()
        if normalized_value and normalized_value not in candidates:
            candidates.append(normalized_value)

    _append_candidate(user.user_id)
    _append_candidate(_extract_external_subject(user))

    if str(user.auth_type or "").strip().lower() != "local" and len(candidates) <= 1:
        _append_candidate(await _resolve_shadow_subject(user))

    for candidate in candidates:
        db_user = await UserService.get_by_username(session, candidate)
        if db_user is not None:
            return db_user

    return None


def _skill_identifier_matches(candidate: Any, skill_name: str) -> bool:
    normalized_skill_name = _normalize_skill_identifier(skill_name)
    if not normalized_skill_name:
        return False

    normalized_candidate = _normalize_skill_identifier(str(candidate or ""))
    if not normalized_candidate:
        return False

    return normalized_candidate == normalized_skill_name or normalized_candidate.split(":")[-1] == normalized_skill_name.split(":")[-1]


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
    if not has_permission(authz, "skills.view"):
        return False

    skill_permissions = authz.permissions.get("skills", {}).get("skill_permissions", [])
    if not isinstance(skill_permissions, list) or not skill_permissions:
        # Admin with an empty list (e.g. frontend saved a full-enable as [])
        # retains open access; all other roles are deny-all.
        return authz.is_admin

    matching_entries = [
        entry
        for entry in skill_permissions
        if isinstance(entry, dict)
        and (
            _skill_identifier_matches(entry.get("skill_id"), skill_name)
            or _skill_identifier_matches(entry.get("skill_name"), skill_name)
        )
    ]
    if not matching_entries:
        return False

    return any(bool(entry.get("authorized", False)) and bool(entry.get("enabled", False)) for entry in matching_entries)


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
        for role in roles:
            effective_permissions = _merge_permissions(
                effective_permissions,
                RoleService.normalize_permissions(role.permissions),
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
