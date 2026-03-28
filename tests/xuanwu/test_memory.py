# -*- coding: utf-8 -*-
"""
记忆系统单元测试

测试 MemoryManager、HybridSearcher 等组件。
"""

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.xuanwu.memory.manager import (
    MemoryManager,
    MemoryEntry,
    MemoryType,
)
from app.xuanwu.memory.search import (
    HybridSearcher,
    SearchResult,
)


class TestMemoryEntry:
    """MemoryEntry 测试类"""
    
    def test_create_entry(self):
        """测试创建记忆条目"""
        entry = MemoryEntry(
            id="test-123",
            content="This is a test memory",
            memory_type=MemoryType.DAILY
        )
        
        assert entry.id == "test-123"
        assert entry.content == "This is a test memory"
        assert entry.memory_type == MemoryType.DAILY
        
    def test_generate_id(self):
        """测试生成 ID"""
        content = "Test content"
        timestamp = datetime.now(timezone.utc)
        
        id1 = MemoryEntry.generate_id(content, timestamp)
        id2 = MemoryEntry.generate_id(content, timestamp)
        
        # 相同输入应该生成相同 ID
        assert id1 == id2
        assert len(id1) == 12
        
    def test_entry_with_tags(self):
        """测试带标签的条目"""
        entry = MemoryEntry(
            id="test-123",
            content="Tagged memory",
            tags=["important", "work", "project-x"]
        )
        
        assert len(entry.tags) == 3
        assert "important" in entry.tags


class TestMemoryManager:
    """MemoryManager 测试类"""
    
    @pytest.fixture
    def temp_workspace(self):
        """创建临时工作区"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
            
    @pytest.mark.asyncio
    async def test_create_manager(self, temp_workspace):
        """测试创建管理器"""
        manager = MemoryManager(workspace=str(temp_workspace))
        assert manager is not None
        
    @pytest.mark.asyncio
    async def test_write_daily(self, temp_workspace):
        """测试写入每日日志"""
        manager = MemoryManager(workspace=str(temp_workspace))
        
        entry = await manager.write_daily(
            "User asked about cloud resources",
            source="session:main",
            tags=["cloud", "resources"]
        )
        
        assert entry is not None
        assert entry.memory_type == MemoryType.DAILY
        assert "cloud" in entry.tags
        
    @pytest.mark.asyncio
    async def test_write_long_term(self, temp_workspace):
        """测试写入长期记忆"""
        manager = MemoryManager(workspace=str(temp_workspace))
        
        entry = await manager.write_long_term(
            "User prefers concise answers",
            source="analysis",
            section="Preferences"
        )
        
        assert entry is not None
        assert entry.memory_type == MemoryType.LONG_TERM


class TestHybridSearcher:
    """HybridSearcher 测试类"""
    
    def test_create_searcher(self):
        """测试创建搜索器"""
        searcher = HybridSearcher()
        assert searcher is not None
        
    def test_index_entry(self):
        """测试索引条目"""
        searcher = HybridSearcher()
        
        entry = MemoryEntry(
            id="test-1",
            content="Cloud resource management best practices"
        )
        
        searcher.index_sync(entry)
        
        # 验证已索引
        assert len(searcher._entries) == 1
        
    @pytest.mark.asyncio
    async def test_text_search(self):
        """测试文本搜索"""
        searcher = HybridSearcher()
        
        # 索引一些条目
        entries = [
            MemoryEntry(id="1", content="Cloud computing fundamentals"),
            MemoryEntry(id="2", content="Database management systems"),
            MemoryEntry(id="3", content="Cloud resource optimization"),
        ]
        
        for entry in entries:
            searcher.index_sync(entry)
            
        # 搜索
        results = await searcher.search("cloud", top_k=5)
        
        # 应该返回包含 "cloud" 的结果
        assert len(results) >= 2
        
    def test_cosine_similarity(self):
        """测试余弦相似度计算"""
        searcher = HybridSearcher()
        
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]
        vec3 = [0.0, 1.0, 0.0]
        
        # 相同向量相似度为 1
        assert searcher._cosine_similarity(vec1, vec2) == 1.0
        
        # 正交向量相似度为 0
        assert searcher._cosine_similarity(vec1, vec3) == 0.0
        
    def test_recency_calculation(self):
        """测试时间衰减计算"""
        searcher = HybridSearcher(half_life_days=30.0)
        
        # 今天的衰减因子应该接近 1
        now = datetime.now(timezone.utc)
        factor_now = searcher._calculate_recency(now)
        assert factor_now > 0.99
        
        # 30 天前的衰减因子应该接近 0.5
        thirty_days_ago = now - timedelta(days=30)
        factor_30 = searcher._calculate_recency(thirty_days_ago)
        assert 0.45 < factor_30 < 0.55
        
    def test_clear_index(self):
        """测试清空索引"""
        searcher = HybridSearcher()
        
        entry = MemoryEntry(id="1", content="Test")
        searcher.index_sync(entry)
        
        searcher.clear()
        
        assert len(searcher._entries) == 0


class TestSearchResult:
    """SearchResult 测试类"""
    
    def test_create_result(self):
        """测试创建搜索结果"""
        entry = MemoryEntry(id="1", content="Test")
        result = SearchResult(
            entry=entry,
            score=0.85,
            vector_score=0.9,
            text_score=0.7
        )
        
        assert result.score == 0.85
        assert result.entry.id == "1"
        
    def test_result_with_highlights(self):
        """测试带高亮的结果"""
        entry = MemoryEntry(id="1", content="Test content")
        result = SearchResult(
            entry=entry,
            score=0.8,
            highlights=["...test...", "...content..."]
        )
        
        assert len(result.highlights) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
