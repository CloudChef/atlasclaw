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

    from app.atlasclaw.models.openai_chat_compat import (
        QwenVllmOpenAIChatModel,
        requires_single_leading_system_message,
    )

    provider = OpenAIProvider(
        api_key=token.api_key,
        base_url=token.base_url,
        http_client=create_traced_http_client(token.provider or "openai"),
    )
    # Use reasoning_content as the OpenAI-compatible thinking field when available.
    # For models/providers that do not emit it, this remains a no-op.
    profile = OpenAIModelProfile(openai_chat_thinking_field="reasoning_content")
    model_class = (
        QwenVllmOpenAIChatModel
        if requires_single_leading_system_message(
            provider=token.provider,
            model=token.model,
            base_url=token.base_url,
        )
        else OpenAIChatModel
    )
    return model_class(token.model, provider=provider, profile=profile)


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


def _provider_allows_empty_api_key(provider: str) -> bool:
    """Return whether a built-in provider explicitly supports keyless access."""

    from app.atlasclaw.models.provider_presets import BUILTIN_PROVIDERS

    preset = BUILTIN_PROVIDERS.get(str(provider or "").strip().lower())
    return preset is not None and not preset.env_key


def _missing_token_fields(token: TokenEntry) -> tuple[str, ...]:
    """Return missing runtime fields under the provider-aware token contract."""

    values = {
        "id": str(token.token_id or "").strip(),
        "provider": str(token.provider or "").strip(),
        "model": str(token.model or "").strip(),
        "base_url": str(token.base_url or "").strip(),
    }
    if not _provider_allows_empty_api_key(token.provider):
        values["api_key"] = str(token.api_key or "").strip()
    return tuple(name for name, value in values.items() if not value)


def _filter_usable_token_entries(
    entries: list[TokenEntry],
    *,
    source: str,
) -> list[TokenEntry]:
    """Apply one usability rule to JSON and both database token sources."""

    usable: list[TokenEntry] = []
    for token in entries:
        missing_fields = _missing_token_fields(token)
        if missing_fields:
            print(
                "[AtlasClaw] Warning: skipping unusable model token "
                f"'{token.token_id or '<empty>'}' from {source}; missing "
                + ", ".join(missing_fields)
            )
            continue
        usable.append(token)
    return usable


def build_token_entries(config) -> tuple[list[TokenEntry], Optional[str]]:
    """Build usable token entries, excluding unresolved environment-backed entries."""
    tokens: list[TokenEntry] = []
    for token_cfg in config.model.tokens:
        token_id = str(token_cfg.id or "").strip()
        provider = expand_env_value(token_cfg.provider).strip()
        model = expand_env_value(token_cfg.model).strip()
        base_url = expand_env_value(token_cfg.base_url).strip()
        raw_api_key = str(token_cfg.api_key or "").strip()
        api_key = expand_env_value(raw_api_key).strip()
        candidate = TokenEntry(
            token_id=token_id,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_type=token_cfg.api_type,
            priority=token_cfg.priority,
            weight=token_cfg.weight,
            context_window=token_cfg.context_window,
        )
        missing_fields = list(_missing_token_fields(candidate))
        # A configured environment reference is a required credential even for a
        # normally keyless provider; silently accepting an unresolved reference
        # would turn a deployment typo into a runtime fallback.
        if raw_api_key and not api_key and "api_key" not in missing_fields:
            missing_fields.append("api_key")
        if missing_fields:
            print(
                "[AtlasClaw] Warning: skipping unusable model token "
                f"'{token_id or '<empty>'}'; missing {', '.join(missing_fields)}"
            )
            continue
        tokens.append(candidate)

    if tokens:
        primary_id = config.model.primary
        if primary_id and not any(token.token_id == primary_id for token in tokens):
            print(f"[AtlasClaw] Warning: primary token '{primary_id}' not found in tokens[], using first token")
            primary_id = tokens[0].token_id
        elif not primary_id:
            primary_id = tokens[0].token_id
        return tokens, primary_id

    if config.model.tokens:
        # Database-backed token configuration is loaded after this helper.
        # Returning an empty JSON set preserves that fallback while main keeps
        # the authoritative "no usable tokens" startup check after merging.
        return [], None

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

    primary_id = f"{provider}-primary"
    candidate = TokenEntry(
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
    missing_fields = _missing_token_fields(candidate)
    if missing_fields:
        # Database model sources have not been loaded yet. Defer the final
        # failure to main's authoritative check after all sources are merged.
        print(
            "[AtlasClaw] Warning: skipping unusable legacy model "
            f"'{config.model.primary}'; missing {', '.join(missing_fields)}"
        )
        return [], None
    return [candidate], primary_id


async def build_token_entries_from_db(session) -> tuple[list[TokenEntry], Optional[str]]:
    """Build token entries from database."""
    from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService

    tokens, _total = await ModelTokenConfigService.list_all(session, is_active=True)
    if not tokens:
        return [], None

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

    usable_entries = _filter_usable_token_entries(
        token_entries,
        source="database tokens",
    )
    primary_id = usable_entries[0].token_id if usable_entries else None
    return usable_entries, primary_id


async def build_token_entries_from_model_configs(session: Any) -> list[TokenEntry]:
    """Load active Model Configuration rows as runtime token entries.

    This is the second database-backed model source. It is intentionally loaded
    before startup decides that the runtime has no usable model credentials.
    """
    from app.atlasclaw.db.orm.model_config import ModelConfigService

    model_configs = await ModelConfigService.list_active(session)
    entries = [
        TokenEntry(
            token_id=model_config.name,
            provider=model_config.provider,
            model=model_config.model_id,
            base_url=model_config.base_url or "",
            api_key=ModelConfigService.get_decrypted_api_key(model_config) or "",
            api_type=model_config.api_type or "openai",
            priority=model_config.priority or 0,
            weight=model_config.weight or 100,
            context_window=model_config.context_window,
        )
        for model_config in model_configs
    ]
    return _filter_usable_token_entries(
        entries,
        source="database model configurations",
    )


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
    """Ensure default local admin account exists when local auth is enabled.

    AtlasClaw HA deployments start with local authentication disabled. If an
    operator subsequently enables it, the supported procedure restarts nodes
    one at a time: the first restarted node creates this account and later
    nodes only read it. This helper deliberately has no HA lease or distributed
    lock for concurrently starting an empty database outside that procedure.
    """
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
