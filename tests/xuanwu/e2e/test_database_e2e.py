# -*- coding: utf-8 -*-
"""End-to-end tests for database persistence.

Tests the complete flow:
1. Database initialization
2. Data persistence through application lifecycle
3. Migration from JSON to database

Run:
    python -m pytest tests/xuanwu/db/test_database_e2e.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.xuanwu.db.database import DatabaseConfig, DatabaseManager, init_database
from app.xuanwu.db.orm import (
    AgentConfigService,
    ModelTokenConfigService,
    UserService,
    ChannelConfigService,
)
from app.xuanwu.db.orm.user import verify_password
from app.xuanwu.db.schemas import (
    AgentCreate,
    TokenCreate,
    UserCreate,
    ChannelCreate,
)


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "e2e_test.db"


@pytest_asyncio.fixture
async def db_manager(db_path: Path) -> AsyncGenerator[DatabaseManager, None]:
    """Create and initialize database manager."""
    config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
    manager = await init_database(config)
    await manager.create_tables()
    
    yield manager
    
    await manager.close()


@pytest.mark.e2e
class TestDatabasePersistence:
    """E2E tests for database persistence."""

    @pytest.mark.asyncio
    async def test_full_agent_lifecycle(self, db_manager: DatabaseManager, db_path: Path):
        """Test complete agent lifecycle: create, read, update, delete."""
        async with db_manager.get_session() as session:
            # Create
            agent = await AgentConfigService.create(
                session,
                AgentCreate(
                    name="lifecycle_agent",
                    display_name="Lifecycle Agent",
                    identity={"role": "assistant"},
                    soul={"prompt": "You are helpful."},
                )
            )
            agent_id = agent.id
        
        # Close and reopen database to test persistence
        await db_manager.close()
        config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        db_manager = await init_database(config)
        
        async with db_manager.get_session() as session:
            # Read after restart
            found = await AgentConfigService.get_by_id(session, agent_id)
            assert found is not None
            assert found.name == "lifecycle_agent"
            assert found.display_name == "Lifecycle Agent"
            
            # Update
            from app.xuanwu.db.schemas import AgentUpdate
            updated = await AgentConfigService.update(
                session,
                agent_id,
                AgentUpdate(display_name="Updated Agent")
            )
            assert updated.display_name == "Updated Agent"
            
            # Delete
            deleted = await AgentConfigService.delete(session, agent_id)
            assert deleted is True
        
        await db_manager.close()

    @pytest.mark.asyncio
    async def test_token_encryption_persistence(self, db_manager: DatabaseManager, db_path: Path):
        """Test that API keys are encrypted and persist correctly."""
        api_key = "sk-secret-api-key-12345"
        
        async with db_manager.get_session() as session:
            token = await ModelTokenConfigService.create(
                session,
                TokenCreate(
                    name="secure_token",
                    provider="openai",
                    model="gpt-4",
                    api_key=api_key,
                )
            )
            token_id = token.id
            
            # Verify encrypted in DB
            assert token.api_key_encrypted != api_key
        
        # Reopen and verify decryption works
        await db_manager.close()
        config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        db_manager = await init_database(config)
        
        async with db_manager.get_session() as session:
            token = await ModelTokenConfigService.get_by_id(session, token_id)
            assert token is not None
            
            decrypted = ModelTokenConfigService.get_decrypted_api_key(token)
            assert decrypted == api_key
        
        await db_manager.close()

    @pytest.mark.asyncio
    async def test_user_password_hashing(self, db_manager: DatabaseManager, db_path: Path):
        """Test that passwords are hashed and verification works after restart."""
        password = "my_secure_password"
        
        async with db_manager.get_session() as session:
            user = await UserService.create(
                session,
                UserCreate(
                    username="auth_user",
                    password=password,
                    roles={},
                )
            )
            user_id = user.id
            
            # Verify hash is not plaintext
            assert user.password != password
        
        # Reopen and verify
        await db_manager.close()
        config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        db_manager = await init_database(config)
        
        async with db_manager.get_session() as session:
            user = await UserService.get_by_id(session, user_id)
            assert user is not None
            
            # Verify correct password (verify_password is module-level function)
            assert verify_password(password, user.password) is True
            # Verify wrong password
            assert verify_password("wrong_password", user.password) is False
        
        await db_manager.close()

    @pytest.mark.asyncio
    async def test_multiple_entities_with_relations(self, db_manager: DatabaseManager):
        """Test creating related entities (user -> channels)."""
        async with db_manager.get_session() as session:
            # Create user
            user = await UserService.create(
                session,
                UserCreate(username="channel_owner", password="password123", roles={})
            )
            
            # Create channels for user
            for i in range(3):
                await ChannelConfigService.create(
                    session,
                    ChannelCreate(
                        name=f"channel_{i}",
                        type="websocket" if i % 2 == 0 else "sse",
                        user_id=user.id,
                        config={"index": i},
                    )
                )
        
        async with db_manager.get_session() as session:
            # Query channels by user
            channels, total = await ChannelConfigService.list_all(
                session, user_id=user.id
            )
            
            assert total == 3
            for ch in channels:
                assert ch.user_id == user.id

    @pytest.mark.asyncio
    async def test_pagination(self, db_manager: DatabaseManager):
        """Test pagination for large datasets."""
        async with db_manager.get_session() as session:
            # Create 25 agents
            for i in range(25):
                await AgentConfigService.create(
                    session,
                    AgentCreate(name=f"page_agent_{i}", display_name=f"Agent {i}")
                )
        
        async with db_manager.get_session() as session:
            # Test pagination
            page1, total = await AgentConfigService.list_all(session, page=1, page_size=10)
            assert len(page1) == 10
            assert total == 25
            
            page2, _ = await AgentConfigService.list_all(session, page=2, page_size=10)
            assert len(page2) == 10
            
            page3, _ = await AgentConfigService.list_all(session, page=3, page_size=10)
            assert len(page3) == 5  # Last page

    @pytest.mark.asyncio
    async def test_unique_constraints(self, db_manager: DatabaseManager):
        """Test that unique constraints are enforced."""
        from sqlalchemy.exc import IntegrityError
        
        async with db_manager.get_session() as session:
            # Create first agent
            await AgentConfigService.create(
                session,
                AgentCreate(name="unique_agent", display_name="Unique")
            )
            await session.commit()
        
        # Use new session for duplicate test
        async with db_manager.get_session() as session:
            # Try to create duplicate - should fail
            try:
                await AgentConfigService.create(
                    session,
                    AgentCreate(name="unique_agent", display_name="Duplicate")
                )
                await session.commit()
                pytest.fail("Should have raised IntegrityError")
            except IntegrityError:
                await session.rollback()


@pytest.mark.e2e
class TestMigrationFromJson:
    """E2E tests for migrating from JSON config to database."""

    @pytest.mark.asyncio
    async def test_migrate_tokens_from_dict(self, db_manager: DatabaseManager):
        """Test migrating tokens from a dict configuration."""
        tokens_config = {
            "openai_token": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "sk-openai-key",
                "priority": 100,
            },
            "anthropic_token": {
                "provider": "anthropic",
                "model": "claude-3",
                "api_key": "sk-anthropic-key",
                "priority": 90,
            }
        }
        
        async with db_manager.get_session() as session:
            # Migrate tokens
            for name, config in tokens_config.items():
                await ModelTokenConfigService.create(
                    session,
                    TokenCreate(
                        name=name,
                        provider=config["provider"],
                        model=config["model"],
                        api_key=config["api_key"],
                        priority=config["priority"],
                    )
                )
        
        async with db_manager.get_session() as session:
            # Verify migration
            tokens, total = await ModelTokenConfigService.list_all(session)
            assert total == 2
            
            openai_token = await ModelTokenConfigService.get_by_name(session, "openai_token")
            assert openai_token is not None
            assert ModelTokenConfigService.get_decrypted_api_key(openai_token) == "sk-openai-key"

    @pytest.mark.asyncio
    async def test_migrate_agents_from_dict(self, db_manager: DatabaseManager):
        """Test migrating agents from a dict configuration."""
        agents_config = {
            "main_agent": {
                "display_name": "Main Agent",
                "identity": {"role": "primary"},
                "soul": {"prompt": "You are the main assistant."},
            },
            "helper_agent": {
                "display_name": "Helper Agent",
                "identity": {"role": "secondary"},
                "soul": {"prompt": "You help with tasks."},
            }
        }
        
        async with db_manager.get_session() as session:
            # Migrate agents
            for name, config in agents_config.items():
                await AgentConfigService.create(
                    session,
                    AgentCreate(
                        name=name,
                        display_name=config["display_name"],
                        identity=config.get("identity"),
                        soul=config.get("soul"),
                    )
                )
        
        async with db_manager.get_session() as session:
            # Verify migration
            agents, total = await AgentConfigService.list_all(session)
            assert total == 2
            
            main = await AgentConfigService.get_by_name(session, "main_agent")
            assert main is not None
            assert main.display_name == "Main Agent"
