# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
FastAPI application entry point for AtlasClaw.

This module creates and configures the FastAPI application, including:
- Static file serving for the frontend
- API routes for session management and agent execution
- CORS middleware for development
- Health check endpoint

Usage:
    uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional


from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=False)

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.atlasclaw.api.routes import create_router, APIContext, install_request_validation_logging, set_api_context
from app.atlasclaw.api.webhook_dispatch import WebhookDispatchManager
from app.atlasclaw.api.channel_hooks import router as channel_hooks_router
from app.atlasclaw.api.channels import router as channels_router, set_channel_manager
from app.atlasclaw.api.agent_info import router as agent_info_router
from app.atlasclaw.api.api_routes import router as db_api_router
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.session.router import SessionManagerRouter
from app.atlasclaw.skills.permission_service import skill_permission_service
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools
from app.atlasclaw.tools.catalog import ToolProfile
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent.context_pruning import (
    ContextPruningSettings as AgentContextPruningSettings,
    SoftTrimConfig as AgentContextPruningSoftTrimConfig,
    HardClearConfig as AgentContextPruningHardClearConfig,
)
from app.atlasclaw.core.config import get_config, get_config_path
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.core.provider_scanner import ProviderScanner
from app.atlasclaw.core.trace import enrich_trace_metadata
from app.atlasclaw.core.workspace import WorkspaceInitializer
from app.atlasclaw.agent.agent_definition import AgentLoader
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.manager import ChannelManager
# Import channel handlers
from app.atlasclaw.channels.handlers.feishu import FeishuHandler
from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from app.atlasclaw.channels.handlers.wecom import WeComHandler
from app.atlasclaw.auth import AuthRegistry
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.agent.agent_pool import AgentInstancePool
from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.hooks.runtime import HookRuntime, HookRuntimeContext
from app.atlasclaw.hooks.runtime_builtin import register_builtin_hook_handlers
from app.atlasclaw.hooks.runtime_models import HookEventType
from app.atlasclaw.hooks.runtime_script import HookScriptHandlerDefinition
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore
from app.atlasclaw.heartbeat.agent_executor import AgentHeartbeatExecutor
from app.atlasclaw.heartbeat.channel_executor import ChannelHeartbeatExecutor
from app.atlasclaw.heartbeat.events import emit_heartbeat_event_to_hook_runtime
from app.atlasclaw.heartbeat.models import (
    HeartbeatJobDefinition,
    HeartbeatJobType,
    HeartbeatTargetDescriptor,
    HeartbeatTargetType,
)
from app.atlasclaw.heartbeat.runtime import HeartbeatRuntime, HeartbeatRuntimeContext
from app.atlasclaw.heartbeat.store import HeartbeatStateStore
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from app.atlasclaw.core.token_health_store import TokenHealthStore
from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
from app.atlasclaw.core.token_pool import TokenEntry, TokenHealth, TokenPool
from app.atlasclaw.db.database import DatabaseConfig, init_database, get_db_manager
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.bootstrap.app_factory_helpers import (
    ExternalBasePathMiddleware,
    StaticFileCacheMiddleware,
    mount_frontend,
    register_core_routers,
    render_frontend_html,
    setup_auth_middleware_from_config,
)
from app.atlasclaw.bootstrap.startup_helpers import (
    build_token_entries_from_db,
    build_provider_instances_from_db,
    build_token_entries,
    check_and_prompt_for_providers,
    create_pydantic_model,
    derive_provider_namespace,
    ensure_default_local_admin,
    load_agent_config_from_db,
    merge_provider_instances,
    merge_token_entries,
    print_root_plugins,
    run_mysql_alembic_upgrade,
    scan_plugin_names,
)



_global_provider_registry: Optional[ServiceProviderRegistry] = None


# Global context components
_session_manager: Optional[SessionManager] = None
_session_manager_router: Optional[SessionManagerRouter] = None
_session_queue: Optional[SessionQueue] = None
_skill_registry: Optional[SkillRegistry] = None
_agent_runner: Optional[AgentRunner] = None
_channel_manager: Optional[ChannelManager] = None
_hook_state_store: Optional[HookStateStore] = None
_memory_sink: Optional[MemorySink] = None
_context_sink: Optional[ContextSink] = None
_hook_runtime: Optional[HookRuntime] = None
_heartbeat_runtime: Optional[HeartbeatRuntime] = None
_heartbeat_store: Optional[HeartbeatStateStore] = None
_heartbeat_task: Optional[asyncio.Task] = None


def _list_workspace_runtime_user_ids(workspace_path: str | Path) -> set[str]:
    users_dir = Path(workspace_path).resolve() / "users"
    if not users_dir.exists():
        return set()
    return {
        item.name
        for item in users_dir.iterdir()
        if item.is_dir() and item.name not in {"default", "anonymous"}
    }


async def _list_db_runtime_user_ids(db_initialized: bool) -> set[str]:
    if not db_initialized:
        return set()
    try:
        async with get_db_manager().get_session() as session:
            users, _ = await UserService.list_all(session, page=1, page_size=1000)
    except Exception as exc:
        print(f"[AtlasClaw] Warning: Failed to load runtime users from database: {exc}")
        return set()
    user_ids: set[str] = set()
    for user in users:
        auth_type = str(getattr(user, "auth_type", "") or "").strip().lower()
        runtime_user_id = user.username if auth_type == "local" else user.id
        if runtime_user_id and runtime_user_id not in {"default", "anonymous"}:
            user_ids.add(runtime_user_id)
    return user_ids


def _list_active_channel_runtime_user_ids(channel_manager: Optional[ChannelManager]) -> set[str]:
    if channel_manager is None:
        return set()
    return {
        str(item.get("user_id", "")).strip()
        for item in channel_manager.list_active_connection_descriptors()
        if str(item.get("user_id", "")).strip() not in {"", "default", "anonymous"}
    }


async def _collect_runtime_user_ids(
    workspace_path: str | Path,
    *,
    db_initialized: bool,
    channel_manager: Optional[ChannelManager] = None,
) -> list[str]:
    user_ids: set[str] = set()
    user_ids.update(_list_workspace_runtime_user_ids(workspace_path))
    user_ids.update(await _list_db_runtime_user_ids(db_initialized))
    user_ids.update(_list_active_channel_runtime_user_ids(channel_manager))
    return sorted(
        user_id
        for user_id in user_ids
        if user_id and user_id not in {"default", "anonymous"}
    )

# Roles in this set receive the core skill catalog (built-in tools +
# standalone markdown skills). Provider capabilities are governed by provider
# permissions and are not duplicated into role skill_permissions.
_FULL_CATALOG_ROLE_IDENTIFIERS = frozenset({"admin"})


async def _ensure_builtin_role_skill_permissions(skill_registry) -> None:
    """Seed / incrementally merge skill_permissions for system-managed
    built-in roles from the skill catalog.

    - **admin** receives the core catalog (built-in tools and standalone
      markdown skills) so it never loses platform capabilities.
    - **user** receives no default skill entries here; provider skills/tools are
      controlled by provider permissions instead of skills.skill_permissions.

    Behaviour:
      - Fresh install (no stored permissions): write the role-appropriate catalog.
      - Upgrade (existing permissions): append any NEW skills that are in the
        catalog but missing from stored permissions.  Existing user choices
        (enabled/disabled) are preserved.
    """
    try:
        from app.atlasclaw.db.database import get_db_manager
        from sqlalchemy import select
        from app.atlasclaw.db.orm.role import (
            RoleModel,
            RoleService,
            SYSTEM_MANAGED_BUILTIN_ROLE_IDENTIFIERS,
        )

        async with get_db_manager().get_session() as session:
            await RoleService.ensure_builtin_roles(session)
            result = await session.execute(
                select(RoleModel).where(
                    RoleModel.identifier.in_(SYSTEM_MANAGED_BUILTIN_ROLE_IDENTIFIERS),
                    RoleModel.is_builtin == True,
                )
            )
            managed_roles = result.scalars().all()
            if not managed_roles:
                return

            # ---- Build catalog entries ----
            tools_snap = skill_registry.tools_snapshot()
            md_skills = skill_registry.md_snapshot()

            def _make_entry(skill_id: str, skill_name: str, description: str) -> dict:
                return {
                    "skill_id": skill_id,
                    "skill_name": skill_name,
                    "description": description,
                    "runtime_enabled": True,
                    "authorized": True,
                    "enabled": True,
                }

            # Core catalog: non-provider executable tools + standalone md skills
            # for admin. Markdown-backed executable tools are represented by
            # their markdown skill entry.
            full_catalog: list[dict] = []
            full_seen: set[str] = set()
            core_tool_count = 0
            core_md_count = 0
            for tool in tools_snap:
                if not skill_permission_service.is_core_catalog_tool_snapshot(tool):
                    continue
                tool_name = str(tool.get("name", "") or "").strip()
                if not tool_name or tool_name in full_seen:
                    continue
                full_seen.add(tool_name)
                core_tool_count += 1
                full_catalog.append(_make_entry(tool_name, tool_name, tool.get("description", "")))
            for md in md_skills:
                if skill_permission_service.is_provider_bound_md_skill_snapshot(md):
                    continue
                md_name = str(md.get("name", "") or "").strip()
                md_qname = str(md.get("qualified_name", "") or "").strip()
                skill_id = md_qname or md_name
                if not skill_id or skill_id in full_seen:
                    continue
                full_seen.add(skill_id)
                core_md_count += 1
                full_catalog.append(_make_entry(skill_id, md_name, md.get("description", "")))

            if not full_catalog:
                return

            # ---- Per-role incremental merge ----
            changed = False
            for role in managed_roles:
                # Admin gets the core catalog; other system-managed roles keep
                # their skill_permissions untouched unless explicitly updated.
                catalog_entries = (
                    full_catalog
                    if role.identifier in _FULL_CATALOG_ROLE_IDENTIFIERS
                    else []
                )
                if not catalog_entries:
                    continue

                perms = role.permissions or {}
                existing_perms: list[dict] = (
                    (perms.get("skills") or {}).get("skill_permissions", [])
                )
                if not isinstance(existing_perms, list):
                    existing_perms = []

                existing_ids: set[str] = {
                    str(e.get("skill_id", "")).strip()
                    for e in existing_perms
                    if str(e.get("skill_id", "")).strip()
                }

                new_entries = [
                    entry for entry in catalog_entries
                    if entry["skill_id"] not in existing_ids
                ]

                if not existing_perms:
                    merged = list(catalog_entries)
                    action = "bootstrapped"
                elif new_entries:
                    merged = existing_perms + new_entries
                    action = "merged"
                else:
                    continue

                new_perms = dict(perms)
                new_perms["skills"] = {
                    **(perms.get("skills") or {}),
                    "skill_permissions": merged,
                }
                role.permissions = new_perms
                changed = True

                if action == "bootstrapped":
                    print(
                        f"[AtlasClaw] Bootstrapped {role.identifier} default skill permissions "
                        f"({len(merged)} entries: "
                        f"{core_tool_count} executable + {core_md_count} markdown)"
                    )
                else:
                    print(
                        f"[AtlasClaw] Merged {len(new_entries)} new skill(s) into "
                        f"{role.identifier} permissions (was {len(existing_perms)}, now {len(merged)})"
                    )

            if changed:
                await session.commit()
    except Exception as e:
        print(f"[AtlasClaw] Warning: Failed to bootstrap builtin role skill permissions: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):


    """Application lifespan handler for startup and shutdown."""
    global _session_manager, _session_manager_router, _session_queue, _skill_registry, _agent_runner, _global_provider_registry, _channel_manager, _hook_state_store, _memory_sink, _context_sink, _hook_runtime, _heartbeat_runtime, _heartbeat_store, _heartbeat_task
    
    config = get_config()
    config_path = get_config_path()
    config_root = config_path.parent if config_path is not None else Path.cwd()
    providers_root = (config_root / config.providers_root).resolve()
    skills_root = (config_root / config.skills_root).resolve()

    provider_plugins = scan_plugin_names(providers_root)
    skill_plugins = scan_plugin_names(skills_root, md_skill_mode=True)
    print_root_plugins("providers_root plugins", providers_root, provider_plugins)
    print_root_plugins("skills_root plugins", skills_root, skill_plugins)

    # Get workspace path from config
    workspace_path = config.workspace.path

    
    # Initialize workspace directory structure
    workspace_initializer = WorkspaceInitializer(workspace_path)
    was_initialized = workspace_initializer.is_initialized()
    workspace_initializer.initialize()
    if not was_initialized:
        print(f"[AtlasClaw] Initialized workspace at: {workspace_path}")

    check_and_prompt_for_providers(providers_root)

    # Initialize database if configured
    db_initialized = False
    if config.database:
        try:
            db_config = DatabaseConfig.from_config({
                "database": {
                    "type": config.database.type,
                    "sqlite": {"path": config.database.sqlite.path} if config.database.sqlite else {},
                    "mysql": {
                        "host": config.database.mysql.host,
                        "port": config.database.mysql.port,
                        "database": config.database.mysql.database,
                        "user": config.database.mysql.user,
                        "password": config.database.mysql.password,
                        "charset": config.database.mysql.charset,
                    } if config.database.mysql else {},
                    "pool_size": config.database.pool_size,
                    "max_overflow": config.database.max_overflow,
                    "echo": config.database.echo,
                }
            })
            await init_database(db_config)

            if db_config.db_type == "sqlite":
                # SQLite: rely on ORM models to auto-create schema
                await get_db_manager().create_tables()
                print("[AtlasClaw] SQLite initialized via ORM models")
            elif db_config.db_type == "mysql":
                # MySQL: enterprise mode, schema/data changes managed by Alembic
                await run_mysql_alembic_upgrade(db_config)
                print("[AtlasClaw] MySQL initialized via Alembic migrations")
            else:
                raise RuntimeError(f"Unsupported database type: {db_config.db_type}")

            db_initialized = True
        except Exception as e:
            print(f"[AtlasClaw] Failed to initialize database ({config.database.type}): {e}")
            raise RuntimeError(f"Database startup failed: {e}") from e

    
    if db_initialized:
        await ensure_default_local_admin(config)

    # Register built-in channel handlers (enterprise messaging platforms)
    ChannelRegistry.register("feishu", FeishuHandler)

    ChannelRegistry.register("dingtalk", DingTalkHandler)
    ChannelRegistry.register("wecom", WeComHandler)
    print(f"[AtlasClaw] Registered built-in channel handlers")
    
    # Initialize ChannelManager
    _channel_manager = ChannelManager(workspace_path)
    set_channel_manager(_channel_manager)
    print(f"[AtlasClaw] Channel manager initialized")
    
    # Scan providers for auth extensions only.
    scan_results = ProviderScanner.scan_providers(providers_root)
    print(f"[AtlasClaw] Provider scan complete: {len(scan_results['auth'])} auth providers")
    
    # Load agent definitions - try database first, fallback to file-based
    agent_loader = AgentLoader(workspace_path)
    main_agent_config = None
    
    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                main_agent_config = await load_agent_config_from_db(session, "main")
                if main_agent_config:
                    print(f"[AtlasClaw] Loaded agent from database: {main_agent_config.display_name}")
        except Exception as e:
            print(f"[AtlasClaw] Warning: Failed to load agent from database: {e}")
    
    # Fallback to file-based agent config
    if main_agent_config is None:
        main_agent_config = agent_loader.load_agent("main")
        print(f"[AtlasClaw] Loaded agent from files: {main_agent_config.display_name}")
    
    # Initialize SessionManager with new workspace-based path
    _session_manager = SessionManager(
        workspace_path=workspace_path,
        user_id="default",
        reset_mode=config.reset.mode,
        daily_reset_hour=config.reset.daily_hour,
        idle_reset_minutes=config.reset.idle_minutes,
    )
    _session_manager_router = SessionManagerRouter.from_manager(_session_manager)
    _session_queue = SessionQueue(max_concurrent=config.agent_defaults.max_concurrent)
    _hook_state_store = HookStateStore(workspace_path=workspace_path)
    _memory_sink = MemorySink(workspace_path=workspace_path)
    _context_sink = ContextSink(_hook_state_store)
    _hook_runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=workspace_path,
            hook_state_store=_hook_state_store,
            memory_sink=_memory_sink,
            context_sink=_context_sink,
            session_manager_router=_session_manager_router,
        )
    )
    register_builtin_hook_handlers(_hook_runtime)
    for handler_config in config.hooks_runtime.script_handlers:
        event_types = {HookEventType(event_name) for event_name in handler_config.events}
        _hook_runtime.register_script_handler(
            HookScriptHandlerDefinition(
                module_name=handler_config.module,
                event_types=event_types,
                command=list(handler_config.command),
                timeout_seconds=handler_config.timeout_seconds,
                enabled=handler_config.enabled,
                cwd=handler_config.cwd,
                priority=handler_config.priority,
            )
        )
    _skill_registry = SkillRegistry()
    
    _global_provider_registry = ServiceProviderRegistry()
    _global_provider_registry.load_from_directory(providers_root)

    config_provider_instances: dict[str, dict[str, dict[str, Any]]] = {
        provider_type: dict(instances)
        for provider_type, instances in (config.service_providers or {}).items()
        if isinstance(instances, dict)
    }
    merged_provider_instances = config_provider_instances

    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                db_provider_instances = await build_provider_instances_from_db(session)
                if db_provider_instances:
                    merged_provider_instances = merge_provider_instances(
                        db_provider_instances,
                        config_provider_instances,
                    )
                    print(
                        "[AtlasClaw] Loaded provider configs from database and merged with JSON"
                    )
        except Exception as e:
            print(f"[AtlasClaw] Warning: Failed to load provider configs from database: {e}")

    if merged_provider_instances:
        _global_provider_registry.load_instances_from_config(merged_provider_instances)

    available_providers = {}
    provider_instances = _global_provider_registry.get_all_instance_configs()

    for provider_type in _global_provider_registry.list_providers():
        instances = _global_provider_registry.list_instances(provider_type)
        if instances:
            available_providers[provider_type] = instances
    
    # Register built-in tools (exec, read, write, web_search, etc.)
    registered_tools = register_builtin_tools(
        _skill_registry,
        profile=ToolProfile.FULL,
        tools_exclusive=list(config.skills.tools_exclusive or []),
        allow_script_execution=bool(config.skills.allow_script_execution),
    )
    print(f"[AtlasClaw] Registered {len(registered_tools)} built-in tools")
    
    # Load markdown skills from multiple sources.
    # 1. Provider skills: load from ALL provider directories (not just
    #    configured ones) so that hot-adding a provider instance at runtime
    #    does not require a restart to pick up its skills.
    loaded_provider_skill_count = 0
    if providers_root.exists():
        for provider_path in providers_root.iterdir():
            if provider_path.is_dir() and not provider_path.name.startswith(("_", ".")):
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    provider_namespace = derive_provider_namespace(provider_path.name)
                    loaded_provider_skill_count += _skill_registry.load_from_directory(
                        str(provider_skills),
                        location="provider",
                        provider=provider_namespace,
                    )
    print(
        f"[AtlasClaw] Loaded {loaded_provider_skill_count} provider markdown skills"
    )

    loaded_standalone_skill_count = 0
    if skills_root.exists():
        loaded_standalone_skill_count = _skill_registry.load_from_directory(
            str(skills_root),
            location="skills-root",
        )
    print(
        f"[AtlasClaw] Loaded {loaded_standalone_skill_count} standalone markdown skills"
    )

    # Bootstrap admin role default skill permissions if not yet stored.
    # This replaces a problematic frontend auto-PUT that was triggered on
    # every role-management page load.  The backend is the right place to
    # seed permissions because it has access to the loaded skill catalog.
    if db_initialized:
        await _ensure_builtin_role_skill_permissions(_skill_registry)

    from pydantic_ai import Agent
    from app.atlasclaw.core.deps import SkillDeps

    # Load token configurations from both JSON and database.
    # Priority: DB tokens override same token_id from JSON, while JSON tokens are still loaded.
    config_token_entries, config_primary_token_id = build_token_entries(config)
    token_entries = list(config_token_entries)
    primary_token_id = config_primary_token_id

    if token_entries:
        print(f"[AtlasClaw] Loaded {len(token_entries)} tokens from JSON config")

    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                db_token_entries, db_primary_token_id = await build_token_entries_from_db(session)
                if db_token_entries:
                    print(f"[AtlasClaw] Loaded {len(db_token_entries)} tokens from database")
                    token_entries = merge_token_entries(db_token_entries, token_entries)
                    primary_token_id = db_primary_token_id or primary_token_id
                    print(f"[AtlasClaw] Combined token pool: {len(token_entries)} (database + JSON)")
        except Exception as e:
            print(f"[AtlasClaw] Warning: Failed to load tokens from database: {e}")

    if not token_entries:
        raise RuntimeError("No token configurations found. Please configure tokens in database or atlasclaw.json")

    if primary_token_id and not any(t.token_id == primary_token_id for t in token_entries):
        print(f"[AtlasClaw] Warning: primary token '{primary_token_id}' not found, using first token")
        primary_token_id = token_entries[0].token_id
    elif not primary_token_id:
        primary_token_id = token_entries[0].token_id


    token_pool = TokenPool()
    for token in token_entries:
        token_pool.register_token(token)

    # Load model configs from DB and register as token entries
    # Model configs can override token entries with the same name
    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                db_model_configs = await ModelConfigService.list_active(session)
                for mc in db_model_configs:
                    entry = TokenEntry(
                        token_id=mc.name,
                        provider=mc.provider,
                        model=mc.model_id,
                        base_url=mc.base_url or "",
                        api_key=ModelConfigService.get_decrypted_api_key(mc) or "",
                        api_type=mc.api_type or "openai",
                        priority=mc.priority or 0,
                        weight=mc.weight or 100,
                        context_window=mc.context_window,
                    )
                    token_pool.register_token(entry)
                if db_model_configs:
                    print(f"[AtlasClaw] Loaded {len(db_model_configs)} model configs from database")
        except Exception as e:
            print(f"[AtlasClaw] Warning: Failed to load model configs from database: {e}")

    health_store = TokenHealthStore(workspace_path)
    # Product requirement: clear token unhealthy state on every restart so one bad
    # session does not poison subsequent runs.
    for token_id in list(token_pool.tokens.keys()):
        token_pool.restore_health(token_id, TokenHealth())
    health_store.save(token_pool.export_health_status())

    token_policy = DynamicTokenPolicy(
        token_pool,
        strategy=config.model.selection_strategy,
        primary_token_id=primary_token_id,
    )
    agent_pool = AgentInstancePool(max_concurrent_per_instance=4)
    token_interceptor = TokenHealthInterceptor(token_pool, health_store)

    agent_configs: dict[str, Any] = {"main": main_agent_config}

    def _build_agent_for(agent_id: str, token: TokenEntry) -> Any:
        agent_cfg = agent_configs.get(agent_id)
        if agent_cfg is None:
            agent_cfg = agent_loader.load_agent(agent_id)
            agent_configs[agent_id] = agent_cfg
        model_instance = create_pydantic_model(token)
        built_agent = Agent(
            model_instance,
            deps_type=SkillDeps,
            system_prompt=agent_cfg.system_prompt or "You are AtlasClaw, an enterprise AI assistant.",
        )
        _skill_registry.register_to_agent(built_agent)
        return built_agent

    seed_token = token_pool.tokens.get(primary_token_id) or token_entries[0]
    agent = _build_agent_for("main", seed_token)

    # Create AgentRunner
    prompt_builder = PromptBuilder(
        PromptBuilderConfig(
            workspace_path=workspace_path,
            md_skills_max_count=config.skills.md_skills_max_count,
            md_skills_desc_max_chars=config.skills.md_skills_desc_max_chars,
            md_skills_max_index_chars=config.skills.md_skills_index_max_chars,
            md_skills_max_file_bytes=config.skills.md_skills_max_file_bytes,
            capability_index_max_count=config.skills.capability_index_max_count,
            capability_index_desc_max_chars=config.skills.capability_index_desc_max_chars,
            capability_index_max_chars=config.skills.capability_index_max_chars,
        )
    )
    runtime_pruning_config = config.context_pruning
    runtime_context_pruning_settings = AgentContextPruningSettings(
        mode=runtime_pruning_config.mode,
        ttl_ms=runtime_pruning_config.ttl_ms,
        keep_last_assistants=runtime_pruning_config.keep_last_assistants,
        soft_trim_ratio=runtime_pruning_config.soft_trim_ratio,
        hard_clear_ratio=runtime_pruning_config.hard_clear_ratio,
        min_prunable_tool_chars=runtime_pruning_config.min_prunable_tool_chars,
        tools_allow=list(runtime_pruning_config.tools.allow),
        tools_deny=list(runtime_pruning_config.tools.deny),
        soft_trim=AgentContextPruningSoftTrimConfig(
            max_chars=runtime_pruning_config.soft_trim.max_chars,
            head_chars=runtime_pruning_config.soft_trim.head_chars,
            tail_chars=runtime_pruning_config.soft_trim.tail_chars,
        ),
        hard_clear=AgentContextPruningHardClearConfig(
            enabled=runtime_pruning_config.hard_clear.enabled,
            placeholder=runtime_pruning_config.hard_clear.placeholder,
        ),
    )
    _agent_runner = AgentRunner(
        agent=agent,
        session_manager=_session_manager,
        session_manager_router=_session_manager_router,
        prompt_builder=prompt_builder,
        session_queue=_session_queue,
        hook_runtime=_hook_runtime,
        agent_id="main",
        token_policy=token_policy,
        agent_pool=agent_pool,
        token_interceptor=token_interceptor,
        agent_factory=_build_agent_for,
        context_pruning_settings=runtime_context_pruning_settings,
    )

    
    # Set agent runner on channel manager for message processing
    _channel_manager.set_agent_runner(_agent_runner)
    _channel_manager.set_session_manager_router(_session_manager_router)

    async def _run_agent_heartbeat(job: HeartbeatJobDefinition) -> dict[str, Any]:
        session_manager = _session_manager_router.for_user(job.owner_user_id)
        heartbeat_target = job.target
        if heartbeat_target and heartbeat_target.session_key:
            heartbeat_session_key = heartbeat_target.session_key
        elif job.isolated_session:
            heartbeat_session_key = SessionKey(
                agent_id="main",
                user_id=job.owner_user_id,
                channel="heartbeat",
                account_id="runtime",
                chat_type=ChatType.THREAD,
                peer_id="heartbeat",
                thread_id=job.job_id,
            ).to_string(scope=SessionScope.PER_ACCOUNT_CHANNEL_PEER)
        else:
            heartbeat_session_key = SessionKey(
                agent_id="main",
                user_id=job.owner_user_id,
            ).to_string(scope=SessionScope.MAIN)

        heartbeat_md = ""
        heartbeat_filename = config.heartbeat.agent_turn.heartbeat_file
        heartbeat_candidates = [
            Path(workspace_path) / "agents" / "main" / heartbeat_filename,
            Path(workspace_path) / heartbeat_filename,
        ]
        for heartbeat_md_path in heartbeat_candidates:
            if heartbeat_md_path.exists():
                heartbeat_md = heartbeat_md_path.read_text(encoding="utf-8").strip()
                break
        heartbeat_run_id = f"heartbeat-{job.job_id}"
        heartbeat_message = heartbeat_md or (
            "Run a lightweight heartbeat check. If no action is required, respond with HEARTBEAT_OK."
        )
        deps = SkillDeps(
            user_info=UserInfo(user_id=job.owner_user_id, display_name=job.owner_user_id),
            session_key=heartbeat_session_key,
            session_manager=session_manager,
            extra=enrich_trace_metadata(
                heartbeat_session_key,
                extra={"run_id": heartbeat_run_id, "heartbeat_job_id": job.job_id},
            ),
        )
        assistant_chunks: list[str] = []
        error_text = ""
        async for event in _agent_runner.run(
            session_key=heartbeat_session_key,
            user_message=heartbeat_message,
            deps=deps,
            max_tool_calls=10,
            timeout_seconds=120,
        ):
            if event.type == "assistant" and event.content:
                assistant_chunks.append(event.content)
            elif event.type == "error":
                error_text = event.error or "heartbeat agent run failed"
                break
        if error_text:
            raise RuntimeError(error_text)
        assistant_message = "".join(assistant_chunks).strip() or "HEARTBEAT_OK"
        return {
            "assistant_message": assistant_message,
            "system_prompt": "heartbeat",
            "message_history": [],
            "tool_calls": [],
            "session_title": "Heartbeat",
            "session_key": heartbeat_session_key,
            "run_id": heartbeat_run_id,
        }

    async def _run_channel_heartbeat(job: HeartbeatJobDefinition) -> dict[str, Any]:
        channel_type = str(job.metadata.get("channel_type", ""))
        connection_id = str(job.metadata.get("connection_id", ""))
        result = await _channel_manager.probe_connection(job.owner_user_id, channel_type, connection_id)
        if not result.get("healthy", False):
            result["reconnect_attempted"] = True
            result["reconnected"] = await _channel_manager.reconnect_connection(
                job.owner_user_id,
                channel_type,
                connection_id,
            )
            if result["reconnected"]:
                refreshed = await _channel_manager.probe_connection(
                    job.owner_user_id,
                    channel_type,
                    connection_id,
                )
                refreshed["reconnected"] = True
                refreshed["summary"] = "reconnected"
                return refreshed
        result.setdefault("summary", "healthy" if result.get("healthy") else "connection_failed")
        return result

    async def _bridge_heartbeat_event(event) -> None:
        if _hook_runtime is None:
            return
        await emit_heartbeat_event_to_hook_runtime(_hook_runtime, event)

    async def _build_agent_heartbeat_jobs() -> list[HeartbeatJobDefinition]:
        if not config.heartbeat.agent_turn.enabled:
            return []
        user_ids = await _collect_runtime_user_ids(
            workspace_path,
            db_initialized=db_initialized,
            channel_manager=_channel_manager,
        )
        jobs: list[HeartbeatJobDefinition] = []
        for user_id in user_ids:
            jobs.append(
                HeartbeatJobDefinition(
                    job_id=f"hb-agent-main-{user_id}",
                    job_type=HeartbeatJobType.AGENT_TURN,
                    owner_user_id=user_id,
                    every_seconds=config.heartbeat.agent_turn.every_seconds,
                    target=HeartbeatTargetDescriptor.from_dict(
                        config.heartbeat.agent_turn.target.model_dump()
                    ),
                    active_hours_timezone=config.heartbeat.defaults.active_hours.timezone,
                    active_hours_start=config.heartbeat.defaults.active_hours.start,
                    active_hours_end=config.heartbeat.defaults.active_hours.end,
                    isolated_session=config.heartbeat.agent_turn.isolated_session,
                    light_context=config.heartbeat.agent_turn.light_context,
                )
            )
        return jobs

    def _build_channel_heartbeat_jobs() -> list[HeartbeatJobDefinition]:
        if not config.heartbeat.channel_connection.enabled:
            return []
        jobs: list[HeartbeatJobDefinition] = []
        for item in _channel_manager.list_active_connection_descriptors():
            jobs.append(
                HeartbeatJobDefinition(
                    job_id=f"hb-channel-{item['user_id']}-{item['channel_type']}-{item['connection_id']}",
                    job_type=HeartbeatJobType.CHANNEL_CONNECTION,
                    owner_user_id=item["user_id"],
                    every_seconds=config.heartbeat.channel_connection.check_interval_seconds,
                    target=HeartbeatTargetDescriptor(
                        type=HeartbeatTargetType.CHANNEL_CONNECTION,
                        user_id=item["user_id"],
                        channel=item["channel_type"],
                        account_id=item["connection_id"],
                    ),
                    active_hours_timezone=config.heartbeat.defaults.active_hours.timezone,
                    active_hours_start=config.heartbeat.defaults.active_hours.start,
                    active_hours_end=config.heartbeat.defaults.active_hours.end,
                    metadata={
                        "channel_type": item["channel_type"],
                        "connection_id": item["connection_id"],
                    },
                )
            )
        return jobs

    if config.heartbeat.enabled:
        _heartbeat_store = HeartbeatStateStore(workspace_path=workspace_path)
        _heartbeat_runtime = HeartbeatRuntime(
            HeartbeatRuntimeContext(
                store=_heartbeat_store,
                agent_executor=AgentHeartbeatExecutor(_run_agent_heartbeat),
                channel_executor=ChannelHeartbeatExecutor(
                    _run_channel_heartbeat,
                    failure_threshold=config.heartbeat.channel_connection.failure_threshold,
                    degraded_threshold=config.heartbeat.channel_connection.degraded_threshold,
                    reconnect_backoff_seconds=config.heartbeat.channel_connection.reconnect_backoff_seconds,
                ),
                emit_event=_bridge_heartbeat_event,
                max_concurrent_jobs=config.heartbeat.runtime.max_concurrent_jobs,
                emit_runtime_events=config.heartbeat.runtime.emit_runtime_events,
                persist_local_event_log=config.heartbeat.runtime.persist_local_event_log,
            )
        )

        async def _heartbeat_loop() -> None:
            while True:
                for job in await _build_agent_heartbeat_jobs():
                    _heartbeat_runtime.register_job(job)
                for job in _build_channel_heartbeat_jobs():
                    _heartbeat_runtime.register_job(job)
                await _heartbeat_runtime.run_once()
                await asyncio.sleep(config.heartbeat.runtime.tick_seconds)

        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    
    # Auto-start enabled channel connections for default user
    async def start_enabled_connections(db_ready: bool):
        """Start all enabled channel connections on startup."""
        if not db_ready:
            print("[AtlasClaw] Skipping channel auto-start: database not initialized")
            return
        try:
            user_ids = await _collect_runtime_user_ids(
                workspace_path,
                db_initialized=db_ready,
                channel_manager=_channel_manager,
            )
            for user_id in user_ids:
                connections = await _channel_manager.get_user_connections_async(user_id)
                for conn in connections:
                    if conn.get("enabled"):
                        channel_type = conn.get("channel_type")
                        connection_id = conn.get("id")
                        print(
                            f"[AtlasClaw] Starting channel connection: "
                            f"{user_id}/{channel_type}/{connection_id}"
                        )
                        success = await _channel_manager.initialize_connection(
                            user_id, channel_type, connection_id
                        )
                        if success:
                            print(
                                f"[AtlasClaw] Channel connection started: "
                                f"{user_id}/{channel_type}/{connection_id}"
                            )
                        else:
                            print(
                                f"[AtlasClaw] Failed to start channel: "
                                f"{user_id}/{channel_type}/{connection_id}"
                            )
        except Exception as e:
            print(f"[AtlasClaw] Error starting channel connections: {e}")
    
    # Schedule connection startup (will run after event loop starts)
    asyncio.create_task(start_enabled_connections(db_initialized))


    webhook_manager = WebhookDispatchManager(config.webhook, _skill_registry)
    webhook_manager.validate_startup()
    
    print(f"[AtlasClaw] Agent created with model: {seed_token.provider}/{seed_token.model}")


    # Expose config on app.state so routes (e.g. SSO) can access it
    # Preserve existing auth config if already set by create_app()
    existing_auth = getattr(app.state.config, 'auth', None) if hasattr(app.state, 'config') else None
    app.state.config = config
    
    # Coerce auth dict → AuthConfig object so SSO routes can call .provider / .oidc
    from app.atlasclaw.auth.config import AuthConfig
    auth_source = config.auth if config.auth is not None else existing_auth
    
    if auth_source is not None:
        if isinstance(auth_source, dict):
            auth_obj = AuthConfig(**auth_source)
        elif isinstance(auth_source, AuthConfig):
            auth_obj = auth_source
        else:
            auth_obj = None
        
        if auth_obj and auth_obj.enabled:
            app.state.config.auth = auth_obj
            print(f"[AtlasClaw] Auth configured with provider='{auth_obj.provider}'")
        else:
            app.state.config.auth = None
            print("[AtlasClaw] Auth disabled or not configured")
    else:
        app.state.config.auth = None
        print("[AtlasClaw] Auth config not present, running in anonymous mode")

    api_context = APIContext(
        session_manager=_session_manager,
        session_manager_router=_session_manager_router,
        hook_state_store=_hook_state_store,
        memory_sink=_memory_sink,
        context_sink=_context_sink,
        hook_runtime=_hook_runtime,
        heartbeat_runtime=_heartbeat_runtime,
        session_queue=_session_queue,
        skill_registry=_skill_registry,
        agent_runner=_agent_runner,
        agent_runners={"main": _agent_runner},
        service_provider_registry=_global_provider_registry,
        available_providers=available_providers,
        provider_instances=provider_instances,
        webhook_manager=webhook_manager,
    )

    set_api_context(api_context)
    
    print("[AtlasClaw] Application started successfully")
    print(f"[AtlasClaw] Session storage: {_session_manager.sessions_dir}")
    print(f"[AtlasClaw] Skills loaded: {len(_skill_registry.list_skills())} executable, {len(_skill_registry.list_md_skills())} markdown")
    
    yield
    
    # Cleanup on shutdown
    print("[AtlasClaw] Application shutting down")
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AtlasClaw Enterprise Assistant",
        description="AI-powered enterprise assistant framework",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_request_validation_logging(app)
    app.add_middleware(StaticFileCacheMiddleware)

    frontend_dir = Path(__file__).parent.parent / "frontend"
    mount_frontend(app, frontend_dir)

    register_core_routers(
        app,
        api_router=create_router(),
        channel_hooks_router=channel_hooks_router,
        channels_router=channels_router,
        agent_info_router=agent_info_router,
        db_api_router=db_api_router,
    )

    # SPA catch-all: serve index.html for all non-API, non-static routes
    # This MUST be AFTER all include_router calls to avoid intercepting API requests
    if frontend_dir.exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_catch_all(full_path: str):
            """SPA catch-all: serve index.html for all non-API, non-static routes"""
            # Skip API routes - should not reach here, but safety check
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "API endpoint not found"})
            index_file = frontend_dir / "index.html"
            if index_file.exists():
                return render_frontend_html(index_file)
            return {"error": "Frontend index.html not found"}

    setup_auth_middleware_from_config(app)
    app.add_middleware(ExternalBasePathMiddleware)
    return app


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.atlasclaw.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
