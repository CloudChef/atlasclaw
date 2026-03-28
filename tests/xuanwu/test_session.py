# -*- coding: utf-8 -*-
"""
会话模块单元测试

测试 SessionKey、SessionContext、SessionQueue 等组件。
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.xuanwu.session.context import (
    SessionKey,
    SessionScope,
    SessionMetadata,
    TranscriptEntry,
    IdentityLinks,
    ChatType,
)
from app.xuanwu.session.queue import SessionQueue, QueueMode


class TestSessionKey:
    """SessionKey 测试类"""
    
    def test_create_session_key(self):
        """测试创建 SessionKey"""
        key = SessionKey(
            agent_id="main",
            channel="telegram",
            chat_type=ChatType.DM,
            peer_id="user-123"
        )
        
        assert key.agent_id == "main"
        assert key.channel == "telegram"
        assert key.chat_type == ChatType.DM
        assert key.peer_id == "user-123"
        
    def test_session_key_to_string(self):
        """测试 SessionKey 序列化"""
        key = SessionKey(
            agent_id="main",
            channel="telegram",
            chat_type=ChatType.DM,
            peer_id="user-123"
        )
        
        key_str = key.to_string()
        assert "agent:main" in key_str
        assert "telegram" in key_str
        assert "dm" in key_str
        assert "user-123" in key_str
        
    def test_session_key_from_string(self):
        """测试 SessionKey 反序列化"""
        key_str = "agent:main:telegram:dm:user-123"
        key = SessionKey.from_string(key_str)
        
        assert key.agent_id == "main"
        assert key.channel == "telegram"
        assert key.chat_type == ChatType.DM
        assert key.peer_id == "user-123"
        
    def test_session_key_round_trip(self):
        """测试序列化往返"""
        original = SessionKey(
            agent_id="assistant",
            channel="slack",
            chat_type=ChatType.GROUP,
            peer_id="U123456",
        )
        
        key_str = original.to_string(SessionScope.PER_CHANNEL_PEER)
        restored = SessionKey.from_string(key_str)
        
        assert restored.agent_id == original.agent_id
        assert restored.channel == original.channel
        assert restored.chat_type == original.chat_type
        assert restored.peer_id == original.peer_id


class TestSessionScope:
    """SessionScope 测试类"""
    
    def test_scope_values(self):
        """测试所有 Scope 值"""
        assert SessionScope.MAIN.value == "main"
        assert SessionScope.PER_PEER.value == "per-peer"
        assert SessionScope.PER_CHANNEL_PEER.value == "per-channel-peer"
        assert SessionScope.PER_ACCOUNT_CHANNEL_PEER.value == "per-account-channel-peer"


class TestSessionMetadata:
    """SessionMetadata 测试类"""
    
    def test_create_metadata(self):
        """测试创建元数据"""
        metadata = SessionMetadata(
            session_key="agent:main:api:dm:user-123",
            display_name="Test Session"
        )
        
        assert metadata.session_key == "agent:main:api:dm:user-123"
        assert metadata.display_name == "Test Session"
        assert metadata.created_at is not None
        assert metadata.input_tokens == 0
        
    def test_metadata_to_dict(self):
        """测试元数据序列化"""
        metadata = SessionMetadata(
            session_key="agent:main:api:dm:user-123"
        )
        
        data = metadata.to_dict()
        assert "session_key" in data
        assert "created_at" in data
        
    def test_metadata_from_dict(self):
        """测试元数据反序列化"""
        metadata = SessionMetadata(session_key="test-key")
        data = metadata.to_dict()
        
        restored = SessionMetadata.from_dict(data)
        assert restored.session_key == metadata.session_key


class TestTranscriptEntry:
    """TranscriptEntry 测试类"""
    
    def test_create_user_entry(self):
        """测试创建用户消息条目"""
        entry = TranscriptEntry(
            role="user",
            content="Hello, assistant!"
        )
        
        assert entry.role == "user"
        assert entry.content == "Hello, assistant!"
        assert entry.timestamp is not None
        
    def test_create_assistant_entry(self):
        """测试创建助手消息条目"""
        entry = TranscriptEntry(
            role="assistant",
            content="Hello! How can I help you?"
        )
        
        assert entry.role == "assistant"
        assert entry.content == "Hello! How can I help you?"
        
    def test_entry_with_tool_calls(self):
        """测试带工具调用的条目"""
        entry = TranscriptEntry(
            role="assistant",
            content="",
            tool_calls=[{
                "name": "search",
                "arguments": {"query": "test"}
            }]
        )
        
        assert len(entry.tool_calls) == 1
        assert entry.tool_calls[0]["name"] == "search"
        
    def test_entry_serialization(self):
        """测试条目序列化"""
        entry = TranscriptEntry(role="user", content="Test")
        data = entry.to_dict()
        
        restored = TranscriptEntry.from_dict(data)
        assert restored.role == entry.role
        assert restored.content == entry.content


class TestIdentityLinks:
    """IdentityLinks 测试类"""
    
    def test_create_identity_links(self):
        """测试创建身份映射"""
        links = IdentityLinks()
        
        links.add_mapping("canonical-user-1", "telegram:123")
        links.add_mapping("canonical-user-1", "slack:U456")
        
        assert links.resolve("telegram:123") == "canonical-user-1"
        assert links.resolve("slack:U456") == "canonical-user-1"
        
    def test_resolve_unmapped(self):
        """测试解析未映射的身份"""
        links = IdentityLinks()
        
        # 未映射的应该返回原值
        result = links.resolve("unknown:123")
        assert result == "unknown:123"


class TestSessionQueue:
    """SessionQueue 测试类"""
    
    def test_create_queue(self):
        """测试创建队列"""
        queue = SessionQueue()
        assert queue is not None
        
    def test_enqueue_message(self):
        """测试入队消息"""
        queue = SessionQueue()
        session_key = "agent:main:api:dm:user-123"
        
        result = queue.enqueue(session_key, "Hello!")
        
        assert result is True
        assert queue.queue_size(session_key) == 1
        
    def test_get_queued_messages(self):
        """测试获取排队消息"""
        queue = SessionQueue()
        session_key = "agent:main:api:dm:user-123"
        
        queue.enqueue(session_key, "Message 1")
        queue.enqueue(session_key, "Message 2")
        
        messages = queue.get_queued_messages(session_key)
        
        assert len(messages) == 2
        assert "Message 1" in messages
        assert "Message 2" in messages
        
    def test_queue_modes(self):
        """测试队列模式"""
        queue = SessionQueue()
        session_key = "agent:main:api:dm:user-123"
        
        queue.set_session_mode(session_key, QueueMode.STEER)
        mode = queue.get_mode(session_key)
        
        assert mode == QueueMode.STEER
        
    def test_queue_cap(self):
        """测试队列容量限制"""
        queue = SessionQueue(cap=3)
        session_key = "agent:main:api:dm:user-123"
        
        for i in range(5):
            queue.enqueue(session_key, f"Message {i}")
            
        assert queue.queue_size(session_key) == 3
        
    def test_clear_queue(self):
        """测试清空队列"""
        queue = SessionQueue()
        session_key = "agent:main:api:dm:user-123"
        
        queue.enqueue(session_key, "Test")
        queue.clear_queue(session_key)
        
        assert queue.queue_size(session_key) == 0
        
    def test_queue_stats(self):
        """测试队列统计"""
        queue = SessionQueue()
        
        stats = queue.get_stats()
        
        assert "active_sessions" in stats
        assert "total_queued_messages" in stats
        assert "max_concurrent" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
