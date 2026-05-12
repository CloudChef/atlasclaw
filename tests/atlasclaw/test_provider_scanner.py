# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for ProviderScanner."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.atlasclaw.auth import AuthRegistry
from app.atlasclaw.core.provider_scanner import ProviderScanner


class TestProviderScanner:
    """Test ProviderScanner functionality."""

    def setup_method(self):
        """Clear registries before each test."""
        AuthRegistry._providers.clear()

    def test_scan_providers_empty_directory(self):
        """Test scanning empty directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            assert results["auth"] == []
            assert results["channels"] == []
            assert results["skills"] == []
            assert results["errors"] == []

    def test_scan_providers_nonexistent_directory(self):
        """Test scanning non-existent directory."""
        results = ProviderScanner.scan_providers(Path("/nonexistent/path"))
        
        assert results["auth"] == []
        assert results["channels"] == []
        assert results["skills"] == []

    def test_scan_providers_ignores_provider_channels(self):
        """Provider-owned channel extensions should not be loaded into core."""
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_dir = Path(temp_dir) / "test_provider"
            channels_dir = provider_dir / "channels"
            channels_dir.mkdir(parents=True)

            handler_file = channels_dir / "test_channel.py"
            handler_file.write_text('''
from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.models import ChannelMode, InboundMessage, OutboundMessage, SendResult

class TestChannelHandler(ChannelHandler):
    channel_type = "test_channel"
    channel_name = "Test Channel"
    channel_mode = ChannelMode.BIDIRECTIONAL
    
    async def setup(self, connection_config):
        return True
    
    async def start(self, context):
        return True
    
    async def stop(self):
        return True
    
    async def handle_inbound(self, request):
        return None
    
    async def send_message(self, outbound):
        return SendResult(success=True)
    
    async def validate_config(self, config):
        from app.atlasclaw.channels.models import ChannelValidationResult
        return ChannelValidationResult(valid=True)
    
    def describe_schema(self):
        return {"type": "object"}
''')

            results = ProviderScanner.scan_providers(Path(temp_dir))

            assert results["channels"] == []

    def test_scan_providers_with_auth(self):
        """Test scanning providers with auth extensions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create provider structure
            provider_dir = Path(temp_dir) / "test_provider"
            auth_dir = provider_dir / "auth"
            auth_dir.mkdir(parents=True)
            
            # Create a test auth provider file
            auth_file = auth_dir / "test_auth.py"
            auth_file.write_text('''
from app.atlasclaw.auth.provider import AuthProvider

class TestAuthProvider(AuthProvider):
    auth_id = "test_auth"
    auth_name = "Test Auth"
    
    async def authenticate(self, credentials):
        return {"id": "user-123", "email": "test@example.com"}
    
    def provider_name(self):
        return "test_auth"
''')
            
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            assert "test_auth" in results["auth"]
            assert AuthRegistry.get("test_auth") is not None

    def test_scan_providers_with_canonical_auth_provider_base(self):
        """Auth extensions should work with the documented AuthProvider import."""
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_dir = Path(temp_dir) / "test_provider"
            auth_dir = provider_dir / "auth"
            auth_dir.mkdir(parents=True)

            auth_file = auth_dir / "modern_auth.py"
            auth_file.write_text('''
from app.atlasclaw.auth.providers.base import AuthProvider

class ModernAuthProvider(AuthProvider):
    auth_id = "modern_auth"
    auth_name = "Modern Auth"

    async def authenticate(self, credential):
        return None

    def provider_name(self):
        return "modern_auth"
''')

            results = ProviderScanner.scan_providers(Path(temp_dir))

            assert "modern_auth" in results["auth"]
            assert AuthRegistry.get("modern_auth") is not None

    def test_scan_providers_with_config(self):
        """Test scanning providers with config.json."""
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_dir = Path(temp_dir) / "test_provider"
            provider_dir.mkdir()
            
            # Create config.json
            config_file = provider_dir / "config.json"
            config_file.write_text('''
{
    "name": "Test Provider",
    "version": "1.0.0",
    "description": "A test provider"
}
''')
            
            # Should not raise error
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            # Config is loaded but not returned in results
            assert results["errors"] == []

    def test_scan_providers_skips_private_files(self):
        """Test that private files (starting with _) are skipped."""
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_dir = Path(temp_dir) / "test_provider"
            auth_dir = provider_dir / "auth"
            auth_dir.mkdir(parents=True)

            private_file = auth_dir / "_private.py"
            private_file.write_text("# This should be ignored")

            results = ProviderScanner.scan_providers(Path(temp_dir))

            assert "_private" not in results["auth"]

    def test_scan_providers_multiple_providers(self):
        """Test scanning multiple providers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            provider1_dir = Path(temp_dir) / "provider1"
            auth1_dir = provider1_dir / "auth"
            auth1_dir.mkdir(parents=True)

            auth1_file = auth1_dir / "auth1.py"
            auth1_file.write_text('''
from app.atlasclaw.auth.provider import AuthProvider

class AuthProviderOne(AuthProvider):
    auth_id = "auth1"
    auth_name = "Auth 1"

    async def authenticate(self, credentials):
        return {"id": "user-1"}

    def provider_name(self):
        return "auth1"
''')

            provider2_dir = Path(temp_dir) / "provider2"
            auth2_dir = provider2_dir / "auth"
            auth2_dir.mkdir(parents=True)

            auth2_file = auth2_dir / "auth2.py"
            auth2_file.write_text('''
from app.atlasclaw.auth.provider import AuthProvider

class AuthProviderTwo(AuthProvider):
    auth_id = "auth2"
    auth_name = "Auth 2"

    async def authenticate(self, credentials):
        return {"id": "user-2"}

    def provider_name(self):
        return "auth2"
''')

            results = ProviderScanner.scan_providers(Path(temp_dir))

            assert "auth1" in results["auth"]
            assert "auth2" in results["auth"]
            assert AuthRegistry.get("auth1") is not None
            assert AuthRegistry.get("auth2") is not None

    def test_import_module_from_path(self):
        """Test importing module from file path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "test_module.py"
            test_file.write_text('''
TEST_VALUE = "hello"

def test_func():
    return "world"
''')
            
            module = ProviderScanner._import_module_from_path(test_file)
            
            assert module.TEST_VALUE == "hello"
            assert module.test_func() == "world"
