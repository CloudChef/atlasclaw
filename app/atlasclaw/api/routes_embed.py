# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider-neutral REST API for the context-aware embed v1 contract."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.atlasclaw.auth.guards import get_optional_authorization_context
from app.atlasclaw.auth.models import ANONYMOUS_USER, UserInfo
from app.atlasclaw.core.embed.context_service import (
    EmbedContextResolution,
    EmbedContextService,
    EmbedPermissionError,
)
from app.atlasclaw.core.embed.route_matcher import normalize_host_path
from app.atlasclaw.session.context import ChatType, SessionKey

from .deps_context import APIContext, get_api_context

_PROTOCOL = "atlasclaw-embed/v1"
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,256}$")


class StrictRequest(BaseModel):
    """Reject fields from later protocol versions at the API boundary."""

    model_config = ConfigDict(extra="forbid")


class EmbedBootstrapRequest(StrictRequest):
    """Bootstrap one surface against the configured default Provider."""

    surface: Literal["floating", "menu"]
    nonce: Optional[str] = Field(default=None, max_length=256)
    candidate_session_key: Optional[str] = Field(default=None, max_length=2048)


class EmbedBootstrapResponse(BaseModel):
    """Bootstrap data consumed only by AtlasClaw-owned frontend code."""

    protocol: Literal["atlasclaw-embed/v1"] = _PROTOCOL
    surface: Literal["floating", "menu"]
    agent_id: str
    session_scope: str
    active_session_key: Optional[str] = None


class EmbedContextResolveRequest(StrictRequest):
    """Minimal v1 context request based on Angular's normalized path."""

    surface_id: str = Field(min_length=22, max_length=256, pattern=_NONCE.pattern)
    generation: int = Field(ge=0)
    path: str = Field(min_length=1, max_length=512)


class EmbedObjectResponse(BaseModel):
    """Minimal object projection safe for the floating context bar."""

    type: str
    id: str
    name: str = ""
    state: str = ""


class EmbedSkillResponse(BaseModel):
    """Matched existing Skill offered as the page's default routing hint."""

    ref: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)


class EmbedContextResolveResponse(BaseModel):
    """Resolved immutable context reference and safe UI projection."""

    generation: int
    status: Literal["resolved", "unsupported", "unavailable"]
    context_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    object: Optional[EmbedObjectResponse] = None
    skill: Optional[EmbedSkillResponse] = None
    object_actions: list[dict[str, Any]] = Field(default_factory=list)


def _build_context_resolve_response(
    generation: int,
    resolution: EmbedContextResolution,
) -> EmbedContextResolveResponse:
    """Serialize one resolution without collapsing matched failures into unsupported paths."""
    snapshot = resolution.snapshot
    if snapshot is None:
        return EmbedContextResolveResponse(
            generation=generation,
            status="unavailable" if resolution.matched else "unsupported",
        )
    return EmbedContextResolveResponse(
        generation=generation,
        status="resolved",
        context_id=snapshot.context_id,
        expires_at=snapshot.expires_at,
        object=EmbedObjectResponse(**snapshot.object.model_dump(exclude={"attributes"})),
        skill=EmbedSkillResponse(
            ref=snapshot.skill_ref,
            name=snapshot.skill_name,
            description=snapshot.skill_description,
        ),
        object_actions=snapshot.object_actions,
    )


def _current_user(request_obj: Request) -> UserInfo:
    user = getattr(request_obj.state, "user_info", ANONYMOUS_USER)
    if user.user_id == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def _integration_or_404(ctx: APIContext) -> Any:
    registry = ctx.embed_integration_registry
    integration = registry.get() if registry is not None else None
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Embed integration not found")
    return integration


async def _validated_chat_active_session(
    ctx: APIContext,
    *,
    user: UserInfo,
    candidate: str | None,
    agent_id: str,
    session_scope: str,
) -> str | None:
    """Validate a localStorage Chat Active Session candidate using current authentication."""
    if not candidate:
        return None
    parsed = SessionKey.from_string(candidate)
    if not embed_session_matches_scope(
        parsed,
        user=user,
        agent_id=agent_id,
        session_scope=session_scope,
    ):
        return None
    manager = ctx.session_manager_router.for_user(user.user_id)
    session = await manager.get_session(candidate)
    return candidate if session is not None else None


def embed_session_matches_scope(
    parsed: SessionKey,
    *,
    user: UserInfo,
    agent_id: str,
    session_scope: str,
) -> bool:
    """Return whether a SessionKey is the exact authenticated Web DM scope."""
    return not (
        parsed.user_id != user.user_id
        or parsed.agent_id != agent_id
        or parsed.account_id != session_scope
        or parsed.channel != "web"
        or parsed.chat_type is not ChatType.DM
        or parsed.peer_id != user.user_id
        or not parsed.thread_id
    )


def register_embed_routes(router: APIRouter) -> None:
    """Register embed bootstrap and context-resolution routes."""

    @router.post("/embed/bootstrap", response_model=EmbedBootstrapResponse)
    async def bootstrap_embed(
        request_obj: Request,
        request: EmbedBootstrapRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> EmbedBootstrapResponse:
        """Validate one surface and optionally resume a scoped Chat Active Session."""
        user = _current_user(request_obj)
        integration = _integration_or_404(ctx)
        if request.surface == "floating":
            if not request.nonce or _NONCE.fullmatch(request.nonce) is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid embed nonce",
                )
        elif request.nonce is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Menu bootstrap does not accept a nonce",
            )
        active_session_key = await _validated_chat_active_session(
            ctx,
            user=user,
            candidate=request.candidate_session_key,
            agent_id=integration.agent_id,
            session_scope=integration.session_scope,
        )
        return EmbedBootstrapResponse(
            surface=request.surface,
            agent_id=integration.agent_id,
            session_scope=integration.session_scope,
            active_session_key=active_session_key,
        )

    @router.post("/embed/context/resolve", response_model=EmbedContextResolveResponse)
    async def resolve_embed_context(
        request_obj: Request,
        request: EmbedContextResolveRequest,
        ctx: APIContext = Depends(get_api_context),
    ) -> EmbedContextResolveResponse:
        """Resolve a normalized Host path into a user-bound context snapshot."""
        user = _current_user(request_obj)
        integration = _integration_or_404(ctx)
        try:
            normalized_path = normalize_host_path(request.path)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        # PAGE_CHANGED is authoritative even when the new path is unsupported
        # or its object cannot be resolved. Marking the generation before any
        # Provider work prevents an older page scope from being restored with
        # stale context fields; a successful resolve replaces the
        # empty marker with its snapshot in EmbedContextSnapshotStore.put().
        generation_registered = ctx.embed_context_store.mark_latest(
            owner_user_id=user.user_id,
            surface_id=request.surface_id,
            generation=request.generation,
            context_id=None,
            max_contexts_per_user=integration.max_contexts_per_user,
            state_ttl_seconds=integration.context_ttl_seconds,
        )
        if not generation_registered:
            return _build_context_resolve_response(
                request.generation,
                EmbedContextResolution(matched=True),
            )
        authz = await get_optional_authorization_context(request_obj)
        service = EmbedContextService(
            ctx,
            ctx.embed_integration_registry,
            ctx.embed_context_store,
        )
        try:
            resolution = await service.resolve(
                surface_id=request.surface_id,
                generation=request.generation,
                path=normalized_path,
                user_info=user,
                request_cookies=dict(request_obj.cookies),
                authz=authz,
            )
        except EmbedPermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return _build_context_resolve_response(request.generation, resolution)
