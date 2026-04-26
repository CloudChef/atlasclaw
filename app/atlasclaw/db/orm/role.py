# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Service operations for role management."""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.models import RoleModel
from app.atlasclaw.db.schemas import RoleCreate, RoleUpdate

logger = logging.getLogger(__name__)


SYSTEM_MANAGED_BUILTIN_ROLE_IDENTIFIERS = frozenset({"admin", "user"})


def _normalize_skill_permissions(entries: Any) -> List[Dict[str, Any]]:
    """Normalize per-skill permissions into a predictable list shape."""
    if not isinstance(entries, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        skill_id = str(entry.get("skill_id") or entry.get("skill_name") or "").strip()
        skill_name = str(entry.get("skill_name") or skill_id).strip()
        if not skill_id:
            continue

        normalized.append({
            "skill_id": skill_id,
            "skill_name": skill_name or skill_id,
            "description": str(entry.get("description") or ""),
            "authorized": bool(entry.get("authorized", False)),
            "enabled": bool(entry.get("enabled", False)),
        })

    return normalized


def _normalize_provider_permissions(entries: Any) -> List[Dict[str, Any]]:
    """Normalize per-provider-instance permissions into a predictable list shape."""
    if not isinstance(entries, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        provider_type = str(entry.get("provider_type") or "").strip()
        instance_name = str(entry.get("instance_name") or "").strip()
        if not provider_type or not instance_name:
            continue

        key = (provider_type, instance_name)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "provider_type": provider_type,
            "instance_name": instance_name,
            "allowed": entry.get("allowed") is not False,
        })

    return normalized


def _merge_permission_dicts(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge role permissions while preserving the known default shape."""
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if key == "skill_permissions":
            merged[key] = _normalize_skill_permissions(value)
            continue

        if key == "provider_permissions":
            merged[key] = _normalize_provider_permissions(value)
            continue

        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_permission_dicts(merged[key], value)
            continue

        merged[key] = copy.deepcopy(value)

    return merged


def build_default_permissions() -> Dict[str, Any]:
    """Build the default role permissions shape used by the editor."""
    return {
        "skills": {
            "module_permissions": {
                "view": False,
                "enable_disable": False,
                "manage_permissions": False,
            },
            "skill_permissions": [],
        },
        "providers": {
            "module_permissions": {
                "manage_permissions": False,
            },
            "provider_permissions": [],
        },
        "channels": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
        "tokens": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
        "agent_configs": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
        "provider_configs": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
        "model_configs": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
        "users": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "assign_roles": False,
            "manage_permissions": False,
        },
        "roles": {
            "view": False,
            "create": False,
            "edit": False,
            "delete": False,
            "manage_permissions": False,
        },
    }


def _build_all_enabled_permissions() -> Dict[str, Any]:
    permissions = build_default_permissions()

    for module_id, config in permissions.items():
        if module_id == "skills":
            config["module_permissions"]["view"] = True
            config["module_permissions"]["enable_disable"] = True
            config["module_permissions"]["manage_permissions"] = True
            continue
        if module_id == "providers":
            config["module_permissions"]["manage_permissions"] = True
            continue

        for permission_name in list(config.keys()):
            config[permission_name] = True

    return permissions


BUILTIN_ROLE_DEFINITIONS: tuple[Dict[str, Any], ...] = (
    {
        "name": "Administrator",
        "identifier": "admin",
        "description": "Full administrative access to manage workspace configuration and access control.",
        "permissions": _build_all_enabled_permissions(),
    },
    {
        "name": "Standard User",
        "identifier": "user",
        "description": "Default collaborator role with access to enabled workspace skills.",
        "permissions": {
            **build_default_permissions(),
            "skills": {
                "module_permissions": {
                    "view": True,
                    "enable_disable": False,
                    "manage_permissions": False,
                },
                "skill_permissions": [],
            },
            "channels": {
                "view": True,
                "create": True,
                "edit": True,
                "delete": True,
                "manage_permissions": False,
            },
        },
    },
    {
        "name": "Viewer",
        "identifier": "viewer",
        "description": "Read-only role for audit and oversight workflows.",
        "permissions": {
            **build_default_permissions(),
            "skills": {
                "module_permissions": {
                    "view": True,
                    "enable_disable": False,
                    "manage_permissions": False,
                },
                "skill_permissions": [],
            },
            "channels": {
                "view": True,
                "create": False,
                "edit": False,
                "delete": False,
                "manage_permissions": False,
            },
            "tokens": {
                "view": True,
                "create": False,
                "edit": False,
                "delete": False,
                "manage_permissions": False,
            },
            "users": {
                "view": True,
                "create": False,
                "edit": False,
                "delete": False,
                "assign_roles": False,
                "manage_permissions": False,
            },
            "roles": {
                "view": True,
                "create": False,
                "edit": False,
                "delete": False,
            },
        },
    },
)


def is_system_managed_builtin_role(identifier: Optional[str]) -> bool:
    """Return whether a built-in role is system-managed and immutable."""
    normalized_identifier = str(identifier or "").strip().lower()
    return normalized_identifier in SYSTEM_MANAGED_BUILTIN_ROLE_IDENTIFIERS


class RoleService:
    """Service operations for Role configuration."""

    @staticmethod
    def normalize_permissions(permissions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a safe permissions payload."""
        if not permissions or not isinstance(permissions, dict):
            return build_default_permissions()
        return _merge_permission_dicts(build_default_permissions(), permissions)

    @staticmethod
    async def ensure_builtin_roles(session: AsyncSession) -> None:
        """Ensure built-in roles exist while repairing system-managed defaults."""
        result = await session.execute(select(RoleModel))
        existing = {role.identifier: role for role in result.scalars().all()}

        changed = False
        for definition in BUILTIN_ROLE_DEFINITIONS:
            identifier = definition["identifier"]
            current = existing.get(identifier)
            normalized_definition_permissions = RoleService.normalize_permissions(
                definition["permissions"]
            )
            if current:
                normalized_current_permissions = RoleService.normalize_permissions(current.permissions)
                if is_system_managed_builtin_role(identifier):
                    # Reset locked modules to canonical defaults, but preserve
                    # user-managed runtime access permissions from DB.
                    target_permissions = dict(normalized_definition_permissions)
                    current_skills = normalized_current_permissions.get("skills")
                    if isinstance(current_skills, dict) and current_skills.get("skill_permissions"):
                        target_permissions["skills"] = current_skills
                    current_providers = normalized_current_permissions.get("providers")
                    if (
                        isinstance(current_providers, dict)
                        and current_providers.get("provider_permissions")
                    ):
                        target_permissions["providers"] = current_providers
                else:
                    target_permissions = normalized_current_permissions
                if current.is_builtin and (
                    current.name != definition["name"]
                    or current.description != definition["description"]
                    or current.permissions != target_permissions
                    or current.is_active is not True
                ):
                    current.name = definition["name"]
                    current.description = definition["description"]
                    current.permissions = target_permissions
                    current.is_active = True
                    current.updated_at = datetime.utcnow()
                    changed = True
                continue

            role = RoleModel(
                name=definition["name"],
                identifier=identifier,
                description=definition["description"],
                permissions=normalized_definition_permissions,
                is_builtin=True,
                is_active=True,
            )
            session.add(role)
            changed = True

        if changed:
            await session.flush()
            logger.info("Ensured built-in roles match canonical defaults")

    @staticmethod
    async def create(session: AsyncSession, role_data: RoleCreate) -> RoleModel:
        """Create a new Role."""
        role = RoleModel(
            name=role_data.name,
            identifier=role_data.identifier,
            description=role_data.description,
            permissions=RoleService.normalize_permissions(role_data.permissions),
            is_active=role_data.is_active,
            is_builtin=False,
        )

        session.add(role)
        await session.flush()
        await session.refresh(role)
        logger.info(f"Created role: {role.identifier} (id={role.id})")
        return role

    @staticmethod
    async def get_by_id(session: AsyncSession, role_id: str) -> Optional[RoleModel]:
        """Get Role by ID."""
        result = await session.execute(select(RoleModel).where(RoleModel.id == role_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> Optional[RoleModel]:
        """Get Role by display name."""
        result = await session.execute(select(RoleModel).where(RoleModel.name == name))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_identifier(session: AsyncSession, identifier: str) -> Optional[RoleModel]:
        """Get Role by identifier."""
        result = await session.execute(select(RoleModel).where(RoleModel.identifier == identifier))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_identifiers(
        session: AsyncSession,
        identifiers: List[str],
        *,
        is_active: Optional[bool] = None,
    ) -> List[RoleModel]:
        """List roles matching a set of identifiers."""
        normalized_identifiers = [str(identifier).strip() for identifier in identifiers if str(identifier).strip()]
        if not normalized_identifiers:
            return []

        query = select(RoleModel).where(RoleModel.identifier.in_(normalized_identifiers))
        if is_active is not None:
            query = query.where(RoleModel.is_active == is_active)

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        session: AsyncSession,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[RoleModel], int]:
        """List all Roles with optional filtering."""
        await RoleService.ensure_builtin_roles(session)

        query = select(RoleModel)
        if is_active is not None:
            query = query.where(RoleModel.is_active == is_active)

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    RoleModel.name.ilike(search_pattern),
                    RoleModel.identifier.ilike(search_pattern),
                    RoleModel.description.ilike(search_pattern),
                )
            )

        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        query = query.order_by(RoleModel.is_builtin.desc(), RoleModel.created_at.asc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(query)
        roles = list(result.scalars().all())
        return roles, total

    @staticmethod
    async def update(
        session: AsyncSession, role_id: str, role_data: RoleUpdate
    ) -> Optional[RoleModel]:
        """Update a Role."""
        role = await RoleService.get_by_id(session, role_id)
        if role is None:
            return None

        update_data = role_data.model_dump(exclude_unset=True)

        if "permissions" in update_data:
            update_data["permissions"] = RoleService.normalize_permissions(update_data["permissions"])

        for key, value in update_data.items():
            setattr(role, key, value)

        role.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(role)

        logger.info(f"Updated role: {role.identifier} (id={role.id})")
        return role

    @staticmethod
    async def delete(session: AsyncSession, role_id: str) -> bool:
        """Delete a Role."""
        role = await RoleService.get_by_id(session, role_id)
        if role is None:
            return False

        await session.delete(role)
        logger.info(f"Deleted role: {role.identifier} (id={role.id})")
        return True
