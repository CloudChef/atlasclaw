# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
main.py 启动流程测试

测试 FastAPI 应用的 lifespan 初始化流程。
验证所有组件正确初始化：SessionManager, SkillRegistry, AgentRunner 等。
"""

import json
import os
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient


def _write_md_skill(path: Path, *, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                "# Body",
            ]
        ),
        encoding="utf-8",
    )


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
        assert config.model.primary == "test-token-1"
        assert len(config.model.tokens) == 3

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

    def test_startup_loads_all_provider_and_standalone_skills(
        self,
        tmp_path,
        monkeypatch,
    ):
        """All skills are loaded into catalog; RBAC controls visibility at runtime."""
        import importlib

        import app.atlasclaw.core.config as config_module
        import app.atlasclaw.main as main_module

        providers_root = tmp_path / "providers"
        skills_root = tmp_path / "skills"
        workspace_path = tmp_path / ".atlasclaw-selective-skills"
        config_path = tmp_path / "atlasclaw.selective-skills.json"

        _write_md_skill(
            providers_root / "SmartCMP-Provider" / "skills" / "request" / "SKILL.md",
            name="request",
            description="SmartCMP request helper",
        )
        _write_md_skill(
            providers_root / "jira" / "skills" / "jira-issue" / "SKILL.md",
            name="jira-issue",
            description="Jira issue helper",
        )
        _write_md_skill(
            skills_root / "github-1.0.0" / "SKILL.md",
            name="github",
            description="GitHub helper",
        )
        _write_md_skill(
            skills_root / "pptx" / "SKILL.md",
            name="pptx",
            description="PPTX helper",
        )

        config_path.write_text(
            json.dumps(
                {
                    "workspace": {"path": str(workspace_path.resolve())},
                    "providers_root": str(providers_root.resolve()),
                    "skills_root": str(skills_root.resolve()),
                    "service_providers": {
                        "smartcmp": {
                            "default": {
                                "base_url": "https://smartcmp.example.com",
                            }
                        }
                    },
                    "model": {
                        "primary": "test-token",
                        "tokens": [
                            {
                                "id": "test-token",
                                "provider": "openai",
                                "model": "gpt-4o-mini",
                                "base_url": "https://api.openai.com/v1",
                                "api_key": "test-key",
                                "api_type": "openai",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path.resolve()))

        old_manager = config_module._config_manager
        config_module._config_manager = config_module.ConfigManager(config_path=str(config_path.resolve()))
        try:
            importlib.reload(main_module)
            with TestClient(main_module.app) as client:
                resp = client.get("/api/health")
                assert resp.status_code == 200

                md_skills = {
                    entry["qualified_name"]: entry
                    for entry in main_module._skill_registry.md_snapshot()
                }
                loaded_names = {entry["name"] for entry in md_skills.values()}

                # Provider skills load from ALL provider dirs (hot-update safe)
                assert "smartcmp:request" in md_skills
                assert "jira:jira-issue" in md_skills
                # ALL standalone skills are loaded (no config allowlist)
                assert "github" in loaded_names
                assert "pptx" in loaded_names
        finally:
            config_module._config_manager = old_manager

    def test_startup_initializes_heartbeat_runtime_when_enabled(self, test_config_path, tmp_path, monkeypatch):
        """Heartbeat-enabled config should bootstrap runtime during lifespan startup."""
        import importlib
        from app.atlasclaw.api.deps_context import get_api_context

        base_config = json.loads(Path(test_config_path).read_text(encoding="utf-8"))
        workspace_path = tmp_path / ".atlasclaw-test"
        (workspace_path / "users" / "workspace-admin").mkdir(parents=True, exist_ok=True)
        base_config["workspace"] = {"path": str(workspace_path)}
        base_config["heartbeat"] = {
            "enabled": True,
            "runtime": {"tick_seconds": 60, "max_concurrent_jobs": 4},
            "agent_turn": {"enabled": True, "every_seconds": 300},
            "channel_connection": {"enabled": False},
        }
        config_path = tmp_path / "heartbeat.test.json"
        config_path.write_text(json.dumps(base_config, ensure_ascii=False, indent=2), encoding="utf-8")
        monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))

        import app.atlasclaw.main as main_module
        importlib.reload(main_module)

        with TestClient(main_module.app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            ctx = get_api_context()
            assert ctx.heartbeat_runtime is not None
            assert main_module._heartbeat_task is not None
            for _ in range(50):
                if ctx.heartbeat_runtime._jobs:
                    break
                time.sleep(0.02)
            owners = {job.owner_user_id for job in ctx.heartbeat_runtime._jobs.values()}
            assert "default" not in owners
            assert "workspace-admin" in owners

    @pytest.mark.asyncio
    async def test_collect_runtime_user_ids_uses_existing_user_isolation(self, tmp_path, monkeypatch):
        """Runtime user discovery should collect real isolated user ids only."""
        import importlib

        workspace_path = tmp_path / ".atlasclaw-test"
        users_dir = workspace_path / "users"
        (users_dir / "workspace-user").mkdir(parents=True, exist_ok=True)

        import app.atlasclaw.main as main_module
        importlib.reload(main_module)

        async def _fake_db_users(_: bool) -> set[str]:
            return {"admin", "default", "anonymous"}

        monkeypatch.setattr(main_module, "_list_db_runtime_user_ids", _fake_db_users)

        class _FakeChannelManager:
            def list_active_connection_descriptors(self):
                return [
                    {"user_id": "channel-user"},
                    {"user_id": "default"},
                    {"user_id": ""},
                ]

        user_ids = await main_module._collect_runtime_user_ids(
            workspace_path,
            db_initialized=True,
            channel_manager=_FakeChannelManager(),
        )

        assert user_ids == ["admin", "channel-user", "workspace-user"]

class TestConfigResolution:
    """测试配置解析"""

    def test_provider_config_resolution(self, test_config_path):
        """验证 provider 配置解析"""
        from app.atlasclaw.core.config import ConfigManager
        
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

        from app.atlasclaw.main import app

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
