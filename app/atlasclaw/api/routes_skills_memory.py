# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.atlasclaw.bootstrap.startup_helpers import derive_provider_namespace
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_any_permission,
    ensure_skill_access,
    get_current_user,
    get_optional_authorization_context,
    get_authorization_context,
)
from app.atlasclaw.core.config import get_config, get_config_path
from app.atlasclaw.memory.access import has_memory_access_from_authz, memory_config_enabled
from app.atlasclaw.memory.formatting import normalize_memory_search_item
from app.atlasclaw.memory.manager import MemoryManager, MemoryType
from app.atlasclaw.skills.frontmatter import parse_frontmatter
from app.atlasclaw.skills.registry import validate_skill_name
from app.atlasclaw.skills.permission_service import skill_permission_service
from .deps_context import APIContext, get_api_context
from .schemas import (
    MemorySearchRequest,
    MemorySearchResult,
    MemoryWriteRequest,
    SkillExecuteRequest,
    SkillExecuteResponse,
)


def _resolve_config_relative_path(config_value: str) -> Path:
    """Resolve a path from config relative to the active config file."""
    config_path = get_config_path()
    config_root = config_path.parent if config_path is not None else Path.cwd()
    return (config_root / config_value).resolve()


def _iter_skill_markdown_files(base_path: Path) -> list[tuple[Path, bool]]:
    """Return markdown skills under a root with their directory-skill marker."""
    if not base_path.exists():
        return []

    files: list[tuple[Path, bool]] = []
    files.extend((skill_file, True) for skill_file in sorted(base_path.glob("*/SKILL.md")))
    files.extend(
        (md_file, False)
        for md_file in sorted(base_path.glob("*.md"))
        if not md_file.name.startswith("_")
    )
    return files


def _discover_md_skill_catalog(
    base_path: Path,
    *,
    location: str,
    provider: str = "",
    max_file_bytes: int = 262144,
) -> list[dict[str, Any]]:
    """Read markdown skill metadata for UI catalog display without registering skills."""
    discovered: list[dict[str, Any]] = []
    for file_path, is_directory_skill in _iter_skill_markdown_files(base_path):
        try:
            if file_path.stat().st_size > max_file_bytes:
                continue
            raw = file_path.read_text(encoding="utf-8")
        except OSError:
            continue

        fm = parse_frontmatter(raw)
        if is_directory_skill:
            name = str(fm.metadata.get("name", file_path.parent.name) or "").strip()
            parent_dir_name = file_path.parent.name
        else:
            name = str(fm.metadata.get("name", file_path.stem) or "").strip()
            parent_dir_name = None
        if validate_skill_name(name, parent_dir_name=parent_dir_name):
            continue

        description = str(fm.metadata.get("description", "") or "").strip()
        if not description:
            continue

        metadata = {
            key: value
            for key, value in fm.metadata.items()
            if key not in ("name", "description")
        }
        provider_name = str(metadata.get("provider_type", "") or provider or "").strip()
        qualified_name = f"{provider_name}:{name}" if provider_name else name
        discovered.append(
            {
                "name": name,
                "description": description,
                "provider": provider_name,
                "qualified_name": qualified_name,
                "file_path": str(file_path.resolve()),
                "location": location,
                "metadata": metadata,
            }
        )

    return discovered


def _build_md_skill_catalog(ctx: APIContext) -> list[dict[str, Any]]:
    """Build full markdown skill catalog while keeping runtime-loaded state explicit."""
    config = get_config()
    providers_root = _resolve_config_relative_path(config.providers_root)
    skills_root = _resolve_config_relative_path(config.skills_root)
    max_file_bytes = int(getattr(config.skills, "md_skills_max_file_bytes", 262144) or 262144)

    catalog_by_qualified_name: dict[str, dict[str, Any]] = {}
    for entry in ctx.skill_registry.md_snapshot():
        key = str(entry.get("qualified_name") or entry.get("name") or "").strip()
        if not key:
            continue
        catalog_by_qualified_name[key] = {
            **entry,
            "runtime_enabled": True,
        }

    if providers_root.exists():
        for provider_path in sorted(providers_root.iterdir(), key=lambda item: item.name.lower()):
            if not provider_path.is_dir() or provider_path.name.startswith(("_", ".")):
                continue
            provider_skills = provider_path / "skills"
            if not provider_skills.exists():
                continue
            provider_namespace = derive_provider_namespace(provider_path.name)
            for entry in _discover_md_skill_catalog(
                provider_skills,
                location="provider",
                provider=provider_namespace,
                max_file_bytes=max_file_bytes,
            ):
                key = entry["qualified_name"]
                if key in catalog_by_qualified_name:
                    continue
                catalog_by_qualified_name[key] = {
                    **entry,
                    "runtime_enabled": False,
                }

    for entry in _discover_md_skill_catalog(
        skills_root,
        location="skills-root",
        max_file_bytes=max_file_bytes,
    ):
        key = entry["qualified_name"]
        if key in catalog_by_qualified_name:
            continue
        catalog_by_qualified_name[key] = {
            **entry,
            "runtime_enabled": False,
        }

    return list(catalog_by_qualified_name.values())


def _ensure_memory_configured(ctx: APIContext) -> MemoryManager:
    """Return the configured base memory manager or raise an HTTP error."""
    if not memory_config_enabled() or ctx.memory_manager is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Memory system not configured",
        )
    return ctx.memory_manager


def _ensure_memory_authorized(authz: AuthorizationContext | None) -> None:
    """Raise 403 when RBAC is active and the user lacks memory tool access."""
    if authz is None:
        return
    if has_memory_access_from_authz(authz):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing permission to use memory skills",
    )


def _scoped_memory_manager(
    base_manager: MemoryManager,
    authz: AuthorizationContext | None,
    current_user: UserInfo,
) -> MemoryManager:
    """Build a memory manager scoped to the authenticated runtime user."""
    user_id = str(current_user.user_id or "default")
    if authz is not None:
        user_id = str(getattr(authz.user, "user_id", "") or user_id or "default")
    return base_manager.for_user(user_id)


def _memory_search_result_payload(result: Any, *, query: str) -> dict[str, Any]:
    """Serialize memory search output with the API schema and citation metadata."""
    normalized = normalize_memory_search_item(result, query=query)
    timestamp = normalized.get("timestamp")
    if not isinstance(timestamp, datetime):
        timestamp = datetime.now(timezone.utc)

    payload = MemorySearchResult(
        id=normalized["id"],
        content=normalized["content"],
        score=normalized["score"],
        source=normalized["source"],
        timestamp=timestamp,
        highlights=normalized["highlights"],
    ).model_dump()
    payload.update(
        {
            "snippet": normalized["snippet"],
            "path": normalized["path"],
            "start_line": normalized["start_line"],
            "end_line": normalized["end_line"],
            "citation": normalized["citation"],
            "query": normalized["query"],
        }
    )
    return payload


def register_skills_memory_routes(router: APIRouter) -> None:
    """Attach skill catalog/execution and permission-gated memory API routes."""

    @router.get("/skills")
    async def list_skills(
        include_metadata: bool = False,
        ctx: APIContext = Depends(get_api_context),
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> dict[str, Any]:
        """Return the role-facing skill catalog visible to administrators."""
        ensure_any_permission(
            authz,
            ("skills.view", "skills.manage_permissions", "roles.manage_permissions"),
            detail="Missing permission: skills.view or skills.manage_permissions",
        )

        tools_snapshot_builder = getattr(ctx.skill_registry, "tools_snapshot", None)
        executable_skills = (
            ctx.skill_registry.tools_snapshot()
            if callable(tools_snapshot_builder)
            else ctx.skill_registry.snapshot_builtins()
        )
        md_skills = _build_md_skill_catalog(ctx)

        all_skills = skill_permission_service.build_role_skill_catalog(
            tools_snapshot=executable_skills,
            md_skills=md_skills,
            include_metadata=include_metadata,
        )

        return {"skills": all_skills}

    @router.post("/skills/execute", response_model=SkillExecuteResponse)
    async def execute_skill(
        request: SkillExecuteRequest,
        ctx: APIContext = Depends(get_api_context),
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> SkillExecuteResponse:
        """Execute an authorized skill through the registry API."""
        ensure_skill_access(
            authz,
            request.skill_name,
            detail=f"Missing permission to execute skill: {request.skill_name}",
        )

        import time

        start = time.monotonic()
        try:
            result = await ctx.skill_registry.execute(request.skill_name, json.dumps(request.args))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Skill execution failed: {str(e)}",
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return SkillExecuteResponse(
            skill_name=request.skill_name,
            result=result,
            duration_ms=duration_ms,
        )

    @router.post("/memory/search")
    async def search_memory(
        request: MemorySearchRequest,
        ctx: APIContext = Depends(get_api_context),
        current_user: UserInfo = Depends(get_current_user),
        authz: AuthorizationContext | None = Depends(get_optional_authorization_context),
    ) -> dict[str, Any]:
        """Search the authenticated user's memory and return citation metadata."""
        base_memory_manager = _ensure_memory_configured(ctx)
        _ensure_memory_authorized(authz)
        memory_manager = _scoped_memory_manager(base_memory_manager, authz, current_user)
        limit = request.top_k if isinstance(request.top_k, int) and request.top_k > 0 else 10
        results = await memory_manager.search(
            request.query,
            limit=limit,
            apply_recency=request.apply_recency,
        )
        return {
            "results": [
                _memory_search_result_payload(item, query=request.query)
                for item in results
            ],
            "query": request.query,
        }

    @router.post("/memory/write")
    async def write_memory(
        request: MemoryWriteRequest,
        ctx: APIContext = Depends(get_api_context),
        current_user: UserInfo = Depends(get_current_user),
        authz: AuthorizationContext | None = Depends(get_optional_authorization_context),
    ) -> dict[str, Any]:
        """Write long-term memory for the authenticated user only."""
        base_memory_manager = _ensure_memory_configured(ctx)
        _ensure_memory_authorized(authz)
        memory_manager = _scoped_memory_manager(base_memory_manager, authz, current_user)

        if request.memory_type != MemoryType.LONG_TERM.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid memory_type: {request.memory_type}",
            )
        entry = await memory_manager.write_long_term(
            request.content,
            source=request.source,
            tags=request.tags,
            section=request.section,
        )

        return {
            "id": entry.id,
            "memory_type": MemoryType.LONG_TERM.value,
            "timestamp": entry.timestamp.isoformat(),
        }
