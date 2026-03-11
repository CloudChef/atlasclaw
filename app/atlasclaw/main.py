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
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=False)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.atlasclaw.api.routes import create_router, APIContext, install_request_validation_logging, set_api_context
from app.atlasclaw.api.webhook_dispatch import WebhookDispatchManager
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools
from app.atlasclaw.tools.catalog import ToolProfile
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.core.config import get_config, get_config_path
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.core.workspace import WorkspaceInitializer, UserWorkspaceInitializer
from app.atlasclaw.agent.agent_definition import AgentLoader


_global_provider_registry: Optional[ServiceProviderRegistry] = None


# Global context components
_session_manager: Optional[SessionManager] = None
_session_queue: Optional[SessionQueue] = None
_skill_registry: Optional[SkillRegistry] = None
_agent_runner: Optional[AgentRunner] = None


def _check_and_prompt_for_providers_skills(workspace_path: str | Path) -> None:
    """Check if providers and skills directories are empty and prompt user to download.

    Args:
        workspace_path: Path to the workspace directory.
    """
    workspace = Path(workspace_path)
    atlasclaw_dir = workspace / ".atlasclaw"
    providers_dir = atlasclaw_dir / "providers"
    skills_dir = atlasclaw_dir / "skills"

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
        print("[AtlasClaw] NOTICE: Providers and/or Skills directories are empty")
        print("=" * 70)

        if providers_empty:
            print(f"  - Providers directory is empty: {providers_dir}")
        if skills_empty:
            print(f"  - Skills directory is empty: {skills_dir}")

        print("\nTo get started with providers and skills, please run:")
        print("\n  git clone https://github.com/CloudChef/atlasclaw-providers.git")
        print(f"  cp -r atlasclaw-providers/providers/* {providers_dir}/")
        print(f"  cp -r atlasclaw-providers/skills/* {skills_dir}/")
        print("\nOr manually download and extract to the directories above.")
        print("=" * 70 + "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    global _session_manager, _session_queue, _skill_registry, _agent_runner, _global_provider_registry
    
    config = get_config()
    config_root = get_config_path().parent if get_config_path() is not None else Path.cwd()
    
    # Get workspace path from config
    workspace_path = config.workspace.path
    
    # Initialize workspace directory structure
    workspace_initializer = WorkspaceInitializer(workspace_path)
    if not workspace_initializer.is_initialized():
        workspace_initializer.initialize()
        print(f"[AtlasClaw] Initialized workspace at: {workspace_path}")

    # Check if providers and skills are empty and prompt user
    _check_and_prompt_for_providers_skills(workspace_path)

    # Initialize default user directory (for non-authenticated mode)
    default_user_initializer = UserWorkspaceInitializer(workspace_path, "default")
    if not default_user_initializer.is_initialized():
        default_user_initializer.initialize()
        print(f"[AtlasClaw] Initialized default user directory")
    
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
    # 1. Built-in provider skills (lowest priority)
    providers_dir = Path(__file__).parent / "providers"
    if providers_dir.exists():
        for provider_path in providers_dir.iterdir():
            if provider_path.is_dir():
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    _skill_registry.load_from_directory(str(provider_skills), location="built-in")

    # 2. Global skills (user home directory)
    global_skills = Path.home() / ".atlasclaw" / "skills"
    if global_skills.exists():
        _skill_registry.load_from_directory(str(global_skills), location="global")
    
    # 3. Workspace skills (highest priority)
    workspace_skills = Path(workspace_path) / ".atlasclaw" / "skills"
    if workspace_skills.exists():
        _skill_registry.load_from_directory(str(workspace_skills), location="workspace")
    
    # 4. Additional webhook skill roots
    if config.webhook.enabled and config.webhook.skill_sources:
        for source in config.webhook.skill_sources:
            source_root = (config_root / source.root).resolve()
            if source_root.exists():
                _skill_registry.load_from_directory(
                    str(source_root),
                    location="external",
                    provider=source.provider,
                )
    
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
