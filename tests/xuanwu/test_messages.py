# -*- coding: utf-8 -*-
"""
消息处理模块单元测试

测试 CommandParser、MessageHandler 等组件。
"""

import pytest

from app.xuanwu.messages.command import (
    CommandParser,
    CommandDefinition,
    CommandCategory,
)
from app.xuanwu.messages.handler import (
    MessageHandler,
    InboundMessage,
    OutboundMessage,
    ChatType,
)


class TestCommandParser:
    """CommandParser 测试类"""
    
    def test_create_parser(self):
        """测试创建解析器"""
        parser = CommandParser()
        assert parser is not None
        
    def test_parse_simple_command(self):
        """测试解析简单命令"""
        parser = CommandParser()
        parser.register_default_commands()
        
        result = parser.parse("/status")
        
        assert result is not None
        assert result.name == "status"
        assert result.is_standalone
        
    def test_parse_command_with_args(self):
        """测试解析带参数的命令"""
        parser = CommandParser()
        parser.register_default_commands()
        
        result = parser.parse("/model gpt-4o")
        
        assert result is not None
        assert result.name == "model"
        assert "gpt-4o" in result.args
        
    def test_parse_non_command(self):
        """测试解析非命令消息"""
        parser = CommandParser()
        
        result = parser.parse("Hello, this is a normal message")
        
        assert result is None
        
    def test_register_custom_command(self):
        """测试注册自定义命令"""
        parser = CommandParser()
        
        custom_cmd = CommandDefinition(
            name="custom",
            category=CommandCategory.SESSION_CONTROL,
            description="Custom command"
        )
        parser.register(custom_cmd)
        
        result = parser.parse("/custom")
        
        assert result is not None
        assert result.definition is not None
        assert result.definition.name == "custom"
        
    def test_bypass_debounce(self):
        """测试绕过防抖判断"""
        parser = CommandParser()
        parser.register_default_commands()
        
        result = parser.parse("/status")
        
        assert parser.should_bypass_debounce(result)
        
    def test_requires_llm(self):
        """测试需要 LLM 判断"""
        parser = CommandParser()
        parser.register_default_commands()
        
        # /status 不需要 LLM
        status_cmd = parser.parse("/status")
        assert not parser.requires_llm(status_cmd)
        
        # /compact 需要 LLM
        compact_cmd = parser.parse("/compact")
        assert parser.requires_llm(compact_cmd)
        
    def test_list_commands(self):
        """测试列出所有命令"""
        parser = CommandParser()
        parser.register_default_commands()
        
        commands = parser.list_commands()
        
        assert len(commands) > 0
        
    def test_list_commands_by_category(self):
        """测试按类别列出命令"""
        parser = CommandParser()
        parser.register_default_commands()
        
        info_commands = parser.list_commands(CommandCategory.INFO_QUERY)
        
        assert all(c.category == CommandCategory.INFO_QUERY for c in info_commands)


class TestMessageHandler:
    """MessageHandler 测试类"""
    
    def test_create_handler(self):
        """测试创建处理器"""
        handler = MessageHandler()
        assert handler is not None
        
    def test_create_inbound_message(self):
        """测试创建入站消息"""
        message = InboundMessage(
            message_id="msg-123",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456",
            body="Hello!"
        )
        
        assert message.message_id == "msg-123"
        assert message.body == "Hello!"
        assert not message.is_group_chat
        
    def test_group_chat_message(self):
        """测试群聊消息"""
        message = InboundMessage(
            message_id="msg-123",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456",
            chat_type=ChatType.GROUP,
            body="Hello group!"
        )
        
        assert message.is_group_chat
        
    def test_shape_response(self):
        """测试响应整形"""
        handler = MessageHandler(response_prefix="[Bot] ")
        
        messages = handler.shape_response(
            "Hello!",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456"
        )
        
        assert len(messages) == 1
        assert messages[0].body.startswith("[Bot] ")
        
    def test_shape_response_no_reply(self):
        """测试 NO_REPLY 响应"""
        handler = MessageHandler()
        
        messages = handler.shape_response(
            "NO_REPLY",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456"
        )
        
        assert len(messages) == 0
        
    def test_shape_response_chunking(self):
        """测试响应分块"""
        handler = MessageHandler()
        
        long_text = "A" * 1000
        
        messages = handler.shape_response(
            long_text,
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456",
            text_chunk_limit=200
        )
        
        assert len(messages) > 1
        assert all(len(m.body) <= 200 for m in messages)


class TestOutboundMessage:
    """OutboundMessage 测试类"""
    
    def test_create_outbound_message(self):
        """测试创建出站消息"""
        message = OutboundMessage(
            body="Hello!",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456"
        )
        
        assert message.body == "Hello!"
        assert message.is_final
        assert not message.is_chunk
        
    def test_chunked_message(self):
        """测试分块消息"""
        message = OutboundMessage(
            body="Chunk 1",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-456",
            is_chunk=True,
            chunk_index=0,
            is_final=False
        )
        
        assert message.is_chunk
        assert message.chunk_index == 0
        assert not message.is_final


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
