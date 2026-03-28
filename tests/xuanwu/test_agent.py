# -*- coding: utf-8 -*-
"""
Agent 模块单元测试

测试 StreamEvent、BlockChunker、CompactionPipeline、PromptBuilder 等组件。
"""

from pathlib import Path

import pytest
import app.xuanwu.core.config as config_module

from app.xuanwu.agent.stream import (
    StreamEvent,
    StreamEventType,
    BlockChunker,
)
from app.xuanwu.agent.compaction import CompactionPipeline, CompactionConfig
from app.xuanwu.agent.prompt_builder import PromptBuilder, PromptBuilderConfig, PromptMode


class TestStreamEvent:
    """StreamEvent 测试类"""
    
    def test_create_lifecycle_start(self):
        """测试创建生命周期开始事件"""
        event = StreamEvent.lifecycle_start()
        
        assert event.type == "lifecycle"
        assert event.phase == "start"
        
    def test_create_lifecycle_end(self):
        """测试创建生命周期结束事件"""
        event = StreamEvent.lifecycle_end()
        
        assert event.type == "lifecycle"
        assert event.phase == "end"
        
    def test_create_assistant_delta(self):
        """测试创建助手文本增量事件"""
        event = StreamEvent.assistant_delta("Hello!")
        
        assert event.type == "assistant"
        assert event.content == "Hello!"
        
    def test_create_tool_event(self):
        """测试创建工具事件"""
        event = StreamEvent.tool_start("search")
        
        assert event.type == "tool"
        assert event.phase == "start"
        assert event.tool == "search"
        
    def test_create_error_event(self):
        """测试创建错误事件"""
        event = StreamEvent.error_event("Something went wrong")
        
        assert event.type == "error"
        assert event.error == "Something went wrong"
        
    def test_event_to_dict(self):
        """测试事件序列化"""
        event = StreamEvent.assistant_delta("Test")
        data = event.to_dict()
        
        assert "type" in data
        assert data["type"] == "assistant"


class TestBlockChunker:
    """BlockChunker 测试类"""
    
    def test_create_chunker(self):
        """测试创建分块器"""
        chunker = BlockChunker(min_chars=100, max_chars=200)
        assert chunker is not None
        
    def test_feed_and_flush(self):
        """测试输入和刷新"""
        # 使用较大的 min_chars 确保不会立即输出
        chunker = BlockChunker(min_chars=100, max_chars=200)
        
        # feed() 返回空列表，因为未达到 min_chars
        chunks1 = chunker.feed("Hello ")
        assert chunks1 == []
        
        chunks2 = chunker.feed("World!")
        assert chunks2 == []
        
        # flush() 返回剩余缓冲
        result = chunker.flush()
        assert result is not None
        assert "Hello World!" in result
        
    def test_chunker_with_long_text(self):
        """测试长文本分块"""
        chunker = BlockChunker(min_chars=10, max_chars=30)
        
        # 喂入长文本
        long_text = "This is a long text. " * 5
        
        # feed() 直接返回分块列表
        chunks = chunker.feed(long_text)
        
        # 刷新剩余
        remaining = chunker.flush()
        if remaining:
            chunks.append(remaining)
            
        # 合并后应该等于原文
        combined = "".join(chunks)
        assert len(combined) > 0
        assert combined == long_text


class TestCompactionPipeline:
    """CompactionPipeline 测试类"""
    
    def test_create_pipeline(self):
        """测试创建压缩管线"""
        config = CompactionConfig(
            context_window=128000,
            reserve_tokens_floor=20000,
            soft_threshold_tokens=4000
        )
        pipeline = CompactionPipeline(config)
        
        assert pipeline is not None
        
    def test_estimate_tokens(self):
        """测试 token 估算"""
        config = CompactionConfig()
        pipeline = CompactionPipeline(config)
        
        messages = [
            {"role": "user", "content": "Hello, this is a test message."}
        ]
        
        tokens = pipeline.estimate_tokens(messages)
        
        assert tokens > 0
        
    def test_should_compact(self):
        """测试是否需要压缩判断"""
        config = CompactionConfig(
            context_window=128000,
            reserve_tokens_floor=20000,
            soft_threshold_tokens=4000
        )
        pipeline = CompactionPipeline(config)
        
        # 小消息列表不需要压缩
        small_messages = [{"role": "user", "content": "Hi"}]
        assert not pipeline.should_compact(small_messages)
        
        # 大消息列表需要压缩
        large_content = "A" * 500000  # 很长的内容
        large_messages = [{"role": "user", "content": large_content}]
        assert pipeline.should_compact(large_messages)


class TestPromptBuilder:
    """PromptBuilder 测试类"""
    
    def test_create_builder(self):
        """测试创建构建器"""
        config = PromptBuilderConfig()
        builder = PromptBuilder(config)
        
        assert builder is not None
        
    def test_build_basic_prompt(self):
        """测试构建基础提示词"""
        config = PromptBuilderConfig()
        builder = PromptBuilder(config)
        
        prompt = builder.build()
        
        assert len(prompt) > 0
        
    def test_prompt_modes(self):
        """测试不同提示模式"""
        # Full 模式
        full_config = PromptBuilderConfig(mode=PromptMode.FULL)
        full_builder = PromptBuilder(full_config)
        full_prompt = full_builder.build()
        
        # Minimal 模式
        minimal_config = PromptBuilderConfig(mode=PromptMode.MINIMAL)
        minimal_builder = PromptBuilder(minimal_config)
        minimal_prompt = minimal_builder.build()
        
        # NONE 模式
        none_config = PromptBuilderConfig(mode=PromptMode.NONE)
        none_builder = PromptBuilder(none_config)
        none_prompt = none_builder.build()
        
        # 验证存在
        assert len(full_prompt) > 0
        assert len(minimal_prompt) > 0
        assert len(none_prompt) >= 0  # NONE 模式可能很短
        
    def test_build_with_tools(self):
        """测试构建带工具的提示词"""
        config = PromptBuilderConfig()
        builder = PromptBuilder(config)
        
        tools = [
            {"name": "search", "description": "Search the web"},
            {"name": "calculator", "description": "Perform calculations"},
        ]
        
        prompt = builder.build(tools=tools)
        
        assert len(prompt) > 0


    def test_builder_uses_configured_workspace_path_when_not_provided(self, tmp_path, monkeypatch):
        """PromptBuilder should resolve workspace path from config when omitted."""
        config_path = tmp_path / "xuanwu.json"
        config_path.write_text(
            '{\n  "workspace": {\n    "path": "custom-workspace"\n  }\n}\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XUANWU_CONFIG", str(config_path))
        config_module._config_manager = None

        builder = PromptBuilder(PromptBuilderConfig(workspace_path=""))

        assert Path(builder.config.workspace_path).resolve() == (
            tmp_path / "custom-workspace"
        ).resolve()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
