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
    build_resolved_provider_instances,
    build_user_provider_instances,
    normalize_provider_runtime_context,
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

_ATLASCLAW_AUTH_COOKIE_NAMES = frozenset({"atlasclaw-authenticate"})


def _extract_provider_cookie_token(request_cookies: Optional[dict[str, str]]) -> str:
    """Return an external browser auth cookie without exposing AtlasClaw's session."""
    if not isinstance(request_cookies, dict):
        return ""

    for cookie_name, cookie_value in request_cookies.items():
        normalized_name = str(cookie_name or "").strip().lower()
        token = str(cookie_value or "").strip()
        if not token or normalized_name in _ATLASCLAW_AUTH_COOKIE_NAMES:
            continue
        if "authenticate" in normalized_name:
            return token
    return ""


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


def _normalize_skill_id(value: str) -> str:
    return str(value or "").strip().lower()


def _skill_id_matches(candidate: str, target: str) -> bool:
    nc = _normalize_skill_id(candidate)
    nt = _normalize_skill_id(target)
    if not nc or not nt:
        return False
    return nc == nt or nc.split(":")[-1] == nt.split(":")[-1]


def _is_skill_enabled(skill_permissions: list[dict], skill_name: str) -> bool:
    """Check if a skill is enabled in the user's role permissions.

    Returns True if:
    - a matching entry has both authorized=True and enabled=True
    Returns False if:
    - skill_permissions is empty (no grants = deny all)
    - a matching entry exists but is not authorized+enabled
    - no matching entry found (skill not in permissions = not allowed)
    """
    for entry in skill_permissions:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("skill_id") or entry.get("skill_name") or ""
        sname = entry.get("skill_name") or sid
        if _skill_id_matches(sid, skill_name) or _skill_id_matches(sname, skill_name):
            return bool(entry.get("authorized")) and bool(entry.get("enabled"))
    return False


def _filter_snapshot_by_permissions(
    tools_snapshot: list[dict],
    md_skills_snapshot: list[dict],
    skill_permissions: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Filter tools and md_skills snapshots based on user role permissions.

    When skill_permissions is empty (no grants), returns empty lists (deny-all).
    """

    filtered_tools = []
    for tool in tools_snapshot:
        # Check by qualified_skill_name, then skill_name, then tool name
        skill_ref = (
            tool.get("qualified_skill_name")
            or tool.get("skill_name")
            or tool.get("name", "")
        )
        if _is_skill_enabled(skill_permissions, skill_ref):
            filtered_tools.append(tool)

    filtered_md = []
    for md in md_skills_snapshot:
        skill_ref = md.get("qualified_name") or md.get("name", "")
        if _is_skill_enabled(skill_permissions, skill_ref):
            filtered_md.append(md)

    return filtered_tools, filtered_md


def build_scoped_deps(
    ctx: APIContext,
    user_info: UserInfo,
    session_key: str,
    *,
    request_cookies: Optional[dict[str, str]] = None,
    provider_config: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> SkillDeps:
    runtime_context_source = (
        dict(getattr(user_info, "extra", {}))
        if isinstance(getattr(user_info, "extra", {}), dict)
        else {}
    )
    # Bridge request-scoped provider cookies into the provider resolver for
    # this request only; user settings continue to store only user-owned values.
    request_cookie_token = str(
        runtime_context_source.get("provider_cookie_token", "") or ""
    ).strip()
    if not request_cookie_token:
        request_cookie_token = _extract_provider_cookie_token(request_cookies)
    if not request_cookie_token and str(getattr(user_info, "auth_type", "") or "").strip() == "cookie":
        request_cookie_token = str(getattr(user_info, "raw_token", "") or "").strip()
    if request_cookie_token:
        runtime_context_source["provider_cookie_available"] = True
        runtime_context_source["provider_cookie_token"] = request_cookie_token

    runtime_context = normalize_provider_runtime_context(runtime_context_source)
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
    resolved_provider_instances = build_resolved_provider_instances(
        base_provider_instances,
        runtime_context=runtime_context,
    )
    user_provider_instances = build_user_provider_instances(
        user_info.user_id,
        workspace_path=str(scoped_session_mgr.workspace_path),
        runtime_context=runtime_context,
        provider_templates=base_provider_instances,
    )
    for provider_type, instances in user_provider_instances.items():
        provider_bucket = resolved_provider_instances.setdefault(provider_type, {})
        for instance_name, instance_config in instances.items():
            provider_bucket[instance_name] = dict(instance_config)

    provider_registry = ResolvedProviderInstanceRegistry(resolved_provider_instances)
    available_providers = provider_registry.get_available_providers_summary()

    # Apply user skill permission filtering if provided via request context.
    # Sentinel: None = no RBAC (no-DB mode), list = RBAC resolved.
    _extra = extra or {}
    user_skill_permissions = _extra.get("_user_skill_permissions")
    if user_skill_permissions is None:
        _ctx = _extra.get("context")
        if isinstance(_ctx, dict):
            user_skill_permissions = _ctx.get("_user_skill_permissions")

    # When RBAC is active (user_skill_permissions is a list, possibly empty),
    # filter tools and md_skills.  Empty list = deny-all.
    # When RBAC is not active (None), skip filtering entirely.
    _rbac_active = isinstance(user_skill_permissions, list)
    disabled_tool_names: set[str] = set()
    if _rbac_active:
        _all_md = ctx.skill_registry.md_snapshot()
        tools_snapshot, md_skills_snapshot = _filter_snapshot_by_permissions(
            tools_snapshot,
            _all_md,
            user_skill_permissions,
        )
        # Collect ALL disabled skill IDs (both md and executable)
        _disabled_skill_ids: set[str] = set()
        for sp_entry in user_skill_permissions:
            if isinstance(sp_entry, dict) and not (sp_entry.get("enabled") and sp_entry.get("authorized")):
                _sid = _normalize_skill_id(sp_entry.get("skill_id") or sp_entry.get("skill_name") or "")
                if _sid:
                    _disabled_skill_ids.add(_sid)

        # 1. Extract handler tool names from disabled md_skills metadata
        for md_entry in _all_md:
            _qname = _normalize_skill_id(md_entry.get("qualified_name") or md_entry.get("name") or "")
            _bare = _qname.split(":")[-1] if _qname else ""
            if _bare in _disabled_skill_ids or _qname in _disabled_skill_ids:
                _md_meta = md_entry.get("metadata") or {}
                for key, value in _md_meta.items():
                    _k = str(key or "").strip()
                    if _k == "tool_name" or (_k.startswith("tool_") and _k.endswith("_name")):
                        _tname = str(value or "").strip()
                        if _tname:
                            disabled_tool_names.add(_tname)

        # 2. Also get tool names from registry._md_skill_tools mapping
        #    This catches executable skills registered from md SKILL.md entrypoints
        _md_skill_tools_map = getattr(ctx.skill_registry, "_md_skill_tools", {})
        if isinstance(_md_skill_tools_map, dict):
            for qual_name, tool_names_set in _md_skill_tools_map.items():
                _qnorm = _normalize_skill_id(qual_name)
                _bare = _qnorm.split(":")[-1] if _qnorm else ""
                if _bare in _disabled_skill_ids or _qnorm in _disabled_skill_ids:
                    for tn in (tool_names_set or set()):
                        _tname = str(tn or "").strip()
                        if _tname:
                            disabled_tool_names.add(_tname)

        print(
            f"[SkillFilter] perms={len(user_skill_permissions)} disabled_skills={sorted(_disabled_skill_ids)}"
            f" disabled_tools={sorted(disabled_tool_names)} tools_after={len(tools_snapshot)} md_after={len(md_skills_snapshot)}"
        )
    else:
        md_skills_snapshot = ctx.skill_registry.md_snapshot()

    deps_extra = {
        **runtime_context,
        "_service_provider_registry": provider_registry,
        "available_providers": available_providers,
        "provider_instances": resolved_provider_instances,
        "provider_config": resolved_provider_instances,
        "tools_snapshot": tools_snapshot,
        "tools_snapshot_authoritative": _rbac_active,
        "tool_groups_snapshot": tool_groups_snapshot,
        "skills_snapshot": ctx.skill_registry.snapshot_builtins(),
        "md_skills_snapshot": md_skills_snapshot,
        "work_dir": str(
            ensure_user_work_dir(
                str(scoped_session_mgr.workspace_path),
                user_info.user_id,
            ),
        ),
    }
    # Expose disabled tool names AND disabled skill IDs for runner-level filtering.
    # The runner's collect_tools_snapshot supplements from the agent object,
    # so the runner needs both sets to exclude disabled-skill handler tools.
    if disabled_tool_names:
        deps_extra["_disabled_tool_names"] = disabled_tool_names
    if isinstance(user_skill_permissions, list):
        # Also pass disabled skill IDs so the runner can filter by skill_name field
        _d_ids = set()
        for sp_entry in user_skill_permissions:
            if isinstance(sp_entry, dict) and not (sp_entry.get("enabled") and sp_entry.get("authorized")):
                _sid = _normalize_skill_id(sp_entry.get("skill_id") or sp_entry.get("skill_name") or "")
                if _sid:
                    _d_ids.add(_sid)
        if _d_ids:
            deps_extra["_disabled_skill_ids"] = _d_ids
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
