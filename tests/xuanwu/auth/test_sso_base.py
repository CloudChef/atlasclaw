# -*- coding: utf-8 -*-
"""Tests for BaseSSOProvider base class."""

from __future__ import annotations

import base64
import hashlib

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.xuanwu.auth.providers.sso_base import BaseSSOProvider
from app.xuanwu.auth.models import AuthResult


class ConcreteSSO(BaseSSOProvider):
    """Concrete implementation of BaseSSOProvider for testing."""

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict:
        return {"access_token": "test_token"}

    async def fetch_userinfo(self, access_token: str) -> dict:
        return {"sub": "user1", "name": "Test User"}

    def extract_identity(self, tokens: dict, userinfo: dict) -> AuthResult:
        return AuthResult(
            subject=userinfo.get("sub", ""),
            display_name=userinfo.get("name", ""),
        )


class ConcreteSSOWithExtraParams(BaseSSOProvider):
    """Concrete implementation with extra auth params for testing."""

    def _extra_auth_params(self) -> dict[str, str]:
        return {"prompt": "consent"}

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict:
        return {"access_token": "test_token"}

    async def fetch_userinfo(self, access_token: str) -> dict:
        return {"sub": "user1", "name": "Test User"}

    def extract_identity(self, tokens: dict, userinfo: dict) -> AuthResult:
        return AuthResult(
            subject=userinfo.get("sub", ""),
            display_name=userinfo.get("name", ""),
        )


class TestBaseSSOProvider:
    """Test BaseSSOProvider functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="https://app.example.com/callback",
            scopes=["openid", "profile", "email"],
            pkce_enabled=True,
            pkce_method="S256",
        )

    def test_generate_pkce_returns_verifier_and_challenge(self):
        """Test that generate_pkce returns (str, str) and both are non-empty."""
        verifier, challenge = self.provider.generate_pkce()

        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 0
        assert len(challenge) > 0

    def test_generate_pkce_s256_challenge_is_correct(self):
        """Test that challenge = base64url(sha256(verifier)) for S256 method."""
        verifier, challenge = self.provider.generate_pkce()

        # Manually compute the expected challenge
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")

        assert challenge == expected_challenge

    def test_generate_pkce_disabled_returns_empty(self):
        """Test that PKCE disabled returns empty strings."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            pkce_enabled=False,
        )

        verifier, challenge = provider.generate_pkce()

        assert verifier == ""
        assert challenge == ""

    def test_generate_pkce_plain_method(self):
        """Test that plain PKCE method returns verifier as challenge."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            pkce_enabled=True,
            pkce_method="plain",
        )

        verifier, challenge = provider.generate_pkce()

        assert verifier == challenge

    def test_build_authorization_url_contains_required_params(self):
        """Test that authorization URL contains required params."""
        url = self.provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert "response_type=code" in url
        assert "client_id=test_client_id" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "state=test_state" in url

    def test_build_authorization_url_includes_pkce_params(self):
        """Test that authorization URL includes PKCE params when enabled."""
        url = self.provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert "code_challenge=test_challenge" in url
        assert "code_challenge_method=S256" in url

    def test_build_authorization_url_no_pkce_when_disabled(self):
        """Test that authorization URL excludes PKCE params when disabled."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            pkce_enabled=False,
        )

        url = provider.build_authorization_url(state="test_state")

        assert "code_challenge" not in url
        assert "code_challenge_method" not in url

    def test_build_authorization_url_includes_extra_params(self):
        """Test that authorization URL includes extra params from _extra_auth_params."""
        provider = ConcreteSSOWithExtraParams(
            issuer="https://example.com",
            client_id="test_client_id",
            redirect_uri="https://app.example.com/callback",
        )

        url = provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert "prompt=consent" in url

    def test_build_authorization_url_uses_correct_endpoint(self):
        """Test that authorization URL uses the correct endpoint."""
        url = self.provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert url.startswith("https://example.com/oauth/authorize?")

    def test_build_authorization_url_custom_endpoint(self):
        """Test that custom authorization endpoint is used."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            authorization_endpoint="https://custom.example.com/auth",
        )

        url = provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert url.startswith("https://custom.example.com/auth?")

    @pytest.mark.asyncio
    async def test_complete_login_calls_exchange_userinfo_extract(self):
        """Test that complete_login correctly orchestrates the call chain."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
        )

        # Mock the three methods
        mock_tokens = {"access_token": "mock_access_token", "id_token": "mock_id_token"}
        mock_userinfo = {"sub": "mock_user", "name": "Mock User", "email": "mock@example.com"}
        mock_result = AuthResult(subject="mock_user", display_name="Mock User")

        provider.exchange_code = AsyncMock(return_value=mock_tokens)
        provider.fetch_userinfo = AsyncMock(return_value=mock_userinfo)
        provider.extract_identity = MagicMock(return_value=mock_result)

        result = await provider.complete_login(code="test_code", code_verifier="test_verifier")

        # Verify call chain
        provider.exchange_code.assert_called_once_with("test_code", "test_verifier")
        provider.fetch_userinfo.assert_called_once_with("mock_access_token")
        provider.extract_identity.assert_called_once_with(mock_tokens, mock_userinfo)

        assert result == mock_result

    @pytest.mark.asyncio
    async def test_complete_login_without_code_verifier(self):
        """Test complete_login without PKCE code_verifier."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            pkce_enabled=False,
        )

        result = await provider.complete_login(code="test_code")

        assert isinstance(result, AuthResult)
        assert result.subject == "user1"
        assert result.display_name == "Test User"

    def test_default_endpoints_auto_discovery(self):
        """Test that default endpoints are auto-discovered from issuer."""
        provider = ConcreteSSO(
            issuer="https://idp.example.com/",  # With trailing slash
            client_id="test_client_id",
        )

        assert provider._authorization_endpoint == "https://idp.example.com/oauth/authorize"
        assert provider._token_endpoint == "https://idp.example.com/oauth/token"
        assert provider._userinfo_endpoint == "https://idp.example.com/oauth/userinfo"
        assert provider._jwks_uri == "https://idp.example.com/.well-known/jwks.json"

    def test_issuer_trailing_slash_stripped(self):
        """Test that issuer trailing slash is stripped."""
        provider = ConcreteSSO(
            issuer="https://example.com///",
            client_id="test_client_id",
        )

        # Only rightmost slashes should be stripped
        assert provider._issuer == "https://example.com"

    def test_default_scopes(self):
        """Test default scopes when not provided."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
        )

        assert provider._scopes == ["openid", "profile", "email"]

    def test_custom_scopes(self):
        """Test custom scopes are preserved."""
        provider = ConcreteSSO(
            issuer="https://example.com",
            client_id="test_client_id",
            scopes=["openid", "custom_scope"],
        )

        assert provider._scopes == ["openid", "custom_scope"]

    def test_extra_auth_params_default_empty(self):
        """Test that default _extra_auth_params returns empty dict."""
        result = self.provider._extra_auth_params()

        assert result == {}
