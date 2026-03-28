# -*- coding: utf-8 -*-
"""
Pytest 閰嶇疆鏂囦欢

閰嶇疆 pytest fixtures 鍜屾彃浠躲€?
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# 娣诲姞椤圭洰鏍圭洰褰曞埌 Python 璺緞
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
# Default all tests to tests/xuanwu.test.json unless caller overrides XUANWU_CONFIG
os.environ.setdefault('XUANWU_CONFIG', str((Path(__file__).parent / 'xuanwu.test.json').resolve()))



@pytest.fixture(scope="session")
def event_loop():
    """鍒涘缓浜嬩欢寰幆 fixture"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def anyio_backend():
    """鎸囧畾 anyio 鍚庣"""
    return "asyncio"


@pytest.fixture(scope="session")
def test_config_path():
    """娴嬭瘯閰嶇疆鏂囦欢璺緞"""
    return Path(__file__).parent / "xuanwu.test.json"


@pytest.fixture(scope="session")
def kimi_env_vars():
    """Kimi LLM 鐜鍙橀噺閰嶇疆
    
    浼樺厛绾?
    1. 鐜鍙橀噺 ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
    2. tests/xuanwu.test.json 閰嶇疆鏂囦欢
    
    浣跨敤鏂瑰紡:
       $env:ANTHROPIC_BASE_URL="https://api.moonshot.cn/anthropic"
       $env:ANTHROPIC_API_KEY="sk-kimi-xxx"
       pytest -m llm
    """
    import json
    
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    
    # 濡傛灉鐜鍙橀噺鏈缃紝浠庢祴璇曢厤缃枃浠惰鍙?
    if not base_url or not api_key:
        test_config_path = Path(__file__).parent / "xuanwu.test.json"
        if test_config_path.exists():
            with open(test_config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            providers = config.get("model", {}).get("providers", {})
            kimi_config = providers.get("kimi", {})
            
            if not base_url:
                base_url = kimi_config.get("base_url", "")
            if not api_key:
                api_key = kimi_config.get("api_key", "")
    
    if not base_url or not api_key:
        pytest.skip("ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY must be set for LLM tests (or configure in tests/xuanwu.test.json)")
    
    return {"base_url": base_url, "api_key": api_key}


@pytest.fixture
def skill_registry():
    """鍒涘缓绌虹殑 SkillRegistry"""
    from app.xuanwu.skills.registry import SkillRegistry
    return SkillRegistry()


@pytest.fixture
def sample_skill_handler():
    """绀轰緥 skill handler锛屼娇鐢?RunContext 绫诲瀷娉ㄨВ"""
    from typing import TYPE_CHECKING
    
    if TYPE_CHECKING:
        from pydantic_ai import RunContext
        from app.xuanwu.core.deps import SkillDeps
    
    async def handler(ctx: "RunContext[SkillDeps]", query: str) -> dict:
        """绀轰緥宸ュ叿鍑芥暟"""
        return {"result": f"Processed: {query}"}
    
    return handler


# pytest 閰嶇疆
def pytest_configure(config):
    """閰嶇疆 pytest markers"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "e2e: marks tests as end-to-end tests requiring live services (set JIRA_E2E=1 etc.)"
    )
    config.addinivalue_line(
        "markers", "llm: marks tests that require LLM API calls (needs ANTHROPIC_API_KEY)"
    )

