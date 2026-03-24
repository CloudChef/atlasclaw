# -*- coding: utf-8 -*-
"""Tests for Model Configuration feature.

Tests:
- ModelConfigService CRUD operations
- API key encryption/decryption
- API endpoints for model config management
- Validation tests
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import ModelConfigModel
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.orm.model_token_config import decrypt_api_key, encrypt_api_key, mask_api_key
from app.atlasclaw.db.schemas import (
    ModelConfigCreate,
    ModelConfigUpdate,
    ModelConfigResponse,
    ModelConfigListResponse,
)
from app.atlasclaw.api.api_routes import router


# ============== Fixtures ==============


@pytest_asyncio.fixture
async def db_manager() -> AsyncGenerator[DatabaseManager, None]:
    """Create a fresh database manager for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_model_configs.db"
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


def _init_test_db(tmp_path: Path) -> DatabaseManager:
    """Initialize test database synchronously (for API tests).
    
    This follows the pattern from test_local_auth_api.py, using asyncio.run()
    to ensure the global _db_manager is properly set before TestClient runs.
    """
    async def _init():
        db_path = tmp_path / "test_model_configs_api.db"
        manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(db_path))
        )
        await manager.create_tables()
        return manager

    return asyncio.run(_init())


def _cleanup_db(manager: DatabaseManager) -> None:
    """Clean up database manager synchronously."""
    asyncio.run(manager.close())


@pytest.fixture
def api_db_manager(tmp_path):
    """Create database manager for API tests using sync initialization.
    
    This sets the global _db_manager, so FastAPI endpoints using get_db_session()
    will automatically use this test database.
    """
    manager = _init_test_db(tmp_path)
    yield manager
    _cleanup_db(manager)


@pytest.fixture
def app(api_db_manager: DatabaseManager):
    """Create test FastAPI application.
    
    No dependency override needed since init_database() sets the global _db_manager,
    and get_db_session() uses get_db_manager() which returns that global.
    """
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# ============== ModelConfigService Unit Tests ==============


class TestModelConfigService:
    """Tests for ModelConfigService CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_model_config(self, session: AsyncSession):
        """Test creating a model configuration with all fields."""
        data = ModelConfigCreate(
            name="gpt-4-turbo",
            display_name="GPT-4 Turbo",
            provider="openai",
            model_id="gpt-4-turbo-preview",
            base_url="https://api.openai.com/v1",
            api_key="sk-test-key-12345678901234567890",
            api_type="openai",
            context_window=128000,
            max_tokens=4096,
            temperature=0.7,
            description="GPT-4 Turbo model for general use",
            capabilities={"vision": True, "function_calling": True},
            priority=100,
            weight=100,
            is_active=True,
            config={"stream": True},
        )

        model = await ModelConfigService.create(session, data)

        assert model.id is not None
        assert model.name == "gpt-4-turbo"
        assert model.display_name == "GPT-4 Turbo"
        assert model.provider == "openai"
        assert model.model_id == "gpt-4-turbo-preview"
        assert model.base_url == "https://api.openai.com/v1"
        assert model.api_type == "openai"
        assert model.context_window == 128000
        assert model.max_tokens == 4096
        assert model.temperature == 0.7
        assert model.description == "GPT-4 Turbo model for general use"
        assert model.priority == 100
        assert model.weight == 100
        assert model.is_active is True
        assert model.created_at is not None
        assert model.updated_at is not None

    @pytest.mark.asyncio
    async def test_create_with_api_key_encryption(self, session: AsyncSession):
        """Test that API key is encrypted when stored in database."""
        original_key = "sk-secret-api-key-123456"
        data = ModelConfigCreate(
            name="encrypted_key_test",
            provider="openai",
            model_id="gpt-4",
            api_key=original_key,
        )

        model = await ModelConfigService.create(session, data)

        # API key should be encrypted (not the same as original)
        assert model.api_key is not None
        assert model.api_key != original_key
        # Encrypted key should be decryptable
        decrypted = decrypt_api_key(model.api_key)
        assert decrypted == original_key

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession):
        """Test getting model config by ID."""
        data = ModelConfigCreate(
            name="get_by_id_test",
            provider="anthropic",
            model_id="claude-3-opus",
        )
        created = await ModelConfigService.create(session, data)

        found = await ModelConfigService.get_by_id(session, created.id)

        assert found is not None
        assert found.id == created.id
        assert found.name == "get_by_id_test"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, session: AsyncSession):
        """Test getting non-existent model config returns None."""
        found = await ModelConfigService.get_by_id(session, "non-existent-uuid")

        assert found is None

    @pytest.mark.asyncio
    async def test_get_by_name(self, session: AsyncSession):
        """Test getting model config by name."""
        data = ModelConfigCreate(
            name="unique_model_name",
            provider="openai",
            model_id="gpt-3.5-turbo",
        )
        await ModelConfigService.create(session, data)

        found = await ModelConfigService.get_by_name(session, "unique_model_name")

        assert found is not None
        assert found.name == "unique_model_name"

    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession):
        """Test listing all model configs with pagination."""
        # Create multiple model configs
        for i in range(5):
            await ModelConfigService.create(
                session,
                ModelConfigCreate(
                    name=f"model_{i}",
                    provider="openai",
                    model_id=f"gpt-model-{i}",
                    priority=i,
                )
            )

        models, total = await ModelConfigService.list_all(session, page=1, page_size=3)

        assert len(models) == 3
        assert total == 5

    @pytest.mark.asyncio
    async def test_list_all_filter_by_provider(self, session: AsyncSession):
        """Test listing model configs filtered by provider."""
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="openai_model", provider="openai", model_id="gpt-4")
        )
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="anthropic_model", provider="anthropic", model_id="claude-3")
        )
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="openai_model_2", provider="openai", model_id="gpt-3.5")
        )

        models, total = await ModelConfigService.list_all(session, provider="openai")

        assert len(models) == 2
        assert total == 2
        assert all(m.provider == "openai" for m in models)

    @pytest.mark.asyncio
    async def test_list_all_filter_by_is_active(self, session: AsyncSession):
        """Test listing model configs filtered by active status."""
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="active_model", provider="openai", model_id="gpt-4", is_active=True)
        )
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="inactive_model", provider="openai", model_id="gpt-3.5", is_active=False)
        )

        active_models, active_total = await ModelConfigService.list_all(session, is_active=True)
        inactive_models, inactive_total = await ModelConfigService.list_all(session, is_active=False)

        assert active_total == 1
        assert all(m.is_active for m in active_models)
        assert inactive_total == 1
        assert all(not m.is_active for m in inactive_models)

    @pytest.mark.asyncio
    async def test_list_active(self, session: AsyncSession):
        """Test listing only active model configs."""
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="active_1", provider="openai", model_id="gpt-4", is_active=True, priority=10)
        )
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="inactive_1", provider="openai", model_id="gpt-3.5", is_active=False)
        )
        await ModelConfigService.create(
            session,
            ModelConfigCreate(name="active_2", provider="anthropic", model_id="claude-3", is_active=True, priority=20)
        )

        active_models = await ModelConfigService.list_active(session)

        assert len(active_models) == 2
        assert all(m.is_active for m in active_models)
        # Should be sorted by priority descending
        assert active_models[0].priority >= active_models[1].priority

    @pytest.mark.asyncio
    async def test_update(self, session: AsyncSession):
        """Test updating a model config."""
        data = ModelConfigCreate(
            name="update_test",
            provider="openai",
            model_id="gpt-4",
            temperature=0.5,
        )
        created = await ModelConfigService.create(session, data)

        updated = await ModelConfigService.update(
            session,
            created.id,
            ModelConfigUpdate(
                display_name="Updated Display Name",
                temperature=0.9,
                max_tokens=8192,
            )
        )

        assert updated is not None
        assert updated.display_name == "Updated Display Name"
        assert updated.temperature == 0.9
        assert updated.max_tokens == 8192
        # Original fields should be unchanged
        assert updated.provider == "openai"

    @pytest.mark.asyncio
    async def test_update_not_found(self, session: AsyncSession):
        """Test updating non-existent model config returns None."""
        result = await ModelConfigService.update(
            session,
            "non-existent-uuid",
            ModelConfigUpdate(display_name="New Name")
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, session: AsyncSession):
        """Test deleting a model config."""
        data = ModelConfigCreate(
            name="delete_test",
            provider="openai",
            model_id="gpt-4",
        )
        created = await ModelConfigService.create(session, data)
        model_id = created.id

        await session.commit()

        deleted = await ModelConfigService.delete(session, model_id)
        await session.commit()

        assert deleted is True
        found = await ModelConfigService.get_by_id(session, model_id)
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, session: AsyncSession):
        """Test deleting non-existent model config returns False."""
        deleted = await ModelConfigService.delete(session, "non-existent-uuid")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_get_masked_api_key(self, session: AsyncSession):
        """Test API key masking format."""
        data = ModelConfigCreate(
            name="mask_test",
            provider="openai",
            model_id="gpt-4",
            api_key="sk-1234567890abcdefghijklmnopqrstuvwxyz",
        )
        model = await ModelConfigService.create(session, data)

        masked = ModelConfigService.get_masked_api_key(model)

        # Should show first 4 and last 4 chars
        assert masked is not None
        assert masked.startswith("sk-1")
        assert masked.endswith("wxyz")
        assert "..." in masked

    @pytest.mark.asyncio
    async def test_get_decrypted_api_key(self, session: AsyncSession):
        """Test decryption returns original API key."""
        original_key = "sk-my-secret-api-key-for-testing"
        data = ModelConfigCreate(
            name="decrypt_test",
            provider="openai",
            model_id="gpt-4",
            api_key=original_key,
        )
        model = await ModelConfigService.create(session, data)

        decrypted = ModelConfigService.get_decrypted_api_key(model)

        assert decrypted == original_key

    @pytest.mark.asyncio
    async def test_get_capabilities(self, session: AsyncSession):
        """Test getting capabilities dict from JSON."""
        capabilities = {"vision": True, "function_calling": True, "streaming": True}
        data = ModelConfigCreate(
            name="capabilities_test",
            provider="openai",
            model_id="gpt-4-vision",
            capabilities=capabilities,
        )
        model = await ModelConfigService.create(session, data)

        result = ModelConfigService.get_capabilities(model)

        assert result == capabilities

    @pytest.mark.asyncio
    async def test_get_config(self, session: AsyncSession):
        """Test getting config dict from JSON."""
        config = {"stream": True, "timeout": 30}
        data = ModelConfigCreate(
            name="config_test",
            provider="openai",
            model_id="gpt-4",
            config=config,
        )
        model = await ModelConfigService.create(session, data)

        result = ModelConfigService.get_config(model)

        assert result == config


# ============== API Endpoint Tests ==============


class TestModelConfigAPI:
    """Tests for Model Config API endpoints."""

    def test_create_model_config_api(self, client):
        """Test POST /api/model-configs returns 201."""
        response = client.post(
            "/api/model-configs",
            json={
                "name": "api_create_test",
                "provider": "openai",
                "model_id": "gpt-4",
                "api_key": "sk-test-key",
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "api_create_test"
        assert data["provider"] == "openai"
        assert data["model_id"] == "gpt-4"
        assert "id" in data

    def test_list_model_configs_api(self, client):
        """Test GET /api/model-configs returns 200 with items."""
        # Create some model configs first
        client.post(
            "/api/model-configs",
            json={"name": "list_test_1", "provider": "openai", "model_id": "gpt-4"}
        )
        client.post(
            "/api/model-configs",
            json={"name": "list_test_2", "provider": "anthropic", "model_id": "claude-3"}
        )

        response = client.get("/api/model-configs")

        assert response.status_code == 200
        data = response.json()
        assert "model_configs" in data
        assert "total" in data
        assert data["total"] >= 2

    def test_get_model_config_api(self, client):
        """Test GET /api/model-configs/{id} returns 200."""
        create_response = client.post(
            "/api/model-configs",
            json={"name": "get_test", "provider": "openai", "model_id": "gpt-4"}
        )
        config_id = create_response.json()["id"]

        response = client.get(f"/api/model-configs/{config_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == config_id
        assert data["name"] == "get_test"

    def test_get_model_config_not_found_api(self, client):
        """Test GET /api/model-configs/{id} returns 404 for non-existent."""
        response = client.get("/api/model-configs/non-existent-uuid")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_model_config_api(self, client):
        """Test PUT /api/model-configs/{id} returns 200."""
        create_response = client.post(
            "/api/model-configs",
            json={"name": "update_api_test", "provider": "openai", "model_id": "gpt-4"}
        )
        config_id = create_response.json()["id"]

        response = client.put(
            f"/api/model-configs/{config_id}",
            json={"display_name": "Updated Display Name", "temperature": 0.9}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "Updated Display Name"
        assert data["temperature"] == 0.9

    def test_delete_model_config_api(self, client):
        """Test DELETE /api/model-configs/{id} returns 204."""
        create_response = client.post(
            "/api/model-configs",
            json={"name": "delete_api_test", "provider": "openai", "model_id": "gpt-4"}
        )
        config_id = create_response.json()["id"]

        response = client.delete(f"/api/model-configs/{config_id}")

        assert response.status_code == 204

        # Verify deleted
        get_response = client.get(f"/api/model-configs/{config_id}")
        assert get_response.status_code == 404

    def test_api_key_masked_in_response(self, client):
        """Test that API key is masked in all API responses."""
        # Create with API key
        create_response = client.post(
            "/api/model-configs",
            json={
                "name": "masked_key_test",
                "provider": "openai",
                "model_id": "gpt-4",
                "api_key": "sk-1234567890abcdefghijklmnopqrstuvwxyz",
            }
        )

        assert create_response.status_code == 201
        data = create_response.json()
        # Should have masked key, not original
        assert "api_key_masked" in data
        if data["api_key_masked"]:
            assert "..." in data["api_key_masked"]
            assert data["api_key_masked"] != "sk-1234567890abcdefghijklmnopqrstuvwxyz"

        # Also verify in GET response
        config_id = data["id"]
        get_response = client.get(f"/api/model-configs/{config_id}")
        get_data = get_response.json()
        if get_data["api_key_masked"]:
            assert "..." in get_data["api_key_masked"]

    def test_filter_by_provider(self, client):
        """Test GET /api/model-configs?provider=openai filters correctly."""
        # Create models with different providers
        client.post(
            "/api/model-configs",
            json={"name": "filter_openai", "provider": "openai", "model_id": "gpt-4"}
        )
        client.post(
            "/api/model-configs",
            json={"name": "filter_anthropic", "provider": "anthropic", "model_id": "claude-3"}
        )

        response = client.get("/api/model-configs?provider=openai")

        assert response.status_code == 200
        data = response.json()
        assert all(m["provider"] == "openai" for m in data["model_configs"])

    def test_filter_by_is_active(self, client):
        """Test GET /api/model-configs?is_active=true filters correctly."""
        # Create active and inactive models
        client.post(
            "/api/model-configs",
            json={"name": "filter_active", "provider": "openai", "model_id": "gpt-4", "is_active": True}
        )
        client.post(
            "/api/model-configs",
            json={"name": "filter_inactive", "provider": "openai", "model_id": "gpt-3.5", "is_active": False}
        )

        active_response = client.get("/api/model-configs?is_active=true")
        inactive_response = client.get("/api/model-configs?is_active=false")

        assert active_response.status_code == 200
        active_data = active_response.json()
        assert all(m["is_active"] for m in active_data["model_configs"])

        assert inactive_response.status_code == 200
        inactive_data = inactive_response.json()
        assert all(not m["is_active"] for m in inactive_data["model_configs"])


# ============== Validation Tests ==============


class TestModelConfigValidation:
    """Tests for model config validation."""

    def test_duplicate_name_rejected(self, client):
        """Test creating two configs with same name returns 409."""
        # Create first config
        client.post(
            "/api/model-configs",
            json={"name": "duplicate_test", "provider": "openai", "model_id": "gpt-4"}
        )

        # Try to create second with same name
        response = client.post(
            "/api/model-configs",
            json={"name": "duplicate_test", "provider": "anthropic", "model_id": "claude-3"}
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    def test_required_fields(self, client):
        """Test missing required fields returns 422."""
        # Missing name
        response = client.post(
            "/api/model-configs",
            json={"provider": "openai", "model_id": "gpt-4"}
        )
        assert response.status_code == 422

        # Missing provider
        response = client.post(
            "/api/model-configs",
            json={"name": "test", "model_id": "gpt-4"}
        )
        assert response.status_code == 422

        # Missing model_id
        response = client.post(
            "/api/model-configs",
            json={"name": "test", "provider": "openai"}
        )
        assert response.status_code == 422

    def test_update_non_existent_returns_404(self, client):
        """Test updating non-existent config returns 404."""
        response = client.put(
            "/api/model-configs/non-existent-uuid",
            json={"display_name": "New Name"}
        )

        assert response.status_code == 404

    def test_delete_non_existent_returns_404(self, client):
        """Test deleting non-existent config returns 404."""
        response = client.delete("/api/model-configs/non-existent-uuid")

        assert response.status_code == 404


# ============== Encryption Utility Tests ==============


class TestEncryptionUtilities:
    """Tests for encryption utility functions."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test encryption and decryption returns original value."""
        original = "sk-my-secret-api-key"
        encrypted = encrypt_api_key(original)
        decrypted = decrypt_api_key(encrypted)

        assert decrypted == original
        assert encrypted != original

    def test_mask_api_key_format(self):
        """Test API key masking format."""
        key = "sk-1234567890abcdef"
        masked = mask_api_key(key)

        assert masked.startswith("sk-1")
        assert masked.endswith("cdef")
        assert "..." in masked

    def test_mask_short_api_key(self):
        """Test masking very short API keys."""
        short_key = "sk-abc"
        masked = mask_api_key(short_key)

        # Short keys should be fully masked
        assert masked == "****"
