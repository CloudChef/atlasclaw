# -*- coding: utf-8 -*-
"""
FastAPI application entry point for Xuanwu.

This module creates and configures the FastAPI application, including:
- Static file serving for the frontend
- API routes for session management and agent execution
- CORS middleware for development
- Health check endpoint

Usage:
    uvicorn app.xuanwu.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional


from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=False)

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.xuanwu.api.routes import create_router, APIContext, install_request_validation_logging, set_api_context
from app.xuanwu.api.webhook_dispatch import WebhookDispatchManager
from app.xuanwu.api.channel_hooks import router as channel_hooks_router
from app.xuanwu.api.channels import router as channels_router, set_channel_manager
from app.xuanwu.api.agent_info import router as agent_info_router
from app.xuanwu.api.api_routes import router as db_api_router
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.queue import SessionQueue
from app.xuanwu.session.router import SessionManagerRouter
from app.xuanwu.skills.registry import SkillRegistry
from app.xuanwu.tools.registration import register_builtin_tools
from app.xuanwu.tools.catalog import ToolProfile
from app.xuanwu.agent.runner import AgentRunner
from app.xuanwu.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.xuanwu.core.config import get_config, get_config_path
from app.xuanwu.core.provider_registry import ServiceProviderRegistry
from app.xuanwu.core.provider_scanner import ProviderScanner
from app.xuanwu.core.workspace import WorkspaceInitializer
from app.xuanwu.agent.agent_definition import AgentLoader
from app.xuanwu.channels import ChannelRegistry
from app.xuanwu.channels.manager import ChannelManager
# Import channel handlers from providers
from app.xuanwu.channels.handlers.feishu import FeishuHandler
from app.xuanwu.channels.handlers.dingtalk import DingTalkHandler
from app.xuanwu.channels.handlers.wecom import WeComHandler
from app.xuanwu.auth import AuthRegistry
from app.xuanwu.agent.agent_pool import AgentInstancePool
from app.xuanwu.agent.token_policy import DynamicTokenPolicy
from app.xuanwu.core.token_health_store import TokenHealthStore
from app.xuanwu.core.token_interceptor import TokenHealthInterceptor
from app.xuanwu.core.token_pool import TokenEntry, TokenPool
from app.xuanwu.db.database import DatabaseConfig, init_database, get_db_manager
from app.xuanwu.db.orm.model_config import ModelConfigService
from app.xuanwu.bootstrap.app_factory_helpers import (
    StaticFileCacheMiddleware,
    mount_frontend,
    register_core_routers,
    setup_auth_middleware_from_config,
)
from app.xuanwu.bootstrap.startup_helpers import (
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

@asynccontextmanager
async def lifespan(app: FastAPI):


    """Application lifespan handler for startup and shutdown."""
    global _session_manager, _session_manager_router, _session_queue, _skill_registry, _agent_runner, _global_provider_registry, _channel_manager
    
    config = get_config()
    config_path = get_config_path()
    config_root = config_path.parent if config_path is not None else Path.cwd()
    providers_root = (config_root / config.providers_root).resolve()
    skills_root = (config_root / config.skills_root).resolve()
    channels_root = (config_root / config.channels_root).resolve()

    provider_plugins = scan_plugin_names(providers_root)
    skill_plugins = scan_plugin_names(skills_root, md_skill_mode=True)
    channel_plugins = scan_plugin_names(channels_root)
    print_root_plugins("providers_root plugins", providers_root, provider_plugins)
    print_root_plugins("skills_root plugins", skills_root, skill_plugins)
    print_root_plugins("channels_root plugins", channels_root, channel_plugins)

    # Get workspace path from config
    workspace_path = config.workspace.path

    
    # Initialize workspace directory structure
    workspace_initializer = WorkspaceInitializer(workspace_path)
    was_initialized = workspace_initializer.is_initialized()
    workspace_initializer.initialize()
    if not was_initialized:
        print(f"[Xuanwu] Initialized workspace at: {workspace_path}")

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
                print("[Xuanwu] SQLite initialized via ORM models")
            elif db_config.db_type == "mysql":
                # MySQL: enterprise mode, schema/data changes managed by Alembic
                await run_mysql_alembic_upgrade(db_config)
                print("[Xuanwu] MySQL initialized via Alembic migrations")
            else:
                raise RuntimeError(f"Unsupported database type: {db_config.db_type}")

            db_initialized = True
        except Exception as e:
            print(f"[Xuanwu] Failed to initialize database ({config.database.type}): {e}")
            raise RuntimeError(f"Database startup failed: {e}") from e

    
    if db_initialized:
        await ensure_default_local_admin(config)

    # Register built-in channel handlers (enterprise messaging platforms)
    ChannelRegistry.register("feishu", FeishuHandler)

    ChannelRegistry.register("dingtalk", DingTalkHandler)
    ChannelRegistry.register("wecom", WeComHandler)
    print(f"[Xuanwu] Registered built-in channel handlers")
    
    # Initialize ChannelManager
    _channel_manager = ChannelManager(workspace_path)
    set_channel_manager(_channel_manager)
    print(f"[Xuanwu] Channel manager initialized")
    
    # Scan providers for channel and auth extensions
    providers_dir = providers_root
    scan_results = ProviderScanner.scan_providers(providers_dir)
    print(f"[Xuanwu] Provider scan complete: {len(scan_results['channels'])} channels, {len(scan_results['auth'])} auth providers")
    
    # Load agent definitions - try database first, fallback to file-based
    agent_loader = AgentLoader(workspace_path)
    main_agent_config = None
    
    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                main_agent_config = await load_agent_config_from_db(session, "main")
                if main_agent_config:
                    print(f"[Xuanwu] Loaded agent from database: {main_agent_config.display_name}")
        except Exception as e:
            print(f"[Xuanwu] Warning: Failed to load agent from database: {e}")
    
    # Fallback to file-based agent config
    if main_agent_config is None:
        main_agent_config = agent_loader.load_agent("main")
        print(f"[Xuanwu] Loaded agent from files: {main_agent_config.display_name}")
    
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
    _skill_registry = SkillRegistry(
        allow_script_execution=bool(config.skills.allow_script_execution)
    )
    
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
                        "[Xuanwu] Loaded provider configs from database and merged with JSON"
                    )
        except Exception as e:
            print(f"[Xuanwu] Warning: Failed to load provider configs from database: {e}")

    if merged_provider_instances:
        _global_provider_registry.load_instances_from_config(merged_provider_instances)

    available_providers = {}
    provider_instances = _global_provider_registry.get_all_instance_configs()

    for provider_type in _global_provider_registry.list_providers():
        instances = _global_provider_registry.list_instances(provider_type)
        if instances:
            available_providers[provider_type] = instances
    
    # Register built-in tools (exec, read, write, web_search, etc.)
    registered_tools = register_builtin_tools(_skill_registry, profile=ToolProfile.FULL)
    print(f"[Xuanwu] Registered {len(registered_tools)} built-in tools")
    
    # Load skills from multiple sources (priority: workspace > global > built-in)

    # 1. External provider skills (from providers_root config)
    if providers_root.exists():
        for provider_path in providers_root.iterdir():
            if provider_path.is_dir() and not provider_path.name.startswith(("_", ".")):
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    provider_namespace = derive_provider_namespace(provider_path.name)
                    _skill_registry.load_from_directory(
                        str(provider_skills),
                        location="provider",
                        provider=provider_namespace
                    )

    # 2. Standalone skills (from skills_root config)
    if skills_root.exists():
        _skill_registry.load_from_directory(str(skills_root), location="skills-root")

    from pydantic_ai import Agent
    from app.xuanwu.core.deps import SkillDeps

    # Load token configurations from both JSON and database.
    # Priority: DB tokens override same token_id from JSON, while JSON tokens are still loaded.
    config_token_entries, config_primary_token_id = build_token_entries(config)
    token_entries = list(config_token_entries)
    primary_token_id = config_primary_token_id

    if token_entries:
        print(f"[Xuanwu] Loaded {len(token_entries)} tokens from JSON config")

    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                db_token_entries, db_primary_token_id = await build_token_entries_from_db(session)
                if db_token_entries:
                    print(f"[Xuanwu] Loaded {len(db_token_entries)} tokens from database")
                    token_entries = merge_token_entries(db_token_entries, token_entries)
                    primary_token_id = db_primary_token_id or primary_token_id
                    print(f"[Xuanwu] Combined token pool: {len(token_entries)} (database + JSON)")
        except Exception as e:
            print(f"[Xuanwu] Warning: Failed to load tokens from database: {e}")

    if not token_entries:
        raise RuntimeError("No token configurations found. Please configure tokens in database or xuanwu.json")

    if primary_token_id and not any(t.token_id == primary_token_id for t in token_entries):
        print(f"[Xuanwu] Warning: primary token '{primary_token_id}' not found, using first token")
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
                    print(f"[Xuanwu] Loaded {len(db_model_configs)} model configs from database")
        except Exception as e:
            print(f"[Xuanwu] Warning: Failed to load model configs from database: {e}")

    health_store = TokenHealthStore(workspace_path)
    restored_health = health_store.load()
    for token_id, health in restored_health.items():
        token_pool.restore_health(token_id, health)

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
            system_prompt=agent_cfg.system_prompt or "You are Xuanwu, an enterprise AI assistant.",
        )
        _skill_registry.register_to_agent(built_agent)
        return built_agent

    seed_token = token_pool.tokens.get(primary_token_id) or token_entries[0]
    agent = _build_agent_for("main", seed_token)

    # Create AgentRunner
    prompt_builder = PromptBuilder(PromptBuilderConfig(workspace_path=workspace_path))
    _agent_runner = AgentRunner(
        agent=agent,
        session_manager=_session_manager,
        session_manager_router=_session_manager_router,
        prompt_builder=prompt_builder,
        session_queue=_session_queue,
        agent_id="main",
        token_policy=token_policy,
        agent_pool=agent_pool,
        token_interceptor=token_interceptor,
        agent_factory=_build_agent_for,
    )

    
    # Set agent runner on channel manager for message processing
    _channel_manager.set_agent_runner(_agent_runner)
    _channel_manager.set_session_manager_router(_session_manager_router)
    
    # Auto-start enabled channel connections for default user
    async def start_enabled_connections(db_ready: bool):
        """Start all enabled channel connections on startup."""
        if not db_ready:
            print("[Xuanwu] Skipping channel auto-start: database not initialized")
            return
        try:
            connections = await _channel_manager.get_user_connections_async("default")
            for conn in connections:
                if conn.get("enabled"):
                    channel_type = conn.get("channel_type")
                    connection_id = conn.get("id")
                    print(f"[Xuanwu] Starting channel connection: {channel_type}/{connection_id}")
                    success = await _channel_manager.initialize_connection(
                        "default", channel_type, connection_id
                    )
                    if success:
                        print(f"[Xuanwu] Channel connection started: {channel_type}/{connection_id}")
                    else:
                        print(f"[Xuanwu] Failed to start channel: {channel_type}/{connection_id}")
        except Exception as e:
            print(f"[Xuanwu] Error starting channel connections: {e}")
    
    # Schedule connection startup (will run after event loop starts)
    asyncio.create_task(start_enabled_connections(db_initialized))


    webhook_manager = WebhookDispatchManager(config.webhook, _skill_registry)
    webhook_manager.validate_startup()
    
    print(f"[Xuanwu] Agent created with model: {seed_token.provider}/{seed_token.model}")


    # Expose config on app.state so routes (e.g. SSO) can access it
    # Preserve existing auth config if already set by create_app()
    existing_auth = getattr(app.state.config, 'auth', None) if hasattr(app.state, 'config') else None
    app.state.config = config
    
    # Coerce auth dict → AuthConfig object so SSO routes can call .provider / .oidc
    from app.xuanwu.auth.config import AuthConfig
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
            print(f"[Xuanwu] Auth configured with provider='{auth_obj.provider}'")
        else:
            app.state.config.auth = None
            print("[Xuanwu] Auth disabled or not configured")
    else:
        app.state.config.auth = None
        print("[Xuanwu] Auth config not present, running in anonymous mode")

    api_context = APIContext(
        session_manager=_session_manager,
        session_manager_router=_session_manager_router,
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
    
    print("[Xuanwu] Application started successfully")
    print(f"[Xuanwu] Session storage: {_session_manager.sessions_dir}")
    print(f"[Xuanwu] Skills loaded: {len(_skill_registry.list_skills())} executable, {len(_skill_registry.list_md_skills())} markdown")
    
    yield
    
    # Cleanup on shutdown
    print("[Xuanwu] Application shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Xuanwu Enterprise Assistant",
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
                return FileResponse(str(index_file))
            return {"error": "Frontend index.html not found"}

    setup_auth_middleware_from_config(app)
    return app


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.xuanwu.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
