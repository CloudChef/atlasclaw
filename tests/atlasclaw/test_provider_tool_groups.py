# -*- coding: utf-8 -*-
"""Provider tool metadata and group snapshot tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.catalog import GROUP_ATLASCLAW, GROUP_WEB
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

    deps = build_scoped_deps(ctx, user, "sess-1")
    groups = deps.extra.get("tool_groups_snapshot", {})
    assert "group:cmp" in groups
    assert set(groups["group:cmp"]) == {"cmp_get_ticket", "cmp_list_pending"}


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
