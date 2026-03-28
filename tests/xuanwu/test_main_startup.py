# -*- coding: utf-8 -*-
"""
main.py 启动流程测试

测试 FastAPI 应用的 lifespan 初始化流程。
验证所有组件正确初始化：SessionManager, SkillRegistry, AgentRunner 等。
"""

import os
import pytest
from pathlib import Path

from fastapi.testclient import TestClient


class TestMainStartup:
    """测试 main.py 启动流程"""

    def test_import_main_module(self):
        """验证可以导入 main 模块"""
        from app.xuanwu import main
        assert main is not None

    def test_app_instance_exists(self):
        """验证 FastAPI app 实例存在"""
        from app.xuanwu.main import app
        assert app is not None
        assert "Xuanwu" in app.title

    def test_app_has_lifespan(self):
        """验证 app 有 lifespan 配置"""
        from app.xuanwu.main import app
        assert app.router.lifespan_context is not None

    def test_config_loading(self, test_config_path):
        """验证配置文件加载"""
        from app.xuanwu.core.config import ConfigManager
        
        config_manager = ConfigManager(config_path=str(test_config_path))
        config = config_manager.load()
        assert config is not None
        assert config.model.primary == "test-token-1"
        assert len(config.model.tokens) == 3

    def test_startup_with_env_vars_succeeds(self, test_config_path):
        """验证有环境变量配置时启动成功"""
        import importlib
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        
        # 重新加载模块
        import app.xuanwu.main as main_module
        importlib.reload(main_module)
        
        # 创建测试客户端应该成功
        with TestClient(main_module.app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"



class TestConfigResolution:
    """测试配置解析"""

    def test_provider_config_resolution(self, test_config_path):
        """验证 provider 配置解析"""
        from app.xuanwu.core.config import ConfigManager
        
        config_manager = ConfigManager(config_path=str(test_config_path))
        config = config_manager.load()
        
        # 验证 model 配置 - 现在使用 tokens 配置
        assert config.model.primary == "test-token-1"
        assert len(config.model.tokens) == 3


class TestSimpleLLMCall:
    """简单 LLM 调用测试"""

    @pytest.mark.llm
    def test_simple_agent_call_to_llm(self):
        token_api_key = os.environ.get("TOKEN_1_API_KEY", "").strip()
        token_base_url = os.environ.get("TOKEN_1_BASE_URL", "").strip()
        token_model = os.environ.get("TOKEN_1_MODEL", "").strip()
        if not token_api_key or not token_base_url or not token_model:
            pytest.xfail("LLM 环境变量未配置，跳过真实 LLM 验证")

        from app.xuanwu.main import app

        with TestClient(app) as client:
            login_resp = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "admin"},
            )
            if login_resp.status_code not in (200, 400):
                assert login_resp.status_code == 200

            session_resp = client.post("/api/sessions", json={"chat_type": "dm"})
            assert session_resp.status_code == 200
            session_key = session_resp.json()["session_key"]

            run_resp = client.post(
                "/api/agent/run",
                json={
                    "session_key": session_key,
                    "message": "Reply with OK only.",
                    "timeout_seconds": 60,
                },
            )
            assert run_resp.status_code == 200
            assert run_resp.json().get("run_id")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "llm"])
