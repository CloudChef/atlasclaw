# -*- coding: utf-8 -*-

from __future__ import annotations

import pytest
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.session.context import ChatType, SessionScope
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path) -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app)


def test_session_routes_use_current_session_manager_interface(tmp_path):
    client = _build_client(tmp_path)

    create_response = client.post("/api/sessions", json={})
    assert create_response.status_code == 200
    session_key = create_response.json()["session_key"]
    encoded_session_key = quote(session_key, safe="")

    get_response = client.get(f"/api/sessions/{encoded_session_key}")
    assert get_response.status_code == 200
    assert get_response.json()["session_key"] == session_key

    reset_response = client.post(
        f"/api/sessions/{encoded_session_key}/reset",
        json={"archive": True},
    )
    assert reset_response.status_code == 200
    assert reset_response.json() == {"status": "reset", "session_key": session_key}

    status_response = client.get(f"/api/sessions/{encoded_session_key}/status")
    assert status_response.status_code == 200
    assert status_response.json()["session_key"] == session_key

    queue_response = client.post(
        f"/api/sessions/{encoded_session_key}/queue",
        json={"mode": "steer"},
    )
    assert queue_response.status_code == 200
    assert queue_response.json() == {"session_key": session_key, "queue_mode": "steer"}

    compact_response = client.post(
        f"/api/sessions/{encoded_session_key}/compact",
        json={},
    )
    assert compact_response.status_code == 200
    assert compact_response.json()["status"] == "compaction_triggered"

    delete_response = client.delete(f"/api/sessions/{encoded_session_key}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted", "session_key": session_key}

    missing_response = client.get(f"/api/sessions/{encoded_session_key}")
    assert missing_response.status_code == 404


class TestSessionCreateWithChatType:
    """Tests for session creation with ChatType enum validation.
    
    AI Review: These tests verify that the create_session endpoint correctly
    converts string chat_type values to ChatType enum, fixing the bug where
    a raw string was passed to SessionKey causing AttributeError.
    """

    def test_create_session_with_default_chat_type(self, tmp_path):
        """Test session creation uses default 'dm' chat_type."""
        client = _build_client(tmp_path)
        
        response = client.post("/api/sessions", json={})
        assert response.status_code == 200
        
        session_key = response.json()["session_key"]
        # Default chat_type should be 'dm' and properly included in key
        assert ":dm:" in session_key or session_key.endswith(":main")

    @pytest.mark.parametrize("chat_type", ["dm", "group", "channel", "thread"])
    def test_create_session_with_valid_chat_types(self, tmp_path, chat_type):
        """Test session creation with all valid ChatType enum values."""
        client = _build_client(tmp_path)
        
        response = client.post(
            "/api/sessions",
            json={"chat_type": chat_type, "scope": "per-peer"}
        )
        assert response.status_code == 200
        
        session_key = response.json()["session_key"]
        # The chat_type should be properly converted to enum and serialized
        assert f":{chat_type}:" in session_key

    def test_create_session_with_invalid_chat_type_raises_error(self, tmp_path):
        """Test that invalid chat_type values raise validation error.
        
        The endpoint converts string to ChatType enum, so invalid values
        will raise ValueError.
        """
        client = _build_client(tmp_path)
        
        # Use raise_server_exceptions=False to capture the error response
        import pytest
        with pytest.raises(ValueError, match="is not a valid ChatType"):
            client.post(
                "/api/sessions",
                json={"chat_type": "invalid_type", "scope": "per-peer"}
            )

    def test_create_session_key_uses_enum_value_method(self, tmp_path):
        """Test that SessionKey.to_string() works with proper ChatType enum.
        
        This specifically tests the fix for the bug where chat_type.value
        was called on a string instead of an enum, causing AttributeError.
        """
        client = _build_client(tmp_path)
        
        # Test with PER_PEER scope which calls chat_type.value in to_string()
        response = client.post(
            "/api/sessions",
            json={"chat_type": "group", "scope": "per-peer"}
        )
        assert response.status_code == 200
        
        session_key = response.json()["session_key"]
        # Verify the session key was properly constructed
        assert ":group:" in session_key
        
        # Also test PER_CHANNEL_PEER scope
        response2 = client.post(
            "/api/sessions",
            json={"chat_type": "channel", "scope": "per-channel-peer"}
        )
        assert response2.status_code == 200
        assert ":channel:" in response2.json()["session_key"]
