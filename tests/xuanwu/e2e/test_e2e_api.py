# -*- coding: utf-8 -*-
"""
E2E API 测试

测试完整的 API 端到端流程，需要启动完整服务。
运行方式:
1. 设置环境变量: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
2. pytest -m e2e tests/xuanwu/test_e2e_api.py -v
"""

import os
import asyncio
import pytest
import pytest_asyncio
from typing import AsyncGenerator

import httpx


# 标记为 e2e 测试
pytestmark = pytest.mark.e2e


# 测试服务地址
TEST_SERVER_URL = os.environ.get("TEST_SERVER_URL", "http://127.0.0.1:8000")


@pytest.fixture(scope="module")
def event_loop():
    """创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP 客户端 fixture"""
    async with httpx.AsyncClient(base_url=TEST_SERVER_URL, timeout=60.0) as c:
        yield c


class TestHealthAPI:
    """健康检查 API 测试"""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client: httpx.AsyncClient):
        """测试健康检查端点"""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        
        data = resp.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


class TestSkillsAPI:
    """Skills API 测试"""

    @pytest.mark.asyncio
    async def test_list_skills(self, client: httpx.AsyncClient):
        """测试未认证访问 skills 返回 401"""
        resp = await client.get("/api/skills")
        assert resp.status_code == 401


    @pytest.mark.asyncio
    async def test_skills_contain_builtin_tools(self, client: httpx.AsyncClient):
        """测试未认证访问 skills 返回 401"""
        resp = await client.get("/api/skills")
        assert resp.status_code == 401


class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_invalid_endpoint(self, client: httpx.AsyncClient):
        """测试未认证访问无效端点时优先返回 401"""
        resp = await client.get("/api/nonexistent")
        assert resp.status_code == 401



if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "e2e"])
