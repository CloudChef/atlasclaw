# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""Webhook markdown-skill dispatch tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.api.service_provider_schemas import (
    clear_provider_schema_definitions,
    register_provider_schema_definition,
)
from app.atlasclaw.api.webhook_dispatch import WebhookDispatchManager
from app.atlasclaw.core.config_schema import (
    WebhookConfig,
    WebhookSystemConfig,
)
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.session.context import SessionKey
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry
from tests.atlasclaw.provider_schema_fixtures import managed_provider_definition


@pytest.fixture(autouse=True)
def _provider_schema_registry():
    clear_provider_schema_definitions()
    register_provider_schema_definition(managed_provider_definition(provider_type="smartcmp"))
    yield
    clear_provider_schema_definitions()


class _RecordingAgentRunner:
    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, session_key, user_message, deps, timeout_seconds=600, **kwargs):
        self.calls.append(
            {
                "session_key": session_key,
                "user_message": user_message,
                "deps": deps,
                "timeout_seconds": timeout_seconds,
            }
        )
        if False:
            yield None


def _write_skill_md(
    path: Path,
    *,
    name: str,
    description: str,
    extra: list[str] | None = None,
) -> None:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if extra:
        lines.extend(extra)
    lines.extend(["---", "# body"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_client(
    tmp_path: Path,
    monkeypatch,
    *,
    allowed_skills: list[str],
    provider_instances: dict | None = None,
    skill_extra: list[str] | None = None,
) -> tuple[TestClient, _RecordingAgentRunner]:
    monkeypatch.setenv("ATLASCLAW_WEBHOOK_SK_SMARTCMP_PREAPPROVAL", "secret-1")
    registry = SkillRegistry()
    _write_skill_md(
        tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
        name="preapproval-agent",
        description="smartcmp preapproval",
        extra=skill_extra,
    )
    registry.load_from_directory(
        str(tmp_path / "skills"),
        location="external",
        provider="smartcmp",
    )
    registry.register(SkillMetadata(name="jira_issue_get", description="tool"), lambda: None)

    webhook_config = WebhookConfig(
        enabled=True,
        header_name="X-AtlasClaw-SK",
        systems=[
            WebhookSystemConfig(
                system_id="smartcmp-preapproval",
                enabled=True,
                sk_env="ATLASCLAW_WEBHOOK_SK_SMARTCMP_PREAPPROVAL",
                default_agent_id="main",
                allowed_skills=allowed_skills,
            )
        ],
    )
    webhook_manager = WebhookDispatchManager(webhook_config, registry)
    webhook_manager.validate_startup()

    runner = _RecordingAgentRunner()
    service_provider_registry = None
    if provider_instances is not None:
        service_provider_registry = ServiceProviderRegistry()
        service_provider_registry._schema_definitions["smartcmp"] = (  # noqa: SLF001
            managed_provider_definition(provider_type="smartcmp")
        )
        service_provider_registry.load_instances_from_config(provider_instances)

    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
        agent_runner=runner,
        webhook_manager=webhook_manager,
        service_provider_registry=service_provider_registry,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app), runner


class TestWebhookDispatchManager:
    def test_validate_startup_accepts_direct_config_secret(self, tmp_path, monkeypatch):
        registry = SkillRegistry()
        _write_skill_md(
            tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
            name="preapproval-agent",
            description="smartcmp preapproval",
        )
        registry.load_from_directory(
            str(tmp_path / "skills"),
            location="external",
            provider="smartcmp",
        )
        monkeypatch.delenv("sk-direct-secret", raising=False)

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="sk-direct-secret",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        manager.validate_startup()
        identity = manager.authenticate("sk-direct-secret")

        assert identity is not None
        assert identity.system_id == "smartcmp-preapproval"

    def test_validate_startup_accepts_direct_sk_env_secret(
        self,
        tmp_path,
        monkeypatch,
    ):
        registry = SkillRegistry()
        _write_skill_md(
            tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
            name="preapproval-agent",
            description="smartcmp preapproval",
        )
        registry.load_from_directory(
            str(tmp_path / "skills"),
            location="external",
            provider="smartcmp",
        )
        monkeypatch.delenv("SK_AtlasClawDirect", raising=False)

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="SK_AtlasClawDirect",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        manager.validate_startup()
        assert manager.authenticate("SK_AtlasClawDirect") is not None

    def test_validate_startup_prefers_env_value_when_sk_env_name_exists(self, tmp_path, monkeypatch):
        registry = SkillRegistry()
        _write_skill_md(
            tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
            name="preapproval-agent",
            description="smartcmp preapproval",
        )
        registry.load_from_directory(
            str(tmp_path / "skills"),
            location="external",
            provider="smartcmp",
        )
        monkeypatch.setenv("SECRET", "env-secret-value")

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="SECRET",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        manager.validate_startup()
        assert manager.authenticate("env-secret-value") is not None
        assert manager.authenticate("SECRET") is None

    def test_validate_startup_requires_non_blank_sk_env(self, tmp_path):
        registry = SkillRegistry()
        _write_skill_md(
            tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
            name="preapproval-agent",
            description="smartcmp preapproval",
        )
        registry.load_from_directory(
            str(tmp_path / "skills"),
            location="external",
            provider="smartcmp",
        )

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        try:
            manager.validate_startup()
        except RuntimeError as exc:
            assert "Missing webhook secret" in str(exc)
        else:
            raise AssertionError("validate_startup should fail when sk_env is blank")

    def test_validate_startup_uses_sk_env_as_literal_when_env_missing(
        self,
        tmp_path,
        monkeypatch,
    ):
        registry = SkillRegistry()
        _write_skill_md(
            tmp_path / "skills" / "preapproval-agent" / "SKILL.md",
            name="preapproval-agent",
            description="smartcmp preapproval",
        )
        registry.load_from_directory(
            str(tmp_path / "skills"),
            location="external",
            provider="smartcmp",
        )
        monkeypatch.delenv("SECRET", raising=False)

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="SECRET",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        try:
            manager.validate_startup()
        except RuntimeError as exc:
            raise AssertionError("validate_startup should accept literal sk_env secret") from exc
        assert manager.authenticate("SECRET") is not None


class TestWebhookDispatchAPI:
    def _smartcmp_robot_provider_instances(self) -> dict:
        return {
            "smartcmp": {
                "cmp": {
                    "base_url": "https://cmp.example.com",
                    "auth_url": "https://login.example.com/platform-api/login",
                    "auth_type": "user_token",
                    "user_token": "base-user-token",
                    "cookie": "base-cookie-token",
                    "robot_auth": {
                        "preapproval_bot": {
                            "auth_type": "provider_token",
                            "provider_token": "cmp_tk_robot_secret",
                            "allowed_skills": ["smartcmp:preapproval-agent"],
                        },
                        "decomposition_bot": {
                            "auth_type": "provider_token",
                            "provider_token": "cmp_tk_decompose_secret",
                            "allowed_skills": ["smartcmp:request-decomposition-agent"],
                        },
                        "broken_bot": {
                            "auth_type": "provider_token",
                            "allowed_skills": ["smartcmp:preapproval-agent"],
                        },
                    },
                }
            }
        }

    def test_dispatch_accepts_allowed_skill(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {"approval_id": "A-10001", "agent_identity": "agent-approver"},
            },
        )

        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}
        assert len(runner.calls) == 1
        assert "smartcmp:preapproval-agent" in runner.calls[0]["user_message"]
        assert "approval_id" in runner.calls[0]["user_message"]
        assert runner.calls[0]["deps"].extra["webhook_skill"] == "smartcmp:preapproval-agent"

    def test_dispatch_carries_preselected_skill_routing_hints(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            skill_extra=[
                "use_when:",
                "  - User wants approval details",
                "avoid_when:",
                "  - User asks for same-type quantity requests",
            ],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {"approval_id": "A-10001", "agent_identity": "agent-approver"},
            },
        )

        assert resp.status_code == 202
        target_md_skill = runner.calls[0]["deps"].extra["target_md_skill"]
        assert target_md_skill["use_when"] == ["User wants approval details"]
        assert target_md_skill["avoid_when"] == ["User asks for same-type quantity requests"]

    def test_dispatch_uses_dispatch_scoped_session_keys_without_reset(
        self,
        tmp_path,
        monkeypatch,
    ):
        reset_calls: list[tuple[str, bool]] = []

        async def record_reset(
            _manager: SessionManager,
            session_key: str,
            archive: bool = True,
        ) -> None:
            reset_calls.append((session_key, archive))

        monkeypatch.setattr(SessionManager, "reset_session", record_reset)
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        for approval_id in ["A-10001", "A-10002"]:
            resp = client.post(
                "/api/webhook/dispatch",
                headers={"X-AtlasClaw-SK": "secret-1"},
                json={
                    "skill": "smartcmp:preapproval-agent",
                    "args": {"approval_id": approval_id},
                },
            )
            assert resp.status_code == 202

        assert len(runner.calls) == 2
        session_keys = [call["session_key"] for call in runner.calls]
        expected_prefix = (
            "agent:main:user:webhook-smartcmp-preapproval:"
            "webhook:dm:smartcmp-preapproval:topic:"
        )
        assert session_keys[0] != session_keys[1]
        assert all(key.startswith(expected_prefix) for key in session_keys)
        assert [call["deps"].session_key for call in runner.calls] == session_keys
        assert [SessionKey.from_string(key).thread_id for key in session_keys]
        assert reset_calls == []

    def test_dispatch_robot_profile_uses_runtime_only_provider_config(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=self._smartcmp_robot_provider_instances(),
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "request_id": "RES20260427000004",
                    "provider_instance": "cmp",
                    "robot_profile": "preapproval_bot",
                    "provider_token": "arg-token-should-not-enter-prompt",
                },
            },
        )

        assert resp.status_code == 202
        assert len(runner.calls) == 1
        call = runner.calls[0]
        assert "arg-token-should-not-enter-prompt" not in call["user_message"]
        assert "cmp_tk_robot_secret" not in call["user_message"]
        assert '"provider_token": "[REDACTED]"' in call["user_message"]
        assert '"provider_instance": "cmp"' in call["user_message"]

        deps_extra = call["deps"].extra
        assert deps_extra["provider_type"] == "smartcmp"
        assert deps_extra["provider_instance_name"] == "cmp"
        assert deps_extra["robot_profile"] == "preapproval_bot"
        assert deps_extra["webhook_args"]["provider_instance"] == "cmp"
        assert deps_extra["webhook_args"]["provider_token"] == "[REDACTED]"

        runtime_config = deps_extra["provider_config"]["smartcmp"]["cmp"]
        assert runtime_config["base_url"] == "https://cmp.example.com"
        assert runtime_config["auth_url"] == "https://login.example.com/platform-api/login"
        assert runtime_config["auth_type"] == "provider_token"
        assert runtime_config["provider_token"] == "cmp_tk_robot_secret"
        assert runtime_config["provider_type"] == "smartcmp"
        assert runtime_config["instance_name"] == "cmp"
        assert "robot_auth" not in runtime_config
        assert "user_token" not in runtime_config
        assert "cookie" not in runtime_config

    def test_dispatch_robot_profile_missing_returns_400_before_runner(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=self._smartcmp_robot_provider_instances(),
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "missing_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "Robot profile not found" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_ignores_legacy_instance_field(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=self._smartcmp_robot_provider_instances(),
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "instance": "cmp",
                    "robot_profile": "preapproval_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "webhook args.provider_instance is required" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_profiles_bucket_shape_is_not_accepted(self, tmp_path, monkeypatch):
        provider_instances = self._smartcmp_robot_provider_instances()
        robot_auth = provider_instances["smartcmp"]["cmp"]["robot_auth"]
        preapproval_profile = robot_auth.pop("preapproval_bot")
        robot_auth["profiles"] = {"preapproval_bot": preapproval_profile}
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=provider_instances,
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "preapproval_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "Robot profile not found" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_nested_auth_container_is_not_accepted(self, tmp_path, monkeypatch):
        provider_instances = self._smartcmp_robot_provider_instances()
        provider_instances["smartcmp"]["cmp"]["robot_auth"]["preapproval_bot"] = {
            "auth": {
                "auth_type": "provider_token",
                "provider_token": "cmp_tk_robot_secret",
            },
            "allowed_skills": ["smartcmp:preapproval-agent"],
        }
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=provider_instances,
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "preapproval_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "must define a single auth_type string" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_allowed_skills_string_is_not_accepted(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider_instances = self._smartcmp_robot_provider_instances()
        provider_instances["smartcmp"]["cmp"]["robot_auth"]["preapproval_bot"][
            "allowed_skills"
        ] = "smartcmp:preapproval-agent"
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=provider_instances,
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "preapproval_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "allowed_skills as a non-empty list" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_auth_type_list_is_not_accepted(self, tmp_path, monkeypatch):
        provider_instances = self._smartcmp_robot_provider_instances()
        provider_instances["smartcmp"]["cmp"]["robot_auth"]["preapproval_bot"]["auth_type"] = [
            "provider_token"
        ]
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=provider_instances,
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "preapproval_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "must define a single auth_type string" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_disallowed_skill_returns_403(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=self._smartcmp_robot_provider_instances(),
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "decomposition_bot",
                },
            },
        )

        assert resp.status_code == 403
        assert "is not allowed to invoke" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_robot_profile_missing_credential_returns_400(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
            provider_instances=self._smartcmp_robot_provider_instances(),
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={
                "skill": "smartcmp:preapproval-agent",
                "args": {
                    "provider_instance": "cmp",
                    "robot_profile": "broken_bot",
                },
            },
        )

        assert resp.status_code == 400
        assert "missing required auth fields" in resp.json()["detail"]
        assert runner.calls == []

    def test_dispatch_rejects_invalid_secret(self, tmp_path, monkeypatch):
        client, _runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "bad-secret"},
            json={"skill": "smartcmp:preapproval-agent", "args": {}},
        )

        assert resp.status_code == 401

    def test_dispatch_rejects_unlisted_skill(self, tmp_path, monkeypatch):
        client, _runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={"skill": "smartcmp:request", "args": {}},
        )

        assert resp.status_code == 403

    def test_dispatch_rejects_executable_tool_name(self, tmp_path, monkeypatch):
        client, _runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-AtlasClaw-SK": "secret-1"},
            json={"skill": "jira_issue_get", "args": {}},
        )

        assert resp.status_code == 400
