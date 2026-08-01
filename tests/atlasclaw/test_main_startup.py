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
from types import SimpleNamespace

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
        from app.atlasclaw.api.deps_context import get_api_context

        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        
        # 重新加载模块
        import app.atlasclaw.main as main_module
        importlib.reload(main_module)
        
        # 创建测试客户端应该成功
        with TestClient(main_module.app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"
            assert get_api_context().memory_manager is not None
            assert not hasattr(main_module, "_channel_event_runtime")
            assert not hasattr(main_module, "_ha_background_runtime")

    def test_dotenv_cannot_enable_ha_runtime(self, monkeypatch):
        """Only the process environment present before dotenv may enable HA."""
        import dotenv
        import importlib

        import app.atlasclaw.main as main_module

        monkeypatch.delenv("ATLASCLAW_ENABLE_HA", raising=False)
        monkeypatch.delenv("ATLASCLAW_HA_NODE_ID", raising=False)

        def _dotenv_with_ha_settings(*_args, **_kwargs):
            monkeypatch.setenv("ATLASCLAW_ENABLE_HA", "true")
            monkeypatch.setenv("ATLASCLAW_HA_NODE_ID", "dotenv-node")
            return True

        monkeypatch.setattr(dotenv, "load_dotenv", _dotenv_with_ha_settings)
        importlib.reload(main_module)

        settings = main_module.HaRuntimeSettings.from_environment(
            main_module._HA_PROCESS_ENVIRONMENT
        )
        assert settings.enabled is False

    def test_ha_startup_preserves_shared_workspace_and_writes_local_token_health(
        self,
        test_config_path,
        tmp_path,
        monkeypatch,
    ):
        """HA startup must validate shared content and keep node state local."""
        import importlib

        from app.atlasclaw.core.workspace import WorkspaceInitializer
        import app.atlasclaw.core.config as config_module
        import app.atlasclaw.main as main_module

        shared_workspace = tmp_path / "shared-workspace"
        assert WorkspaceInitializer(str(shared_workspace)).initialize() is True
        runtime_state = shared_workspace / "runtime_state.json"
        shared_state_before = runtime_state.read_bytes()
        node_working_directory = tmp_path / "node-a"
        node_working_directory.mkdir()

        config = json.loads(Path(test_config_path).read_text(encoding="utf-8"))
        config["workspace"] = {"path": str(shared_workspace)}
        config_path = tmp_path / "atlasclaw.ha.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
        monkeypatch.setenv("ATLASCLAW_ENABLE_HA", "true")
        monkeypatch.setenv("ATLASCLAW_HA_NODE_ID", "node-a")
        monkeypatch.chdir(node_working_directory)

        old_manager = config_module._config_manager
        config_module._config_manager = config_module.ConfigManager(config_path=str(config_path))
        try:
            importlib.reload(main_module)
            with TestClient(main_module.app) as client:
                assert client.get("/api/health").status_code == 200

            assert runtime_state.read_bytes() == shared_state_before
            assert (node_working_directory / "runtime" / "token_health.json").exists()
            assert not (shared_workspace / "token_health.json").exists()
        finally:
            config_module._config_manager = old_manager

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

    def test_unresolved_model_tokens_are_not_registered_as_fallbacks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unset environment-backed token must not become a runtime fallback."""
        from app.atlasclaw.bootstrap.startup_helpers import build_token_entries

        monkeypatch.delenv("UNSET_FALLBACK_API_KEY", raising=False)
        token_defaults = {
            "api_type": "openai",
            "priority": 0,
            "weight": 100,
            "context_window": None,
        }
        config = SimpleNamespace(
            model=SimpleNamespace(
                primary="primary",
                tokens=[
                    SimpleNamespace(
                        id="empty-fallback",
                        provider="openai",
                        model="gpt-test",
                        base_url="https://llm.example.test/v1",
                        api_key="${UNSET_FALLBACK_API_KEY}",
                        **token_defaults,
                    ),
                    SimpleNamespace(
                        id="primary",
                        provider="openai",
                        model="gpt-test",
                        base_url="https://llm.example.test/v1",
                        api_key="usable-key",
                        **token_defaults,
                    ),
                ],
            )
        )

        tokens, primary_id = build_token_entries(config)

        assert [token.token_id for token in tokens] == ["primary"]
        assert primary_id == "primary"

    def test_keyless_ollama_token_remains_available(self) -> None:
        """Keep providers whose preset explicitly permits an empty API key."""

        from app.atlasclaw.bootstrap.startup_helpers import build_token_entries

        config = SimpleNamespace(
            model=SimpleNamespace(
                primary="ollama-local",
                tokens=[
                    SimpleNamespace(
                        id="ollama-local",
                        provider="ollama",
                        model="qwen3:8b",
                        base_url="http://127.0.0.1:11434/v1",
                        api_key="",
                        api_type="openai",
                        priority=0,
                        weight=100,
                        context_window=None,
                    )
                ],
            )
        )

        tokens, primary_id = build_token_entries(config)

        assert [token.token_id for token in tokens] == ["ollama-local"]
        assert tokens[0].api_key == ""
        assert primary_id == "ollama-local"

    def test_all_unresolved_json_tokens_allow_database_token_loading(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return an empty JSON set so startup can still merge database tokens."""
        from app.atlasclaw.bootstrap.startup_helpers import build_token_entries

        monkeypatch.delenv("UNSET_ONLY_API_KEY", raising=False)
        config = SimpleNamespace(
            model=SimpleNamespace(
                primary="empty-json-token",
                tokens=[
                    SimpleNamespace(
                        id="empty-json-token",
                        provider="openai",
                        model="gpt-test",
                        base_url="https://llm.example.test/v1",
                        api_key="${UNSET_ONLY_API_KEY}",
                        api_type="openai",
                        priority=0,
                        weight=100,
                        context_window=None,
                    )
                ],
            )
        )

        tokens, primary_id = build_token_entries(config)

        assert tokens == []
        assert primary_id is None

    @pytest.mark.asyncio
    async def test_database_model_config_can_populate_an_empty_json_pool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DB-only deployment must reach Model Configuration loading."""

        from app.atlasclaw.bootstrap.startup_helpers import (
            build_token_entries,
            build_token_entries_from_model_configs,
            merge_token_entries,
        )
        from app.atlasclaw.db.orm.model_config import ModelConfigService

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = SimpleNamespace(
            model=SimpleNamespace(
                primary="main",
                tokens=[],
                providers={},
            )
        )
        database_model = SimpleNamespace(
            name="database-primary",
            provider="openai",
            model_id="database-model-id",
            base_url="https://models.example.test/v1",
            api_type="openai",
            priority=100,
            weight=100,
            context_window=64_000,
        )

        async def _list_active(_session):
            return [database_model]

        monkeypatch.setattr(ModelConfigService, "list_active", _list_active)
        monkeypatch.setattr(
            ModelConfigService,
            "get_decrypted_api_key",
            lambda _model_config: "database-api-key",
        )

        json_entries, primary_id = build_token_entries(config)
        database_entries = await build_token_entries_from_model_configs(object())
        token_entries = merge_token_entries(database_entries, json_entries)
        primary_id = primary_id or token_entries[0].token_id

        assert [entry.token_id for entry in token_entries] == ["database-primary"]
        assert primary_id == "database-primary"

    @pytest.mark.asyncio
    async def test_database_token_sources_filter_invalid_and_keep_keyless(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Apply one provider-aware usability rule to both database sources."""

        from app.atlasclaw.bootstrap.startup_helpers import (
            build_token_entries_from_db,
            build_token_entries_from_model_configs,
        )
        from app.atlasclaw.db.orm.model_config import ModelConfigService
        from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService

        valid_model_config = SimpleNamespace(
            name="database-model",
            provider="openai",
            model_id="database-model-id",
            base_url="https://models.example.test/v1",
            api_type="openai",
            priority=10,
            weight=50,
            context_window=64_000,
        )
        invalid_model_config = SimpleNamespace(
            **{
                **vars(valid_model_config),
                "name": "invalid-database-model",
            }
        )
        keyless_model_config = SimpleNamespace(
            **{
                **vars(valid_model_config),
                "name": "ollama-database-model",
                "provider": "ollama",
                "model_id": "qwen3:8b",
                "base_url": "http://127.0.0.1:11434/v1",
            }
        )
        invalid_token = SimpleNamespace(
            name="invalid-token",
            provider="openai",
            model="gpt-test",
            base_url="https://models.example.test/v1",
            priority=0,
            weight=100,
        )
        keyless_token = SimpleNamespace(
            name="ollama-token",
            provider="ollama",
            model="qwen3:8b",
            base_url="http://127.0.0.1:11434/v1",
            priority=0,
            weight=100,
        )

        async def _list_active(_session):
            return [
                valid_model_config,
                invalid_model_config,
                keyless_model_config,
            ]

        async def _list_tokens(_session, **_kwargs):
            return [invalid_token, keyless_token], 2

        monkeypatch.setattr(ModelConfigService, "list_active", _list_active)
        monkeypatch.setattr(
            ModelConfigService,
            "get_decrypted_api_key",
            lambda model_config: (
                "database-api-key"
                if model_config is valid_model_config
                else ""
            ),
        )
        monkeypatch.setattr(ModelTokenConfigService, "list_all", _list_tokens)
        monkeypatch.setattr(
            ModelTokenConfigService,
            "get_decrypted_api_key",
            lambda _token: "",
        )

        model_entries = await build_token_entries_from_model_configs(object())
        token_entries, primary_id = await build_token_entries_from_db(object())

        assert [entry.token_id for entry in model_entries] == [
            "database-model",
            "ollama-database-model",
        ]
        assert [entry.token_id for entry in token_entries] == ["ollama-token"]
        assert model_entries[1].api_key == token_entries[0].api_key == ""
        assert primary_id == "ollama-token"


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
