# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Built-in tool registration filtering tests."""

from __future__ import annotations

from pathlib import Path

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


def _build_client(tmp_path: Path, registry: SkillRegistry) -> TestClient:
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
            user_id="builtin-tool-viewer",
            display_name="Builtin Tool Viewer",
            roles=["viewer"],
            extra={},
            auth_type="test",
        ),
        role_identifiers=["viewer"],
        permissions=permissions,
        is_admin=False,
    )
    return TestClient(app)


def test_register_builtin_tools_supports_tools_exclusive_single_name() -> None:
    registry = SkillRegistry()

    registered = register_builtin_tools(registry, tools_exclusive=["read"])

    assert "read" not in registered
    assert registry.get("read") is None
    assert registry.get("write") is None
    assert registry.get("web_search") is not None


def test_register_builtin_tools_supports_tools_exclusive_internal_runtime_tool() -> None:
    registry = SkillRegistry()

    registered = register_builtin_tools(registry, tools_exclusive=["skill_exec"])

    assert "skill_exec" not in registered
    assert registry.get("skill_exec") is None
    assert registry.get("skill_write") is not None


def test_register_builtin_tools_supports_tools_exclusive_internal_group() -> None:
    registry = SkillRegistry()

    registered = register_builtin_tools(registry, tools_exclusive=["group:catalog"])

    assert "atlasclaw_catalog_query" not in registered
    assert registry.get("atlasclaw_catalog_query") is None
    assert registry.get("skill_exec") is not None


def test_register_builtin_tools_supports_tools_exclusive_atlasclaw_group() -> None:
    registry = SkillRegistry()

    registered = register_builtin_tools(registry, tools_exclusive=["group:atlasclaw"])

    assert registered == []
    assert registry.get("read") is None
    assert registry.get("atlasclaw_catalog_query") is None
    assert registry.get("skill_exec") is None


def test_register_builtin_tools_supports_tools_exclusive_group() -> None:
    registry = SkillRegistry()

    registered = register_builtin_tools(registry, tools_exclusive=["group:fs"])

    for tool_name in ("read", "write", "edit", "delete", "exec", "process"):
        assert tool_name not in registered
        assert registry.get(tool_name) is None
    assert registry.get("web_search") is not None


def test_skills_api_does_not_show_excluded_builtin_tools(tmp_path: Path) -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry, tools_exclusive=["group:fs"])
    client = _build_client(tmp_path, registry)

    response = client.get("/api/skills?include_metadata=true")

    assert response.status_code == 200
    payload = response.json()
    skills = {item["name"]: item for item in payload["skills"]}

    for tool_name in ("read", "write", "edit", "delete", "exec", "process"):
        assert tool_name not in skills
    assert "group:fs" not in skills
    assert "group:runtime" not in skills
    assert "group:catalog" not in skills
    assert "atlasclaw_catalog_query" not in skills


def test_markdown_script_backed_tools_remain_enabled_without_builtin_high_risk_tools(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "script-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: script-skill",
                "description: script backed tool",
                "tool_name: script_tool",
                "entrypoint: run.py:handler",
                "---",
                "# body",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "run.py").write_text("print('hello')\n", encoding="utf-8")

    registry = SkillRegistry()
    register_builtin_tools(registry)
    registry.load_from_directory(str(tmp_path / "skills"), location="workspace")

    assert registry.get("script_tool") is not None


def test_builtin_high_risk_tools_are_not_registered() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    tools = {item["name"]: item for item in registry.tools_snapshot()}
    assert {"write", "edit", "delete", "exec", "process"}.isdisjoint(tools)
    assert tools["read"]["capability_class"] == "fs_read"
    assert tools["atlasclaw_catalog_query"]["coordination_only"] is True
    assert tools["atlasclaw_catalog_query"]["routing_visibility"] == "internal"


def test_builtin_does_not_register_external_artifact_skill_tools() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    tools = {item["name"]: item for item in registry.tools_snapshot()}
    assert {
        "docx_create_document",
        "excel_create_workbook",
        "pdf_create_document",
        "txt_create_document",
    }.isdisjoint(tools)
