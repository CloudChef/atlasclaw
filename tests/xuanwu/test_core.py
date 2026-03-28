# -*- coding: utf-8 -*-
"""
核心模块单元测试

测试 SkillDeps、ConfigManager、ConfigSchema 等核心组件。
"""

import os
import tempfile
import json
from pathlib import Path

import pytest

from app.xuanwu.core.deps import SkillDeps
from app.xuanwu.core.config import ConfigManager
from app.xuanwu.core.config_schema import (
    XuanwuConfig,
    ModelConfig,
    AgentDefaultsConfig,
    ResetConfig,
)


class TestSkillDeps:
    """SkillDeps 测试类"""
    
    def test_create_skill_deps(self):
        """测试创建 SkillDeps 实例"""
        deps = SkillDeps(
            user_token="test-token",
            peer_id="user-123",
            session_key="agent:main:api:dm:user-123"
        )
        
        assert deps.user_token == "test-token"
        assert deps.peer_id == "user-123"
        assert deps.session_key == "agent:main:api:dm:user-123"
        assert deps.abort_signal is not None
        assert not deps.abort_signal.is_set()
        
    def test_skill_deps_abort_signal(self):
        """测试 abort_signal 功能"""
        deps = SkillDeps()
        
        assert not deps.is_aborted()
        deps.abort()
        assert deps.is_aborted()
        deps.reset_abort()
        assert not deps.is_aborted()
        
    def test_skill_deps_extra_dict(self):
        """测试 extra 字典"""
        deps = SkillDeps()
        
        deps.extra["custom_key"] = "custom_value"
        assert deps.extra["custom_key"] == "custom_value"
        
    def test_skill_deps_isolation(self):
        """测试多实例隔离"""
        deps1 = SkillDeps(user_token="token1")
        deps2 = SkillDeps(user_token="token2")
        
        assert deps1.user_token != deps2.user_token
        assert deps1.abort_signal is not deps2.abort_signal
        assert deps1.extra is not deps2.extra


class TestConfigSchema:
    """配置 Schema 测试类"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = XuanwuConfig()
        
        assert config.log_level is not None
        assert config.model is not None
        assert config.agent_defaults is not None
        assert config.reset is not None
        
    def test_model_config(self):
        """测试模型配置"""
        model = ModelConfig(
            primary="gpt-4o",
            fallbacks=["gpt-4o-mini", "claude-3-sonnet"]
        )
        
        assert model.primary == "gpt-4o"
        assert len(model.fallbacks) == 2
        
    def test_agent_defaults_config(self):
        """测试 Agent 默认配置"""
        agent = AgentDefaultsConfig(
            timeout_seconds=300,
            max_concurrent=8
        )
        
        assert agent.timeout_seconds == 300
        assert agent.max_concurrent == 8
        
    def test_reset_config(self):
        """测试重置配置"""
        reset = ResetConfig(
            mode="daily",
            daily_hour=6
        )
        
        assert reset.daily_hour == 6


class TestConfigManager:
    """ConfigManager 测试类"""
    
    @pytest.fixture
    def temp_config_dir(self):
        """创建临时配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
            
    def test_create_config_manager(self, temp_config_dir):
        """测试创建 ConfigManager"""
        manager = ConfigManager(config_path=str(temp_config_dir / "config.json"))
        assert manager is not None
        
    def test_get_config(self, temp_config_dir):
        """测试获取配置"""
        manager = ConfigManager(config_path=str(temp_config_dir / "config.json"))
        config = manager.config
        
        assert config is not None
        assert isinstance(config, XuanwuConfig)
        
    def test_set_config(self, temp_config_dir):
        """测试设置配置覆盖"""
        manager = ConfigManager(config_path=str(temp_config_dir / "config.json"))
        
        manager.set("model.primary", "gpt-4-turbo")
        config = manager.config
        
        assert config.model.primary == "gpt-4-turbo"
        
    def test_get_config_value(self, temp_config_dir):
        """测试获取配置值"""
        manager = ConfigManager(config_path=str(temp_config_dir / "config.json"))
        
        timeout = manager.get("agent_defaults.timeout_seconds")
        assert timeout == 600  # 默认值

    def test_loads_dotenv_from_config_directory(self, temp_config_dir, monkeypatch):
        """配置文件所在目录的 .env 会被自动加载"""
        config_path = temp_config_dir / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "model": {
                        "primary": "deepseek/test",
                        "providers": {
                            "deepseek": {
                                "base_url": "${FROM_DOTENV}",
                                "api_key": "dummy",
                                "api_type": "openai",
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        (temp_config_dir / ".env").write_text("FROM_DOTENV=https://dotenv.example\n", encoding="utf-8")
        monkeypatch.delenv("FROM_DOTENV", raising=False)

        manager = ConfigManager(config_path=str(config_path))
        manager.load()

        assert os.environ.get("FROM_DOTENV") == "https://dotenv.example"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
