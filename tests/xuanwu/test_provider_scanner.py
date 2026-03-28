# -*- coding: utf-8 -*-
"""Tests for ProviderScanner."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.xuanwu.auth import AuthRegistry
from app.xuanwu.channels import ChannelRegistry
from app.xuanwu.core.provider_scanner import ProviderScanner


class TestProviderScanner:
    """Test ProviderScanner functionality."""

    def setup_method(self):
        """Clear registries before each test."""
        ChannelRegistry._handlers.clear()
        ChannelRegistry._instances.clear()
        ChannelRegistry._connections.clear()
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

    def test_scan_providers_with_channels(self):
        """Test scanning providers with channel extensions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create provider structure
            provider_dir = Path(temp_dir) / "test_provider"
            channels_dir = provider_dir / "channels"
            channels_dir.mkdir(parents=True)
            
            # Create a test channel handler file
            handler_file = channels_dir / "test_channel.py"
            handler_file.write_text('''
from app.xuanwu.channels.handler import ChannelHandler
from app.xuanwu.channels.models import ChannelMode, InboundMessage, OutboundMessage, SendResult

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
        from app.xuanwu.channels.models import ChannelValidationResult
        return ChannelValidationResult(valid=True)
    
    def describe_schema(self):
        return {"type": "object"}
''')
            
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            assert "test_channel" in results["channels"]
            assert ChannelRegistry.get("test_channel") is not None

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
from app.xuanwu.auth.provider import AuthProvider

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
            channels_dir = provider_dir / "channels"
            channels_dir.mkdir(parents=True)
            
            # Create private file
            private_file = channels_dir / "_private.py"
            private_file.write_text("# This should be ignored")
            
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            assert "_private" not in results["channels"]

    def test_scan_providers_multiple_providers(self):
        """Test scanning multiple providers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create first provider
            provider1_dir = Path(temp_dir) / "provider1"
            channels1_dir = provider1_dir / "channels"
            channels1_dir.mkdir(parents=True)
            
            handler1_file = channels1_dir / "channel1.py"
            handler1_file.write_text('''
from app.xuanwu.channels.handler import ChannelHandler
from app.xuanwu.channels.models import ChannelMode, SendResult

class Channel1Handler(ChannelHandler):
    channel_type = "channel1"
    channel_name = "Channel 1"
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
        from app.xuanwu.channels.models import ChannelValidationResult
        return ChannelValidationResult(valid=True)
    
    def describe_schema(self):
        return {"type": "object"}
''')
            
            # Create second provider
            provider2_dir = Path(temp_dir) / "provider2"
            channels2_dir = provider2_dir / "channels"
            channels2_dir.mkdir(parents=True)
            
            handler2_file = channels2_dir / "channel2.py"
            handler2_file.write_text('''
from app.xuanwu.channels.handler import ChannelHandler
from app.xuanwu.channels.models import ChannelMode, SendResult

class Channel2Handler(ChannelHandler):
    channel_type = "channel2"
    channel_name = "Channel 2"
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
        from app.xuanwu.channels.models import ChannelValidationResult
        return ChannelValidationResult(valid=True)
    
    def describe_schema(self):
        return {"type": "object"}
''')
            
            results = ProviderScanner.scan_providers(Path(temp_dir))
            
            assert "channel1" in results["channels"]
            assert "channel2" in results["channels"]
            assert ChannelRegistry.get("channel1") is not None
            assert ChannelRegistry.get("channel2") is not None

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
