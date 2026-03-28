# -*- coding: utf-8 -*-
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.xuanwu.api.routes import APIContext, create_router, set_api_context
from app.xuanwu.api.webhook_dispatch import WebhookDispatchManager
from app.xuanwu.core.config_schema import (
    WebhookConfig,
    WebhookSystemConfig,
)
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.queue import SessionQueue
from app.xuanwu.skills.registry import SkillMetadata, SkillRegistry


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


def _write_skill_md(path: Path, *, name: str, description: str, extra: list[str] | None = None) -> None:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if extra:
        lines.extend(extra)
    lines.extend(["---", "# body"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_client(tmp_path: Path, monkeypatch, *, allowed_skills: list[str]) -> tuple[TestClient, _RecordingAgentRunner]:
    monkeypatch.setenv("XUANWU_WEBHOOK_SK_SMARTCMP_PREAPPROVAL", "secret-1")
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
    registry.register(SkillMetadata(name="jira_issue_get", description="tool"), lambda: None)

    webhook_config = WebhookConfig(
        enabled=True,
        header_name="X-Xuanwu-SK",
        systems=[
            WebhookSystemConfig(
                system_id="smartcmp-preapproval",
                enabled=True,
                sk_env="XUANWU_WEBHOOK_SK_SMARTCMP_PREAPPROVAL",
                default_agent_id="main",
                allowed_skills=allowed_skills,
            )
        ],
    )
    webhook_manager = WebhookDispatchManager(webhook_config, registry)
    webhook_manager.validate_startup()

    runner = _RecordingAgentRunner()
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
        agent_runner=runner,
        webhook_manager=webhook_manager,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())
    return TestClient(app), runner


class TestWebhookDispatchManager:
    def test_validate_startup_requires_env_secret(self, tmp_path, monkeypatch):
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
        monkeypatch.delenv("XUANWU_WEBHOOK_SK_SMARTCMP_PREAPPROVAL", raising=False)

        manager = WebhookDispatchManager(
            WebhookConfig(
                enabled=True,
                systems=[
                    WebhookSystemConfig(
                        system_id="smartcmp-preapproval",
                        sk_env="XUANWU_WEBHOOK_SK_SMARTCMP_PREAPPROVAL",
                        allowed_skills=["smartcmp:preapproval-agent"],
                    )
                ],
            ),
            registry,
        )

        try:
            manager.validate_startup()
        except RuntimeError as exc:
            assert "XUANWU_WEBHOOK_SK_SMARTCMP_PREAPPROVAL" in str(exc)
        else:
            raise AssertionError("validate_startup should fail when the webhook secret is missing")


class TestWebhookDispatchAPI:
    def test_dispatch_accepts_allowed_skill(self, tmp_path, monkeypatch):
        client, runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-Xuanwu-SK": "secret-1"},
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

    def test_dispatch_rejects_invalid_secret(self, tmp_path, monkeypatch):
        client, _runner = _build_client(
            tmp_path,
            monkeypatch,
            allowed_skills=["smartcmp:preapproval-agent"],
        )

        resp = client.post(
            "/api/webhook/dispatch",
            headers={"X-Xuanwu-SK": "bad-secret"},
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
            headers={"X-Xuanwu-SK": "secret-1"},
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
            headers={"X-Xuanwu-SK": "secret-1"},
            json={"skill": "jira_issue_get", "args": {}},
        )

        assert resp.status_code == 400
