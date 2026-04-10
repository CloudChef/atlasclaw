# -*- coding: utf-8 -*-
"""Unit tests for database infrastructure.

Tests:
- DatabaseManager initialization and connection
- CRUD operations for all entities
- Encryption/decryption for sensitive fields
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import AgentModel, TokenModel, UserModel, ChannelModel
from app.atlasclaw.db.orm import (
    AgentConfigService,
    ModelTokenConfigService,
    UserService,
    ChannelConfigService,
)
from app.atlasclaw.db.orm.model_token_config import decrypt_api_key
from app.atlasclaw.db.orm.user import verify_password, hash_password
from app.atlasclaw.db.schemas import (
    AgentCreate,
    AgentUpdate,
    TokenCreate,
    TokenUpdate,
    UserCreate,
    UserUpdate,
    ChannelCreate,
    ChannelUpdate,
)


@pytest_asyncio.fixture
async def db_manager() -> AsyncGenerator[DatabaseManager, None]:
    """Create a fresh database manager for each test."""
    # Use a temporary file for SQLite
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        
        manager = await init_database(config)
        await manager.create_tables()
        
        yield manager
        
        await manager.close()


@pytest_asyncio.fixture
async def session(db_manager: DatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Get a database session."""
    async with db_manager.get_session() as s:
        yield s


class TestDatabaseManager:
    """Tests for DatabaseManager."""

    def test_singleton_pattern(self):
        """DatabaseManager should be a singleton."""
        manager1 = DatabaseManager.get_instance()
        manager2 = DatabaseManager.get_instance()
        assert manager1 is manager2

    @pytest.mark.asyncio
    async def test_initialize_sqlite(self):
        """Test SQLite database initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            config = DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
            
            manager = DatabaseManager()
            await manager.initialize(config)
            
            assert manager.is_initialized
            assert manager.engine is not None
            
            await manager.close()

    @pytest.mark.asyncio
    async def test_get_session_before_init_raises(self):
        """Getting session before initialization should raise."""
        manager = DatabaseManager()
        # Reset the singleton for this test
        manager._engine = None
        manager._session_factory = None
        
        with pytest.raises(RuntimeError, match="Database not initialized"):
            async with manager.get_session():
                pass

    @pytest.mark.asyncio
    async def test_create_tables(self, db_manager: DatabaseManager):
        """Test creating database tables."""
        # Tables should be created without error
        async with db_manager.engine.begin() as conn:
            # Check agents table exists
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='agents'")
            )
            assert result.fetchone() is not None


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_default_sqlite_config(self):
        """Test default SQLite configuration."""
        config = DatabaseConfig()
        assert config.db_type == "sqlite"
        assert config.sqlite_path == "./data/atlasclaw.db"

    def test_mysql_config(self):
        """Test MySQL configuration."""
        config = DatabaseConfig(
            db_type="mysql",
            mysql_host="localhost",
            mysql_port=3306,
            mysql_database="testdb",
            mysql_user="testuser",
            mysql_password="testpass",
        )
        assert config.db_type == "mysql"
        assert config.mysql_host == "localhost"

    def test_from_config_dict(self):
        """Test creating config from dict."""
        config = DatabaseConfig.from_config({
            "database": {
                "type": "sqlite",
                "sqlite": {"path": "/tmp/test.db"}
            }
        })
        assert config.db_type == "sqlite"
        assert config.sqlite_path == "/tmp/test.db"

    def test_get_connection_url_sqlite(self):
        """Test SQLite connection URL generation."""
        config = DatabaseConfig(db_type="sqlite", sqlite_path="/tmp/test.db")
        url = config.get_connection_url()
        # Note: SQLite URL format includes absolute path with extra slash
        assert "sqlite+aiosqlite:///" in url
        assert "test.db" in url

    def test_get_connection_url_mysql(self):
        """Test MySQL connection URL generation."""
        config = DatabaseConfig(
            db_type="mysql",
            mysql_host="localhost",
            mysql_port=3306,
            mysql_database="testdb",
            mysql_user="user",
            mysql_password="pass",
        )
        url = config.get_connection_url()
        assert "mysql+aiomysql://" in url
        assert "user:pass@localhost:3306/testdb" in url

    def test_unsupported_db_type_raises(self):
        """Test unsupported database type raises ValueError."""
        config = DatabaseConfig(db_type="postgres")
        with pytest.raises(ValueError, match="Unsupported database type"):
            config.get_connection_url()


class TestAgentConfigService:
    """Tests for AgentConfigService CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_agent(self, session: AsyncSession):
        """Test creating an agent configuration."""
        data = AgentCreate(
            name="test_agent",
            display_name="Test Agent",
            identity={"role": "assistant"},
            soul={"prompt": "You are helpful."},
        )
        
        agent = await AgentConfigService.create(session, data)
        
        assert agent.id is not None
        assert agent.name == "test_agent"
        assert agent.display_name == "Test Agent"
        assert agent.is_active is True

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession):
        """Test getting agent by ID."""
        data = AgentCreate(name="test_agent", display_name="Test")
        created = await AgentConfigService.create(session, data)
        
        found = await AgentConfigService.get_by_id(session, created.id)
        
        assert found is not None
        assert found.id == created.id

    @pytest.mark.asyncio
    async def test_get_by_name(self, session: AsyncSession):
        """Test getting agent by name."""
        data = AgentCreate(name="unique_agent", display_name="Unique")
        await AgentConfigService.create(session, data)
        
        found = await AgentConfigService.get_by_name(session, "unique_agent")
        
        assert found is not None
        assert found.name == "unique_agent"

    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession):
        """Test listing all agents with pagination."""
        # Create multiple agents
        for i in range(5):
            await AgentConfigService.create(
                session,
                AgentCreate(name=f"agent_{i}", display_name=f"Agent {i}")
            )
        
        agents, total = await AgentConfigService.list_all(session, page=1, page_size=3)
        
        assert len(agents) == 3
        assert total == 5

    @pytest.mark.asyncio
    async def test_update_agent(self, session: AsyncSession):
        """Test updating an agent."""
        data = AgentCreate(name="test_agent", display_name="Original")
        created = await AgentConfigService.create(session, data)
        
        updated = await AgentConfigService.update(
            session,
            created.id,
            AgentUpdate(display_name="Updated")
        )
        
        assert updated is not None
        assert updated.display_name == "Updated"

    @pytest.mark.asyncio
    async def test_delete_agent(self, session: AsyncSession):
        """Test deleting an agent."""
        data = AgentCreate(name="to_delete", display_name="Delete Me")
        created = await AgentConfigService.create(session, data)
        agent_id = created.id
        
        # Commit the creation
        await session.commit()
        
        deleted = await AgentConfigService.delete(session, agent_id)
        await session.commit()
        
        assert deleted is True
        
        found = await AgentConfigService.get_by_id(session, agent_id)
        assert found is None


class TestModelTokenConfigService:
    """Tests for ModelTokenConfigService CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_token(self, session: AsyncSession):
        """Test creating a token configuration."""
        data = TokenCreate(
            name="test_token",
            provider="openai",
            model="gpt-4",
            api_key="sk-test-key-12345",
            priority=100,
        )
        
        token = await ModelTokenConfigService.create(session, data)
        
        assert token.id is not None
        assert token.name == "test_token"
        assert token.provider == "openai"
        # API key should be encrypted
        assert token.api_key_encrypted != "sk-test-key-12345"

    @pytest.mark.asyncio
    async def test_api_key_encryption(self, session: AsyncSession):
        """Test API key is encrypted and can be decrypted."""
        data = TokenCreate(
            name="secure_token",
            provider="anthropic",
            model="claude-3",
            api_key="sk-secret-key-abc",
        )
        
        token = await ModelTokenConfigService.create(session, data)
        
        # Decrypt and verify
        decrypted = ModelTokenConfigService.get_decrypted_api_key(token)
        assert decrypted == "sk-secret-key-abc"

    @pytest.mark.asyncio
    async def test_masked_api_key(self, session: AsyncSession):
        """Test API key masking for display."""
        data = TokenCreate(
            name="mask_token",
            provider="openai",
            model="gpt-4",
            api_key="sk-1234567890abcdefghijklmnopqrstuvwxyz",
        )
        
        token = await ModelTokenConfigService.create(session, data)
        masked = ModelTokenConfigService.get_masked_api_key(token)
        
        # Should show only first 4 and last 4 chars
        assert masked.startswith("sk-1")
        assert masked.endswith("wxyz")
        assert "..." in masked

    @pytest.mark.asyncio
    async def test_update_token(self, session: AsyncSession):
        """Test updating a token."""
        data = TokenCreate(
            name="update_token",
            provider="openai",
            model="gpt-3.5",
            api_key="old-key",
        )
        created = await ModelTokenConfigService.create(session, data)
        
        updated = await ModelTokenConfigService.update(
            session,
            created.id,
            TokenUpdate(model="gpt-4", api_key="new-key")
        )
        
        assert updated is not None
        assert updated.model == "gpt-4"
        # New key should be encrypted differently
        decrypted = ModelTokenConfigService.get_decrypted_api_key(updated)
        assert decrypted == "new-key"

    @pytest.mark.asyncio
    async def test_list_by_provider(self, session: AsyncSession):
        """Test listing tokens by provider."""
        await ModelTokenConfigService.create(
            session,
            TokenCreate(name="openai_1", provider="openai", model="gpt-4", api_key="key1")
        )
        await ModelTokenConfigService.create(
            session,
            TokenCreate(name="anthropic_1", provider="anthropic", model="claude-3", api_key="key2")
        )
        
        tokens, total = await ModelTokenConfigService.list_all(
            session, provider="openai"
        )
        
        assert len(tokens) == 1
        assert tokens[0].provider == "openai"


class TestUserService:
    """Tests for UserService CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_user(self, session: AsyncSession):
        """Test creating a user."""
        data = UserCreate(
            username="testuser",
            email="test@example.com",
            password="secure_password_123",
            roles={"user": True},
        )
        
        user = await UserService.create(session, data)
        
        assert user.id is not None
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        # Password should be hashed
        assert user.password != "secure_password_123"

    @pytest.mark.asyncio
    async def test_password_hashing(self, session: AsyncSession):
        """Test password hashing and verification."""
        data = UserCreate(
            username="authuser",
            password="my_password",
            roles={"user": True},
        )
        
        user = await UserService.create(session, data)
        
        # Verify correct password (verify_password takes password, hash)
        assert verify_password("my_password", user.password) is True
        # Verify wrong password
        assert verify_password("wrong_password", user.password) is False

    @pytest.mark.asyncio
    async def test_admin_state_is_derived_from_roles(self, session: AsyncSession):
        """Admin state is computed from the assigned admin role."""
        user = await UserService.create(
            session,
            UserCreate(
                username="roleadmin",
                password="password123",
                roles={"admin": True},
            ),
        )

        assert user.is_admin is True

        updated = await UserService.update(
            session,
            user.id,
            UserUpdate(roles={"viewer": True}),
        )

        assert updated is not None
        assert updated.is_admin is False

    @pytest.mark.asyncio
    async def test_get_by_username(self, session: AsyncSession):
        """Test getting user by username."""
        await UserService.create(
            session,
            UserCreate(username="findme", password="password123", roles={})
        )
        
        found = await UserService.get_by_username(session, "findme")
        
        assert found is not None
        assert found.username == "findme"

    @pytest.mark.asyncio
    async def test_get_by_email(self, session: AsyncSession):
        """Test getting user by email."""
        await UserService.create(
            session,
            UserCreate(username="emailuser", email="unique@example.com", password="password123", roles={})
        )
        
        found = await UserService.get_by_email(session, "unique@example.com")
        
        assert found is not None
        assert found.email == "unique@example.com"

    @pytest.mark.asyncio
    async def test_update_user(self, session: AsyncSession):
        """Test updating a user."""
        data = UserCreate(username="updateuser", password="oldpass123", roles={})
        created = await UserService.create(session, data)
        
        updated = await UserService.update(
            session,
            created.id,
            UserUpdate(display_name="Updated Name", password="newpass123")
        )
        
        assert updated is not None
        assert updated.display_name == "Updated Name"
        # Password should be updated
        assert verify_password("newpass123", updated.password) is True

    @pytest.mark.asyncio
    async def test_search_users(self, session: AsyncSession):
        """Test searching users."""
        await UserService.create(
            session,
            UserCreate(username="alice", display_name="Alice Smith", password="password123", roles={})
        )
        await UserService.create(
            session,
            UserCreate(username="bob", display_name="Bob Jones", password="password123", roles={})
        )
        
        users, total = await UserService.list_all(session, search="Alice")
        
        assert len(users) == 1
        assert users[0].username == "alice"


class TestChannelConfigService:
    """Tests for ChannelConfigService CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_channel(self, session: AsyncSession):
        """Test creating a channel configuration."""
        # First create a user
        user = await UserService.create(
            session,
            UserCreate(username="channel_user", password="password123", roles={})
        )
        
        data = ChannelCreate(
            name="test_websocket",
            type="websocket",
            user_id=user.id,
            config={"port": 8765},
        )
        
        channel = await ChannelConfigService.create(session, data)
        
        assert channel.id is not None
        assert channel.name == "test_websocket"
        assert channel.type == "websocket"
        assert channel.is_active is True

    @pytest.mark.asyncio
    async def test_list_by_type(self, session: AsyncSession):
        """Test listing channels by type."""
        user = await UserService.create(
            session,
            UserCreate(username="list_user", password="password123", roles={})
        )
        
        await ChannelConfigService.create(
            session,
            ChannelCreate(name="ws_1", type="websocket", user_id=user.id)
        )
        await ChannelConfigService.create(
            session,
            ChannelCreate(name="sse_1", type="sse", user_id=user.id)
        )
        
        channels, total = await ChannelConfigService.list_all(
            session, channel_type="websocket"
        )
        
        assert len(channels) == 1
        assert channels[0].type == "websocket"

    @pytest.mark.asyncio
    async def test_list_by_user(self, session: AsyncSession):
        """Test listing channels by user."""
        user1 = await UserService.create(
            session,
            UserCreate(username="user1_channels", password="password123", roles={})
        )
        user2 = await UserService.create(
            session,
            UserCreate(username="user2_channels", password="password123", roles={})
        )
        
        await ChannelConfigService.create(
            session,
            ChannelCreate(name="u1_ch", type="websocket", user_id=user1.id)
        )
        await ChannelConfigService.create(
            session,
            ChannelCreate(name="u2_ch", type="sse", user_id=user2.id)
        )
        
        channels, total = await ChannelConfigService.list_all(
            session, user_id=user1.id
        )
        
        assert len(channels) == 1
        assert channels[0].user_id == user1.id

    @pytest.mark.asyncio
    async def test_update_channel(self, session: AsyncSession):
        """Test updating a channel."""
        user = await UserService.create(
            session,
            UserCreate(username="update_channel_user", password="password123", roles={})
        )
        
        data = ChannelCreate(
            name="update_channel",
            type="websocket",
            user_id=user.id,
            config={"port": 8080}
        )
        created = await ChannelConfigService.create(session, data)
        
        updated = await ChannelConfigService.update(
            session,
            created.id,
            ChannelUpdate(config={"port": 9090}, is_active=False)
        )
        
        assert updated is not None
        # Config is encrypted in DB, need to decrypt for verification
        from app.atlasclaw.db.orm.channel_config import _decrypt_config
        decrypted_config = _decrypt_config(updated.config)
        assert decrypted_config["port"] == 9090
        assert updated.is_active is False

    @pytest.mark.asyncio
    async def test_delete_channel(self, session: AsyncSession):
        """Test deleting a channel."""
        user = await UserService.create(
            session,
            UserCreate(username="delete_channel_user", password="password123", roles={})
        )
        
        data = ChannelCreate(
            name="delete_channel",
            type="websocket",
            user_id=user.id
        )
        created = await ChannelConfigService.create(session, data)
        channel_id = created.id
        
        # Commit creation
        await session.commit()
        
        deleted = await ChannelConfigService.delete(session, channel_id)
        await session.commit()
        
        assert deleted is True
        
        found = await ChannelConfigService.get_by_id(session, channel_id)
        assert found is None
