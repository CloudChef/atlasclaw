# -*- coding: utf-8 -*-
"""
Config workspace and multi-layer loading tests.

Tests for ConfigManager workspace configuration loading.
"""

import json
import pytest
from pathlib import Path

from app.xuanwu.core.config import ConfigManager
from app.xuanwu.core.config_schema import XuanwuConfig


class TestConfigWorkspaceLoading:
    """Test ConfigManager workspace configuration loading."""

    def test_load_workspace_config(self, tmp_path):
        """场景：工作区配置覆盖全局配置"""
        # Create global config
        global_config = {"model": {"primary": "global-model"}, "log_level": "info"}
        global_config_path = tmp_path / "global_config.json"
        with open(global_config_path, "w") as f:
            json.dump(global_config, f)

        # Create workspace config
        workspace_config = {"model": {"primary": "workspace-model"}}
        workspace_config_path = tmp_path / "xuanwu.json"
        with open(workspace_config_path, "w") as f:
            json.dump(workspace_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(global_config_path))
        config = config_manager.load()

        # Workspace config should override global
        assert config.model.primary == "workspace-model"

    def test_load_user_config(self, tmp_path):
        """场景：加载用户区配置
        
        用户配置存储在 user_setting.json 中，包含 channels, preferences。
        注意：providers 是系统级配置，不在用户配置中。
        支持向后兼容旧格式 atlasclaw.json。
        """
        # Create workspace config with workspace path
        workspace_config = {
            "model": {"primary": "workspace-model"}, 
            "log_level": "info",
            "workspace": {"path": str(tmp_path)}
        }
        workspace_config_path = tmp_path / "xuanwu.json"
        with open(workspace_config_path, "w") as f:
            json.dump(workspace_config, f)

        # Create user config (new format)
        user_config = {
            "channels": {"feishu": {"connections": []}},
            "providers": {},
            "preferences": {"language": "zh-CN"}
        }
        users_dir = tmp_path / "users" / "test_user"
        users_dir.mkdir(parents=True)
        user_config_path = users_dir / "user_setting.json"
        with open(user_config_path, "w") as f:
            json.dump(user_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(workspace_config_path))
        config_manager.load()

        # Load user config
        user_config_loaded = config_manager.load_user_config("test_user")
        assert user_config_loaded.get("channels", {}).get("feishu") is not None
        assert user_config_loaded.get("preferences", {}).get("language") == "zh-CN"
    
    def test_load_user_config_legacy_format(self, tmp_path):
        """场景：向后兼容旧格式 atlasclaw.json"""
        # Create workspace config
        workspace_config = {
            "model": {"primary": "workspace-model"},
            "workspace": {"path": str(tmp_path)}
        }
        workspace_config_path = tmp_path / "xuanwu.json"
        with open(workspace_config_path, "w") as f:
            json.dump(workspace_config, f)

        # Create user config (legacy format - atlasclaw.json)
        # Note: providers field in legacy format will be ignored as providers are system-level
        legacy_user_config = {
            "channels": {"slack": {"connections": []}},
            "preferences": {"theme": "dark"}
        }
        users_dir = tmp_path / "users" / "legacy_user"
        users_dir.mkdir(parents=True)
        user_config_path = users_dir / "atlasclaw.json"  # Legacy format
        with open(user_config_path, "w") as f:
            json.dump(legacy_user_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(workspace_config_path))
        config_manager.load()

        # Load user config - should convert legacy format
        user_config_loaded = config_manager.load_user_config("legacy_user")
        assert "channels" in user_config_loaded
        assert "preferences" in user_config_loaded

    def test_load_legacy_root_config_filename(self, tmp_path, monkeypatch):
        """场景：向后兼容项目根 atlasclaw.json"""
        legacy_config_path = tmp_path / "atlasclaw.json"
        with open(legacy_config_path, "w") as f:
            json.dump({"model": {"primary": "legacy-root-model"}}, f)

        monkeypatch.delenv("XUANWU_CONFIG", raising=False)
        monkeypatch.delenv("ATLASCLAW_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        config_manager = ConfigManager()
        config = config_manager.load()

        assert config.model.primary == "legacy-root-model"
        assert config_manager.resolved_config_path == legacy_config_path.resolve()

    def test_load_legacy_config_env_path(self, tmp_path, monkeypatch):
        """场景：向后兼容 ATLASCLAW_CONFIG"""
        legacy_config_path = tmp_path / "legacy-env-config.json"
        with open(legacy_config_path, "w") as f:
            json.dump({"model": {"primary": "legacy-env-model"}}, f)

        monkeypatch.delenv("XUANWU_CONFIG", raising=False)
        monkeypatch.setenv("ATLASCLAW_CONFIG", str(legacy_config_path))

        config_manager = ConfigManager()
        config = config_manager.load()

        assert config.model.primary == "legacy-env-model"
        assert config_manager.resolved_config_path == legacy_config_path.resolve()

    def test_load_legacy_env_prefix(self, tmp_path, monkeypatch):
        """场景：向后兼容 ATLASCLAW_* 环境变量前缀"""
        config_path = tmp_path / "minimal.json"
        with open(config_path, "w") as f:
            json.dump({}, f)

        monkeypatch.delenv("XUANWU_CONFIG", raising=False)
        monkeypatch.delenv("ATLASCLAW_CONFIG", raising=False)
        monkeypatch.delenv("XUANWU_AGENT_DEFAULTS__TIMEOUT_SECONDS", raising=False)
        monkeypatch.setenv("ATLASCLAW_AGENT_DEFAULTS__TIMEOUT_SECONDS", "123")

        config_manager = ConfigManager(config_path=str(config_path))
        config = config_manager.load()

        assert config.agent_defaults.timeout_seconds == 123

    def test_config_merge_priority(self, tmp_path):
        """场景：验证配置优先级（工作区 > 全局 > 默认）"""
        # Create global config with some values
        global_config = {
            "model": {"primary": "global-model", "fallbacks": []},
            "log_level": "info"
        }
        global_config_path = tmp_path / "global_config.json"
        with open(global_config_path, "w") as f:
            json.dump(global_config, f)

        # Create workspace config that overrides only primary
        workspace_config = {"model": {"primary": "workspace-model"}}
        workspace_config_path = tmp_path / "xuanwu.json"
        with open(workspace_config_path, "w") as f:
            json.dump(workspace_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(global_config_path))
        config = config_manager.load()

        # Workspace should override primary, but fallbacks should remain from global
        assert config.model.primary == "workspace-model"

    def test_missing_config_uses_defaults(self, tmp_path):
        """场景：缺失的配置项使用默认值填充"""
        # Create minimal config
        minimal_config = {"log_level": "debug"}
        config_path = tmp_path / "minimal_config.json"
        with open(config_path, "w") as f:
            json.dump(minimal_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(config_path))
        config = config_manager.load()

        # Should have default values for missing items
        assert config.log_level.value == "debug"
        assert config.workspace.path == "./.xuanwu"  # default value


class TestWorkspaceConfigSchema:
    """Test WorkspaceConfig schema."""

    def test_workspace_config_defaults(self):
        """Test: Verify WorkspaceConfig defaults"""
        from app.xuanwu.core.config_schema import WorkspaceConfig

        config = WorkspaceConfig()
        assert config.path == "./.xuanwu"

    def test_workspace_config_custom_values(self):
        """Test: Verify WorkspaceConfig custom values"""
        from app.xuanwu.core.config_schema import WorkspaceConfig

        config = WorkspaceConfig(path="/custom/path")
        assert config.path == "/custom/path"


class TestConfigManagerIntegration:
    """Integration tests for ConfigManager."""

    def test_full_config_loading_flow(self, tmp_path):
        """场景：完整配置加载流程"""
        # Create global config
        global_config = {
            "model": {"primary": "deepseek/deepseek-chat"},
            "log_level": "info"
        }
        global_config_path = tmp_path / "global_config.json"
        with open(global_config_path, "w") as f:
            json.dump(global_config, f)

        # Create workspace config
        workspace_config = {"workspace": {"path": str(tmp_path)}}
        workspace_config_path = tmp_path / "xuanwu.json"
        with open(workspace_config_path, "w") as f:
            json.dump(workspace_config, f)

        # Load config
        config_manager = ConfigManager(config_path=str(global_config_path))
        config = config_manager.load()

        # Verify merged config
        assert config.model.primary == "deepseek/deepseek-chat"
        assert config.workspace.path == str(tmp_path)
