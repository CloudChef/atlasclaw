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

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Any, Optional


from dotenv import load_dotenv

import os

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=False)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.atlasclaw.api.routes import create_router, APIContext, install_request_validation_logging, set_api_context
from app.atlasclaw.api.webhook_dispatch import WebhookDispatchManager
from app.atlasclaw.api.channel_hooks import router as channel_hooks_router
from app.atlasclaw.api.channels import router as channels_router, set_channel_manager
from app.atlasclaw.api.agent_info import router as agent_info_router
from app.atlasclaw.api.api_routes import router as db_api_router
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
from app.atlasclaw.core.workspace import WorkspaceInitializer
from app.atlasclaw.agent.agent_definition import AgentLoader
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.manager import ChannelManager
# Import channel handlers from providers
from app.atlasclaw.channels.handlers.feishu import FeishuHandler
from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from app.atlasclaw.channels.handlers.wecom import WeComHandler
from app.atlasclaw.auth import AuthRegistry
from app.atlasclaw.auth.shadow_store import ShadowUserStore
from app.atlasclaw.agent.agent_pool import AgentInstancePool
from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.core.token_health_store import TokenHealthStore
from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database, get_db_manager
from app.atlasclaw.db.orm.model_config import ModelConfigService



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


def _scan_plugin_names(root: Path, *, md_skill_mode: bool = False) -> list[str]:
    """Collect plugin names from a configured root path for startup logging."""
    if not root.exists() or not root.is_dir():
        return []

    names: set[str] = set()
    if md_skill_mode:
        for skill_file in root.glob("*/SKILL.md"):
            if skill_file.is_file():
                names.add(skill_file.parent.name)
        for md_file in root.glob("*.md"):
            if md_file.is_file() and not md_file.name.startswith("_"):
                names.add(md_file.stem)
    else:
        for child in root.iterdir():
            if child.is_dir():
                names.add(child.name)

    return sorted(names)


def _print_root_plugins(label: str, root: Path, plugins: list[str]) -> None:
    """Print configured root path and discovered plugin names."""
    if not root.exists():
        print(f"[AtlasClaw] {label}: {root} (not found)")
        return

    count = len(plugins)
    if plugins:
        print(f"[AtlasClaw] {label}: {root} ({count}) -> {', '.join(plugins)}")
    else:
        print(f"[AtlasClaw] {label}: {root} (0) -> (none)")


def _check_and_prompt_for_providers(providers_root: Path) -> None:
    """Check if providers_root directory is empty."""
    providers_dir = providers_root

    def _is_empty_or_missing(dir_path: Path) -> bool:
        """Check if directory is empty or doesn't exist."""
        if not dir_path.exists():
            return True
        try:
            return not any(dir_path.iterdir())
        except (OSError, PermissionError):
            return True

    providers_empty = _is_empty_or_missing(providers_dir)

    if providers_empty:
        print("\n" + "=" * 70)
        print("[AtlasClaw] NOTICE: providers_root directory is empty")
        print("=" * 70)

        print(f"  - Providers root is empty: {providers_dir}")

        print("\nTo get started with providers and skills, please run:")
        print("\n  git clone https://github.com/CloudChef/atlasclaw-providers.git")
        print(f"  # Configure atlasclaw.json with \"providers_root\": \"{providers_dir}\"")
        print("\nOr manually place provider folders under the providers_root directory above.")
        print("=" * 70 + "\n")


def _expand_env_value(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        import os

        return os.environ.get(value[2:-1], "")
    return value


async def _run_mysql_alembic_upgrade(db_config: DatabaseConfig) -> None:
    """Run Alembic migrations to head for MySQL deployments."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    alembic_ini_path = Path(__file__).parent.parent.parent / "alembic.ini"
    if not alembic_ini_path.exists():
        raise RuntimeError(f"alembic.ini not found: {alembic_ini_path}")

    def _upgrade() -> None:
        alembic_cfg = AlembicConfig(str(alembic_ini_path))
        alembic_cfg.set_main_option("sqlalchemy.url", db_config.get_connection_url())
        command.upgrade(alembic_cfg, "head")

    await asyncio.to_thread(_upgrade)


def _create_pydantic_model(token: TokenEntry):

    if token.api_type == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key=token.api_key, base_url=token.base_url)
        return AnthropicModel(token.model, provider=provider)

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key=token.api_key, base_url=token.base_url)
    return OpenAIChatModel(token.model, provider=provider)


def _merge_token_entries(primary: list[TokenEntry], secondary: list[TokenEntry]) -> list[TokenEntry]:
    """Merge tokens by token_id, keeping primary list precedence on conflicts."""
    merged: list[TokenEntry] = []
    seen_ids: set[str] = set()

    for token in [*primary, *secondary]:
        token_id = (token.token_id or "").strip()
        if not token_id or token_id in seen_ids:
            continue
        seen_ids.add(token_id)
        merged.append(token)

    return merged


def _build_token_entries(config) -> tuple[list[TokenEntry], Optional[str]]:
    """Build token entries from config.

    Returns:
        tuple of (token_entries, primary_token_id)
    """

    tokens: list[TokenEntry] = []
    for token_cfg in config.model.tokens:
        tokens.append(
            TokenEntry(
                token_id=token_cfg.id,
                provider=token_cfg.provider,
                model=token_cfg.model,
                base_url=_expand_env_value(token_cfg.base_url),
                api_key=_expand_env_value(token_cfg.api_key),
                api_type=token_cfg.api_type,
                priority=token_cfg.priority,
                weight=token_cfg.weight,
            )
        )

    if tokens:
        primary_id = config.model.primary
        # Validate primary exists in tokens
        if primary_id and not any(t.token_id == primary_id for t in tokens):
            print(f"[AtlasClaw] Warning: primary token '{primary_id}' not found in tokens[], using first token")
            primary_id = tokens[0].token_id
        elif not primary_id:
            primary_id = tokens[0].token_id
        return tokens, primary_id

    # Legacy fallback: build from providers config
    model_name = config.model.primary
    if "/" in model_name:
        provider, model = model_name.split("/", 1)
    else:
        provider, model = "openai", model_name

    provider_config = config.model.providers.get(provider, {})

    # Fall back to built-in provider presets if no explicit config exists
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS
    preset = BUILTIN_PROVIDERS.get(provider)

    base_url = _expand_env_value(provider_config.get("base_url", ""))
    api_key = _expand_env_value(provider_config.get("api_key", ""))
    api_type = provider_config.get("api_type", "")

    # Use preset defaults if not configured
    if not base_url and preset:
        base_url = preset.base_url
    if not api_type and preset:
        api_type = preset.api_type
    if not api_key and preset and preset.env_key:
        # Try to get API key from environment using preset's env_key
        import os
        api_key = os.environ.get(preset.env_key, "")

    api_type = api_type or "openai"

    if not base_url:
        raise RuntimeError(
            f"Missing base_url for provider '{provider}'. "
            f"Set environment variable or configure in atlasclaw.json under model.providers.{provider}"
        )
    if not api_key:
        env_hint = f" or set {preset.env_key}" if preset and preset.env_key else ""
        raise RuntimeError(
            f"Missing api_key for provider '{provider}'. "
            f"Configure in atlasclaw.json under model.providers.{provider}{env_hint}"
        )

    primary_id = f"{provider}-primary"
    return [
        TokenEntry(
            token_id=primary_id,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_type=api_type,
            priority=100,
            weight=100,
        )
    ], primary_id


async def _build_token_entries_from_db(session) -> tuple[list[TokenEntry], Optional[str]]:
    """Build token entries from database.
    
    Returns:
        tuple of (token_entries, primary_token_id) or (None, None) if database is empty.
    """
    from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService

    tokens, _total = await ModelTokenConfigService.list_all(session, is_active=True)

    if not tokens:
        return None, None

    token_entries: list[TokenEntry] = []
    for token in tokens:
        # Decrypt API key
        api_key = ModelTokenConfigService.get_decrypted_api_key(token) or ""
        token_entries.append(
            TokenEntry(
                token_id=token.name,
                provider=token.provider,
                model=token.model,
                base_url=token.base_url or "",
                api_key=api_key,
                api_type="openai",
                priority=token.priority,
                weight=token.weight,
            )
        )

    # Use the first active token as primary
    primary_id = token_entries[0].token_id if token_entries else None
    return token_entries, primary_id


def _merge_provider_instances(
    primary: dict[str, dict[str, dict[str, Any]]],
    secondary: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Merge provider instances with primary precedence (provider_type + instance_name)."""
    merged: dict[str, dict[str, dict[str, Any]]] = {}

    for source in [secondary, primary]:
        for provider_type, instances in (source or {}).items():
            if not isinstance(instances, dict):
                continue
            provider_bucket = merged.setdefault(provider_type, {})
            for instance_name, instance_cfg in instances.items():
                provider_bucket[instance_name] = dict(instance_cfg or {})

    return merged


async def _build_provider_instances_from_db(session) -> dict[str, dict[str, dict[str, Any]]]:
    """Build nested provider instance configs from database."""
    from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService

    return await ServiceProviderConfigService.list_active_as_nested(session)


async def _load_agent_config_from_db(session, agent_id: str):

    """Load agent configuration from database.
    
    Returns:
        AgentConfig object or None if not found in database.
    """
    from app.atlasclaw.db.orm.agent_config import AgentConfigService
    from app.atlasclaw.agent.agent_definition import AgentConfig
    
    agent_model = await AgentConfigService.get_by_name(session, agent_id)
    if agent_model is None:
        return None
    
    # Convert database model to AgentConfig
    soul = agent_model.soul or {}
    identity = agent_model.identity or {}
    user = agent_model.user or {}
    memory = agent_model.memory or {}
    
    return AgentConfig(
        agent_id=agent_id,
        name=agent_model.name,
        display_name=agent_model.display_name,
        system_prompt=soul.get("system_prompt", ""),
        capabilities=soul.get("capabilities", []),
        allowed_providers=soul.get("allowed_providers", []),
        allowed_skills=soul.get("allowed_skills", []),
        avatar=identity.get("avatar", "🤖"),
        tone=identity.get("tone", "professional"),
        interaction_style=user.get("interaction_style", ""),
        memory_strategy=memory.get("memory_strategy", ""),
        max_context_rounds=memory.get("max_context_rounds", 20),
    )


async def _ensure_default_local_admin(config) -> None:
    """Ensure default local admin account exists when local auth is enabled."""
    import logging

    logger = logging.getLogger(__name__)

    from app.atlasclaw.auth.config import AuthConfig
    from app.atlasclaw.db.orm.user import UserService
    from app.atlasclaw.db.schemas import UserCreate

    if config.auth is None:
        return

    auth_cfg = config.auth if isinstance(config.auth, AuthConfig) else AuthConfig(**config.auth)
    if auth_cfg.provider.lower() != "local" or not auth_cfg.local.enabled:
        return

    username = auth_cfg.local.default_admin_username or "admin"
    password = auth_cfg.local.default_admin_password or "admin"

    async with get_db_manager().get_session() as session:
        existing = await UserService.get_by_username(session, username)
        if existing:
            return

        await UserService.create(
            session,
            UserCreate(
                username=username,
                password=password,
                display_name="Administrator",
                roles={"admin": True},
                auth_type="local",
                is_admin=True,
                is_active=True,
            ),
        )

    print(f"[AtlasClaw] Created default local admin user: {username}")


@asynccontextmanager
async def lifespan(app: FastAPI):


    """Application lifespan handler for startup and shutdown."""
    global _session_manager, _session_queue, _skill_registry, _agent_runner, _global_provider_registry, _channel_manager
    
    config = get_config()
    config_path = get_config_path()
    config_root = config_path.parent if config_path is not None else Path.cwd()
    providers_root = (config_root / config.providers_root).resolve()
    skills_root = (config_root / config.skills_root).resolve()
    channels_root = (config_root / config.channels_root).resolve()

    provider_plugins = _scan_plugin_names(providers_root)
    skill_plugins = _scan_plugin_names(skills_root, md_skill_mode=True)
    channel_plugins = _scan_plugin_names(channels_root)
    _print_root_plugins("providers_root plugins", providers_root, provider_plugins)
    _print_root_plugins("skills_root plugins", skills_root, skill_plugins)
    _print_root_plugins("channels_root plugins", channels_root, channel_plugins)

    # Get workspace path from config
    workspace_path = config.workspace.path

    
    # Initialize workspace directory structure
    workspace_initializer = WorkspaceInitializer(workspace_path)
    if not workspace_initializer.is_initialized():
        workspace_initializer.initialize()
        print(f"[AtlasClaw] Initialized workspace at: {workspace_path}")

    _check_and_prompt_for_providers(providers_root)

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
                await _run_mysql_alembic_upgrade(db_config)
                print("[AtlasClaw] MySQL initialized via Alembic migrations")
            else:
                raise RuntimeError(f"Unsupported database type: {db_config.db_type}")

            db_initialized = True
        except Exception as e:
            print(f"[AtlasClaw] Failed to initialize database ({config.database.type}): {e}")
            raise RuntimeError(f"Database startup failed: {e}") from e

    
    if db_initialized:
        await _ensure_default_local_admin(config)

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
    providers_dir = providers_root
    scan_results = ProviderScanner.scan_providers(providers_dir)
    print(f"[AtlasClaw] Provider scan complete: {len(scan_results['channels'])} channels, {len(scan_results['auth'])} auth providers")
    
    # Load agent definitions - try database first, fallback to file-based
    agent_loader = AgentLoader(workspace_path)
    main_agent_config = None
    
    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                main_agent_config = await _load_agent_config_from_db(session, "main")
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
    _session_queue = SessionQueue(max_concurrent=config.agent_defaults.max_concurrent)
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
                db_provider_instances = await _build_provider_instances_from_db(session)
                if db_provider_instances:
                    merged_provider_instances = _merge_provider_instances(
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
    registered_tools = register_builtin_tools(_skill_registry, profile=ToolProfile.FULL)
    print(f"[AtlasClaw] Registered {len(registered_tools)} built-in tools")
    
    # Load skills from multiple sources (priority: workspace > global > built-in)

    # 1. External provider skills (from providers_root config)
    if providers_root.exists():
        for provider_path in providers_root.iterdir():
            if provider_path.is_dir() and not provider_path.name.startswith(("_", ".")):
                provider_skills = provider_path / "skills"
                if provider_skills.exists():
                    provider_namespace = _derive_provider_namespace(provider_path.name)
                    _skill_registry.load_from_directory(
                        str(provider_skills),
                        location="provider",
                        provider=provider_namespace
                    )

    # 2. Standalone skills (from skills_root config)
    if skills_root.exists():
        _skill_registry.load_from_directory(str(skills_root), location="skills-root")

    from pydantic_ai import Agent
    from app.atlasclaw.core.deps import SkillDeps

    # Load token configurations from both JSON and database.
    # Priority: DB tokens override same token_id from JSON, while JSON tokens are still loaded.
    config_token_entries, config_primary_token_id = _build_token_entries(config)
    token_entries = list(config_token_entries)
    primary_token_id = config_primary_token_id

    if token_entries:
        print(f"[AtlasClaw] Loaded {len(token_entries)} tokens from JSON config")

    if db_initialized:
        try:
            async with get_db_manager().get_session() as session:
                db_token_entries, db_primary_token_id = await _build_token_entries_from_db(session)
                if db_token_entries:
                    print(f"[AtlasClaw] Loaded {len(db_token_entries)} tokens from database")
                    token_entries = _merge_token_entries(db_token_entries, token_entries)
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
                    )
                    token_pool.register_token(entry)
                if db_model_configs:
                    print(f"[AtlasClaw] Loaded {len(db_model_configs)} model configs from database")
        except Exception as e:
            print(f"[AtlasClaw] Warning: Failed to load model configs from database: {e}")

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
        model_instance = _create_pydantic_model(token)
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
    prompt_builder = PromptBuilder(PromptBuilderConfig())
    _agent_runner = AgentRunner(
        agent=agent,
        session_manager=_session_manager,
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
    
    # Auto-start enabled channel connections for default user
    async def start_enabled_connections(db_ready: bool):
        """Start all enabled channel connections on startup."""
        if not db_ready:
            print("[AtlasClaw] Skipping channel auto-start: database not initialized")
            return
        try:
            connections = await _channel_manager.get_user_connections_async("default")
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
    
    # Add Cache-Control middleware for static files
    # This ensures browsers always revalidate with server using ETag/Last-Modified
    # Returns 304 if unchanged, new content if changed
    class StaticFileCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            # Add no-cache header for static resource paths
            path = request.url.path
            if path.startswith(("/static/", "/scripts/", "/styles/", "/locales/")):
                response.headers["Cache-Control"] = "no-cache"
            return response
    
    app.add_middleware(StaticFileCacheMiddleware)
    
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
        
        # Redirect old .html URLs to clean URLs for SPA compatibility
        @app.get("/channels.html", include_in_schema=False)
        async def redirect_channels():
            return RedirectResponse(url="/channels", status_code=301)
        
        @app.get("/models.html", include_in_schema=False)
        async def redirect_models():
            return RedirectResponse(url="/models", status_code=301)

        # Serve login page
        @app.get("/login.html", include_in_schema=False)
        async def serve_login():
            login_path = frontend_dir / "login.html"
            if login_path.exists():
                return FileResponse(str(login_path))
            return {"error": "Login page not found"}

        # Serve config.json
        @app.get("/config.json", include_in_schema=False)
        async def serve_config():
            config_path = frontend_dir / "config.json"
            if config_path.exists():
                return FileResponse(str(config_path))
            return {"apiBaseUrl": ""}

    
    # Include API routes
    api_router = create_router()
    app.include_router(api_router)
    
    # Include channel webhook routes
    app.include_router(channel_hooks_router)
    
    # Include channel management routes
    app.include_router(channels_router)
    
    # Include agent info routes
    app.include_router(agent_info_router)
    
    # Include database API routes (Agent, Token, User CRUD)
    app.include_router(db_api_router)

    # SPA catch-all: serve index.html for all non-API, non-static routes
    # This MUST be AFTER all include_router calls to avoid intercepting API requests
    if frontend_dir.exists():
        from fastapi.responses import JSONResponse
        
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

    # Register AuthMiddleware — must be done at app creation time
    # (middleware cannot be added after startup)
    # Use config from lifespan (already loaded with correct working directory)
    try:
        from app.atlasclaw.auth.middleware import setup_auth_middleware
        from app.atlasclaw.auth.config import AuthConfig
        from app.atlasclaw.core.config import get_config

        # Always use the active global config manager (supports ATLASCLAW_CONFIG in tests/runtime)
        _cfg = get_config()

        _auth = _cfg.auth if _cfg else None

        if isinstance(_auth, dict):
            _auth = AuthConfig(**_auth)
        # Respect the enabled flag — disabled auth runs in anonymous mode
        if _auth is not None and not _auth.enabled:
            _auth = None
        auth_workspace_path = str(Path(_cfg.workspace.path).resolve())
        shadow_store = ShadowUserStore(workspace_path=auth_workspace_path)
        setup_auth_middleware(app, _auth, shadow_store=shadow_store)

        # Store config reference for routes to use
        app.state.config = _cfg
        if _auth is not None and isinstance(_auth, AuthConfig):
            app.state.config.auth = _auth
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"Config setup warning: {_e}")

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
