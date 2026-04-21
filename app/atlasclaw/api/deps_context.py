# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request, status

from ..agent.routing import AgentRouter
from ..auth.models import UserInfo
from ..core.deps import SkillDeps
from ..core.security_guard import ensure_user_work_dir
from ..core.trace import enrich_trace_metadata
from ..core.user_provider_bindings import (
    ResolvedProviderInstanceRegistry,
    build_user_provider_instances,
)
from ..memory.manager import MemoryManager
from ..session.manager import SessionManager
from ..session.queue import SessionQueue
from ..session.router import SessionManagerRouter
from ..skills.registry import SkillRegistry
from ..hooks.runtime import HookRuntime
from ..hooks.runtime_sinks import ContextSink, MemorySink
from ..hooks.runtime_store import HookStateStore
from ..heartbeat.runtime import HeartbeatRuntime
from .sse import SSEManager
from .webhook_dispatch import WebhookDispatchManager


@dataclass
class APIContext:
    session_manager: SessionManager
    session_queue: SessionQueue
    skill_registry: SkillRegistry
    session_manager_router: Optional[SessionManagerRouter] = None
    memory_manager: Optional[MemoryManager] = None
    hook_state_store: Optional[HookStateStore] = None
    memory_sink: Optional[MemorySink] = None
    context_sink: Optional[ContextSink] = None
    hook_runtime: Optional[HookRuntime] = None
    heartbeat_runtime: Optional[HeartbeatRuntime] = None
    sse_manager: Optional[SSEManager] = None
    agent_runner: Optional[Any] = None
    agent_runners: dict[str, Any] | None = None
    agent_router: Optional[AgentRouter] = None
    service_provider_registry: Optional[Any] = None
    available_providers: dict[str, list[str]] = None
    provider_instances: dict[str, dict[str, dict[str, Any]]] = None
    webhook_manager: Optional[WebhookDispatchManager] = None
    active_runs: dict[str, dict[str, Any]] = None

    def __post_init__(self):
        if self.active_runs is None:
            self.active_runs = {}
        if self.sse_manager is None:
            self.sse_manager = SSEManager()
        if self.available_providers is None:
            self.available_providers = {}
        if self.provider_instances is None:
            self.provider_instances = {}
        if self.agent_runners is None:
            self.agent_runners = {}
        if self.agent_runner is None and self.agent_runners:
            self.agent_runner = self.agent_runners.get("main") or next(
                iter(self.agent_runners.values()),
                None,
            )
        if self.session_manager_router is None:
            self.session_manager_router = SessionManagerRouter.from_manager(self.session_manager)


_api_context: Optional[APIContext] = None


def set_api_context(ctx: APIContext) -> None:
    global _api_context
    _api_context = ctx


def get_api_context() -> APIContext:
    if _api_context is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API context not initialized",
        )
    return _api_context


def extract_atlas_token_from_request(request: Request, header_name: str, cookie_name: str) -> str:
    token = request.headers.get(header_name, "").strip()
    if token:
        return token

    token = request.headers.get("AtlasClaw-Authenticate", "").strip()
    if token:
        return token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    token = request.cookies.get(cookie_name, "").strip()
    if token:
        return token

    token = request.cookies.get("AtlasClaw-Authenticate", "").strip()
    if token:
        return token

    return ""


def is_admin_from_roles(roles: list[str]) -> bool:
    return any(str(role).lower() == "admin" for role in roles)


def resolve_workspace_path(request: Request, ctx: Optional[APIContext] = None) -> str:
    config = getattr(request.app.state, "config", None)
    workspace = getattr(config, "workspace", None) if config is not None else None
    configured_path = getattr(workspace, "path", None) if workspace is not None else None
    if configured_path:
        return str(Path(configured_path).resolve())

    resolved_ctx = ctx or get_api_context()
    session_workspace = getattr(resolved_ctx.session_manager, "workspace_path", None)
    if session_workspace:
        return str(Path(session_workspace).resolve())

    return str(Path(".").resolve())


def build_scoped_deps(
    ctx: APIContext,
    user_info: UserInfo,
    session_key: str,
    *,
    request_cookies: Optional[dict[str, str]] = None,
    provider_config: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> SkillDeps:
    scoped_session_mgr = ctx.session_manager_router.for_user(user_info.user_id)
    scoped_memory_mgr: Optional[MemoryManager] = None
    if ctx.memory_manager is not None:
        scoped_memory_mgr = MemoryManager(
            workspace=str(ctx.memory_manager._workspace),
            user_id=user_info.user_id,
        )

    tools_snapshot_builder = getattr(ctx.skill_registry, "tools_snapshot", None)
    if callable(tools_snapshot_builder):
        tools_snapshot = tools_snapshot_builder()
    else:
        tools_snapshot = ctx.skill_registry.snapshot()

    tool_groups_builder = getattr(ctx.skill_registry, "tool_groups_snapshot", None)
    if callable(tool_groups_builder):
        tool_groups_snapshot = tool_groups_builder()
    else:
        tool_groups_snapshot = {}

    # Request-scoped provider_config may already include runtime overrides
    # (for example DB-backed instance values). Preserve that shape before
    # layering in per-user provider bindings from the workspace.
    base_provider_instances = (
        provider_config
        if isinstance(provider_config, dict) and provider_config
        else (ctx.provider_instances or {})
    )
    merged_provider_instances = {
        provider_type: {
            instance_name: dict(instance_config)
            for instance_name, instance_config in instances.items()
        }
        for provider_type, instances in base_provider_instances.items()
        if isinstance(instances, dict)
    }
    user_provider_instances = build_user_provider_instances(
        user_info.user_id,
        workspace_path=str(scoped_session_mgr.workspace_path),
    )
    for provider_type, instances in user_provider_instances.items():
        provider_bucket = merged_provider_instances.setdefault(provider_type, {})
        for instance_name, instance_config in instances.items():
            provider_bucket[instance_name] = dict(instance_config)

    provider_registry = ResolvedProviderInstanceRegistry(merged_provider_instances)
    available_providers = provider_registry.get_available_providers_summary()

    deps_extra = {
        "_service_provider_registry": provider_registry,
        "available_providers": available_providers,
        "provider_instances": merged_provider_instances,
        "provider_config": merged_provider_instances,
        "tools_snapshot": tools_snapshot,
        "tools_snapshot_authoritative": False,
        "tool_groups_snapshot": tool_groups_snapshot,
        "skills_snapshot": ctx.skill_registry.snapshot_builtins(),
        "md_skills_snapshot": ctx.skill_registry.md_snapshot(),
        "work_dir": str(
            ensure_user_work_dir(
                str(scoped_session_mgr.workspace_path),
                user_info.user_id,
            ),
        ),
    }
    if extra:
        deps_extra.update(extra)
    deps_extra = enrich_trace_metadata(session_key, extra=deps_extra)

    return SkillDeps(
        user_info=user_info,
        session_key=session_key,
        session_manager=scoped_session_mgr,
        memory_manager=scoped_memory_mgr,
        cookies=request_cookies or {},
        extra=deps_extra,
    )
