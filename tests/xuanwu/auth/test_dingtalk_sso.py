# -*- coding: utf-8 -*-
"""Tests for DingTalkSSOProvider."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.xuanwu.auth.providers.dingtalk_sso import DingTalkSSOProvider
from app.xuanwu.auth.models import AuthResult, AuthenticationError


class TestDingTalkSSOProvider:
    """Test DingTalkSSOProvider functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = DingTalkSSOProvider(
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="https://app.example.com/callback",
            corp_id="test_corp_id",
            subject_field="unionId",
        )

    def test_extra_auth_params_includes_prompt_consent(self):
        """Test that _extra_auth_params returns dict with prompt=consent."""
        result = self.provider._extra_auth_params()

        assert "prompt" in result
        assert result["prompt"] == "consent"

    def test_build_authorization_url_has_prompt_consent(self):
        """Test that authorization URL includes prompt=consent."""
        url = self.provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert "prompt=consent" in url

    def test_default_endpoints(self):
        """Test that default endpoints are correct for DingTalk."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
        )

        assert provider._authorization_endpoint == "https://login.dingtalk.com/oauth2/auth"
        assert provider._token_endpoint == "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
        assert provider._userinfo_endpoint == "https://api.dingtalk.com/v1.0/contact/users/me"

    def test_default_issuer(self):
        """Test that default issuer is DingTalk login domain."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
        )

        assert provider._issuer == "https://login.dingtalk.com"

    def test_default_scopes(self):
        """Test that default scopes are correct for DingTalk."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
        )

        assert provider._scopes == ["openid", "corpid"]

    def test_custom_endpoints(self):
        """Test that custom endpoints override defaults."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            authorization_endpoint="https://custom.example.com/auth",
            token_endpoint="https://custom.example.com/token",
            userinfo_endpoint="https://custom.example.com/userinfo",
        )

        assert provider._authorization_endpoint == "https://custom.example.com/auth"
        assert provider._token_endpoint == "https://custom.example.com/token"
        assert provider._userinfo_endpoint == "https://custom.example.com/userinfo"

    @pytest.mark.asyncio
    async def test_exchange_code_sends_json_body(self):
        """Test that exchange_code sends JSON body with camelCase fields."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"accessToken": "test_access_token"}'
            mock_response.json.return_value = {"accessToken": "test_access_token"}
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            await self.provider.exchange_code(code="test_code")

            # Verify post was called with json body
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args.kwargs

            assert "json" in call_kwargs
            json_body = call_kwargs["json"]
            assert json_body["clientId"] == "test_client_id"
            assert json_body["clientSecret"] == "test_client_secret"
            assert json_body["grantType"] == "authorization_code"
            assert json_body["code"] == "test_code"

    @pytest.mark.asyncio
    async def test_exchange_code_includes_code_verifier_when_pkce(self):
        """Test that PKCE enabled includes codeVerifier in JSON body."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"accessToken": "test_access_token"}'
            mock_response.json.return_value = {"accessToken": "test_access_token"}
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            await self.provider.exchange_code(code="test_code", code_verifier="test_verifier")

            call_kwargs = mock_client.post.call_args.kwargs
            json_body = call_kwargs["json"]
            assert "codeVerifier" in json_body
            assert json_body["codeVerifier"] == "test_verifier"

    @pytest.mark.asyncio
    async def test_exchange_code_no_code_verifier_when_empty(self):
        """Test that empty code_verifier is not included in JSON body."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"accessToken": "test_access_token"}'
            mock_response.json.return_value = {"accessToken": "test_access_token"}
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            await self.provider.exchange_code(code="test_code", code_verifier="")

            call_kwargs = mock_client.post.call_args.kwargs
            json_body = call_kwargs["json"]
            assert "codeVerifier" not in json_body

    @pytest.mark.asyncio
    async def test_fetch_userinfo_uses_dingtalk_header(self):
        """Test that fetch_userinfo uses x-acs-dingtalk-access-token header."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"nick": "Test User", "unionId": "test_union_id"}'
            mock_response.json.return_value = {"nick": "Test User", "unionId": "test_union_id"}
            mock_response.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_response

            await self.provider.fetch_userinfo(access_token="test_access_token")

            mock_client.get.assert_called_once()
            call_kwargs = mock_client.get.call_args.kwargs
            assert "headers" in call_kwargs
            assert "x-acs-dingtalk-access-token" in call_kwargs["headers"]
            assert call_kwargs["headers"]["x-acs-dingtalk-access-token"] == "test_access_token"

    @pytest.mark.asyncio
    async def test_fetch_userinfo_empty_token_returns_empty_dict(self):
        """Test that fetch_userinfo with empty token returns empty dict."""
        result = await self.provider.fetch_userinfo(access_token="")

        assert result == {}

    def test_extract_identity_uses_union_id(self):
        """Test that subject_field=unionId extracts unionId from userinfo."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            subject_field="unionId",
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "openId": "test_open_id",
            "nick": "Test User",
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.subject == "test_union_id"

    def test_extract_identity_uses_open_id(self):
        """Test that subject_field=openId extracts openId from userinfo."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            subject_field="openId",
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "openId": "test_open_id",
            "nick": "Test User",
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.subject == "test_open_id"

    def test_extract_identity_fallback_to_open_id(self):
        """Test that subject falls back to openId when primary field is missing."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            subject_field="unionId",
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "openId": "test_open_id",  # No unionId
            "nick": "Test User",
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.subject == "test_open_id"

    def test_extract_identity_corp_id_mapping(self):
        """Test that corp_id constructor param maps to AuthResult.tenant_id."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            corp_id="configured_corp_id",
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
            "corpId": "userinfo_corp_id",
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.tenant_id == "configured_corp_id"

    def test_extract_identity_corp_id_from_userinfo(self):
        """Test that tenant_id falls back to userinfo.corpId when corp_id not configured."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            corp_id="",  # Not configured
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
            "corpId": "userinfo_corp_id",
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.tenant_id == "userinfo_corp_id"

    def test_extract_identity_default_tenant_id(self):
        """Test that tenant_id defaults to 'default' when no corp_id available."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
            corp_id="",
        )

        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
            # No corpId in userinfo
        }

        result = provider.extract_identity(tokens, userinfo)

        assert result.tenant_id == "default"

    def test_extract_identity_auth_type(self):
        """Test that extra contains auth_type: oidc:dingtalk."""
        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert "auth_type" in result.extra
        assert result.extra["auth_type"] == "oidc:dingtalk"

    def test_extract_identity_provider_id(self):
        """Test that extra contains provider_id: dingtalk."""
        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert "provider_id" in result.extra
        assert result.extra["provider_id"] == "dingtalk"

    def test_extract_identity_display_name(self):
        """Test that display_name is extracted from nick field."""
        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User Nick",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert result.display_name == "Test User Nick"

    def test_extract_identity_email(self):
        """Test that email is extracted from userinfo."""
        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
            "email": "test@example.com",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert result.email == "test@example.com"

    def test_extract_identity_raw_token(self):
        """Test that raw_token uses accessToken from tokens."""
        tokens = {"accessToken": "the_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert result.raw_token == "the_access_token"

    def test_extract_identity_userinfo_in_extra(self):
        """Test that userinfo fields are included in extra."""
        tokens = {"accessToken": "test_access_token"}
        userinfo = {
            "unionId": "test_union_id",
            "nick": "Test User",
            "avatarUrl": "https://example.com/avatar.png",
            "customField": "custom_value",
        }

        result = self.provider.extract_identity(tokens, userinfo)

        assert result.extra["unionId"] == "test_union_id"
        assert result.extra["avatarUrl"] == "https://example.com/avatar.png"
        assert result.extra["customField"] == "custom_value"

    @pytest.mark.asyncio
    async def test_complete_login_flow(self):
        """Test complete login flow with mocked exchange_code and fetch_userinfo."""
        mock_tokens = {
            "accessToken": "mock_access_token",
            "refreshToken": "mock_refresh_token",
            "expireIn": 7200,
        }
        mock_userinfo = {
            "unionId": "mock_union_id",
            "openId": "mock_open_id",
            "nick": "Mock User",
            "email": "mock@example.com",
            "corpId": "mock_corp_id",
        }

        self.provider.exchange_code = AsyncMock(return_value=mock_tokens)
        self.provider.fetch_userinfo = AsyncMock(return_value=mock_userinfo)

        result = await self.provider.complete_login(code="test_code", code_verifier="test_verifier")

        # Verify call chain
        self.provider.exchange_code.assert_called_once_with("test_code", "test_verifier")
        self.provider.fetch_userinfo.assert_called_once_with("mock_access_token")

        # Verify result
        assert isinstance(result, AuthResult)
        assert result.subject == "mock_union_id"
        assert result.display_name == "Mock User"
        assert result.email == "mock@example.com"
        assert result.tenant_id == "test_corp_id"  # From constructor, not userinfo
        assert result.raw_token == "mock_access_token"
        assert result.extra["auth_type"] == "oidc:dingtalk"

    @pytest.mark.asyncio
    async def test_exchange_code_handles_http_error(self):
        """Test that exchange_code raises AuthenticationError on HTTP error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Bad Request",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.post.return_value = mock_response

            with pytest.raises(AuthenticationError) as exc_info:
                await self.provider.exchange_code(code="invalid_code")

            assert "Token exchange failed" in str(exc_info.value)

    def test_pkce_enabled_by_default(self):
        """Test that PKCE is enabled by default."""
        provider = DingTalkSSOProvider(
            client_id="test_client_id",
        )

        assert provider._pkce_enabled is True
        assert provider._pkce_method == "S256"

    def test_authorization_url_contains_all_params(self):
        """Test that authorization URL contains all required params."""
        url = self.provider.build_authorization_url(state="test_state", code_challenge="test_challenge")

        assert "response_type=code" in url
        assert "client_id=test_client_id" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "state=test_state" in url
        assert "code_challenge=test_challenge" in url
        assert "code_challenge_method=S256" in url
        assert "prompt=consent" in url
