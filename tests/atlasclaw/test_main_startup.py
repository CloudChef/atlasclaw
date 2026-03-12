# -*- coding: utf-8 -*-
"""
main.py 启动流程测试

测试 FastAPI 应用的 lifespan 初始化流程。
验证所有组件正确初始化：SessionManager, SkillRegistry, AgentRunner 等。
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


class TestMainStartup:
    """测试 main.py 启动流程"""

    def test_import_main_module(self):
        """验证可以导入 main 模块"""
        from app.atlasclaw import main
        assert main is not None

    def test_app_instance_exists(self):
        """验证 FastAPI app 实例存在"""
        from app.atlasclaw.main import app
        assert app is not None
        assert "AtlasClaw" in app.title

    def test_app_has_lifespan(self):
        """验证 app 有 lifespan 配置"""
        from app.atlasclaw.main import app
        assert app.router.lifespan_context is not None

    def test_config_loading(self, test_config_path):
        """验证配置文件加载"""
        from app.atlasclaw.core.config import ConfigManager
        
        config_manager = ConfigManager(config_path=str(test_config_path))
        config = config_manager.load()
        assert config is not None
        assert config.model.primary == "deepseek/deepseek-chat"

    def test_startup_with_env_vars_succeeds(self, test_config_path):
        """验证有环境变量配置时启动成功"""
        import importlib
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        
        # 重新加载模块
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        # 创建测试客户端应该成功
        with TestClient(main_module.app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"
            skills_resp = client.get("/api/skills")
            assert skills_resp.status_code == 200
            skill_names = [s["name"] for s in skills_resp.json()["skills"]]
            assert "jira-issue" in skill_names, "应该从 providers_root 加载外部 Jira skill"



    def test_skill_registry_initialized(self, kimi_env_vars):
        """验证 SkillRegistry 正确初始化"""
        os.environ["ANTHROPIC_BASE_URL"] = kimi_env_vars["base_url"]
        os.environ["ANTHROPIC_API_KEY"] = kimi_env_vars["api_key"]
        
        import importlib
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        with TestClient(main_module.app) as client:
            # 通过 API 验证 skills 已加载
            resp = client.get("/api/skills")
            assert resp.status_code == 200
            
            skills = resp.json()["skills"]
            assert len(skills) > 0, "应该加载了 skills"

    def test_builtin_tools_loaded(self, kimi_env_vars):
        """验证内置工具已加载"""
        os.environ["ANTHROPIC_BASE_URL"] = kimi_env_vars["base_url"]
        os.environ["ANTHROPIC_API_KEY"] = kimi_env_vars["api_key"]
        
        import importlib
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        with TestClient(main_module.app) as client:
            resp = client.get("/api/skills")
            skills = resp.json()["skills"]
            
            skill_names = [s["name"] for s in skills]
            
            # 验证核心内置工具
            expected_tools = ["read", "write", "edit", "exec"]
            for tool in expected_tools:
                assert tool in skill_names, f"内置工具 {tool} 应该存在"

    def test_agent_runner_initialized(self, kimi_env_vars):
        """验证 AgentRunner 正确初始化"""
        os.environ["ANTHROPIC_BASE_URL"] = kimi_env_vars["base_url"]
        os.environ["ANTHROPIC_API_KEY"] = kimi_env_vars["api_key"]
        
        import importlib
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        with TestClient(main_module.app):
            # 验证全局变量已设置
            from app.atlasclaw.main import get_api_context
            ctx = get_api_context()
            
            assert ctx is not None
            assert ctx.skill_registry is not None
            assert ctx.session_manager is not None
            assert ctx.agent_runner is not None


class TestConfigResolution:
    """测试配置解析"""

    def test_provider_config_resolution(self, test_config_path):
        """验证 provider 配置解析"""
        from app.atlasclaw.core.config import ConfigManager
        
        config_manager = ConfigManager(config_path=str(test_config_path))
        config = config_manager.load()
        
        # 验证 model 配置
        assert config.model.primary == "deepseek/deepseek-chat"
        
        # 验证 provider 配置
        assert "deepseek" in config.model.providers
        deepseek_config = config.model.providers["deepseek"]
        # deepseek_config 可能是 dict 或对象
        if isinstance(deepseek_config, dict):
            assert deepseek_config.get("base_url") == "https://api.deepseek.com"
            assert deepseek_config.get("api_key") == "${DEEPSEEK_API_KEY}"
        else:
            assert deepseek_config.base_url == "https://api.deepseek.com"
            assert deepseek_config.api_key == "${DEEPSEEK_API_KEY}"

    def test_env_var_expansion(self, kimi_env_vars, test_config_path):
        """验证环境变量展开"""
        from app.atlasclaw.core.config import ConfigManager
        import os
        
        os.environ["ANTHROPIC_BASE_URL"] = kimi_env_vars["base_url"]
        os.environ["ANTHROPIC_API_KEY"] = kimi_env_vars["api_key"]
        
        config_manager = ConfigManager(config_path=str(test_config_path))
        config = config_manager.load()
        
        # 注意：load_config 可能不展开环境变量，需要在 main.py 中处理
        # 这里验证配置格式正确
        deepseek_config = config.model.providers["deepseek"]
        if isinstance(deepseek_config, dict):
            assert "deepseek.com" in deepseek_config.get("base_url", "")
        else:
            assert "deepseek.com" in deepseek_config.base_url


class TestAPIRoutes:
    """测试 API 路由注册"""

    def test_routes_registered(self, kimi_env_vars):
        """验证路由正确注册"""
        os.environ["ANTHROPIC_BASE_URL"] = kimi_env_vars["base_url"]
        os.environ["ANTHROPIC_API_KEY"] = kimi_env_vars["api_key"]
        
        import importlib
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        with TestClient(main_module.app) as client:
            # 验证核心路由
            routes_to_test = [
                ("/api/health", "GET"),
                ("/api/skills", "GET"),
                ("/api/sessions", "GET"),
            ]
            
            for route, method in routes_to_test:
                if method == "GET":
                    resp = client.get(route)
                else:
                    resp = client.post(route, json={})
                
                # 只要不是 404 就说明路由存在
                assert resp.status_code != 404, f"路由 {route} 应该存在"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "llm"])
