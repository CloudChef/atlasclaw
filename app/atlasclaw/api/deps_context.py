# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request, status

from ..agent.routing import AgentRouter
from ..agent.selected_capability import (
    SELECTED_CAPABILITY_KEY,
    get_selected_capability_from_extra,
    selected_capability_provider_instance_ref,
)
from ..auth.guards import has_provider_instance_access
from ..auth.models import UserInfo
from ..core.config import get_config
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

_DEFAULT_ATLASCLAW_AUTH_COOKIE_NAME = "AtlasClaw-Authenticate"
_DEFAULT_HOST_COOKIE_AUTH_COOKIE_NAME = "CloudChef-Authenticate"


def _normalize_cookie_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _get_field(source: Any, field_name: str) -> Any:
    if isinstance(source, dict):
        return source.get(field_name)
    return getattr(source, field_name, None)


def _get_configured_auth_cookie_name(section_name: str) -> str:
    try:
        auth_config = _get_field(get_config(), "auth")
    except Exception:
        return ""

    section_config = _get_field(auth_config, section_name)
    if section_config is None:
        return ""

    expanded_builder = getattr(section_config, "expanded", None)
    if callable(expanded_builder):
        try:
            section_config = expanded_builder()
        except Exception:
            pass

    return str(_get_field(section_config, "cookie_name") or "").strip()


def _get_cookie_name_candidates(*values: Any) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_name = _normalize_cookie_name(value)
        if normalized_name and normalized_name not in seen:
            names.append(normalized_name)
            seen.add(normalized_name)
    return tuple(names)


def _get_atlas_auth_cookie_names() -> tuple[str, ...]:
    # Mirror AuthMiddleware._extract_atlas_token(): the configured JWT cookie
    # name and the default fallback are both AtlasClaw session cookies.
    return _get_cookie_name_candidates(
        _get_configured_auth_cookie_name("jwt"),
        _DEFAULT_ATLASCLAW_AUTH_COOKIE_NAME,
    )


def _get_host_cookie_auth_cookie_names() -> tuple[str, ...]:
    configured_name = _get_configured_auth_cookie_name("host_cookie")
    return _get_cookie_name_candidates(configured_name or _DEFAULT_HOST_COOKIE_AUTH_COOKIE_NAME)


def _extract_provider_cookie_token(request_cookies: Optional[dict[str, str]]) -> str:
    """Return an external browser auth cookie without exposing AtlasClaw's session."""
    if not isinstance(request_cookies, dict):
        return ""

    atlas_auth_cookie_names = set(_get_atlas_auth_cookie_names())
    request_cookie_tokens = {
        _normalize_cookie_name(cookie_name): str(cookie_value or "").strip()
        for cookie_name, cookie_value in request_cookies.items()
    }
    for normalized_name in _get_host_cookie_auth_cookie_names():
        if normalized_name in atlas_auth_cookie_names:
            continue
        token = request_cookie_tokens.get(normalized_name, "")
        if token:
            return token
    return ""


def _get_provider_permissions_from_extra(extra: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Read request-scoped provider rules from either direct or nested context."""
    raw_permissions = extra.get("_provider_permissions")
    if raw_permissions is None:
        context = extra.get("context")
        if isinstance(context, dict):
            raw_permissions = context.get("_provider_permissions")

    return raw_permissions if isinstance(raw_permissions, list) else None


def _filter_provider_instances_by_permissions(
    provider_instances: dict[str, dict[str, dict[str, Any]]],
    provider_permissions: list[dict[str, Any]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Filter instances using the same default-allow rule as authorization guards."""
    if provider_permissions is None:
        return provider_instances

    from ..auth.guards import AuthorizationContext

    authz = AuthorizationContext(
        user=UserInfo(user_id="provider-permission-filter"),
        permissions={
            "providers": {
                "provider_permissions": provider_permissions,
            },
        },
    )
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


def _visible_provider_types(
    provider_instances: dict[str, dict[str, dict[str, Any]]],
) -> set[str]:
    return {
        str(provider_type or "").strip().lower()
        for provider_type, instances in (provider_instances or {}).items()
        if str(provider_type or "").strip() and isinstance(instances, dict) and instances
    }


def _provider_type_from_tool_snapshot(tool: dict[str, Any]) -> str:
    provider_type = str(tool.get("provider_type", "") or "").strip().lower()
    if provider_type:
        return provider_type

    capability_class = str(tool.get("capability_class", "") or "").strip().lower()
    if capability_class.startswith("provider:"):
        inferred_provider = capability_class.split(":", 1)[1].strip()
        if inferred_provider and inferred_provider != "generic":
            return inferred_provider
    return ""


def _provider_type_from_md_skill_snapshot(skill: dict[str, Any]) -> str:
    metadata = skill.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return str(
        metadata.get("provider_type", "")
        or skill.get("provider_type", "")
        or skill.get("provider", "")
        or ""
    ).strip().lower()


def _filter_provider_bound_snapshots(
    tools_snapshot: list[dict[str, Any]],
    md_skills_snapshot: list[dict[str, Any]],
    provider_instances: dict[str, dict[str, dict[str, Any]]],
    *,
    enforce: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hide provider-bound tools/skills when no visible instance remains.

    Provider permissions are instance-scoped, but provider tools are routed at
    provider type level. If every instance for a provider type is hidden, the
    corresponding tools must leave the request toolset as well; otherwise
    routing caches can keep treating a denied provider as available.
    """
    visible_providers = _visible_provider_types(provider_instances)
    if not visible_providers and not enforce:
        return tools_snapshot, md_skills_snapshot
    if not tools_snapshot and not md_skills_snapshot:
        return tools_snapshot, md_skills_snapshot

    filtered_tools: list[dict[str, Any]] = []
    for tool in tools_snapshot or []:
        if not isinstance(tool, dict):
            continue
        provider_type = _provider_type_from_tool_snapshot(tool)
        if provider_type and provider_type not in visible_providers:
            continue
        filtered_tools.append(tool)

    filtered_md_skills: list[dict[str, Any]] = []
    for skill in md_skills_snapshot or []:
        if not isinstance(skill, dict):
            continue
        provider_type = _provider_type_from_md_skill_snapshot(skill)
        if provider_type and provider_type not in visible_providers:
            continue
        filtered_md_skills.append(skill)

    return filtered_tools, filtered_md_skills


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


def _skill_permission_matches_any(
    skill_permissions: list[dict],
    skill_names: list[str],
) -> bool:
    for skill_name in skill_names:
        if _is_skill_enabled(skill_permissions, skill_name):
            return True
    return False


def _build_md_skill_lookup(md_skills_snapshot: list[dict]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for md in md_skills_snapshot or []:
        if not isinstance(md, dict):
            continue
        identifiers = {
            _normalize_skill_id(md.get("qualified_name") or ""),
            _normalize_skill_id(md.get("name") or ""),
        }
        identifiers.discard("")
        for identifier in identifiers:
            lookup[identifier] = md
    return lookup


def _build_md_tool_skill_refs(
    skill_registry: SkillRegistry,
    md_skills_snapshot: list[dict],
) -> dict[str, list[str]]:
    """Return tool name -> markdown skill identifiers for permission expansion."""
    md_lookup = _build_md_skill_lookup(md_skills_snapshot)
    tool_refs: dict[str, list[str]] = {}

    md_skill_tools_map = getattr(skill_registry, "_md_skill_tools", {})
    if not isinstance(md_skill_tools_map, dict):
        return tool_refs

    for qualified_name, tool_names in md_skill_tools_map.items():
        qualified_ref = _normalize_skill_id(qualified_name)
        if not qualified_ref:
            continue
        md_entry = md_lookup.get(qualified_ref, {})
        refs = [
            ref for ref in (
                qualified_ref,
                _normalize_skill_id(md_entry.get("qualified_name") if isinstance(md_entry, dict) else ""),
                _normalize_skill_id(md_entry.get("name") if isinstance(md_entry, dict) else ""),
                qualified_ref.split(":")[-1],
            )
            if ref
        ]
        seen_refs: set[str] = set()
        normalized_refs = [
            ref for ref in refs
            if not (ref in seen_refs or seen_refs.add(ref))
        ]
        for tool_name in tool_names or set():
            normalized_tool = _normalize_skill_id(tool_name)
            if not normalized_tool:
                continue
            bucket = tool_refs.setdefault(normalized_tool, [])
            for ref in normalized_refs:
                if ref not in bucket:
                    bucket.append(ref)

    return tool_refs


def _filter_snapshot_by_permissions(
    tools_snapshot: list[dict],
    md_skills_snapshot: list[dict],
    skill_permissions: list[dict],
    md_tool_skill_refs: dict[str, list[str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Filter tools and md_skills snapshots based on user role permissions.

    When skill_permissions is empty (no grants), returns empty lists (deny-all).
    """

    parent_refs_by_tool = md_tool_skill_refs or {}

    filtered_tools = []
    for tool in tools_snapshot:
        # Check by qualified_skill_name, then skill_name, then tool name
        skill_ref = (
            tool.get("qualified_skill_name")
            or tool.get("skill_name")
            or tool.get("name", "")
        )
        candidate_refs = [skill_ref]
        tool_name = _normalize_skill_id(tool.get("name", ""))
        if tool_name:
            candidate_refs.extend(parent_refs_by_tool.get(tool_name, []))
        if _skill_permission_matches_any(skill_permissions, candidate_refs):
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
    _extra = extra or {}
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

    provider_permissions = _get_provider_permissions_from_extra(_extra)
    resolved_provider_instances = _filter_provider_instances_by_permissions(
        resolved_provider_instances,
        provider_permissions,
    )
    provider_registry = ResolvedProviderInstanceRegistry(resolved_provider_instances)
    available_providers = provider_registry.get_available_providers_summary()

    # Apply user skill permission filtering if provided via request context.
    # Sentinel: None = no RBAC (no-DB mode), list = RBAC resolved.
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
        md_tool_skill_refs = _build_md_tool_skill_refs(ctx.skill_registry, _all_md)
        tools_snapshot, md_skills_snapshot = _filter_snapshot_by_permissions(
            tools_snapshot,
            _all_md,
            user_skill_permissions,
            md_tool_skill_refs,
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

    provider_snapshot_filter_active = provider_permissions is not None or bool(base_provider_instances)
    tool_count_before_provider_filter = len(tools_snapshot or [])
    md_count_before_provider_filter = len(md_skills_snapshot or [])
    tools_snapshot, md_skills_snapshot = _filter_provider_bound_snapshots(
        tools_snapshot,
        md_skills_snapshot,
        resolved_provider_instances,
        enforce=provider_snapshot_filter_active,
    )
    provider_snapshot_filtered = (
        provider_snapshot_filter_active
        and (
            len(tools_snapshot or []) != tool_count_before_provider_filter
            or len(md_skills_snapshot or []) != md_count_before_provider_filter
        )
    )
    visible_tool_names = {
        str(tool.get("name", "") or "").strip()
        for tool in tools_snapshot
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    }
    if visible_tool_names:
        tool_groups_snapshot = {
            str(group_id): [
                str(tool_name).strip()
                for tool_name in (tool_names or [])
                if str(tool_name).strip() in visible_tool_names
            ]
            for group_id, tool_names in (tool_groups_snapshot or {}).items()
        }
        tool_groups_snapshot = {
            group_id: tool_names
            for group_id, tool_names in tool_groups_snapshot.items()
            if tool_names
        }
    else:
        tool_groups_snapshot = {}

    deps_extra = {
        **runtime_context,
        "_service_provider_registry": provider_registry,
        "available_providers": available_providers,
        "provider_instances": resolved_provider_instances,
        "provider_config": resolved_provider_instances,
        "tools_snapshot": tools_snapshot,
        # When provider filtering removes snapshots, keep this request snapshot
        # authoritative so the runner does not re-add denied provider tools.
        "tools_snapshot_authoritative": _rbac_active or provider_snapshot_filtered,
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
    selected_capability = get_selected_capability_from_extra(deps_extra)
    if selected_capability:
        deps_extra[SELECTED_CAPABILITY_KEY] = dict(selected_capability)
        provider_type, instance_name = selected_capability_provider_instance_ref(
            selected_capability
        )
        if provider_type and instance_name:
            provider_instance = provider_registry.get_instance_config(provider_type, instance_name)
            if isinstance(provider_instance, dict):
                deps_extra["provider_type"] = provider_type
                deps_extra["provider_instance_name"] = instance_name
                deps_extra["provider_instance"] = provider_instance
    deps_extra = enrich_trace_metadata(session_key, extra=deps_extra)

    return SkillDeps(
        user_info=user_info,
        session_key=session_key,
        session_manager=scoped_session_mgr,
        memory_manager=scoped_memory_mgr,
        cookies=request_cookies or {},
        extra=deps_extra,
    )
