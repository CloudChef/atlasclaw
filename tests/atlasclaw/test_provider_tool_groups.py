# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Provider tool metadata and group snapshot tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.catalog import GROUP_ATLASCLAW, GROUP_CATALOG, GROUP_WEB
from app.atlasclaw.tools.registration import register_builtin_tools


def _write_provider_skill(base: Path) -> None:
    skill_dir = base / "smartcmp-helper"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = """---
name: smartcmp-helper
description: SmartCMP helper tools
provider_type: smartcmp
group: cmp
tool_list_name: cmp_list_pending
tool_list_entrypoint: run.py:list_pending
tool_get_name: cmp_get_ticket
tool_get_entrypoint: run.py:get_ticket
tool_get_priority: 180
---
# SmartCMP
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    run_py = (
        "async def list_pending(ctx=None, **kwargs):\n"
        "    return {'ok': True, 'op': 'list_pending', 'kwargs': kwargs}\n\n"
        "async def get_ticket(ctx=None, **kwargs):\n"
        "    return {'ok': True, 'op': 'get_ticket', 'kwargs': kwargs}\n"
    )
    (skill_dir / "run.py").write_text(run_py, encoding="utf-8")


def test_registry_tools_snapshot_contains_provider_source_and_group_ids(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    tools = {item["name"]: item for item in registry.tools_snapshot()}
    assert "cmp_list_pending" in tools
    assert "cmp_get_ticket" in tools

    for tool_name in ("cmp_list_pending", "cmp_get_ticket"):
        tool = tools[tool_name]
        assert tool["source"] == "provider"
        assert tool["provider_type"] == "smartcmp"
        assert "group:cmp" in tool["group_ids"]
        assert "group:smartcmp" in tool["group_ids"]
        assert tool["capability_class"] == "provider:smartcmp"

    assert tools["cmp_get_ticket"]["priority"] == 180


def test_registry_tool_groups_snapshot_merges_provider_group(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    groups = registry.tool_groups_snapshot()
    assert "group:cmp" in groups
    assert set(groups["group:cmp"]) == {"cmp_get_ticket", "cmp_list_pending"}
    assert "group:smartcmp" in groups


def test_build_scoped_deps_exposes_tool_group_snapshot(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(str(workspace))
    session_queue = SessionQueue()
    ctx = APIContext(
        session_manager=session_manager,
        session_queue=session_queue,
        skill_registry=registry,
    )

    user = UserInfo(
        user_id="u1",
        display_name="Admin",
        raw_token="token",
        roles=["admin"],
    )

    deps = build_scoped_deps(ctx, user, "agent:main:user:u1:web:dm:peer-1:topic:thread-42")
    groups = deps.extra.get("tool_groups_snapshot", {})
    assert "group:cmp" in groups
    assert set(groups["group:cmp"]) == {"cmp_get_ticket", "cmp_list_pending"}
    assert deps.extra.get("thread_id") == "thread-42"
    assert deps.extra.get("trace_id") == "thread-42"


def test_build_scoped_deps_merges_user_provider_instances_over_template_config(tmp_path, monkeypatch) -> None:
    registry = SkillRegistry()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(str(workspace))
    session_queue = SessionQueue()
    ctx = APIContext(
        session_manager=session_manager,
        session_queue=session_queue,
        skill_registry=registry,
        available_providers={"github": ["default"]},
        provider_instances={
            "github": {
                "default": {
                    "base_url": "https://api.github.com",
                    "auth_type": "user_token",
                }
            }
        },
    )

    user = UserInfo(
        user_id="u1",
        display_name="Admin",
        raw_token="token",
        roles=["admin"],
    )

    monkeypatch.setattr(
        "app.atlasclaw.api.deps_context.build_user_provider_instances",
        lambda user_id, workspace_path=None: {
            "github": {
                "default": {
                    "provider_type": "github",
                    "instance_name": "default",
                    "base_url": "https://api.github.com",
                    "auth_type": "user_token",
                    "user_token": "github_pat_user_123",
                }
            }
        },
    )

    deps = build_scoped_deps(ctx, user, "agent:main:user:u1:web:dm:peer-1:topic:thread-42")

    github_default = deps.extra["provider_instances"]["github"]["default"]
    assert github_default["user_token"] == "github_pat_user_123"
    assert github_default["base_url"] == "https://api.github.com"
    assert deps.extra["available_providers"]["github"] == ["default"]

    registry_adapter = deps.extra["_service_provider_registry"]
    assert registry_adapter.get_instance_config("github", "default")["user_token"] == "github_pat_user_123"


def test_register_builtin_tools_exposes_explicit_runtime_metadata() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    tools = {item["name"]: item for item in registry.tools_snapshot()}

    assert tools["web_search"]["source"] == "builtin"
    assert tools["web_search"]["capability_class"] == "web_search"
    assert set(tools["web_search"]["group_ids"]) == {GROUP_WEB, GROUP_ATLASCLAW}

    assert tools["openmeteo_weather"]["source"] == "builtin"
    assert tools["openmeteo_weather"]["capability_class"] == "weather"
    assert set(tools["openmeteo_weather"]["group_ids"]) == {GROUP_WEB, GROUP_ATLASCLAW}

    assert tools["atlasclaw_catalog_query"]["source"] == "builtin"
    assert tools["atlasclaw_catalog_query"]["capability_class"] == "atlasclaw_catalog"
    assert tools["atlasclaw_catalog_query"]["result_mode"] == "tool_only_ok"
    assert set(tools["atlasclaw_catalog_query"]["group_ids"]) == {
        GROUP_CATALOG,
        GROUP_ATLASCLAW,
    }
    assert tools["delete"]["source"] == "builtin"
    assert set(tools["delete"]["group_ids"]) == {"group:fs", GROUP_ATLASCLAW}
