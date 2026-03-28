#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Configuration migration script.

Migrates configuration from xuanwu.json to database.
Usage:
    python scripts/migrate_config.py [--config xuanwu.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def expand_env_value(value: str) -> str:
    """Expand environment variable reference."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


async def migrate_tokens(config: dict[str, Any], dry_run: bool = False) -> int:
    """Migrate token configurations to database.
    
    Returns:
        Number of tokens migrated.
    """
    from app.xuanwu.db.database import DatabaseConfig, init_database, get_db_manager
    from app.xuanwu.db.orm.model_token_config import ModelTokenConfigService
    from app.xuanwu.db.schemas import TokenCreate
    
    model_config = config.get("model", {})
    tokens = model_config.get("tokens", [])
    
    if not tokens:
        print("[Token] No tokens found in config")
        return 0
    
    # Initialize database
    db_config = DatabaseConfig.from_config(config)
    await init_database(db_config)
    
    migrated = 0
    async with get_db_manager().get_session() as session:
        for token_data in tokens:
            token_name = token_data.get("id", "unknown")
            
            # Check if token already exists
            existing = await ModelTokenConfigService.get_by_name(session, token_name)
            if existing:
                print(f"[Token] Skipping existing: {token_name}")
                continue
            
            # Expand environment variables
            api_key = expand_env_value(token_data.get("api_key", ""))
            base_url = expand_env_value(token_data.get("base_url", ""))
            
            if dry_run:
                print(f"[Token] Would create: {token_name} ({token_data.get('provider')}/{token_data.get('model')})")
                migrated += 1
                continue
            
            # Create token in database
            create_data = TokenCreate(
                name=token_name,
                provider=token_data.get("provider", "openai"),
                model=token_data.get("model", ""),
                base_url=base_url,
                api_key=api_key,
                api_type=token_data.get("api_type", "openai"),
                priority=token_data.get("priority", 100),
                weight=token_data.get("weight", 100),
                is_active=True,
            )
            
            await ModelTokenConfigService.create(session, create_data)
            print(f"[Token] Migrated: {token_name}")
            migrated += 1
    
    return migrated


async def migrate_agents(config: dict[str, Any], workspace_path: str, dry_run: bool = False) -> int:
    """Migrate agent configurations from Markdown files to database.
    
    Returns:
        Number of agents migrated.
    """
    from app.xuanwu.db.database import DatabaseConfig, init_database, get_db_manager
    from app.xuanwu.db.orm.agent_config import AgentConfigService
    from app.xuanwu.db.schemas import AgentCreate
    from app.xuanwu.agent.agent_definition import AgentLoader
    
    # Initialize database
    db_config = DatabaseConfig.from_config(config)
    await init_database(db_config)
    
    loader = AgentLoader(workspace_path)
    agent_ids = loader.list_agents()
    
    if not agent_ids:
        print("[Agent] No agents found in workspace")
        return 0
    
    migrated = 0
    async with get_db_manager().get_session() as session:
        for agent_id in agent_ids:
            # Check if agent already exists
            existing = await AgentConfigService.get_by_name(session, agent_id)
            if existing:
                print(f"[Agent] Skipping existing: {agent_id}")
                continue
            
            # Load agent configuration
            agent_config = loader.load_agent(agent_id)
            
            if dry_run:
                print(f"[Agent] Would create: {agent_id} ({agent_config.display_name})")
                migrated += 1
                continue
            
            # Create agent in database
            create_data = AgentCreate(
                name=agent_id,
                display_name=agent_config.display_name,
                identity={
                    "avatar": agent_config.avatar,
                    "tone": agent_config.tone,
                },
                soul={
                    "system_prompt": agent_config.system_prompt,
                    "capabilities": agent_config.capabilities,
                    "allowed_providers": agent_config.allowed_providers,
                    "allowed_skills": agent_config.allowed_skills,
                },
                user={
                    "interaction_style": agent_config.interaction_style,
                },
                memory={
                    "memory_strategy": agent_config.memory_strategy,
                    "max_context_rounds": agent_config.max_context_rounds,
                },
                is_active=True,
            )
            
            await AgentConfigService.create(session, create_data)
            print(f"[Agent] Migrated: {agent_id}")
            migrated += 1
    
    return migrated


async def migrate_channels(config: dict[str, Any], workspace_path: str, dry_run: bool = False) -> int:
    """Migrate channel configurations from user_setting.json to database.
    
    Returns:
        Number of channels migrated.
    """
    from app.xuanwu.db.database import DatabaseConfig, init_database, get_db_manager
    from app.xuanwu.db.orm.channel_config import ChannelConfigService
    from app.xuanwu.db.orm.user import UserService
    from app.xuanwu.db.schemas import ChannelCreate
    
    # Initialize database
    db_config = DatabaseConfig.from_config(config)
    await init_database(db_config)
    
    # Look for user_setting.json in default user workspace
    user_setting_path = Path(workspace_path) / "users" / "default" / "user_setting.json"
    
    if not user_setting_path.exists():
        print("[Channel] No user_setting.json found")
        return 0
    
    try:
        with open(user_setting_path, "r", encoding="utf-8") as f:
            user_setting = json.load(f)
    except Exception as e:
        print(f"[Channel] Failed to read user_setting.json: {e}")
        return 0
    
    channels = user_setting.get("channels", {})
    if not channels:
        print("[Channel] No channels found in user_setting.json")
        return 0
    
    migrated = 0
    async with get_db_manager().get_session() as session:
        # Ensure default user exists
        default_user = await UserService.get_by_username(session, "default")
        if not default_user:
            print("[Channel] Default user not found in database")
            return 0
        
        for channel_name, channel_data in channels.items():
            channel_type = channel_data.get("channel_type", channel_data.get("type", "unknown"))
            
            # Check if channel already exists
            existing = await ChannelConfigService.get_by_name(session, channel_name)
            if existing:
                print(f"[Channel] Skipping existing: {channel_name}")
                continue
            
            if dry_run:
                print(f"[Channel] Would create: {channel_name} ({channel_type})")
                migrated += 1
                continue
            
            # Create channel in database
            create_data = ChannelCreate(
                name=channel_name,
                channel_type=channel_type,
                config=channel_data.get("config", {}),
                user_id=default_user.id,
                is_active=channel_data.get("enabled", True),
                is_default=False,
            )
            
            await ChannelConfigService.create(session, create_data)
            print(f"[Channel] Migrated: {channel_name}")
            migrated += 1
    
    return migrated


async def main():
    parser = argparse.ArgumentParser(description="Migrate configuration to database")
    parser.add_argument(
        "--config",
        default="xuanwu.json",
        help="Path to configuration file (default: xuanwu.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually migrating",
    )
    parser.add_argument(
        "--tokens",
        action="store_true",
        help="Migrate only token configurations",
    )
    parser.add_argument(
        "--agents",
        action="store_true",
        help="Migrate only agent configurations",
    )
    parser.add_argument(
        "--channels",
        action="store_true",
        help="Migrate only channel configurations",
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file not found: {config_path}")
        sys.exit(1)
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    workspace_path = config.get("workspace", {}).get("path", "./.xuanwu")
    
    print(f"Configuration file: {config_path}")
    print(f"Workspace: {workspace_path}")
    print(f"Dry run: {args.dry_run}")
    print("-" * 50)
    
    total_migrated = 0
    
    # Determine what to migrate
    migrate_all = not (args.tokens or args.agents or args.channels)
    
    if migrate_all or args.tokens:
        print("\n[1] Migrating tokens...")
        count = await migrate_tokens(config, args.dry_run)
        total_migrated += count
        print(f"    Migrated {count} tokens")
    
    if migrate_all or args.agents:
        print("\n[2] Migrating agents...")
        count = await migrate_agents(config, workspace_path, args.dry_run)
        total_migrated += count
        print(f"    Migrated {count} agents")
    
    if migrate_all or args.channels:
        print("\n[3] Migrating channels...")
        count = await migrate_channels(config, workspace_path, args.dry_run)
        total_migrated += count
        print(f"    Migrated {count} channels")
    
    print("-" * 50)
    print(f"Total migrated: {total_migrated}")
    
    if args.dry_run:
        print("\nThis was a dry run. Run without --dry-run to apply changes.")


if __name__ == "__main__":
    asyncio.run(main())
