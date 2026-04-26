# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.guards import AuthorizationContext, get_authorization_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.orm.role import build_default_permissions
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools


def _build_client(tmp_path, registry: SkillRegistry | None = None) -> TestClient:
    if registry is None:
        registry = SkillRegistry()
        register_builtin_tools(registry)
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.include_router(create_router())

    permissions = build_default_permissions()
    permissions["skills"]["module_permissions"]["view"] = True
    app.dependency_overrides[get_authorization_context] = lambda: AuthorizationContext(
        user=UserInfo(
            user_id="skill-metadata-viewer",
            display_name="Skill Metadata Viewer",
            roles=["viewer"],
            extra={},
            auth_type="test",
        ),
        role_identifiers=["viewer"],
        permissions=permissions,
        is_admin=False,
    )
    return TestClient(app)


def test_skills_api_include_metadata_uses_normalized_tool_snapshot(tmp_path) -> None:
    client = _build_client(tmp_path)

    response = client.get("/api/skills?include_metadata=true")

    assert response.status_code == 200
    payload = response.json()
    skills = {item["name"]: item for item in payload["skills"]}

    assert "web_search" not in skills
    assert "openmeteo_weather" not in skills

    web_group = skills["group:web"]
    assert web_group["type"] == "tool_group"
    assert web_group["group_id"] == "group:web"
    assert set(web_group["member_skill_ids"]) == {"web_search", "web_fetch", "openmeteo_weather"}
    assert web_group["source"] == "builtin"
    assert web_group["capability_class"] == "group:web"
    assert set(web_group["group_ids"]) == {"group:web", "group:atlasclaw"}


def test_skills_api_includes_standalone_markdown_and_excludes_provider_skills(tmp_path) -> None:
    standalone_dir = tmp_path / "skills" / "release-helper"
    standalone_dir.mkdir(parents=True)
    (standalone_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: release-helper",
                "description: Helps prepare release notes",
                "---",
                "# Release Helper",
            ]
        ),
        encoding="utf-8",
    )
    provider_dir = tmp_path / "providers" / "smartcmp" / "skills" / "request"
    provider_dir.mkdir(parents=True)
    (provider_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: request",
                "description: SmartCMP request helper",
                "---",
                "# Request",
            ]
        ),
        encoding="utf-8",
    )

    registry = SkillRegistry()
    register_builtin_tools(registry)
    registry.load_from_directory(str(tmp_path / "skills"), location="workspace")
    registry.load_from_directory(
        str(tmp_path / "providers" / "smartcmp" / "skills"),
        location="provider",
        provider="smartcmp",
    )
    client = _build_client(tmp_path, registry)

    response = client.get("/api/skills?include_metadata=true")

    assert response.status_code == 200
    skills = {item["name"]: item for item in response.json()["skills"]}
    assert skills["release-helper"]["type"] == "markdown"
    assert skills["release-helper"]["provider_type"] == ""
    assert "request" not in skills
