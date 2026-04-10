# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_any_permission,
    ensure_skill_access,
    get_authorization_context,
)
from .deps_context import APIContext, get_api_context
from .schemas import (
    MemorySearchRequest,
    MemoryWriteRequest,
    SkillExecuteRequest,
    SkillExecuteResponse,
)


def register_skills_memory_routes(router: APIRouter) -> None:
    @router.get("/skills")
    async def list_skills(
        ctx: APIContext = Depends(get_api_context),
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> dict[str, Any]:
        ensure_any_permission(
            authz,
            ("skills.view", "skills.manage_permissions", "rbac.manage_permissions"),
            detail="Missing permission: skills.view or skills.manage_permissions",
        )

        executable_skills = ctx.skill_registry.snapshot_builtins()
        md_skills = ctx.skill_registry.md_snapshot()

        all_skills = []
        for s in executable_skills:
            all_skills.append(
                {
                    "name": s["name"],
                    "description": s["description"],
                    "category": s.get("category", "utility"),
                    "type": "executable",
                },
            )
        for s in md_skills:
            all_skills.append(
                {
                    "name": s["name"],
                    "description": s["description"],
                    "category": s.get("metadata", {}).get("category", "skill"),
                    "type": "markdown",
                },
            )

        return {"skills": all_skills}

    @router.post("/skills/execute", response_model=SkillExecuteResponse)
    async def execute_skill(
        request: SkillExecuteRequest,
        ctx: APIContext = Depends(get_api_context),
        authz: AuthorizationContext = Depends(get_authorization_context),
    ) -> SkillExecuteResponse:
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
    ) -> dict[str, Any]:
        if not ctx.memory_manager:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Memory system not configured",
            )
        return {"results": [], "query": request.query}

    @router.post("/memory/write")
    async def write_memory(
        request: MemoryWriteRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> dict[str, Any]:
        if not ctx.memory_manager:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Memory system not configured",
            )

        if request.memory_type == "daily":
            entry = await ctx.memory_manager.write_daily(
                request.content,
                source=request.source,
                tags=request.tags,
            )
        else:
            entry = await ctx.memory_manager.write_long_term(
                request.content,
                source=request.source,
                tags=request.tags,
                section=request.section,
            )

        return {
            "id": entry.id,
            "memory_type": request.memory_type,
            "timestamp": entry.timestamp.isoformat(),
        }
