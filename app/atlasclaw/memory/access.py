# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Permission helpers for user-scoped memory runtime access."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.atlasclaw.auth.guards import AuthorizationContext, has_permission, has_skill_access
from app.atlasclaw.core.config import get_config
from app.atlasclaw.core.config_schema import DEFAULT_DIRECT_MEMORY_CHAT_TYPES
from app.atlasclaw.session.context import ChatType, SessionKey
from app.atlasclaw.skills.permission_service import skill_permission_service
from app.atlasclaw.tools.catalog import GROUP_MEMORY, GROUP_TOOLS


MEMORY_TOOL_NAMES: tuple[str, ...] = tuple(GROUP_TOOLS[GROUP_MEMORY])


def memory_config_enabled() -> bool:
    """Return whether the workspace memory feature is globally enabled."""
    try:
        return bool(getattr(get_config().memory, "enabled", True))
    except Exception:
        return True


def _extract_user_skill_permissions(extra: Any) -> list[dict[str, Any]] | None:
    """Read request-scoped skill permissions from deps.extra-style payloads."""
    if not isinstance(extra, dict):
        return None
    direct = extra.get("_user_skill_permissions")
    if isinstance(direct, list):
        return direct
    context = extra.get("context")
    if isinstance(context, dict):
        nested = context.get("_user_skill_permissions")
        if isinstance(nested, list):
            return nested
    return None


def has_memory_access_from_extra(extra: Any) -> bool:
    """Return whether request-scoped RBAC permits memory tools.

    A missing permission list means RBAC is not active for the caller, matching
    the existing ``build_scoped_deps`` behavior for no-database local runs.
    """
    skill_permissions = _extract_user_skill_permissions(extra)
    if skill_permissions is None:
        return True
    return any(
        skill_permission_service.is_skill_enabled(skill_permissions, tool_name)
        for tool_name in MEMORY_TOOL_NAMES
    )


def has_memory_access_from_authz(authz: AuthorizationContext) -> bool:
    """Return whether the resolved authorization context can use memory tools."""
    if has_permission(authz, "skills.allow_all"):
        return True
    return any(has_skill_access(authz, tool_name) for tool_name in MEMORY_TOOL_NAMES)


def memory_manager_from_deps(deps: Any) -> Any:
    """Return a request-scoped MemoryManager from SkillDeps-style objects."""
    memory_manager = getattr(deps, "memory_manager", None)
    if memory_manager is not None:
        return memory_manager
    extra = getattr(deps, "extra", {})
    if isinstance(extra, dict):
        return extra.get("memory_manager")
    return None


def memory_available_for_deps(deps: Any) -> bool:
    """Return whether this agent run may read or write user memory."""
    if not memory_config_enabled():
        return False
    if memory_manager_from_deps(deps) is None:
        return False
    return has_memory_access_from_extra(getattr(deps, "extra", {}))


def normalize_memory_chat_type(value: Any) -> str:
    """Normalize chat-type aliases used by memory features."""
    normalized = str(value or "").strip().lower()
    if normalized == "direct":
        return ChatType.DM.value
    return normalized


def memory_chat_type_allowed(
    session_key: str,
    allowed_chat_types: Iterable[Any] | None,
) -> bool:
    """Return whether a session key may run implicit memory behavior."""
    allowed = {
        normalize_memory_chat_type(item)
        for item in (allowed_chat_types or DEFAULT_DIRECT_MEMORY_CHAT_TYPES)
        if str(item or "").strip()
    }
    if not allowed:
        return False
    try:
        chat_type = SessionKey.from_string(session_key).chat_type.value
    except Exception:
        chat_type = ChatType.DM.value
    return normalize_memory_chat_type(chat_type) in allowed
