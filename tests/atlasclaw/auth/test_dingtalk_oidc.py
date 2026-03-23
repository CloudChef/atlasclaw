# -*- coding: utf-8 -*-
"""Tests for DingTalk OIDC authentication integration."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.atlasclaw.auth.config import AuthConfig, DingTalkOIDCAuthConfig


# ───────────────────────────────────────────────────────────
# DingTalkOIDCAuthConfig
# ───────────────────────────────────────────────────────────

class TestDingTalkOIDCAuthConfig:
    """Tests for DingTalkOIDCAuthConfig model."""

    def test_default_values(self):
        """Config should have sensible defaults for DingTalk OAuth2."""
        config = DingTalkOIDCAuthConfig()
        assert config.issuer == ""
        assert config.client_id == ""
        # DingTalk only supports openid scope
        assert config.scopes == ["openid"]
        # DingTalk does not support PKCE
        assert config.pkce_enabled is False
        assert config.pkce_method == "S256"
        assert config.sub_mapping == "userid"
        assert config.corp_id == ""
        # DingTalk-specific endpoint defaults
        assert config.authorization_endpoint == "https://login.dingtalk.com/oauth2/auth"
        assert config.token_endpoint == "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
        assert config.userinfo_endpoint == "https://api.dingtalk.com/v1.0/contact/users/me"

    def test_expanded_resolves_env_vars(self, monkeypatch):
        """expanded() should replace ${VAR} with environment values."""
        monkeypatch.setenv("TEST_DT_ISSUER", "https://idaas.example.com")
        monkeypatch.setenv("TEST_DT_CLIENT", "my-client-id")
        monkeypatch.setenv("TEST_DT_CORP", "corp123")

        config = DingTalkOIDCAuthConfig(
            issuer="${TEST_DT_ISSUER}",
            client_id="${TEST_DT_CLIENT}",
            corp_id="${TEST_DT_CORP}",
        )
        expanded = config.expanded()
        assert expanded.issuer == "https://idaas.example.com"
        assert expanded.client_id == "my-client-id"
        assert expanded.corp_id == "corp123"

    def test_expanded_keeps_non_env_values(self):
        """expanded() should pass through literal values unchanged."""
        config = DingTalkOIDCAuthConfig(
            issuer="https://literal.example.com",
            client_id="literal-id",
        )
        expanded = config.expanded()
        assert expanded.issuer == "https://literal.example.com"
        assert expanded.client_id == "literal-id"


# ───────────────────────────────────────────────────────────
# AuthConfig validation for dingtalk_oidc
# ───────────────────────────────────────────────────────────

class TestAuthConfigDingTalkValidation:
    """Tests for dingtalk_oidc validation in AuthConfig."""

    def test_validate_dingtalk_oidc_missing_issuer(self):
        """Should raise ValueError when issuer is missing."""
        config = AuthConfig(
            provider="dingtalk_oidc",
            dingtalk_oidc=DingTalkOIDCAuthConfig(client_id="test-id"),
        )
        with pytest.raises(ValueError, match="issuer"):
            config.validate_provider_config()

    def test_validate_dingtalk_oidc_missing_client_id(self):
        """Should raise ValueError when client_id is missing."""
        config = AuthConfig(
            provider="dingtalk_oidc",
            dingtalk_oidc=DingTalkOIDCAuthConfig(issuer="https://test.example.com"),
        )
        with pytest.raises(ValueError, match="client_id"):
            config.validate_provider_config()

    def test_validate_dingtalk_oidc_valid(self):
        """Should not raise when both issuer and client_id are provided."""
        config = AuthConfig(
            provider="dingtalk_oidc",
            dingtalk_oidc=DingTalkOIDCAuthConfig(
                issuer="https://test.example.com",
                client_id="test-client",
            ),
        )
        config.validate_provider_config()  # Should not raise


# ───────────────────────────────────────────────────────────
# DingTalkSSOProvider
# ───────────────────────────────────────────────────────────

class TestDingTalkSSOProvider:
    """Tests for DingTalkSSOProvider (DingTalk OAuth2, not OIDC)."""

    def test_init_defaults(self):
        """Provider should initialize with DingTalk default endpoints."""
        from app.atlasclaw.auth.providers.dingtalk_sso import (
            DingTalkSSOProvider,
            DINGTALK_AUTHORIZATION_ENDPOINT,
            DINGTALK_TOKEN_ENDPOINT,
            DINGTALK_USERINFO_ENDPOINT,
        )
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-id",
        )
        # Verify default endpoints are set
        assert provider._authorization_endpoint == DINGTALK_AUTHORIZATION_ENDPOINT
        assert provider._token_endpoint == DINGTALK_TOKEN_ENDPOINT
        assert provider._userinfo_endpoint == DINGTALK_USERINFO_ENDPOINT
        assert provider._corp_id == ""

    def test_init_custom_endpoints(self):
        """Provider should use custom endpoints when provided."""
        from app.atlasclaw.auth.providers.dingtalk_sso import DingTalkSSOProvider
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-id",
            authorization_endpoint="https://custom.example.com/auth",
            token_endpoint="https://custom.example.com/token",
            userinfo_endpoint="https://custom.example.com/userinfo",
            corp_id="corp123",
        )
        assert provider._authorization_endpoint == "https://custom.example.com/auth"
        assert provider._token_endpoint == "https://custom.example.com/token"
        assert provider._userinfo_endpoint == "https://custom.example.com/userinfo"
        assert provider._corp_id == "corp123"

    @pytest.mark.asyncio
    async def test_exchange_code(self):
        """exchange_code should POST JSON with camelCase parameters."""
        from app.atlasclaw.auth.providers.dingtalk_sso import DingTalkSSOProvider
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-app-key",
            client_secret="test-app-secret",
        )

        token_response = {
            "expireIn": 7200,
            "accessToken": "mock-access-token",
            "refreshToken": "mock-refresh-token",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = token_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.exchange_code("auth-code-123")

            # Verify JSON payload uses camelCase
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args.kwargs
            assert call_kwargs["json"] == {
                "clientId": "test-app-key",
                "clientSecret": "test-app-secret",
                "code": "auth-code-123",
                "grantType": "authorization_code",
            }
            assert call_kwargs["headers"]["Content-Type"] == "application/json"

        assert result == token_response
        assert result["accessToken"] == "mock-access-token"

    @pytest.mark.asyncio
    async def test_fetch_userinfo(self):
        """fetch_userinfo should use x-acs-dingtalk-access-token header."""
        from app.atlasclaw.auth.providers.dingtalk_sso import DingTalkSSOProvider
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-id",
        )

        userinfo_response = {
            "nick": "Test User",
            "unionId": "union-123",
            "openId": "open-456",
            "email": "test@example.com",
            "mobile": "13500000000",
            "avatarUrl": "https://avatar.example.com/test.png",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = userinfo_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.fetch_userinfo("mock-access-token")

            # Verify DingTalk custom header is used (not Bearer)
            mock_client.get.assert_called_once()
            call_kwargs = mock_client.get.call_args.kwargs
            assert call_kwargs["headers"]["x-acs-dingtalk-access-token"] == "mock-access-token"

        assert result == userinfo_response
        assert result["unionId"] == "union-123"

    @pytest.mark.asyncio
    async def test_complete_login(self):
        """complete_login should build AuthResult from token and userinfo."""
        from app.atlasclaw.auth.providers.dingtalk_sso import DingTalkSSOProvider
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-id",
            corp_id="corp-abc",
        )

        token_response = {
            "expireIn": 7200,
            "accessToken": "mock-access-token",
            "refreshToken": "mock-refresh-token",
        }
        userinfo_response = {
            "nick": "Test User",
            "unionId": "union-123",
            "openId": "open-456",
            "email": "test@example.com",
            "mobile": "13500000000",
            "avatarUrl": "https://avatar.example.com/test.png",
        }

        with patch.object(provider, "exchange_code", new_callable=AsyncMock) as mock_exchange:
            with patch.object(provider, "fetch_userinfo", new_callable=AsyncMock) as mock_userinfo:
                mock_exchange.return_value = token_response
                mock_userinfo.return_value = userinfo_response

                result = await provider.complete_login("auth-code-123")

                mock_exchange.assert_called_once_with("auth-code-123", "")
                mock_userinfo.assert_called_once_with("mock-access-token")

        # Verify AuthResult fields
        assert result.subject == "union-123"  # unionId as subject
        assert result.display_name == "Test User"
        assert result.email == "test@example.com"
        assert result.tenant_id == "corp-abc"  # corp_id maps to tenant_id
        assert result.id_token == ""  # DingTalk does not return id_token
        assert result.raw_token == "mock-access-token"
        assert result.extra["unionId"] == "union-123"
        assert result.extra["openId"] == "open-456"
        assert result.extra["mobile"] == "13500000000"

    @pytest.mark.asyncio
    async def test_complete_login_uses_openid_fallback(self):
        """complete_login should use openId when unionId is missing."""
        from app.atlasclaw.auth.providers.dingtalk_sso import DingTalkSSOProvider
        provider = DingTalkSSOProvider(
            issuer="https://idaas.example.com",
            client_id="test-id",
        )

        token_response = {"accessToken": "mock-token"}
        userinfo_response = {
            "nick": "User Without UnionId",
            "openId": "open-only-789",
            "email": "noUnion@example.com",
        }

        with patch.object(provider, "exchange_code", new_callable=AsyncMock) as mock_exchange:
            with patch.object(provider, "fetch_userinfo", new_callable=AsyncMock) as mock_userinfo:
                mock_exchange.return_value = token_response
                mock_userinfo.return_value = userinfo_response

                result = await provider.complete_login("code")

        # Should fall back to openId
        assert result.subject == "open-only-789"
        assert result.tenant_id == "default"  # No corp_id, default tenant


# ───────────────────────────────────────────────────────────
# Provider factory with AuthRegistry fallback
# ───────────────────────────────────────────────────────────

class TestProviderFactoryDingTalkOIDC:
    """Tests for create_provider with dingtalk_oidc via AuthRegistry."""

    def test_create_provider_dingtalk_oidc_via_registry(self):
        """dingtalk_oidc should be created as built-in provider with correct config."""
        from app.atlasclaw.auth.providers import create_provider
        from app.atlasclaw.auth.providers.oidc import OIDCProvider
    
        config = AuthConfig(
            provider="dingtalk_oidc",
            dingtalk_oidc=DingTalkOIDCAuthConfig(
                issuer="https://test.example.com",
                client_id="test-client",
            ),
        )
        result = create_provider(config)
    
        # dingtalk_oidc is now a built-in provider returning OIDCProvider instance
        assert isinstance(result, OIDCProvider)
        # Verify key config is correctly passed (using private attributes)
        assert result._issuer == "https://test.example.com"
        assert result._client_id == "test-client"

    def test_create_provider_unknown_raises(self):
        """create_provider should raise ValueError for unknown providers."""
        from app.atlasclaw.auth.providers import create_provider

        config = AuthConfig(provider="nonexistent")
        with pytest.raises(ValueError, match="Unknown auth provider"):
            create_provider(config)
