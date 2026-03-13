# -*- coding: utf-8 -*-
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

from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=False)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.atlasclaw.api.routes import create_router, APIContext, install_request_validation_logging, set_api_context
from app.atlasclaw.api.webhook_dispatch import WebhookDispatchManager
from app.atlasclaw.api.channel_hooks import router as channel_hooks_router
from app.atlasclaw.api.channels import router as channels_router, set_channel_manager
from app.atlasclaw.api.agent_info import router as agent_info_router
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools
from app.atlasclaw.tools.catalog import ToolProfile
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.core.config import get_config, get_config_path
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.core.provider_scanner import ProviderScanner
from app.atlasclaw.core.workspace import WorkspaceInitializer, UserWorkspaceInitializer
from app.atlasclaw.agent.agent_definition import AgentLoader
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.manager import ChannelManager
# Import channel handlers from providers
from app.atlasclaw.channels.handlers.feishu import FeishuHandler
from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from providers.wecom.channels.wecom import WeComHandler
from app.atlasclaw.auth import AuthRegistry


_global_provider_registry: Optional[ServiceProviderRegistry] = None


# Global context components
_session_manager: Optional[SessionManager] = None
_session_queue: Optional[SessionQueue] = None
_skill_registry: Optional[SkillRegistry] = None
_agent_runner: Optional[AgentRunner] = None
_channel_manager: Optional[ChannelManager] = None


def _derive_provider_namespace(provider_dir_name: str) -> str:
    """Normalize a provider directory name into a stable provider namespace."""
    normalized = re.sub(r"[^a-z0-9]+", "-", provider_dir_name.strip().lower()).strip("-")
    if normalized.endswith("-provider"):
        normalized = normalized[: -len("-provider")]
    return normalized or provider_dir_name.strip().lower()


def _check_and_prompt_for_providers_skills(workspace_path: str | Path, providers_root: Path) -> None:
    """Check if providers_root and workspace skills directories are empty.

    Args:
        workspace_path: Path to the workspace directory (the .atlasclaw directory).
        providers_root: Resolved provider repository path.
    """
    workspace = Path(workspace_path)
    providers_dir = providers_root
    skills_dir = workspace / "skills"  # skills is directly under workspace

    def _is_empty_or_missing(dir_path: Path) -> bool:
        """Check if directory is empty or doesn't exist."""
        if not dir_path.exists():
            return True
        try:
            return not any(dir_path.iterdir())
        except (OSError, PermissionError):
            return True

    providers_empty = _is_empty_or_missing(providers_dir)
    skills_empty = _is_empty_or_missing(skills_dir)

    if providers_empty or skills_empty:
        print("\n" + "=" * 70)
        print("[AtlasClaw] NOTICE: providers_root and/or workspace skills directories are empty")
        print("=" * 70)

        if providers_empty:
            print(f"  - Providers root is empty: {providers_dir}")
        if skills_empty:
            print(f"  - Workspace skills directory is empty: {skills_dir}")

        print("\nTo get started with providers and skills, please run:")
        print("\n  git clone https://github.com/CloudChef/atlasclaw-providers.git")
        print(f"  # Configure atlasclaw.json with \"providers_root\": \"{providers_dir}\"")
        print("\nOr manually place provider folders under the providers_root directory above.")
        print("=" * 70 + "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    global _session_manager, _session_queue, _skill_registry, _agent_runner, _global_provider_registry, _channel_manager
    
    config = get_config()
    config_path = get_config_path()
    config_root = config_path.parent if config_path is not None else Path.cwd()
    providers_root = (config_root / config.providers_root).resolve()
    
    # Get workspace path from config
    workspace_path = config.workspace.path
    
    # Initialize workspace directory structure
    workspace_initializer = WorkspaceInitializer(workspace_path)
    if not workspace_initializer.is_initialized():
        workspace_initializer.initialize()
        print(f"[AtlasClaw] Initialized workspace at: {workspace_path}")

    # Check if providers and skills are empty and prompt user
    _check_and_prompt_for_providers_skills(workspace_path, providers_root)

    # Initialize default user directory (for non-authenticated mode)
    default_user_initializer = UserWorkspaceInitializer(workspace_path, "default")
    if not default_user_initializer.is_initialized():
        default_user_initializer.initialize()
        print(f"[AtlasClaw] Initialized default user directory")
    
    # Register built-in channel handlers (enterprise messaging platforms)
    ChannelRegistry.register("feishu", FeishuHandler)
    ChannelRegistry.register("dingtalk", DingTalkHandler)
    ChannelRegistry.register("wecom", WeComHandler)
    print(f"[AtlasClaw] Registered built-in channel handlers")
    
    # Initialize ChannelManager
    _channel_manager = ChannelManager(workspace_path)
    set_channel_manager(_channel_manager)
    print(f"[AtlasClaw] Channel manager initialized")
    
    # Scan providers for channel and auth extensions
    providers_dir = Path(workspace_path) / ".atlasclaw" / "providers"
    scan_results = ProviderScanner.scan_providers(providers_dir)
    print(f"[AtlasClaw] Provider scan complete: {len(scan_results['channels'])} channels, {len(scan_results['auth'])} auth providers")
    
    # Load agent definitions
    agent_loader = AgentLoader(workspace_path)
    main_agent_config = agent_loader.load_agent("main")
    print(f"[AtlasClaw] Loaded agent: {main_agent_config.display_name}")
    
    # Initialize SessionManager with new workspace-based path
    _session_manager = SessionManager(
        workspace_path=workspace_path,
        user_id="default",
        reset_mode=config.reset.mode,
        daily_reset_hour=config.reset.daily_hour,
        idle_reset_minutes=config.reset.idle_minutes,
    )
    _session_queue = SessionQueue()
    _skill_registry = SkillRegistry()
    
    _global_provider_registry = ServiceProviderRegistry()
    _global_provider_registry.load_from_directory(providers_root)
    if config.service_providers:
        _global_provider_registry.load_instances_from_config(config.service_providers)
    
    available_providers = {}
    provider_instances = _global_provider_registry.get_all_instance_configs()
    for provider_type in _global_provider_registry.list_providers():
        instances = _global_provider_registry.list_instances(provider_type)
        if instances:
            available_providers[provider_type] = instances
    
    # Register built-in tools (exec, read, write, web_search, etc.)
    registered_tools = register_builtin_tools(_skill_registry, profile=ToolProfile.FULL)
    print(f"[AtlasClaw] Registered {len(registered_tools)} built-in tools")
    
    # Load skills from multiple sources (priority: workspace > global > built-in)

    # 1. Built-in skills from app providers
    providers_dir = Path(__file__).parent / "providers"
    if providers_dir.exists():
        for provider_path in providers_dir.iterdir():
            if provider_path.is_dir():
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    _skill_registry.load_from_directory(str(provider_skills), location="built-in")



    # 2. Workspace provider skills (providers inside workspace)
    workspace_providers_dir = Path(workspace_path) / "providers"
    if workspace_providers_dir.exists():
        for provider_path in workspace_providers_dir.iterdir():
            if provider_path.is_dir():
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    provider_name = provider_path.name
                    _skill_registry.load_from_directory(
                        str(provider_skills), 
                        location="workspace-provider",
                        provider=provider_name
                    )

    # 3. Global skills (user home directory)
    global_skills = Path.home() / ".atlasclaw" / "skills"
    if global_skills.exists():
        _skill_registry.load_from_directory(str(global_skills), location="global")
    
    # 4. Workspace skills (highest priority)
    workspace_skills = Path(workspace_path) / "skills"
    if workspace_skills.exists():
        _skill_registry.load_from_directory(str(workspace_skills), location="workspace")
    
    model_name = config.model.primary
    
    # Resolve model provider config from atlasclaw.json
    if "/" in model_name:
        provider, model = model_name.split("/", 1)
    else:
        provider, model = "openai", model_name
    
    provider_config = config.model.providers.get(provider, {})
    if not provider_config:
        raise RuntimeError(
            f"Provider '{provider}' not configured in atlasclaw.json. "
            f"Please add provider config under model.providers.{provider}"
        )
    
    import os
    base_url = provider_config.get("base_url", "")
    api_key = provider_config.get("api_key", "")
    api_type = provider_config.get("api_type", "openai")
    
    # Expand environment variables in config (e.g., "${ANTHROPIC_BASE_URL}")
    if base_url.startswith("${") and base_url.endswith("}"):
        env_var = base_url[2:-1]
        base_url = os.environ.get(env_var, "")
    if api_key.startswith("${") and api_key.endswith("}"):
        env_var = api_key[2:-1]
        api_key = os.environ.get(env_var, "")
    
    # Validate credentials
    if not base_url:
        raise RuntimeError(
            f"Missing base_url for provider '{provider}'. "
            f"Set environment variable or configure in atlasclaw.json"
        )
    if not api_key:
        raise RuntimeError(
            f"Missing api_key for provider '{provider}'. "
            f"Set environment variable or configure in atlasclaw.json"
        )
    
    # Set environment variables based on api_type
    if api_type == "anthropic":
        os.environ["ANTHROPIC_BASE_URL"] = base_url
        os.environ["ANTHROPIC_API_KEY"] = api_key
        pydantic_model = f"anthropic:{model}"
    else:
        # Default to OpenAI-compatible API
        os.environ["OPENAI_BASE_URL"] = base_url
        os.environ["OPENAI_API_KEY"] = api_key
        pydantic_model = f"openai:{model}"
    
    # Create PydanticAI Agent
    from pydantic_ai import Agent
    from app.atlasclaw.core.deps import SkillDeps
    
    agent = Agent(
        pydantic_model,
        deps_type=SkillDeps,
        system_prompt=main_agent_config.system_prompt or "You are AtlasClaw, an enterprise AI assistant.",
    )
    
    # Register all skills as agent tools
    _skill_registry.register_to_agent(agent)
    
    # Create AgentRunner
    prompt_builder = PromptBuilder(PromptBuilderConfig())
    _agent_runner = AgentRunner(
        agent=agent,
        session_manager=_session_manager,
        prompt_builder=prompt_builder,
        session_queue=_session_queue,
    )
    
    # Set agent runner on channel manager for message processing
    _channel_manager.set_agent_runner(_agent_runner)
    
    # Auto-start enabled channel connections for default user
    async def start_enabled_connections():
        """Start all enabled channel connections on startup."""
        try:
            connections = _channel_manager.get_user_connections("default")
            for conn in connections:
                if conn.get("enabled"):
                    channel_type = conn.get("channel_type")
                    connection_id = conn.get("id")
                    print(f"[AtlasClaw] Starting channel connection: {channel_type}/{connection_id}")
                    success = await _channel_manager.initialize_connection(
                        "default", channel_type, connection_id
                    )
                    if success:
                        print(f"[AtlasClaw] Channel connection started: {channel_type}/{connection_id}")
                    else:
                        print(f"[AtlasClaw] Failed to start channel: {channel_type}/{connection_id}")
        except Exception as e:
            print(f"[AtlasClaw] Error starting channel connections: {e}")
    
    # Schedule connection startup (will run after event loop starts)
    import asyncio
    asyncio.create_task(start_enabled_connections())

    webhook_manager = WebhookDispatchManager(config.webhook, _skill_registry)
    webhook_manager.validate_startup()
    
    print(f"[AtlasClaw] Agent created with model: {pydantic_model}")
    
    api_context = APIContext(
        session_manager=_session_manager,
        session_queue=_session_queue,
        skill_registry=_skill_registry,
        agent_runner=_agent_runner,
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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AtlasClaw Enterprise Assistant",
        description="AI-powered enterprise assistant framework",
        version="0.1.0",
        lifespan=lifespan,
    )
    
    # CORS middleware for development
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
    
    # Mount static files for frontend
    frontend_dir = Path(__file__).parent.parent / "frontend"
    
    if frontend_dir.exists():
        # Mount static directories
        static_dir = frontend_dir / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        
        scripts_dir = frontend_dir / "scripts"
        if scripts_dir.exists():
            app.mount("/scripts", StaticFiles(directory=str(scripts_dir)), name="scripts")
        
        styles_dir = frontend_dir / "styles"
        if styles_dir.exists():
            app.mount("/styles", StaticFiles(directory=str(styles_dir)), name="styles")
        
        locales_dir = frontend_dir / "locales"
        if locales_dir.exists():
            app.mount("/locales", StaticFiles(directory=str(locales_dir)), name="locales")
        
        # Serve index.html for root path
        @app.get("/", include_in_schema=False)
        async def serve_index():
            index_path = frontend_dir / "index.html"
            if index_path.exists():
                return FileResponse(str(index_path))
            return {"error": "Frontend not found"}
        
        # Serve channels.html for channel management
        @app.get("/channels.html", include_in_schema=False)
        async def serve_channels():
            channels_path = frontend_dir / "channels.html"
            if channels_path.exists():
                return FileResponse(str(channels_path))
            return {"error": "Channels page not found"}
        
        # Serve config.json
        @app.get("/config.json", include_in_schema=False)
        async def serve_config():
            config_path = frontend_dir / "config.json"
            if config_path.exists():
                return FileResponse(str(config_path))
            return {"apiBaseUrl": "http://127.0.0.1:8000"}
    
    # Include API routes
    api_router = create_router()
    app.include_router(api_router)
    
    # Include channel webhook routes
    app.include_router(channel_hooks_router)
    
    # Include channel management routes
    app.include_router(channels_router)
    
    # Include agent info routes
    app.include_router(agent_info_router)
    
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
