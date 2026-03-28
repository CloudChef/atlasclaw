# -*- coding: utf-8 -*-
"""
ResponseHandler 出站消息处理模块单元测试

测试 ResponseHandler、BlockStreamingConfig、HumanDelayConfig 等组件。
"""

import asyncio

import pytest

from app.xuanwu.api.response_handler import (
    BlockStreamingConfig,
    HumanDelayConfig,
    HumanDelayMode,
    NoopChannelAdapter,
    ResponseChunk,
    ResponseConfig,
    ResponseHandler,
)


class TestHumanDelayConfig:
    """HumanDelayConfig 测试类"""

    def test_off_returns_zero(self):
        """OFF 模式返回 0 延迟"""
        config = HumanDelayConfig(mode=HumanDelayMode.OFF)
        assert config.get_delay_seconds() == 0.0

    def test_natural_returns_range(self):
        """NATURAL 模式返回 0.8-2.5 范围"""
        config = HumanDelayConfig(mode=HumanDelayMode.NATURAL)
        delays = [config.get_delay_seconds() for _ in range(20)]
        assert all(0.0 <= d <= 3.0 for d in delays)  # 允许一点浮动
        # 至少有一些变化
        assert len(set(f"{d:.2f}" for d in delays)) > 1

    def test_custom_returns_range(self):
        """CUSTOM 模式返回自定义范围"""
        config = HumanDelayConfig(
            mode=HumanDelayMode.CUSTOM, min_ms=100, max_ms=200,
        )
        delays = [config.get_delay_seconds() for _ in range(20)]
        assert all(0.05 <= d <= 0.25 for d in delays)


class TestResponseConfig:
    """ResponseConfig 测试类"""

    def test_default_config(self):
        """测试默认配置"""
        config = ResponseConfig()
        assert config.text_chunk_limit == 4096
        assert config.no_reply_token == "NO_REPLY"
        assert not config.block_streaming.enabled


class TestResponseHandler:
    """ResponseHandler 测试类"""

    def test_create_handler(self):
        """测试创建处理器"""
        handler = ResponseHandler()
        assert handler is not None

    def test_suppress_no_reply_with_token(self):
        """测试静默令牌检测"""
        handler = ResponseHandler()
        cleaned, suppress = handler.suppress_no_reply("NO_REPLY")
        assert suppress
        assert cleaned == ""

    def test_suppress_no_reply_partial(self):
        """测试静默令牌部分匹配"""
        handler = ResponseHandler()
        cleaned, suppress = handler.suppress_no_reply("Some text NO_REPLY here")
        assert not suppress  # 清理后仍有内容
        assert "Some text" in cleaned

    def test_suppress_no_reply_none(self):
        """测试无静默令牌"""
        handler = ResponseHandler()
        cleaned, suppress = handler.suppress_no_reply("Normal text")
        assert not suppress
        assert cleaned == "Normal text"

    @pytest.mark.asyncio
    async def test_process_simple_stream(self):
        """测试处理简单流"""
        handler = ResponseHandler()

        async def content_stream():
            yield "Hello "
            yield "World!"

        chunks = []
        async for chunk in handler.process(content_stream()):
            chunks.append(chunk)

        assert len(chunks) >= 1
        # 最后一个块应包含所有内容
        final = chunks[-1]
        assert final.is_final
        assert "Hello World!" in final.content

    @pytest.mark.asyncio
    async def test_process_with_prefix(self):
        """测试带前缀的处理"""
        config = ResponseConfig(response_prefix="[Bot] ")
        handler = ResponseHandler(config)

        async def content_stream():
            yield "Response text"

        chunks = []
        async for chunk in handler.process(content_stream()):
            chunks.append(chunk)

        final = chunks[-1]
        assert final.content.startswith("[Bot] ")

    @pytest.mark.asyncio
    async def test_process_filters_no_reply(self):
        """测试过滤静默令牌"""
        handler = ResponseHandler()

        async def content_stream():
            yield "NO_REPLY"

        chunks = []
        async for chunk in handler.process(content_stream()):
            chunks.append(chunk)

        # 应该没有内容（或空内容）
        # 由于 buffer 被清空，不应产生内容块
        assert len(chunks) == 0 or all(c.content == "" for c in chunks)

    @pytest.mark.asyncio
    async def test_process_truncates_to_limit(self):
        """测试截断到渠道限制"""
        config = ResponseConfig(text_chunk_limit=50)
        handler = ResponseHandler(config)

        async def content_stream():
            yield "A" * 200

        chunks = []
        async for chunk in handler.process(content_stream()):
            chunks.append(chunk)

        final = chunks[-1]
        assert len(final.content) <= 50

    @pytest.mark.asyncio
    async def test_process_with_block_streaming(self):
        """测试分块流式传输"""
        config = ResponseConfig(
            block_streaming=BlockStreamingConfig(
                enabled=True,
                min_chars=10,
                max_chars=30,
            ),
            human_delay=HumanDelayConfig(mode=HumanDelayMode.OFF),
        )
        handler = ResponseHandler(config)

        async def content_stream():
            for word in "This is a moderately long text that should be chunked into multiple pieces for streaming purposes. " * 3:
                yield word

        chunks = []
        async for chunk in handler.process(content_stream()):
            chunks.append(chunk)

        # 应该产生多个块
        assert len(chunks) >= 2
        # 最后一个应该是 final
        assert chunks[-1].is_final

    @pytest.mark.asyncio
    async def test_process_with_adapter(self):
        """测试与渠道适配器联动"""
        handler = ResponseHandler()
        adapter = NoopChannelAdapter()

        async def content_stream():
            yield "Test output"

        chunks = []
        async for chunk in handler.process(content_stream(), adapter=adapter):
            chunks.append(chunk)

        assert len(chunks) >= 1

    def test_find_break_point_paragraph(self):
        """测试段落断点"""
        handler = ResponseHandler()
        text = "First paragraph.\n\nSecond paragraph."
        pos = handler._find_break_point(text, 5, 30, "paragraph")
        assert pos > 0
        assert text[pos - 1] == "\n"

    def test_find_break_point_sentence(self):
        """测试句子断点"""
        handler = ResponseHandler()
        text = "First sentence. Second sentence."
        pos = handler._find_break_point(text, 5, 25, "sentence")
        assert pos > 0

    def test_create_chunk_index(self):
        """测试块索引递增"""
        handler = ResponseHandler()
        c1 = handler._create_chunk("a", False)
        c2 = handler._create_chunk("b", True)
        assert c1.chunk_index == 0
        assert c2.chunk_index == 1


class TestResponseChunk:
    """ResponseChunk 测试类"""

    def test_create_chunk(self):
        """测试创建块"""
        chunk = ResponseChunk(content="Hello", is_final=True, chunk_index=0)
        assert chunk.content == "Hello"
        assert chunk.is_final
        assert chunk.chunk_index == 0

    def test_chunk_metadata(self):
        """测试块元数据"""
        chunk = ResponseChunk(
            content="test",
            metadata={"source": "llm"},
        )
        assert chunk.metadata["source"] == "llm"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
