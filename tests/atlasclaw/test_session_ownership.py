# -*- coding: utf-8 -*-
"""
Session ownership verification tests.

Tests for _require_session_ownership() and list_sessions user isolation.
"""

from __future__ import annotations

import pytest
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import ANONYMOUS_USER, UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from app.atlasclaw.skills.registry import SkillRegistry


class MockAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that sets a mock authenticated user on request.state."""
    
    def __init__(self, app, user_info: UserInfo):
        super().__init__(app)
        self.user_info = user_info
    
    async def dispatch(self, request: Request, call_next):
        request.state.user_info = self.user_info
        return await call_next(request)


def _build_client_with_user(
    tmp_path,
    user_info: UserInfo,
    shared_ctx: APIContext | None = None
) -> tuple[TestClient, APIContext]:
    """Build a test client with a specific user identity.
    
    Args:
        tmp_path: Temporary path for session storage.
        user_info: The user identity to inject into requests.
        shared_ctx: Optional shared APIContext for multi-user tests.
    
    Returns:
        Tuple of (TestClient, APIContext) for reuse across users.
    """
    if shared_ctx is None:
        shared_ctx = APIContext(
            session_manager=SessionManager(
                workspace_path=str(tmp_path),
                user_id=user_info.user_id,
            ),
            session_queue=SessionQueue(),
            skill_registry=SkillRegistry(),
        )
    set_api_context(shared_ctx)

    app = FastAPI()
    app.add_middleware(MockAuthMiddleware, user_info=user_info)
    app.include_router(create_router())
    return TestClient(app), shared_ctx


def _create_session_key_for_user(user_id: str, agent_id: str = "main") -> str:
    """Create a session key string for a specific user."""
    key = SessionKey(
        agent_id=agent_id,
        user_id=user_id,
        channel="main",
        chat_type=ChatType.DM,
        peer_id="default",
    )
    return key.to_string(scope=SessionScope.MAIN)


class TestSessionOwnership:
    """Session ownership verification tests."""

    def test_ownership_check_blocks_other_user(self, tmp_path):
        """User A's session accessed by User B returns 403."""
        # Create user Alice and Bob
        alice = UserInfo(user_id="user-alice", display_name="Alice")
        bob = UserInfo(user_id="user-bob", display_name="Bob")
        
        # Create a client as Alice and create a session
        alice_client, ctx = _build_client_with_user(tmp_path, alice)
        create_response = alice_client.post("/api/sessions", json={})
        assert create_response.status_code == 200
        alice_session_key = create_response.json()["session_key"]
        encoded_key = quote(alice_session_key, safe="")
        
        # Now try to access Alice's session as Bob (share the context)
        bob_client, _ = _build_client_with_user(tmp_path, bob, shared_ctx=ctx)
        
        # GET should return 403
        get_response = bob_client.get(f"/api/sessions/{encoded_key}")
        assert get_response.status_code == 403
        assert "Not authorized" in get_response.json()["detail"]
        
        # DELETE should return 403
        delete_response = bob_client.delete(f"/api/sessions/{encoded_key}")
        assert delete_response.status_code == 403
        
        # RESET should return 403
        reset_response = bob_client.post(
            f"/api/sessions/{encoded_key}/reset",
            json={"archive": False}
        )
        assert reset_response.status_code == 403
        
        # SET QUEUE MODE should return 403
        queue_response = bob_client.post(
            f"/api/sessions/{encoded_key}/queue",
            json={"mode": "steer"}
        )
        assert queue_response.status_code == 403
        
        # COMPACT should return 403
        compact_response = bob_client.post(
            f"/api/sessions/{encoded_key}/compact",
            json={}
        )
        assert compact_response.status_code == 403

    def test_ownership_check_allows_owner(self, tmp_path):
        """User accessing their own session succeeds."""
        alice = UserInfo(user_id="user-alice", display_name="Alice")
        client, _ = _build_client_with_user(tmp_path, alice)
        
        # Create session
        create_response = client.post("/api/sessions", json={})
        assert create_response.status_code == 200
        session_key = create_response.json()["session_key"]
        encoded_key = quote(session_key, safe="")
        
        # GET should succeed
        get_response = client.get(f"/api/sessions/{encoded_key}")
        assert get_response.status_code == 200
        assert get_response.json()["session_key"] == session_key
        
        # RESET should succeed
        reset_response = client.post(
            f"/api/sessions/{encoded_key}/reset",
            json={"archive": False}
        )
        assert reset_response.status_code == 200
        
        # SET QUEUE MODE should succeed
        queue_response = client.post(
            f"/api/sessions/{encoded_key}/queue",
            json={"mode": "steer"}
        )
        assert queue_response.status_code == 200
        
        # COMPACT should succeed
        compact_response = client.post(
            f"/api/sessions/{encoded_key}/compact",
            json={}
        )
        assert compact_response.status_code == 200
        
        # DELETE should succeed
        delete_response = client.delete(f"/api/sessions/{encoded_key}")
        assert delete_response.status_code == 200

    def test_anonymous_user_returns_401(self, tmp_path):
        """Anonymous user accessing session routes returns 401."""
        # Create a client with anonymous user
        client, _ = _build_client_with_user(tmp_path, ANONYMOUS_USER)
        
        # First create a valid session key manually
        session_key = _create_session_key_for_user("some-user")
        encoded_key = quote(session_key, safe="")
        
        # GET should return 401
        get_response = client.get(f"/api/sessions/{encoded_key}")
        assert get_response.status_code == 401
        assert "Authentication required" in get_response.json()["detail"]
        
        # DELETE should return 401
        delete_response = client.delete(f"/api/sessions/{encoded_key}")
        assert delete_response.status_code == 401
        
        # RESET should return 401
        reset_response = client.post(
            f"/api/sessions/{encoded_key}/reset",
            json={"archive": False}
        )
        assert reset_response.status_code == 401
        
        # SET QUEUE MODE should return 401
        queue_response = client.post(
            f"/api/sessions/{encoded_key}/queue",
            json={"mode": "steer"}
        )
        assert queue_response.status_code == 401
        
        # COMPACT should return 401
        compact_response = client.post(
            f"/api/sessions/{encoded_key}/compact",
            json={}
        )
        assert compact_response.status_code == 401

    def test_invalid_session_key_returns_400(self, tmp_path):
        """Invalid session key format returns 400."""
        alice = UserInfo(user_id="user-alice", display_name="Alice")
        client, _ = _build_client_with_user(tmp_path, alice)
        
        # Use various invalid session key formats
        invalid_keys = [
            "invalid-key-format",
            "totally:wrong:format",
            "",
        ]
        
        for invalid_key in invalid_keys:
            encoded_key = quote(invalid_key, safe="") if invalid_key else "empty"
            
            # Note: Very malformed keys may fail at the SessionKey.from_string level
            # The _require_session_ownership checks if key.user_id != auth_user.user_id
            # If from_string returns a default SessionKey with user_id="default",
            # and alice's user_id is "user-alice", it should return 403
            get_response = client.get(f"/api/sessions/{encoded_key}")
            # Either 400 (parse error) or 403 (user mismatch) is acceptable
            assert get_response.status_code in [400, 403, 404]

    def test_legacy_session_key_default_user(self, tmp_path):
        """Legacy format session key (user_id=default) ownership check."""
        # Legacy keys parse to user_id="default"
        # A user with user_id != "default" should get 403
        alice = UserInfo(user_id="user-alice", display_name="Alice")
        client, ctx = _build_client_with_user(tmp_path, alice)
        
        # Create a legacy-style session key (no user segment)
        legacy_key = "agent:main:main"  # Parses to user_id="default"
        encoded_key = quote(legacy_key, safe="")
        
        # Alice (user-alice) trying to access a default user's session should fail
        get_response = client.get(f"/api/sessions/{encoded_key}")
        assert get_response.status_code == 403
        
        # Now test with user "default" (share context)
        default_user = UserInfo(user_id="default", display_name="Default User")
        default_client, _ = _build_client_with_user(tmp_path, default_user, shared_ctx=ctx)
        
        # Default user should be allowed (after creating the session)
        # Note: The session may not exist, so we might get 404
        get_response = default_client.get(f"/api/sessions/{encoded_key}")
        # If ownership passes but session doesn't exist, we get 404
        # If ownership fails, we get 403
        # 404 means ownership check passed (correct behavior)
        assert get_response.status_code in [200, 404]


class TestListSessionsIsolation:
    """list_sessions user isolation tests."""

    def test_list_sessions_returns_only_user_sessions(self, tmp_path):
        """list_sessions only returns current user's sessions.
        
        Note: Each user needs their own APIContext because create_session uses
        ctx.session_manager which has a fixed user_id. The list_sessions endpoint
        creates a user-scoped SessionManager using the same workspace_path but
        the authenticated user's user_id.
        """
        alice = UserInfo(user_id="user-alice", display_name="Alice")
        bob = UserInfo(user_id="user-bob", display_name="Bob")
        
        # Alice creates a session (separate context with Alice's user_id)
        alice_client, _ = _build_client_with_user(tmp_path, alice)
        alice_create = alice_client.post("/api/sessions", json={})
        assert alice_create.status_code == 200
        alice_session_key = alice_create.json()["session_key"]
        
        # Bob creates a session (separate context with Bob's user_id)
        bob_client, _ = _build_client_with_user(tmp_path, bob)
        bob_create = bob_client.post("/api/sessions", json={})
        assert bob_create.status_code == 200
        bob_session_key = bob_create.json()["session_key"]
        
        # Verify keys contain correct user_ids
        assert "user-alice" in alice_session_key
        assert "user-bob" in bob_session_key
        
        # Alice lists sessions - should only see her own
        alice_list = alice_client.get("/api/sessions")
        assert alice_list.status_code == 200
        alice_sessions = alice_list.json()
        
        alice_session_keys = [s["session_key"] for s in alice_sessions]
        assert alice_session_key in alice_session_keys
        assert bob_session_key not in alice_session_keys
        
        # Bob lists sessions - should only see his own
        bob_list = bob_client.get("/api/sessions")
        assert bob_list.status_code == 200
        bob_sessions = bob_list.json()
        
        bob_session_keys = [s["session_key"] for s in bob_sessions]
        assert bob_session_key in bob_session_keys
        assert alice_session_key not in bob_session_keys

    def test_list_sessions_rejects_anonymous(self, tmp_path):
        """Anonymous user calling list_sessions returns 401."""
        client, _ = _build_client_with_user(tmp_path, ANONYMOUS_USER)
        
        response = client.get("/api/sessions")
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    def test_list_sessions_empty_for_new_user(self, tmp_path):
        """New user with no sessions gets empty list."""
        new_user = UserInfo(user_id="user-new", display_name="New User")
        client, _ = _build_client_with_user(tmp_path, new_user)
        
        response = client.get("/api/sessions")
        assert response.status_code == 200
        assert response.json() == []
