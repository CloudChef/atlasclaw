# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.context import SessionKey, TranscriptEntry
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.session.context import ChatType, SessionScope
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path, user_id: str = "default") -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        request.state.user_info = UserInfo(user_id=user_id, display_name=user_id)
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app)


def test_session_routes_use_current_session_manager_interface(tmp_path):
    client = _build_client(tmp_path)

    create_response = client.post("/api/sessions", json={})
    assert create_response.status_code == 200
    session_key = create_response.json()["session_key"]
    assert create_response.json()["title"] == ""
    assert create_response.json()["title_status"] == "empty"
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


class TestThreadSessionsAndOwnership:

    @pytest.mark.asyncio
    async def test_list_sessions_returns_all_current_user_sessions_across_channels(self, tmp_path):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        bob_manager = SessionManager(workspace_path=str(tmp_path), user_id="bob")

        await alice_manager.get_or_create("agent:main:user:alice:web:dm:alice:topic:web-thread-1")
        await alice_manager.get_or_create("agent:main:user:alice:feishu:dm:feishu-user-1")
        await bob_manager.get_or_create("agent:main:user:bob:web:dm:bob:topic:bob-thread-1")

        client = _build_client(tmp_path, user_id="alice")
        response = client.get("/api/sessions")

        assert response.status_code == 200
        session_keys = [item["session_key"] for item in response.json()]
        assert "agent:main:user:alice:web:dm:alice:topic:web-thread-1" in session_keys
        assert "agent:main:user:alice:feishu:dm:feishu-user-1" in session_keys
        assert "agent:main:user:bob:web:dm:bob:topic:bob-thread-1" not in session_keys

    def test_create_thread_session_returns_distinct_thread_keys(self, tmp_path):
        client = _build_client(tmp_path, user_id="alice")

        first = client.post("/api/sessions/threads", json={"channel": "web", "chat_type": "dm"})
        second = client.post("/api/sessions/threads", json={"channel": "web", "chat_type": "dm"})

        assert first.status_code == 200
        assert second.status_code == 200

        first_key = SessionKey.from_string(first.json()["session_key"])
        second_key = SessionKey.from_string(second.json()["session_key"])

        assert first_key.user_id == "alice"
        assert second_key.user_id == "alice"
        assert first_key.thread_id
        assert second_key.thread_id
        assert first.json()["session_key"] != second.json()["session_key"]

    @pytest.mark.asyncio
    async def test_get_session_history_returns_persisted_transcript_entries(self, tmp_path):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="system", content="hidden system"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="hello atlas"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="hi there"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="tool", content="internal tool output"),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert response.json()["messages"] == [
            {
                "role": "user",
                "content": "hello atlas",
                "timestamp": response.json()["messages"][0]["timestamp"],
                "workspace_downloads": [],
            },
            {
                "role": "assistant",
                "content": "hi there",
                "timestamp": response.json()["messages"][1]["timestamp"],
                "workspace_downloads": [],
            },
        ]

    @pytest.mark.asyncio
    async def test_get_session_history_returns_generated_workspace_downloads(self, tmp_path):
        work_dir = tmp_path / "users" / "alice" / "work_dir"
        work_dir.mkdir(parents=True)
        (work_dir / "report.xlsx").write_bytes(b"report")

        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="generate report"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="assistant",
                content="",
                tool_calls=[{"id": "call-1", "name": "skill_exec", "args": {}}],
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="tool",
                tool_name="skill_exec",
                content=json.dumps(
                    {
                        "is_error": False,
                        "details": {"download_path": ["report.xlsx"]},
                    }
                ),
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="The report is ready: report.xlsx"),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert response.json()["messages"][1]["workspace_downloads"] == [
            {"path": "report.xlsx", "label": ""}
        ]

    @pytest.mark.parametrize(
        ("method", "path_template", "payload"),
        [
            ("get", "/api/sessions/{key}", None),
            ("get", "/api/sessions/{key}/history", None),
            ("post", "/api/sessions/{key}/reset", {"archive": True}),
            ("get", "/api/sessions/{key}/status", None),
            ("post", "/api/sessions/{key}/queue", {"mode": "steer"}),
            ("post", "/api/sessions/{key}/compact", {}),
            ("delete", "/api/sessions/{key}", None),
        ],
    )
    def test_direct_session_routes_reject_other_users_session_keys(
        self,
        tmp_path,
        method,
        path_template,
        payload,
    ):
        owner_client = _build_client(tmp_path, user_id="bob")
        create_response = owner_client.post(
            "/api/sessions",
            json={"channel": "web", "scope": "per-peer"},
        )
        assert create_response.status_code == 200
        owner_session_key = create_response.json()["session_key"]
        encoded_session_key = quote(owner_session_key, safe="")

        attacker_client = _build_client(tmp_path, user_id="alice")
        kwargs = {"json": payload} if payload is not None else {}
        response = getattr(attacker_client, method)(
            path_template.format(key=encoded_session_key),
            **kwargs,
        )

        assert response.status_code == 404
