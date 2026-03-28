# -*- coding: utf-8 -*-
"""Tests for auth registry."""

from __future__ import annotations

import pytest

from app.xuanwu.auth import AuthProvider, AuthRegistry


class MockAuthProvider(AuthProvider):
    """Mock auth provider for testing."""
    
    auth_id = "mock"
    auth_name = "Mock Auth"
    
    async def authenticate(self, credentials):
        return {"id": "user-123", "email": "test@example.com"}
    
    def provider_name(self):
        return "mock"


class AnotherMockProvider(AuthProvider):
    """Another mock auth provider for testing."""
    
    auth_id = "another"
    auth_name = "Another Auth"
    
    async def authenticate(self, credentials):
        return None
    
    def provider_name(self):
        return "another"


class TestAuthRegistry:
    """Test AuthRegistry functionality."""
    
    def setup_method(self):
        """Clear registry before each test."""
        AuthRegistry._providers.clear()
    
    def test_register_provider(self):
        """Test registering an auth provider."""
        AuthRegistry.register("mock", MockAuthProvider)
        
        assert "mock" in AuthRegistry._providers
        assert AuthRegistry._providers["mock"] == MockAuthProvider
    
    def test_get_provider(self):
        """Test getting a registered provider."""
        AuthRegistry.register("mock", MockAuthProvider)
        
        provider_class = AuthRegistry.get("mock")
        assert provider_class == MockAuthProvider
    
    def test_get_nonexistent_provider(self):
        """Test getting a non-existent provider."""
        provider_class = AuthRegistry.get("nonexistent")
        assert provider_class is None
    
    def test_list_providers(self):
        """Test listing registered providers."""
        AuthRegistry.register("mock", MockAuthProvider)
        AuthRegistry.register("another", AnotherMockProvider)
        
        providers = AuthRegistry.list_providers()
        assert len(providers) == 2
        
        ids = [p["id"] for p in providers]
        assert "mock" in ids
        assert "another" in ids
    
    def test_register_invalid_provider(self):
        """Test registering an invalid provider class."""
        class NotAProvider:
            pass
        
        with pytest.raises(ValueError):
            AuthRegistry.register("invalid", NotAProvider)
    
    def test_provider_class_attributes(self):
        """Test provider class attributes."""
        assert MockAuthProvider.auth_id == "mock"
        assert MockAuthProvider.auth_name == "Mock Auth"
        assert AnotherMockProvider.auth_id == "another"
