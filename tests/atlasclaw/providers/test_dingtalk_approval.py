# -*- coding: utf-8 -*-
"""DingTalk Approval Provider unit tests.

Tests for DingTalkApprovalClient and skill handlers.
Uses unittest.mock and aiohttp mocking for HTTP calls.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

# Add _shared directory to path for importing DingTalk client
_shared_dir = _ROOT / "providers" / "dingtalk" / "skills" / "_shared"
sys.path.insert(0, str(_shared_dir))

from dingtalk_client import DingTalkApprovalClient, DingTalkAPIError


def _load_handler_module(skill_name: str):
    """Load a handler module from the provider skill directory.
    
    Uses importlib to avoid module name conflicts when multiple
    skills have modules named 'handler'.
    """
    handler_path = _ROOT / "providers" / "dingtalk" / "skills" / skill_name / "scripts" / "handler.py"
    spec = importlib.util.spec_from_file_location(f"handler_{skill_name}", handler_path)
    module = importlib.util.module_from_spec(spec)
    
    # Temporarily add _shared to sys.path for the module import
    _shared_dir = _ROOT / "providers" / "dingtalk" / "skills" / "_shared"
    original_path = sys.path.copy()
    sys.path.insert(0, str(_shared_dir))
    
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path = original_path
    
    return module


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def client_config() -> Dict[str, str]:
    """Default client configuration for testing."""
    return {
        "app_key": "test_app_key",
        "app_secret": "test_app_secret",
        "agent_id": "test_agent_id",
        "base_url": "https://oapi.dingtalk.com",
    }


@pytest.fixture
def mock_token_response() -> Dict[str, Any]:
    """Mock successful token response."""
    return {
        "errcode": 0,
        "errmsg": "ok",
        "access_token": "mock_access_token_12345",
        "expires_in": 7200,
    }


@pytest.fixture
def mock_context() -> MagicMock:
    """Create a mock RunContext with SkillDeps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.extra = {
        "provider_instance": {
            "app_key": "test_app_key",
            "app_secret": "test_app_secret",
            "agent_id": "test_agent_id",
            "base_url": "https://oapi.dingtalk.com",
        }
    }
    return ctx


# ============================================================================
# DingTalkApprovalClient Tests
# ============================================================================


class TestDingTalkApprovalClient:
    """Test DingTalkApprovalClient API methods."""

    def test_init_with_params(self, client_config: Dict[str, str]):
        """Verify constructor parameters are correctly set."""
        client = DingTalkApprovalClient(
            app_key=client_config["app_key"],
            app_secret=client_config["app_secret"],
            agent_id=client_config["agent_id"],
            base_url=client_config["base_url"],
        )

        assert client.app_key == "test_app_key"
        assert client.app_secret == "test_app_secret"
        assert client.agent_id == "test_agent_id"
        assert client.base_url == "https://oapi.dingtalk.com"
        assert client._access_token is None
        assert client._token_expires == 0

    def test_init_with_env_fallback(self):
        """Verify environment variable fallback."""
        with patch.dict(os.environ, {
            "DINGTALK_APP_KEY": "env_app_key",
            "DINGTALK_APP_SECRET": "env_app_secret",
            "DINGTALK_AGENT_ID": "env_agent_id",
        }):
            client = DingTalkApprovalClient()

            assert client.app_key == "env_app_key"
            assert client.app_secret == "env_app_secret"
            assert client.agent_id == "env_agent_id"

    @pytest.mark.asyncio
    async def test_get_access_token_success(
        self, client_config: Dict[str, str], mock_token_response: Dict[str, Any]
    ):
        """Mock HTTP response, verify token retrieval."""
        client = DingTalkApprovalClient(**client_config)

        # Mock aiohttp session
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=mock_token_response)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False

        client._session = mock_session

        token = await client.get_access_token()

        assert token == "mock_access_token_12345"
        assert client._access_token == "mock_access_token_12345"
        assert client._token_expires > time.time()

    @pytest.mark.asyncio
    async def test_get_access_token_caching(
        self, client_config: Dict[str, str], mock_token_response: Dict[str, Any]
    ):
        """Verify token caching - second call should not make HTTP request."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set cached token
        client._access_token = "cached_token"
        client._token_expires = time.time() + 3600  # Valid for 1 hour

        # Mock session that should NOT be called
        mock_session = AsyncMock()
        mock_session.get = MagicMock()
        mock_session.closed = False
        client._session = mock_session

        token = await client.get_access_token()

        assert token == "cached_token"
        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_access_token_refresh(
        self, client_config: Dict[str, str], mock_token_response: Dict[str, Any]
    ):
        """Simulate token expiry, verify refresh logic."""
        client = DingTalkApprovalClient(**client_config)

        # Set expired token
        client._access_token = "expired_token"
        client._token_expires = time.time() - 100  # Expired

        # Mock fresh token response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=mock_token_response)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        token = await client.get_access_token()

        assert token == "mock_access_token_12345"
        assert client._access_token == "mock_access_token_12345"
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_process_instance_success(self, client_config: Dict[str, str]):
        """Mock API, verify create approval instance."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock API response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 0,
            "errmsg": "ok",
            "process_instance_id": "instance_12345",
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        instance_id = await client.create_process_instance(
            process_code="PROC-001",
            originator_user_id="user123",
            dept_id=12345,
            form_values=[{"name": "Amount", "value": "1000"}],
        )

        assert instance_id == "instance_12345"
        mock_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_process_instance_success(self, client_config: Dict[str, str]):
        """Mock API, verify query approval details."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock API response
        mock_instance = {
            "title": "报销申请",
            "status": "COMPLETED",
            "result": "agree",
            "originator_userid": "user123",
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 0,
            "errmsg": "ok",
            "process_instance": mock_instance,
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        result = await client.get_process_instance("instance_12345")

        assert result["title"] == "报销申请"
        assert result["status"] == "COMPLETED"
        assert result["result"] == "agree"

    @pytest.mark.asyncio
    async def test_list_process_instance_ids_success(self, client_config: Dict[str, str]):
        """Mock API, verify list query."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock API response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 0,
            "errmsg": "ok",
            "result": {
                "list": ["instance_001", "instance_002", "instance_003"],
                "next_cursor": 3,
            },
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        result = await client.list_process_instance_ids(process_code="PROC-001")

        assert result["list"] == ["instance_001", "instance_002", "instance_003"]
        assert result["next_cursor"] == 3

    @pytest.mark.asyncio
    async def test_get_todo_num_success(self, client_config: Dict[str, str]):
        """Mock API, verify todo count query."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock API response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 0,
            "errmsg": "ok",
            "count": 5,
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        result = await client.get_todo_num("user123")

        assert result["count"] == 5

    @pytest.mark.asyncio
    async def test_get_user_by_mobile_success(self, client_config: Dict[str, str]):
        """Mock API, verify user query by mobile."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock API response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 0,
            "errmsg": "ok",
            "result": {"userid": "user123"},
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        result = await client.get_user_by_mobile("13800138000")

        assert result["userid"] == "user123"

    @pytest.mark.asyncio
    async def test_api_error_handling(self, client_config: Dict[str, str]):
        """Simulate API error (errcode != 0), verify exception."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock error response
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "errcode": 40029,
            "errmsg": "invalid access_token",
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        mock_session.closed = False
        client._session = mock_session

        with pytest.raises(DingTalkAPIError) as exc_info:
            await client.get_process_instance("instance_12345")

        assert exc_info.value.errcode == 40029
        assert "invalid access_token" in exc_info.value.errmsg

    @pytest.mark.asyncio
    async def test_http_error_handling(self, client_config: Dict[str, str]):
        """Simulate HTTP error, verify error handling."""
        client = DingTalkApprovalClient(**client_config)

        # Pre-set valid token
        client._access_token = "valid_token"
        client._token_expires = time.time() + 3600

        # Mock HTTP exception
        import aiohttp
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection failed"))
        mock_session.closed = False
        client._session = mock_session

        with pytest.raises(aiohttp.ClientError):
            await client.get_process_instance("instance_12345")


# ============================================================================
# Skill Handler Tests
# ============================================================================


class TestApprovalCreateHandler:
    """Test approval-create skill handler."""

    @pytest.mark.asyncio
    async def test_create_approval_success(self, mock_context: MagicMock):
        """Normal approval creation."""
        handler_module = _load_handler_module("approval-create")
        handler = handler_module.handler

        # Mock DingTalkApprovalClient
        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.create_process_instance = AsyncMock(return_value="instance_12345")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(
                ctx=mock_context,
                process_code="PROC-001",
                originator_user_id="user123",
                dept_id=12345,
                form_component_values=[{"name": "Amount", "value": "1000"}],
            )

            assert result["success"] is True
            assert result["data"]["process_instance_id"] == "instance_12345"
            assert "审批实例创建成功" in result["message"]

    @pytest.mark.asyncio
    async def test_create_approval_api_error(self, mock_context: MagicMock):
        """API error handling."""
        handler_module = _load_handler_module("approval-create")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.create_process_instance = AsyncMock(
                side_effect=DingTalkAPIError(40029, "invalid access_token")
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(
                ctx=mock_context,
                process_code="PROC-001",
                originator_user_id="user123",
                dept_id=12345,
                form_component_values=[{"name": "Amount", "value": "1000"}],
            )

            assert result["success"] is False
            assert "钉钉API错误" in result["message"]
            assert result["data"]["error_code"] == 40029


class TestApprovalQueryHandler:
    """Test approval-query skill handler."""

    @pytest.mark.asyncio
    async def test_query_single_instance(self, mock_context: MagicMock):
        """Query by process_instance_id."""
        handler_module = _load_handler_module("approval-query")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get_process_instance = AsyncMock(return_value={
                "title": "报销申请",
                "status": "COMPLETED",
                "result": "agree",
            })
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(
                ctx=mock_context,
                process_instance_id="instance_12345",
            )

            assert result["success"] is True
            assert result["data"]["title"] == "报销申请"
            assert result["data"]["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_query_instance_list(self, mock_context: MagicMock):
        """Query by process_code (list mode)."""
        handler_module = _load_handler_module("approval-query")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.list_process_instance_ids = AsyncMock(return_value={
                "list": ["instance_001", "instance_002"],
                "next_cursor": None,
            })
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(
                ctx=mock_context,
                process_code="PROC-001",
            )

            assert result["success"] is True
            assert result["data"]["instance_ids"] == ["instance_001", "instance_002"]

    @pytest.mark.asyncio
    async def test_query_missing_params(self, mock_context: MagicMock):
        """Missing query parameters."""
        handler_module = _load_handler_module("approval-query")
        handler = handler_module.handler

        result = await handler(ctx=mock_context)

        assert result["success"] is False
        assert "参数错误" in result["message"]
        assert "process_instance_id" in result["message"] or "process_code" in result["message"]


class TestApprovalTodoHandler:
    """Test approval-todo skill handler."""

    @pytest.mark.asyncio
    async def test_todo_by_userid(self, mock_context: MagicMock):
        """Query todo by userid."""
        handler_module = _load_handler_module("approval-todo")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get_todo_num = AsyncMock(return_value={"count": 5})
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(ctx=mock_context, userid="user123")

            assert result["success"] is True
            assert result["data"]["userid"] == "user123"
            assert result["data"]["count"] == 5

    @pytest.mark.asyncio
    async def test_todo_by_mobile(self, mock_context: MagicMock):
        """Query todo by mobile (lookup user first)."""
        handler_module = _load_handler_module("approval-todo")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get_user_by_mobile = AsyncMock(return_value={"userid": "user456"})
            mock_instance.get_todo_num = AsyncMock(return_value={"count": 3})
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(ctx=mock_context, mobile="13800138000")

            assert result["success"] is True
            assert result["data"]["userid"] == "user456"
            assert result["data"]["count"] == 3
            mock_instance.get_user_by_mobile.assert_called_once_with("13800138000")

    @pytest.mark.asyncio
    async def test_todo_missing_params(self, mock_context: MagicMock):
        """Missing userid or mobile."""
        handler_module = _load_handler_module("approval-todo")
        handler = handler_module.handler

        result = await handler(ctx=mock_context)

        assert result["success"] is False
        assert "参数错误" in result["message"]
        assert "userid" in result["message"] or "mobile" in result["message"]

    @pytest.mark.asyncio
    async def test_todo_user_not_found(self, mock_context: MagicMock):
        """User not found by mobile."""
        handler_module = _load_handler_module("approval-todo")
        handler = handler_module.handler

        with patch.object(handler_module, "DingTalkApprovalClient") as MockClient:
            mock_instance = AsyncMock()
            # Return empty result (no userid)
            mock_instance.get_user_by_mobile = AsyncMock(return_value={})
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await handler(ctx=mock_context, mobile="13800138000")

            assert result["success"] is False
            assert "未找到" in result["message"]
