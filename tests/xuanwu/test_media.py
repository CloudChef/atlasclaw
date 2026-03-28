# -*- coding: utf-8 -*-
"""
媒体理解模块单元测试

测试 MediaContent、UnderstandingResult、MediaUnderstandingHandler、
LinkExtractor、LinkExtractorConfig、TTSConfig、TTSResult、TTSManager 等组件。
"""

import tempfile
from pathlib import Path

import pytest

from app.xuanwu.media.understanding import (
    DocumentExtractor,
    MediaContent,
    MediaType,
    MediaUnderstandingHandler,
    UnderstandingResult,
    create_media_handler,
)
from app.xuanwu.media.link_extractor import (
    ExtractedLink,
    LinkExtractor,
    LinkExtractorConfig,
    LinkUnderstandingHandler,
)
from app.xuanwu.media.tts import (
    TTSConfig,
    TTSFormat,
    TTSManager,
    TTSProvider,
    TTSResult,
    TTSSynthesizer,
)


# ── MediaContent ────────────────────────────────────────────────


class TestMediaContent:
    """MediaContent 测试类"""

    def test_create_audio(self):
        """测试创建音频内容"""
        mc = MediaContent(media_type=MediaType.AUDIO, mime_type="audio/mp3")
        assert mc.media_type == MediaType.AUDIO

    def test_create_image(self):
        """测试创建图片内容"""
        mc = MediaContent(media_type=MediaType.IMAGE, mime_type="image/png")
        assert mc.media_type == MediaType.IMAGE

    def test_from_path_text(self):
        """测试从文本文件路径创建"""
        f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        try:
            f.write(b"hello world")
            f.flush()
            f.close()
            mc = MediaContent.from_path(f.name)
            assert mc.media_type == MediaType.DOCUMENT
            assert mc.size_bytes > 0
        finally:
            Path(f.name).unlink(missing_ok=True)

    def test_from_path_image(self):
        """测试从图片文件路径创建"""
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            f.write(b"\x89PNG\r\n")
            f.flush()
            f.close()
            mc = MediaContent.from_path(f.name)
            assert mc.media_type == MediaType.IMAGE
        finally:
            Path(f.name).unlink(missing_ok=True)

    def test_to_base64_from_data(self):
        """测试从内存数据生成 base64"""
        mc = MediaContent(
            media_type=MediaType.IMAGE,
            data=b"fake image data",
        )
        b64 = mc.to_base64()
        assert len(b64) > 0

    def test_to_base64_empty(self):
        """测试空内容"""
        mc = MediaContent(media_type=MediaType.UNKNOWN)
        assert mc.to_base64() == ""


class TestUnderstandingResult:
    """UnderstandingResult 测试类"""

    def test_success_result(self):
        """测试成功结果"""
        r = UnderstandingResult(success=True, text="Transcribed text", provider="openai")
        assert r.success
        assert r.text == "Transcribed text"

    def test_failure_result(self):
        """测试失败结果"""
        r = UnderstandingResult(success=False, error="API timeout")
        assert not r.success
        assert r.error == "API timeout"


# ── DocumentExtractor ───────────────────────────────────────────


class TestDocumentExtractor:
    """DocumentExtractor 测试类"""

    @pytest.mark.asyncio
    async def test_extract_text_file(self):
        """测试提取文本文件"""
        f = tempfile.NamedTemporaryFile(
            suffix=".txt", mode="w", delete=False, encoding="utf-8",
        )
        try:
            f.write("This is test content for extraction.")
            f.flush()
            f.close()

            extractor = DocumentExtractor()
            mc = MediaContent(
                media_type=MediaType.DOCUMENT,
                path=f.name,
                mime_type="text/plain",
            )
            result = await extractor.understand(mc)
            assert result.success
            assert "test content" in result.text
        finally:
            Path(f.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_extract_nonexistent_file(self):
        """测试提取不存在的文件"""
        extractor = DocumentExtractor()
        mc = MediaContent(
            media_type=MediaType.DOCUMENT,
            path="/nonexistent/file.txt",
        )
        result = await extractor.understand(mc)
        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extract_unsupported_type(self):
        """测试不支持的文件类型"""
        f = tempfile.NamedTemporaryFile(suffix=".xyz", delete=False)
        try:
            f.write(b"binary data")
            f.flush()
            f.close()

            extractor = DocumentExtractor()
            mc = MediaContent(
                media_type=MediaType.DOCUMENT,
                path=f.name,
            )
            result = await extractor.understand(mc)
            assert not result.success
            assert "Unsupported" in result.error
        finally:
            Path(f.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_extract_truncation(self):
        """测试内容截断"""
        f = tempfile.NamedTemporaryFile(
            suffix=".txt", mode="w", delete=False, encoding="utf-8",
        )
        try:
            f.write("A" * 100000)
            f.flush()
            f.close()

            extractor = DocumentExtractor(max_chars=1000)
            mc = MediaContent(
                media_type=MediaType.DOCUMENT,
                path=f.name,
            )
            result = await extractor.understand(mc)
            assert result.success
            assert len(result.text) <= 1100  # 截断 + 标记
        finally:
            Path(f.name).unlink(missing_ok=True)


# ── MediaUnderstandingHandler ───────────────────────────────────


class TestMediaUnderstandingHandler:
    """MediaUnderstandingHandler 测试类"""

    def test_create_handler(self):
        """测试创建处理器"""
        handler = MediaUnderstandingHandler()
        assert handler is not None

    def test_register_provider(self):
        """测试注册提供商"""
        handler = MediaUnderstandingHandler()
        extractor = DocumentExtractor()
        handler.register_provider(extractor)
        assert handler.supports(MediaType.DOCUMENT)

    def test_supports_unregistered(self):
        """测试不支持的类型"""
        handler = MediaUnderstandingHandler()
        assert not handler.supports(MediaType.AUDIO)

    @pytest.mark.asyncio
    async def test_understand_no_provider(self):
        """测试无提供商时理解"""
        handler = MediaUnderstandingHandler()
        mc = MediaContent(media_type=MediaType.AUDIO)
        result = await handler.understand(mc)
        assert not result.success
        assert "No provider" in result.error

    @pytest.mark.asyncio
    async def test_understand_with_document(self):
        """测试文档理解"""
        handler = create_media_handler(enable_document_extraction=True)

        f = tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", delete=False, encoding="utf-8",
        )
        try:
            f.write("# Test\nContent here")
            f.flush()
            f.close()

            mc = MediaContent.from_path(f.name)
            result = await handler.understand(mc)
            assert result.success
            assert "Content" in result.text
        finally:
            Path(f.name).unlink(missing_ok=True)


# ── LinkExtractor ───────────────────────────────────────────────


class TestLinkExtractor:
    """LinkExtractor 测试类"""

    def test_extract_urls(self):
        """测试从文本提取 URL"""
        text = "Visit https://example.com and http://test.org for details."
        urls = LinkExtractor.extract_urls(text)
        assert len(urls) == 2
        assert "https://example.com" in urls
        assert "http://test.org" in urls

    def test_extract_urls_deduplicate(self):
        """测试 URL 去重"""
        text = "https://example.com and https://example.com again"
        urls = LinkExtractor.extract_urls(text)
        assert len(urls) == 1

    def test_extract_urls_clean_trailing(self):
        """测试清理尾部标点"""
        text = "See https://example.com."
        urls = LinkExtractor.extract_urls(text)
        assert urls[0] == "https://example.com"

    def test_extract_no_urls(self):
        """测试无 URL 的文本"""
        urls = LinkExtractor.extract_urls("No links here.")
        assert len(urls) == 0

    def test_is_valid_url(self):
        """测试 URL 验证"""
        assert LinkExtractor.is_valid_url("https://example.com")
        assert LinkExtractor.is_valid_url("http://test.org/path?q=1")
        assert not LinkExtractor.is_valid_url("not a url")
        assert not LinkExtractor.is_valid_url("")


class TestLinkExtractorConfig:
    """LinkExtractorConfig 测试类"""

    def test_default_allows_all(self):
        """默认允许所有 URL"""
        config = LinkExtractorConfig()
        assert config.is_allowed("https://example.com")
        assert config.is_allowed("https://any-site.org")

    def test_blacklist(self):
        """测试黑名单"""
        config = LinkExtractorConfig(blacklist=["evil.com", "*.malware.org"])
        assert not config.is_allowed("https://evil.com/page")
        assert not config.is_allowed("https://sub.malware.org/page")
        assert config.is_allowed("https://safe.com")

    def test_whitelist(self):
        """测试白名单"""
        config = LinkExtractorConfig(whitelist=["docs.example.com"])
        assert config.is_allowed("https://docs.example.com/api")
        assert not config.is_allowed("https://other.com")

    def test_blacklist_overrides_whitelist(self):
        """黑名单优先于白名单"""
        config = LinkExtractorConfig(
            whitelist=["*.example.com"],
            blacklist=["bad.example.com"],
        )
        assert not config.is_allowed("https://bad.example.com")
        assert config.is_allowed("https://good.example.com")


class TestExtractedLink:
    """ExtractedLink 测试类"""

    def test_auto_extract_domain(self):
        """测试自动提取域名"""
        link = ExtractedLink(url="https://docs.example.com/path")
        assert link.domain == "docs.example.com"

    def test_error_link(self):
        """测试错误链接"""
        link = ExtractedLink(url="https://example.com", error="Timeout")
        assert link.error == "Timeout"


class TestLinkUnderstandingHandler:
    """LinkUnderstandingHandler 测试类"""

    def test_create_handler(self):
        """测试创建处理器"""
        handler = LinkUnderstandingHandler()
        assert handler is not None

    def test_inject_link_content_empty(self):
        """测试注入空链接"""
        handler = LinkUnderstandingHandler()
        result = handler.inject_link_content("Original text", [])
        assert result == "Original text"

    def test_inject_link_content(self):
        """测试注入链接内容"""
        handler = LinkUnderstandingHandler()
        links = [
            ExtractedLink(
                url="https://example.com",
                title="Example Site",
                description="An example website",
                content="Page content here",
            ),
        ]
        result = handler.inject_link_content("Check this link", links)
        assert "Example Site" in result
        assert "Page content here" in result

    def test_inject_link_content_skips_errors(self):
        """测试跳过错误链接"""
        handler = LinkUnderstandingHandler()
        links = [
            ExtractedLink(url="https://broken.com", error="timeout"),
        ]
        result = handler.inject_link_content("Text", links)
        assert result == "Text"


# ── TTS ─────────────────────────────────────────────────────────


class TestTTSConfig:
    """TTSConfig 测试类"""

    def test_default_config(self):
        """测试默认配置"""
        config = TTSConfig()
        assert config.voice == "alloy"
        assert config.speed == 1.0
        assert config.format == TTSFormat.MP3

    def test_validate_ok(self):
        """测试验证通过"""
        config = TTSConfig(speed=1.5)
        ok, error = config.validate()
        assert ok

    def test_validate_speed_too_low(self):
        """测试速度过低"""
        config = TTSConfig(speed=0.1)
        ok, error = config.validate()
        assert not ok
        assert "Speed" in error

    def test_validate_speed_too_high(self):
        """测试速度过高"""
        config = TTSConfig(speed=5.0)
        ok, error = config.validate()
        assert not ok


class TestTTSResult:
    """TTSResult 测试类"""

    def test_success_result(self):
        """测试成功结果"""
        result = TTSResult(
            success=True,
            audio_data=b"fake audio",
            format=TTSFormat.MP3,
            characters=10,
        )
        assert result.success
        assert result.audio_data == b"fake audio"

    def test_save_audio(self):
        """测试保存音频"""
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        try:
            f.close()
            result = TTSResult(success=True, audio_data=b"audio bytes")
            assert result.save(f.name)
            assert Path(f.name).read_bytes() == b"audio bytes"
        finally:
            Path(f.name).unlink(missing_ok=True)

    def test_save_no_data(self):
        """测试无数据保存"""
        result = TTSResult(success=True)
        assert not result.save("/tmp/nope.mp3")

    def test_to_base64_from_data(self):
        """测试从数据生成 base64"""
        result = TTSResult(success=True, audio_data=b"test")
        b64 = result.to_base64()
        assert len(b64) > 0

    def test_to_base64_from_cached(self):
        """测试使用缓存的 base64"""
        result = TTSResult(success=True, audio_base64="cached_b64")
        assert result.to_base64() == "cached_b64"

    def test_to_base64_empty(self):
        """测试空数据"""
        result = TTSResult(success=False)
        assert result.to_base64() == ""


class TestTTSManager:
    """TTSManager 测试类"""

    def test_create_manager(self):
        """测试创建管理器"""
        mgr = TTSManager()
        assert mgr is not None

    def test_register_provider(self):
        """测试注册提供商"""
        mgr = TTSManager()
        mgr.register_provider(TTSProvider.OPENAI, "fake-key")
        assert TTSProvider.OPENAI in mgr._synthesizers

    @pytest.mark.asyncio
    async def test_synthesize_no_provider(self):
        """测试无提供商时合成"""
        mgr = TTSManager()
        result = await mgr.synthesize("Hello")
        assert not result.success
        assert "No TTS provider" in result.error

    @pytest.mark.asyncio
    async def test_synthesize_unknown_provider(self):
        """测试指定未注册的提供商"""
        mgr = TTSManager()
        result = await mgr.synthesize("Hello", provider=TTSProvider.GOOGLE)
        assert not result.success
        assert "not registered" in result.error

    def test_cache_key_deterministic(self):
        """测试缓存键确定性"""
        mgr = TTSManager()
        config = TTSConfig(voice="alloy", speed=1.0)
        k1 = mgr._cache_key("Hello", config)
        k2 = mgr._cache_key("Hello", config)
        assert k1 == k2

    def test_cache_key_differs(self):
        """测试不同输入产生不同缓存键"""
        mgr = TTSManager()
        k1 = mgr._cache_key("Hello", TTSConfig())
        k2 = mgr._cache_key("World", TTSConfig())
        assert k1 != k2

    def test_clear_cache(self):
        """测试清空缓存"""
        mgr = TTSManager()
        mgr._cache["key1"] = TTSResult(success=True)
        mgr._cache["key2"] = TTSResult(success=True)
        count = mgr.clear_cache()
        assert count == 2
        assert len(mgr._cache) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
