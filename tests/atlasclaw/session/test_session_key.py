# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
SessionKey 用户维度单元测试

涵盖：新格式序列化/反序列化、旧格式兼容（无 user: 段 → user_id="default"）。
"""

from __future__ import annotations

import pytest

from app.atlasclaw.session.context import SessionKey, SessionScope, ChatType, SessionKeyFactory


class TestSessionKeyUserDimension:

    def test_to_string_includes_user_segment(self):
        key = SessionKey(
            agent_id="main",
            user_id="u-a1b2c3",
            channel="api",
            chat_type=ChatType.DM,
            peer_id="bob",
        )
        s = key.to_string(scope=SessionScope.PER_CHANNEL_PEER)
        assert "user:u-a1b2c3" in s
        assert s == "agent:main:user:u-a1b2c3:api:dm:bob"

    def test_to_string_main_scope(self):
        key = SessionKey(agent_id="main", user_id="u-xyz")
        s = key.to_string(scope=SessionScope.MAIN)
        assert s == "agent:main:user:u-xyz:main"

    def test_to_string_per_peer_scope(self):
        key = SessionKey(agent_id="main", user_id="u-123", chat_type=ChatType.DM, peer_id="alice")
        s = key.to_string(scope=SessionScope.PER_PEER)
        assert s == "agent:main:user:u-123:dm:alice"

    def test_from_string_new_format(self):
        s = "agent:main:user:u-a1b2c3:api:dm:bob"
        key = SessionKey.from_string(s)
        assert key.agent_id == "main"
        assert key.user_id == "u-a1b2c3"
        assert key.channel == "api"
        assert key.chat_type == ChatType.DM
        assert key.peer_id == "bob"

    def test_from_string_roundtrip(self):
        key = SessionKey(
            agent_id="main",
            user_id="u-abc",
            channel="telegram",
            chat_type=ChatType.DM,
            peer_id="user42",
        )
        s = key.to_string(scope=SessionScope.PER_CHANNEL_PEER)
        key2 = SessionKey.from_string(s)
        assert key2.user_id == "u-abc"
        assert key2.channel == "telegram"
        assert key2.peer_id == "user42"

    def test_from_string_legacy_no_user_segment(self):
        """Legacy keys without user: segment fill user_id='default'."""
        s = "agent:main:telegram:dm:user_42"
        key = SessionKey.from_string(s)
        assert key.user_id == "default"
        assert key.agent_id == "main"
        assert key.channel == "telegram"
        assert key.peer_id == "user_42"

    def test_from_string_legacy_main(self):
        s = "agent:main:main"
        key = SessionKey.from_string(s)
        assert key.user_id == "default"
        assert key.agent_id == "main"

    def test_session_key_factory_injects_user_id(self):
        factory = SessionKeyFactory()
        key = factory.create(
            scope=SessionScope.PER_CHANNEL_PEER,
            agent_id="main",
            user_id="u-factory",
            channel="api",
            peer_id="charlie",
        )
        assert key.user_id == "u-factory"
        s = key.to_string(scope=SessionScope.PER_CHANNEL_PEER)
        assert "user:u-factory" in s

    def test_thread_id_in_new_format(self):
        key = SessionKey(
            agent_id="main",
            user_id="u-t1",
            channel="slack",
            chat_type=ChatType.GROUP,
            peer_id="group1",
            thread_id="thread-42",
        )
        s = key.to_string(scope=SessionScope.PER_CHANNEL_PEER)
        assert "topic:thread-42" in s
        key2 = SessionKey.from_string(s)
        assert key2.thread_id == "thread-42"
        assert key2.user_id == "u-t1"

    def test_roundtrip_escapes_colon_delimiters_in_segments(self):
        """External IDs containing ':' should not be split as session-key delimiters."""
        key = SessionKey(
            agent_id="agent:main",
            user_id="oidc:alice",
            channel="web:socket",
            account_id="acct:primary",
            chat_type=ChatType.GROUP,
            peer_id="group:42",
            thread_id="topic:2026:05",
        )

        serialized = key.to_string(scope=SessionScope.PER_ACCOUNT_CHANNEL_PEER)
        restored = SessionKey.from_string(serialized)

        assert restored.agent_id == "agent:main"
        assert restored.user_id == "oidc:alice"
        assert restored.channel == "web:socket"
        assert restored.account_id == "acct:primary"
        assert restored.chat_type == ChatType.GROUP
        assert restored.peer_id == "group:42"
        assert restored.thread_id == "topic:2026:05"

    def test_roundtrip_preserves_percent_literals_in_segments(self):
        """Percent literals should not be confused with future delimiter escaping."""
        key = SessionKey(
            agent_id="main",
            user_id="alice%prod",
            channel="web",
            chat_type=ChatType.DM,
            peer_id="peer%3Araw",
        )

        serialized = key.to_string(scope=SessionScope.PER_CHANNEL_PEER)
        restored = SessionKey.from_string(serialized)

        assert restored.user_id == "alice%prod"
        assert restored.peer_id == "peer%3Araw"
