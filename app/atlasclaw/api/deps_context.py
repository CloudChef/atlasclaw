# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

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
from ..skills.permission_service import skill_permission_service
from ..skills.registry import SkillRegistry
from ..tools.catalog import INTERNAL_TOOL_NAMES
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
    """Filter instances using provider allowlist semantics when rules are present."""
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


def _normalize_provider_auth_chain(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    chain: list[str] = []
    for item in raw_values:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in chain:
            chain.append(normalized)
    return chain


def _provider_value_present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _provider_auth_mode_has_configured_value(instance_config: dict[str, Any], mode: str) -> bool:
    normalized = str(mode or "").strip().lower()
    if normalized == "provider_token":
        return _provider_value_present(instance_config.get("provider_token"))
    if normalized == "user_token":
        return _provider_value_present(instance_config.get("user_token"))
    if normalized == "cookie":
        return _provider_value_present(instance_config.get("cookie"))
    if normalized == "credential":
        return _provider_value_present(instance_config.get("credential")) or (
            _provider_value_present(instance_config.get("username"))
            and _provider_value_present(instance_config.get("password"))
        )
    return False


def _provider_instance_missing_user_token(instance_config: dict[str, Any]) -> bool:
    auth_chain = _normalize_provider_auth_chain(instance_config.get("auth_type"))
    if "user_token" not in auth_chain:
        return False
    if _provider_auth_mode_has_configured_value(instance_config, "user_token"):
        return False
    other_modes = [mode for mode in auth_chain if mode != "user_token"]
    if not other_modes:
        return True
    return not any(
        _provider_auth_mode_has_configured_value(instance_config, mode)
        for mode in other_modes
    )


def _provider_instance_uses_configured_user_token(instance_config: dict[str, Any]) -> bool:
    auth_chain = _normalize_provider_auth_chain(instance_config.get("auth_type"))
    return "user_token" in auth_chain and _provider_auth_mode_has_configured_value(
        instance_config,
        "user_token",
    )


def _build_provider_auth_diagnostics(
    visible_provider_instances: dict[str, dict[str, dict[str, Any]]],
    resolved_provider_instances: dict[str, dict[str, dict[str, Any]]],
    user_provider_instances: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    diagnostics: dict[str, dict[str, dict[str, Any]]] = {}
    for provider_type, instances in (visible_provider_instances or {}).items():
        if not isinstance(instances, dict):
            continue
        normalized_provider_type = str(provider_type or "").strip().lower()
        if not normalized_provider_type:
            continue
        resolved_bucket = resolved_provider_instances.get(normalized_provider_type, {})
        if not isinstance(resolved_bucket, dict):
            resolved_bucket = {}
        user_bucket = (user_provider_instances or {}).get(normalized_provider_type, {})
        if not isinstance(user_bucket, dict):
            user_bucket = {}
        for instance_name, instance_config in instances.items():
            normalized_instance_name = str(instance_name or "").strip()
            if not normalized_instance_name or not isinstance(instance_config, dict):
                continue

            resolved_config = resolved_bucket.get(normalized_instance_name)
            user_config = user_bucket.get(normalized_instance_name)
            if isinstance(resolved_config, dict):
                # Resolved providers are usable; only user-owned credentials need
                # account-setting guidance when a later tool call rejects them.
                if isinstance(user_config, dict) and _provider_instance_uses_configured_user_token(
                    user_config
                ):
                    auth_chain = _normalize_provider_auth_chain(user_config.get("auth_type"))
                    diagnostics.setdefault(normalized_provider_type, {})[
                        normalized_instance_name
                    ] = {
                        "provider_type": normalized_provider_type,
                        "instance_name": normalized_instance_name,
                        "auth_chain": auth_chain,
                        "missing_user_token": False,
                        "user_token_configured": True,
                        "contact_admin": False,
                    }
                continue

            auth_chain = _normalize_provider_auth_chain(instance_config.get("auth_type"))
            missing_user_token = _provider_instance_missing_user_token(instance_config)
            diagnostics.setdefault(normalized_provider_type, {})[normalized_instance_name] = {
                "provider_type": normalized_provider_type,
                "instance_name": normalized_instance_name,
                "auth_chain": auth_chain,
                "missing_user_token": missing_user_token,
                "user_token_configured": False,
                "contact_admin": not missing_user_token,
            }
    return diagnostics


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


def _skill_permission_matches_any(
    skill_permissions: list[dict],
    skill_names: list[str],
) -> bool:
    for skill_name in skill_names:
        if skill_permission_service.is_skill_enabled(skill_permissions, skill_name):
            return True
    return False


def _build_md_skill_lookup(md_skills_snapshot: list[dict]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for md in md_skills_snapshot or []:
        if not isinstance(md, dict):
            continue
        identifiers = {
            skill_permission_service.normalize_key(md.get("qualified_name") or ""),
            skill_permission_service.normalize_key(md.get("name") or ""),
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
        qualified_ref = skill_permission_service.normalize_key(qualified_name)
        if not qualified_ref:
            continue
        md_entry = md_lookup.get(qualified_ref, {})
        refs = [
            ref for ref in (
                qualified_ref,
                skill_permission_service.normalize_key(md_entry.get("qualified_name") if isinstance(md_entry, dict) else ""),
                skill_permission_service.normalize_key(md_entry.get("name") if isinstance(md_entry, dict) else ""),
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
            normalized_tool = skill_permission_service.normalize_key(tool_name)
            if not normalized_tool:
                continue
            bucket = tool_refs.setdefault(normalized_tool, [])
            for ref in normalized_refs:
                if ref not in bucket:
                    bucket.append(ref)

    return tool_refs


def _is_internal_runtime_tool(tool: dict[str, Any]) -> bool:
    name = str((tool or {}).get("name", "") or "").strip()
    return bool(name) and name in INTERNAL_TOOL_NAMES


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
        if _is_internal_runtime_tool(tool):
            filtered_tools.append(tool)
            continue
        if skill_permission_service.is_provider_bound_tool_snapshot(tool):
            filtered_tools.append(tool)
            continue
        # Check by qualified_skill_name, then skill_name, then tool name
        skill_ref = (
            tool.get("qualified_skill_name")
            or tool.get("skill_name")
            or tool.get("name", "")
        )
        candidate_refs = [skill_ref]
        tool_name = skill_permission_service.normalize_key(tool.get("name", ""))
        if tool_name:
            candidate_refs.extend(parent_refs_by_tool.get(tool_name, []))
        if _skill_permission_matches_any(skill_permissions, candidate_refs):
            filtered_tools.append(tool)

    filtered_md = []
    for md in md_skills_snapshot:
        if skill_permission_service.is_provider_bound_md_skill_snapshot(md):
            filtered_md.append(md)
            continue
        skill_ref = md.get("qualified_name") or md.get("name", "")
        if skill_permission_service.is_skill_enabled(skill_permissions, skill_ref):
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
    internal_runtime_tools_builder = getattr(
        ctx.skill_registry,
        "internal_runtime_tools_snapshot",
        None,
    )
    if callable(internal_runtime_tools_builder):
        internal_runtime_tools_snapshot = internal_runtime_tools_builder()
    else:
        internal_runtime_tools_snapshot = []

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
    provider_permissions = _get_provider_permissions_from_extra(_extra)
    visible_provider_instances = _filter_provider_instances_by_permissions(
        base_provider_instances,
        provider_permissions,
    )
    resolved_provider_instances = build_resolved_provider_instances(
        visible_provider_instances,
        runtime_context=runtime_context,
    )
    user_provider_instances = build_user_provider_instances(
        user_info.user_id,
        workspace_path=str(scoped_session_mgr.workspace_path),
        runtime_context=runtime_context,
        provider_templates=visible_provider_instances,
    )
    for provider_type, instances in user_provider_instances.items():
        provider_bucket = resolved_provider_instances.setdefault(provider_type, {})
        for instance_name, instance_config in instances.items():
            provider_bucket[instance_name] = dict(instance_config)

    resolved_provider_instances = _filter_provider_instances_by_permissions(
        resolved_provider_instances,
        provider_permissions,
    )
    provider_auth_diagnostics = _build_provider_auth_diagnostics(
        visible_provider_instances,
        resolved_provider_instances,
        user_provider_instances,
    )
    provider_registry = ResolvedProviderInstanceRegistry(resolved_provider_instances)
    available_providers = provider_registry.get_available_providers_summary()
    all_md_skills_snapshot = ctx.skill_registry.md_snapshot()

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
        md_tool_skill_refs = _build_md_tool_skill_refs(ctx.skill_registry, all_md_skills_snapshot)
        tools_snapshot, md_skills_snapshot = _filter_snapshot_by_permissions(
            tools_snapshot,
            all_md_skills_snapshot,
            user_skill_permissions,
            md_tool_skill_refs,
        )
        # Collect ALL disabled skill IDs (both md and executable)
        _disabled_skill_ids: set[str] = set()
        for sp_entry in user_skill_permissions:
            if isinstance(sp_entry, dict) and not (sp_entry.get("enabled") and sp_entry.get("authorized")):
                _sid = skill_permission_service.normalize_key(sp_entry.get("skill_id") or sp_entry.get("skill_name") or "")
                if _sid:
                    _disabled_skill_ids.add(_sid)

        # 1. Extract handler tool names from disabled md_skills metadata
        for md_entry in all_md_skills_snapshot:
            _qname = skill_permission_service.normalize_key(
                md_entry.get("qualified_name") or md_entry.get("name") or ""
            )
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
                _qnorm = skill_permission_service.normalize_key(qual_name)
                _bare = _qnorm.split(":")[-1] if _qnorm else ""
                if _bare in _disabled_skill_ids or _qnorm in _disabled_skill_ids:
                    for tn in (tool_names_set or set()):
                        _tname = str(tn or "").strip()
                        if _tname:
                            disabled_tool_names.add(_tname)

    else:
        md_skills_snapshot = all_md_skills_snapshot

    tool_count_before_provider_filter = len(tools_snapshot or [])
    md_count_before_provider_filter = len(md_skills_snapshot or [])
    tools_snapshot, md_skills_snapshot = skill_permission_service.filter_provider_bound_snapshots(
        tools_snapshot,
        md_skills_snapshot,
        visible_provider_instances,
        enforce=True,
    )
    provider_snapshot_filtered = (
        len(tools_snapshot or []) != tool_count_before_provider_filter
        or len(md_skills_snapshot or []) != md_count_before_provider_filter
    )
    builtin_skills_snapshot = [
        skill
        for skill in ctx.skill_registry.snapshot_builtins()
        if isinstance(skill, dict)
    ]
    builtin_count_before_provider_filter = len(builtin_skills_snapshot)
    skills_snapshot, _ = skill_permission_service.filter_provider_bound_snapshots(
        builtin_skills_snapshot,
        [],
        visible_provider_instances,
        enforce=True,
    )
    skills_snapshot_filtered = len(skills_snapshot or []) != builtin_count_before_provider_filter
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
        "provider_auth_diagnostics": provider_auth_diagnostics,
        "tools_snapshot": tools_snapshot,
        # The request-scoped snapshot is authoritative for provider-bound
        # capability availability after role and provider filters run.
        "tools_snapshot_authoritative": (
            _rbac_active or provider_snapshot_filtered or skills_snapshot_filtered
        ),
        "tool_groups_snapshot": tool_groups_snapshot,
        "skills_snapshot": skills_snapshot,
        "md_skills_snapshot": md_skills_snapshot,
        "internal_runtime_tools_snapshot": internal_runtime_tools_snapshot,
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
                _sid = skill_permission_service.normalize_key(sp_entry.get("skill_id") or sp_entry.get("skill_name") or "")
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
