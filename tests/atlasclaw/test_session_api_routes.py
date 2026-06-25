# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.api.routes_session import (
    _completed_object_action_key,
    _with_completed_object_action_state,
)
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


def test_completed_object_action_state_scopes_by_object_type():
    references = [
        {
            "object_type": "approval_request",
            "object_id": "RES20260625000010",
            "object_actions": [
                {
                    "action_id": "analyze",
                    "kind": "agent_prompt",
                    "agent_prompt": {"default": "Analyze approval"},
                    "effect": "read",
                },
                {
                    "action_id": "approve",
                    "kind": "agent_prompt",
                    "agent_prompt": {"default": "Approve approval"},
                    "effect": "mutate",
                },
            ],
        },
        {
            "object_type": "resource",
            "object_id": "RES20260625000010",
            "object_actions": [
                {
                    "action_id": "analyze",
                    "kind": "agent_prompt",
                    "agent_prompt": {"default": "Analyze resource"},
                    "effect": "read",
                }
            ],
        },
    ]

    updated = _with_completed_object_action_state(
        references,
        {_completed_object_action_key("RES20260625000010", "approval_request")},
    )

    assert updated[0]["object_actions"][0]["disabled"] is True
    assert updated[0]["object_actions"][1]["disabled"] is True
    assert "disabled" not in updated[1]["object_actions"][0]


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

    def test_create_session_with_invalid_chat_type_returns_client_error(self, tmp_path):
        """Test that invalid chat_type values raise validation error.
        
        The endpoint converts string to ChatType enum, so invalid values are
        rejected as client errors instead of bubbling ValueError as a 500.
        """
        client = _build_client(tmp_path)

        response = client.post(
            "/api/sessions",
            json={"chat_type": "invalid_type", "scope": "per-peer"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid chat_type: invalid_type"

    def test_create_session_with_invalid_scope_returns_client_error(self, tmp_path):
        """Invalid scope values should be rejected as client errors."""
        client = _build_client(tmp_path)

        response = client.post("/api/sessions", json={"scope": "not-a-scope"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid session scope: not-a-scope"

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
    def test_direct_session_routes_accept_keys_with_path_separators(
        self,
        tmp_path,
        method,
        path_template,
        payload,
    ):
        """Direct session routes should accept session keys derived from peer IDs with '/'."""
        client = _build_client(tmp_path, user_id="alice")
        create_response = client.post(
            "/api/sessions",
            json={"scope": "per-peer", "peer_id": "team/42"},
        )
        assert create_response.status_code == 200
        session_key = create_response.json()["session_key"]
        encoded_session_key = quote(session_key, safe="")

        kwargs = {"json": payload} if payload is not None else {}
        response = getattr(client, method)(
            path_template.format(key=encoded_session_key),
            **kwargs,
        )

        assert response.status_code == 200
        if path_template.endswith("/history"):
            assert "messages" in response.json()
        elif method != "delete":
            assert response.json()["session_key"] == session_key


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
                "object_actions": [],
            },
            {
                "role": "assistant",
                "content": "hi there",
                "timestamp": response.json()["messages"][1]["timestamp"],
                "workspace_downloads": [],
                "object_actions": [],
            },
        ]

    @pytest.mark.asyncio
    async def test_get_session_history_hides_internal_user_turns(self, tmp_path):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="查看我的审批"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="Found 1 approval request."),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="user",
                content="查看 RES20260518000001 的审批详情",
                metadata={"visible_user_turn": False},
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="CMP Request Detail: RES20260518000001"),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert [item["content"] for item in response.json()["messages"]] == [
            "查看我的审批",
            "Found 1 approval request.",
            "CMP Request Detail: RES20260518000001",
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
        assert response.json()["messages"][1]["object_actions"] == []

    @pytest.mark.asyncio
    async def test_get_session_history_attaches_object_actions_from_tool_result_internal_metadata(
        self,
        tmp_path,
    ):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="show request"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="assistant",
                content="",
                tool_results=[
                    {
                        "tool_name": "provider_lookup",
                        "content": {"success": True, "output": ""},
                        "_internal": json.dumps(
                            {
                                "index": "7",
                                "object_id": "REQ-003",
                                "object_name": "VM request",
                                "object_actions": [
                                    {
                                        "action_id": "open_detail",
                                        "kind": "open_url",
                                        "href": "https://console.example.com/requests/REQ-003",
                                    }
                                ],
                            }
                        ),
                    }
                ],
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="Request REQ-003 is ready to view."),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert response.json()["messages"] == [
            {
                "role": "user",
                "content": "show request",
                "timestamp": response.json()["messages"][0]["timestamp"],
                "workspace_downloads": [],
                "object_actions": [],
            },
            {
                "role": "assistant",
                "content": "Request REQ-003 is ready to view.",
                "timestamp": response.json()["messages"][1]["timestamp"],
                "workspace_downloads": [],
                "object_actions": [
                    {
                        "index": 7,
                        "object_type": "",
                        "object_id": "REQ-003",
                        "object_name": "VM request",
                        "object_actions": [
                            {
                                "action_id": "open_detail",
                                "kind": "open_url",
                                "href": "https://console.example.com/requests/REQ-003",
                            }
                        ],
                    }
                ],
            },
        ]

    @pytest.mark.asyncio
    async def test_get_session_history_uses_latest_object_action_group_for_detail_answers(
        self,
        tmp_path,
    ):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="show vm-1 detail"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="assistant",
                content="",
                tool_results=[
                    {
                        "tool_name": "list_resources",
                        "content": {
                            "object_actions": [
                                {
                                    "index": 1,
                                    "object_id": "vm-1",
                                    "object_name": "vm-1",
                                    "object_actions": [
                                        {
                                            "action_id": "open_detail",
                                            "kind": "open_url",
                                            "href": "https://console.example.com/resources/vm-1",
                                        }
                                    ],
                                },
                                {
                                    "index": 2,
                                    "object_id": "vm-2",
                                    "object_name": "vm-2",
                                    "object_actions": [
                                        {
                                            "action_id": "open_detail",
                                            "kind": "open_url",
                                            "href": "https://console.example.com/resources/vm-2",
                                        }
                                    ],
                                },
                            ]
                        },
                    },
                    {
                        "tool_name": "resource_detail",
                        "content": {
                            "object_id": "vm-1",
                            "object_name": "vm-1",
                            "object_actions": [
                                {
                                    "action_id": "open_detail",
                                    "kind": "open_url",
                                    "href": "https://console.example.com/resources/vm-1",
                                }
                            ],
                        },
                    },
                ],
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="vm-1 detail is ready."),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert response.json()["messages"][1]["object_actions"] == [
            {
                "index": None,
                "object_type": "",
                "object_id": "vm-1",
                "object_name": "vm-1",
                "object_actions": [
                    {
                        "action_id": "open_detail",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-1",
                    }
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_get_session_history_disables_completed_approval_mutate_actions(
        self,
        tmp_path,
    ):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="show approval detail"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="tool",
                tool_name="smartcmp_get_request_detail",
                content={
                    "success": True,
                    "output": "detail",
                    "_internal": json.dumps(
                        [
                            {
                                "object_type": "approval_request",
                                "object_id": "RES20260625000010",
                                "object_name": "test-ui-10",
                                "object_actions": [
                                    {
                                        "action_id": "analyze",
                                        "kind": "agent_prompt",
                                        "agent_prompt": {"default": "Analyze RES20260625000010"},
                                        "effect": "read",
                                    },
                                    {
                                        "action_id": "approve",
                                        "kind": "agent_prompt",
                                        "agent_prompt": {"default": "Approve RES20260625000010"},
                                        "effect": "mutate",
                                    },
                                    {
                                        "action_id": "reject",
                                        "kind": "agent_prompt",
                                        "agent_prompt": {"default": "Reject RES20260625000010"},
                                        "effect": "mutate",
                                    },
                                ],
                            },
                            {
                                "object_type": "resource",
                                "object_id": "RES20260625000010",
                                "object_name": "same id resource",
                                "object_actions": [
                                    {
                                        "action_id": "analyze",
                                        "kind": "agent_prompt",
                                        "agent_prompt": {"default": "Analyze resource RES20260625000010"},
                                        "effect": "read",
                                    },
                                ],
                            },
                        ]
                    ),
                },
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="Approval detail is ready."),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="同意"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="tool",
                tool_name="smartcmp_approve",
                content={
                    "success": True,
                    "output": "\n".join(
                        [
                            "[SUCCESS] Approval completed.",
                            "##APPROVE_RESULT_START##",
                            '{"approved_ids": ["RES20260625000010"], "status": "approved"}',
                            "##APPROVE_RESULT_END##",
                        ]
                    ),
                },
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="Approval completed."),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        object_actions = response.json()["messages"][1]["object_actions"]
        actions = object_actions[0]["object_actions"]
        assert actions[0]["disabled"] is True
        assert actions[0]["disabled_reason"] == "completed"
        assert actions[1]["disabled"] is True
        assert actions[1]["disabled_reason"] == "completed"
        assert actions[2]["disabled"] is True
        assert actions[2]["disabled_reason"] == "completed"
        assert "disabled" not in object_actions[1]["object_actions"][0]

    @pytest.mark.asyncio
    async def test_get_session_history_clears_object_actions_after_later_empty_tool_result(
        self,
        tmp_path,
    ):
        alice_manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
        session_key = "agent:main:user:alice:web:dm:alice:topic:web-thread-1"
        await alice_manager.get_or_create(session_key)
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="user", content="show current object"),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="assistant",
                content="",
                tool_results=[
                    {
                        "tool_name": "object_lookup",
                        "content": {
                            "object_id": "vm-1",
                            "object_name": "vm-1",
                            "object_actions": [
                                {
                                    "action_id": "open_detail",
                                    "kind": "open_url",
                                    "href": "https://console.example.com/resources/vm-1",
                                }
                            ],
                        },
                    },
                    {
                        "tool_name": "object_lookup",
                        "content": {"output": "latest result intentionally has no actions"},
                    },
                ],
            ),
        )
        await alice_manager.append_transcript(
            session_key,
            TranscriptEntry(role="assistant", content="Latest result has no object action."),
        )

        client = _build_client(tmp_path, user_id="alice")
        encoded_session_key = quote(session_key, safe="")

        response = client.get(f"/api/sessions/{encoded_session_key}/history")

        assert response.status_code == 200
        assert response.json()["messages"][1]["object_actions"] == []

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
