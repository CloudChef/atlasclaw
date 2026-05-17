# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Reusable startup helpers extracted from main.py."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Optional

from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.core.trace import create_traced_http_client
from app.atlasclaw.db.database import DatabaseConfig, get_db_manager


def derive_provider_namespace(provider_dir_name: str) -> str:
    """Normalize a provider directory name into a stable provider namespace."""
    normalized = re.sub(r"[^a-z0-9]+", "-", provider_dir_name.strip().lower()).strip("-")
    if normalized.endswith("-provider"):
        normalized = normalized[: -len("-provider")]
    return normalized or provider_dir_name.strip().lower()


def scan_plugin_names(root: Path, *, md_skill_mode: bool = False) -> list[str]:
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


def print_root_plugins(label: str, root: Path, plugins: list[str]) -> None:
    """Print configured root path and discovered plugin names."""
    if not root.exists():
        print(f"[AtlasClaw] {label}: {root} (not found)")
        return

    if plugins:
        print(f"[AtlasClaw] {label}: {root} ({len(plugins)}) -> {', '.join(plugins)}")
    else:
        print(f"[AtlasClaw] {label}: {root} (0) -> (none)")


def check_and_prompt_for_providers(providers_root: Path) -> None:
    """Check if providers_root directory is empty."""

    def _is_empty_or_missing(dir_path: Path) -> bool:
        if not dir_path.exists():
            return True
        try:
            return not any(dir_path.iterdir())
        except (OSError, PermissionError):
            return True

    if _is_empty_or_missing(providers_root):
        print("\n" + "=" * 70)
        print("[AtlasClaw] NOTICE: providers_root directory is empty")
        print("=" * 70)
        print(f"  - Providers root is empty: {providers_root}")
        print("\nTo get started with providers and skills, please run:")
        print("\n  git clone https://github.com/CloudChef/atlasclaw-providers.git")
        print(f"  # Configure atlasclaw.json with \"providers_root\": \"{providers_root}\"")
        print("\nOr manually place provider folders under the providers_root directory above.")
        print("=" * 70 + "\n")


def expand_env_value(value: str) -> str:
    """Expand ${VAR} placeholders from environment for config values."""
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


async def run_alembic_upgrade(db_config: DatabaseConfig) -> None:
    """Run Alembic migrations to head for configured database deployments."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    alembic_ini_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not alembic_ini_path.exists():
        raise RuntimeError(f"alembic.ini not found: {alembic_ini_path}")

    def _upgrade() -> None:
        alembic_cfg = AlembicConfig(str(alembic_ini_path))
        alembic_cfg.set_main_option("sqlalchemy.url", db_config.get_connection_url())
        command.upgrade(alembic_cfg, "head")

    await asyncio.to_thread(_upgrade)


async def run_mysql_alembic_upgrade(db_config: DatabaseConfig) -> None:
    """Run Alembic migrations to head for MySQL deployments."""
    await run_alembic_upgrade(db_config)


def create_pydantic_model(token: TokenEntry):
    """Create pydantic-ai model instance from token entry."""
    if token.api_type == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(
            api_key=token.api_key,
            base_url=token.base_url,
            http_client=create_traced_http_client(token.provider or "anthropic"),
        )
        return AnthropicModel(token.model, provider=provider)

    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        api_key=token.api_key,
        base_url=token.base_url,
        http_client=create_traced_http_client(token.provider or "openai"),
    )
    # Use reasoning_content as the OpenAI-compatible thinking field when available.
    # For models/providers that do not emit it, this remains a no-op.
    profile = OpenAIModelProfile(openai_chat_thinking_field="reasoning_content")
    return OpenAIChatModel(token.model, provider=provider, profile=profile)


def merge_token_entries(primary: list[TokenEntry], secondary: list[TokenEntry]) -> list[TokenEntry]:
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


def build_token_entries(config) -> tuple[list[TokenEntry], Optional[str]]:
    """Build token entries from config."""
    tokens: list[TokenEntry] = []
    for token_cfg in config.model.tokens:
        tokens.append(
            TokenEntry(
                token_id=token_cfg.id,
                provider=token_cfg.provider,
                model=token_cfg.model,
                base_url=expand_env_value(token_cfg.base_url),
                api_key=expand_env_value(token_cfg.api_key),
                api_type=token_cfg.api_type,
                priority=token_cfg.priority,
                weight=token_cfg.weight,
                context_window=token_cfg.context_window,
            )
        )

    if tokens:
        primary_id = config.model.primary
        if primary_id and not any(token.token_id == primary_id for token in tokens):
            print(f"[AtlasClaw] Warning: primary token '{primary_id}' not found in tokens[], using first token")
            primary_id = tokens[0].token_id
        elif not primary_id:
            primary_id = tokens[0].token_id
        return tokens, primary_id

    model_name = config.model.primary
    if "/" in model_name:
        provider, model = model_name.split("/", 1)
    else:
        provider, model = "openai", model_name

    provider_config = config.model.providers.get(provider, {})
    from app.atlasclaw.models.providers import BUILTIN_PROVIDERS

    preset = BUILTIN_PROVIDERS.get(provider)
    base_url = expand_env_value(provider_config.get("base_url", ""))
    api_key = expand_env_value(provider_config.get("api_key", ""))
    api_type = provider_config.get("api_type", "")

    if not base_url and preset:
        base_url = preset.base_url
    if not api_type and preset:
        api_type = preset.api_type
    if not api_key and preset and preset.env_key:
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
            context_window=None,
        )
    ], primary_id


async def build_token_entries_from_db(session) -> tuple[list[TokenEntry], Optional[str]]:
    """Build token entries from database."""
    from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService

    tokens, _total = await ModelTokenConfigService.list_all(session, is_active=True)
    if not tokens:
        return None, None

    token_entries: list[TokenEntry] = []
    for token in tokens:
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
                context_window=None,
            )
        )

    primary_id = token_entries[0].token_id if token_entries else None
    return token_entries, primary_id


def merge_provider_instances(
    primary: dict[str, dict[str, dict[str, Any]]],
    secondary: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Merge provider instances with primary precedence."""
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for source in [secondary, primary]:
        for provider_type, instances in (source or {}).items():
            if not isinstance(instances, dict):
                continue
            provider_bucket = merged.setdefault(provider_type, {})
            for instance_name, instance_cfg in instances.items():
                provider_bucket[instance_name] = dict(instance_cfg or {})
    return merged


async def build_provider_instances_from_db(session) -> dict[str, dict[str, dict[str, Any]]]:
    """Build nested provider instance configs from database."""
    from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService

    return await ServiceProviderConfigService.list_active_as_nested(session)


async def load_agent_config_from_db(session, agent_id: str):
    """Load agent configuration from database."""
    from app.atlasclaw.db.orm.agent_config import AgentConfigService
    from app.atlasclaw.agent.agent_definition import AgentConfig

    agent_model = await AgentConfigService.get_by_name(session, agent_id)
    if agent_model is None:
        return None

    soul = agent_model.soul or {}
    identity = agent_model.identity or {}
    user = agent_model.user or {}
    memory = agent_model.memory or {}

    return AgentConfig(
        agent_id=agent_id,
        name=str(soul.get("name", "") or agent_model.name),
        display_name=agent_model.display_name,
        system_prompt=soul.get("system_prompt", ""),
        capabilities=soul.get("capabilities", []),
        allowed_providers=soul.get("allowed_providers", []),
        allowed_skills=soul.get("allowed_skills", []),
        avatar=identity.get("avatar", "🤻"),
        tone=identity.get("tone", "professional"),
        interaction_style=user.get("interaction_style", ""),
        memory_strategy=memory.get("memory_strategy", ""),
        max_context_rounds=memory.get("max_context_rounds", 20),
    )


async def ensure_default_local_admin(config) -> None:
    """Ensure default local admin account exists when local auth is enabled."""
    from app.atlasclaw.auth.config import AuthConfig
    from app.atlasclaw.db.orm.user import UserService
    from app.atlasclaw.db.schemas import UserCreate

    if config.auth is None:
        return

    auth_cfg = config.auth if isinstance(config.auth, AuthConfig) else AuthConfig(**config.auth)
    provider_name = auth_cfg.provider.lower()
    if provider_name not in {"local", "host_cookie"} or not auth_cfg.local.enabled:
        return

    username = auth_cfg.local.default_admin_username or "admin"
    password = auth_cfg.local.default_admin_password or "admin"

    async with get_db_manager().get_session() as session:
        existing = await UserService.get_by_username(
            session,
            username,
            auth_type="local",
        )
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
                is_active=True,
            ),
        )

    print(f"[AtlasClaw] Created default local admin user: {username}")
