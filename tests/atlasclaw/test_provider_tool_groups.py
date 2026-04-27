# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider tool metadata and group snapshot tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry
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


def _write_standalone_md_tool_skill(base: Path) -> None:
    skill_dir = base / "vm-request"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = """---
name: vm-request
description: VM request helper workflow
group: request
tool_prepare_name: vm_request_prepare
tool_prepare_entrypoint: run.py:prepare
---
# VM request
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    run_py = (
        "async def prepare(ctx=None, **kwargs):\n"
        "    return {'ok': True, 'op': 'prepare', 'kwargs': kwargs}\n"
    )
    (skill_dir / "run.py").write_text(run_py, encoding="utf-8")


async def _dummy_provider_skill(ctx=None, **kwargs):
    return {"ok": True, "kwargs": kwargs}


def _patch_auth_cookie_names(
    monkeypatch,
    *,
    atlas_cookie_name: str = "AtlasClaw-Authenticate",
    host_cookie_name: str = "AtlasClaw-Host-Authenticate",
) -> None:
    jwt_config = SimpleNamespace(
        expanded=lambda: SimpleNamespace(cookie_name=atlas_cookie_name),
    )
    host_config = SimpleNamespace(
        expanded=lambda: SimpleNamespace(cookie_name=host_cookie_name),
    )
    monkeypatch.setattr(
        "app.atlasclaw.api.deps_context.get_config",
        lambda: SimpleNamespace(auth=SimpleNamespace(jwt=jwt_config, host_cookie=host_config)),
    )


def _build_provider_cookie_test_context(tmp_path: Path) -> APIContext:
    registry = SkillRegistry()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": ["cookie", "user_token"],
                    "user_token": "fallback-user-token",
                }
            }
        },
    )


def _build_local_user() -> UserInfo:
    return UserInfo(
        user_id="u1",
        display_name="Admin",
        raw_token="atlas-jwt",
        roles=["admin"],
        auth_type="local",
    )


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
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.test",
                    "auth_type": "user_token",
                    "user_token": "provider-token",
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

    deps = build_scoped_deps(ctx, user, "agent:main:user:u1:web:dm:peer-1:topic:thread-42")
    groups = deps.extra.get("tool_groups_snapshot", {})
    assert "group:cmp" in groups
    assert set(groups["group:cmp"]) == {"cmp_get_ticket", "cmp_list_pending"}
    assert deps.extra.get("thread_id") == "thread-42"
    assert deps.extra.get("trace_id") == "thread-42"


def test_build_scoped_deps_hides_provider_tools_without_provider_instance_when_rbac_active(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={},
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={
            "context": {
                "_user_skill_permissions": [
                    {
                        "skill_id": "smartcmp-helper",
                        "skill_name": "smartcmp-helper",
                        "authorized": True,
                        "enabled": True,
                    }
                ]
            }
        },
    )

    tool_names = {tool["name"] for tool in deps.extra["tools_snapshot"]}
    md_names = {skill["qualified_name"] for skill in deps.extra["md_skills_snapshot"]}
    assert "cmp_list_pending" not in tool_names
    assert "cmp_get_ticket" not in tool_names
    assert "smartcmp:smartcmp-helper" not in md_names
    assert deps.extra["tool_groups_snapshot"] == {}


def test_build_scoped_deps_keeps_generic_provider_coordination_tools_without_provider_instance(tmp_path) -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={},
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
    )

    tools = {tool["name"]: tool for tool in deps.extra["tools_snapshot"]}
    assert "list_provider_instances" in tools
    assert "select_provider_instance" in tools
    assert tools["list_provider_instances"]["capability_class"] == "provider:generic"
    assert tools["select_provider_instance"]["coordination_only"] is True
    assert deps.extra["tools_snapshot_authoritative"] is False


def test_build_scoped_deps_provider_filter_is_authoritative_without_rbac(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={},
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
    )

    tool_names = {tool["name"] for tool in deps.extra["tools_snapshot"]}
    assert "cmp_list_pending" not in tool_names
    assert "cmp_get_ticket" not in tool_names
    assert deps.extra["tools_snapshot_authoritative"] is True


def test_build_scoped_deps_filters_provider_source_builtin_skills_without_provider_instance(tmp_path) -> None:
    registry = SkillRegistry()
    registry.register(
        SkillMetadata(
            name="provider_builtin_request",
            description="Provider-backed executable helper",
            source="provider",
            provider_type="smartcmp",
            capability_class="provider:smartcmp",
        ),
        _dummy_provider_skill,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={},
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
    )

    skill_names = {skill["name"] for skill in deps.extra["skills_snapshot"]}
    assert "provider_builtin_request" not in skill_names


def test_build_scoped_deps_keeps_provider_tools_with_provider_instance_when_rbac_active(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.test",
                    "auth_type": "user_token",
                    "user_token": "provider-token",
                }
            }
        },
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={
            "context": {
                "_user_skill_permissions": [
                    {
                        "skill_id": "smartcmp-helper",
                        "skill_name": "smartcmp-helper",
                        "authorized": True,
                        "enabled": True,
                    }
                ]
            }
        },
    )

    tool_names = {tool["name"] for tool in deps.extra["tools_snapshot"]}
    md_names = {skill["qualified_name"] for skill in deps.extra["md_skills_snapshot"]}
    assert {"cmp_list_pending", "cmp_get_ticket"}.issubset(tool_names)
    assert "smartcmp:smartcmp-helper" in md_names
    assert set(deps.extra["tool_groups_snapshot"]["group:cmp"]) == {
        "cmp_get_ticket",
        "cmp_list_pending",
    }
    assert deps.extra["provider_auth_diagnostics"] == {}


def test_build_scoped_deps_records_user_token_auth_context_from_account_settings(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

    workspace = tmp_path / "workspace"
    user_settings_dir = workspace / "users" / "u1"
    user_settings_dir.mkdir(parents=True, exist_ok=True)
    (user_settings_dir / "user_setting.json").write_text(
        """
{
  "providers": {
    "smartcmp": {
      "cmp": {
        "configured": true,
        "config": {
          "user_token": "saved-user-token"
        }
      }
    }
  }
}
""",
        encoding="utf-8",
    )
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={
            "smartcmp": {
                "cmp": {
                    "base_url": "https://cmp.example.test",
                    "auth_type": ["cookie", "user_token"],
                }
            }
        },
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
    )

    diagnostic = deps.extra["provider_auth_diagnostics"]["smartcmp"]["cmp"]
    assert diagnostic["missing_user_token"] is False
    assert diagnostic["user_token_configured"] is True
    assert diagnostic["contact_admin"] is False
    assert "saved-user-token" not in str(diagnostic)


def test_build_scoped_deps_keeps_non_provider_skill_without_provider_instance_when_rbac_active(tmp_path) -> None:
    _write_standalone_md_tool_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={},
    )
    user = UserInfo(user_id="u1", display_name="User", raw_token="token", roles=["custom"])
    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={
            "context": {
                "_user_skill_permissions": [
                    {
                        "skill_id": "vm-request",
                        "skill_name": "vm-request",
                        "authorized": True,
                        "enabled": True,
                    }
                ]
            }
        },
    )

    tools = {tool["name"]: tool for tool in deps.extra["tools_snapshot"]}
    md_names = {skill["qualified_name"] for skill in deps.extra["md_skills_snapshot"]}
    assert "vm_request_prepare" in tools
    assert "vm-request" in md_names


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
        lambda user_id, workspace_path=None, runtime_context=None, provider_templates=None: {
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
    assert deps.extra["provider_config"]["github"]["default"]["user_token"] == "github_pat_user_123"

    registry_adapter = deps.extra["_service_provider_registry"]
    assert registry_adapter.get_instance_config("github", "default")["user_token"] == "github_pat_user_123"


def test_build_scoped_deps_filters_provider_instances_by_runtime_permissions(tmp_path) -> None:
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
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": "user_token",
                    "user_token": "cmp-user-token",
                }
            },
            "jira": {
                "prod": {
                    "base_url": "https://jira.example.com",
                    "auth_type": "user_token",
                    "user_token": "jira-user-token",
                }
            },
        },
    )

    user = UserInfo(
        user_id="u1",
        display_name="User",
        raw_token="token",
        roles=["user"],
    )

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={
            "context": {
                "_provider_permissions": [
                    {
                        "provider_type": "smartcmp",
                        "instance_name": "default",
                        "allowed": False,
                    }
                ]
            }
        },
    )

    assert "smartcmp" not in deps.extra["provider_instances"]
    assert deps.extra["available_providers"] == {"jira": ["prod"]}
    assert deps.extra["tools_snapshot_authoritative"] is True
    assert "cmp_list_pending" not in {
        tool["name"] for tool in deps.extra["tools_snapshot"]
    }
    assert "cmp_get_ticket" not in {
        tool["name"] for tool in deps.extra["tools_snapshot"]
    }
    assert all(
        (skill.get("metadata") or {}).get("provider_type") != "smartcmp"
        for skill in deps.extra["md_skills_snapshot"]
    )
    assert "group:smartcmp" not in deps.extra["tool_groups_snapshot"]
    registry_adapter = deps.extra["_service_provider_registry"]
    assert registry_adapter.get_instance_config("smartcmp", "default") is None
    assert registry_adapter.get_instance_config("jira", "prod")["base_url"] == "https://jira.example.com"


def test_build_scoped_deps_keeps_provider_tools_when_instance_is_allowed(tmp_path) -> None:
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
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": "user_token",
                    "user_token": "cmp-user-token",
                }
            },
        },
    )

    user = UserInfo(
        user_id="u1",
        display_name="User",
        raw_token="token",
        roles=["user"],
    )

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={
            "context": {
                "_provider_permissions": []
            }
        },
    )

    tool_names = {tool["name"] for tool in deps.extra["tools_snapshot"]}
    assert {"cmp_list_pending", "cmp_get_ticket"}.issubset(tool_names)
    assert "group:smartcmp" in deps.extra["tool_groups_snapshot"]
    assert deps.extra["available_providers"] == {"smartcmp": ["default"]}


def test_build_scoped_deps_keeps_provider_tools_when_visible_instance_lacks_user_auth(tmp_path) -> None:
    _write_provider_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external", provider="smartcmp")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={
            "smartcmp": {
                "cmp": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": ["cookie", "user_token"],
                }
            },
        },
    )
    user = UserInfo(
        user_id="u1",
        display_name="User",
        raw_token="token",
        roles=["user"],
    )

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        extra={"context": {"_provider_permissions": []}},
    )

    tool_names = {tool["name"] for tool in deps.extra["tools_snapshot"]}
    assert {"cmp_list_pending", "cmp_get_ticket"}.issubset(tool_names)
    assert "group:smartcmp" in deps.extra["tool_groups_snapshot"]
    assert deps.extra["provider_instances"] == {}
    assert deps.extra["available_providers"] == {}
    assert deps.extra["provider_auth_diagnostics"]["smartcmp"]["cmp"]["missing_user_token"] is True
    assert deps.extra["provider_auth_diagnostics"]["smartcmp"]["cmp"]["contact_admin"] is False


def test_build_scoped_deps_reload_markdown_skill_tools_after_skill_permission_toggle(tmp_path) -> None:
    _write_standalone_md_tool_skill(tmp_path)
    registry = SkillRegistry()
    registry.load_from_directory(str(tmp_path), location="external")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = APIContext(
        session_manager=SessionManager(str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )
    user = UserInfo(
        user_id="u1",
        display_name="User",
        raw_token="token",
        roles=["user"],
    )
    session_key = "agent:main:user:u1:web:dm:peer-1:topic:thread-42"

    denied_deps = build_scoped_deps(
        ctx,
        user,
        session_key,
        extra={
            "context": {
                "_user_skill_permissions": [
                    {
                        "skill_id": "vm-request",
                        "skill_name": "vm-request",
                        "authorized": True,
                        "enabled": False,
                    }
                ]
            }
        },
    )
    assert "vm_request_prepare" not in {
        tool["name"] for tool in denied_deps.extra["tools_snapshot"]
    }
    assert all(
        skill.get("name") != "vm-request"
        for skill in denied_deps.extra["md_skills_snapshot"]
    )
    assert "group:request" not in denied_deps.extra["tool_groups_snapshot"]

    allowed_deps = build_scoped_deps(
        ctx,
        user,
        session_key,
        extra={
            "context": {
                "_user_skill_permissions": [
                    {
                        "skill_id": "vm-request",
                        "skill_name": "vm-request",
                        "authorized": True,
                        "enabled": True,
                    }
                ]
            }
        },
    )
    assert "vm_request_prepare" in {
        tool["name"] for tool in allowed_deps.extra["tools_snapshot"]
    }
    assert any(
        skill.get("name") == "vm-request"
        for skill in allowed_deps.extra["md_skills_snapshot"]
    )
    assert allowed_deps.extra["tool_groups_snapshot"]["group:request"] == [
        "vm_request_prepare"
    ]


def test_build_scoped_deps_exposes_provider_sso_context_and_resolves_template_instances(tmp_path) -> None:
    registry = SkillRegistry()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(str(workspace))
    session_queue = SessionQueue()
    ctx = APIContext(
        session_manager=session_manager,
        session_queue=session_queue,
        skill_registry=registry,
        provider_instances={
            "generic": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": ["sso", "user_token"],
                    "user_token": "saved-user-token",
                }
            }
        },
    )

    user = UserInfo(
        user_id="u1",
        display_name="Admin",
        raw_token="atlas-jwt",
        roles=["admin"],
        extra={
            "provider_sso_available": True,
            "provider_sso_token": "oidc-access-token",
        },
    )

    deps = build_scoped_deps(ctx, user, "agent:main:user:u1:web:dm:peer-1:topic:thread-42")

    assert deps.extra["provider_sso_available"] is True
    assert deps.extra["provider_sso_token"] == "oidc-access-token"
    generic_default = deps.extra["provider_instances"]["generic"]["default"]
    assert generic_default["auth_type"] == "sso"
    assert "user_token" not in generic_default


def test_build_scoped_deps_resolves_cookie_auth_from_user_cookie_context(tmp_path) -> None:
    registry = SkillRegistry()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(str(workspace))
    session_queue = SessionQueue()
    ctx = APIContext(
        session_manager=session_manager,
        session_queue=session_queue,
        skill_registry=registry,
        provider_instances={
            "smartcmp": {
                "default": {
                    "base_url": "https://cmp.example.com",
                    "auth_type": ["cookie", "provider_token"],
                    "provider_token": "shared-provider-token",
                }
            }
        },
    )

    user = UserInfo(
        user_id="u1",
        display_name="Admin",
        raw_token="request-cookie-token",
        roles=["admin"],
        auth_type="cookie",
    )

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
    )

    assert deps.extra["provider_cookie_available"] is True
    assert deps.extra["provider_cookie_token"] == "request-cookie-token"
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "cookie"
    assert smartcmp_default["cookie"] == "request-cookie-token"
    assert "provider_token" not in smartcmp_default


def test_build_scoped_deps_resolves_cookie_auth_from_request_cookies(tmp_path, monkeypatch) -> None:
    _patch_auth_cookie_names(monkeypatch)
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={"AtlasClaw-Host-Authenticate": "browser-cookie-token"},
    )

    assert deps.extra["provider_cookie_available"] is True
    assert deps.extra["provider_cookie_token"] == "browser-cookie-token"
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "cookie"
    assert smartcmp_default["cookie"] == "browser-cookie-token"
    assert "user_token" not in smartcmp_default


def test_build_scoped_deps_excludes_default_atlas_auth_cookie(tmp_path, monkeypatch) -> None:
    _patch_auth_cookie_names(monkeypatch)
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={"AtlasClaw-Authenticate": "atlas-session-token"},
    )

    assert deps.extra["provider_cookie_available"] is False
    assert deps.extra["provider_cookie_token"] == ""
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "user_token"
    assert smartcmp_default["user_token"] == "fallback-user-token"
    assert "cookie" not in smartcmp_default


def test_build_scoped_deps_excludes_configured_atlas_auth_cookie(tmp_path, monkeypatch) -> None:
    _patch_auth_cookie_names(
        monkeypatch,
        atlas_cookie_name="Custom-Atlas-Authenticate",
        host_cookie_name="Host-Token",
    )
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={"Custom-Atlas-Authenticate": "atlas-session-token"},
    )

    assert deps.extra["provider_cookie_available"] is False
    assert deps.extra["provider_cookie_token"] == ""
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "user_token"
    assert smartcmp_default["user_token"] == "fallback-user-token"
    assert "cookie" not in smartcmp_default


def test_build_scoped_deps_uses_real_provider_cookie_with_custom_atlas_cookie(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_auth_cookie_names(
        monkeypatch,
        atlas_cookie_name="Custom-Atlas-Authenticate",
        host_cookie_name="Host-Token",
    )
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={
            "Custom-Atlas-Authenticate": "atlas-session-token",
            "Host-Token": "provider-cookie-token",
        },
    )

    assert deps.extra["provider_cookie_available"] is True
    assert deps.extra["provider_cookie_token"] == "provider-cookie-token"
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "cookie"
    assert smartcmp_default["cookie"] == "provider-cookie-token"
    assert "user_token" not in smartcmp_default


def test_build_scoped_deps_ignores_default_host_cookie_when_host_cookie_is_custom(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_auth_cookie_names(
        monkeypatch,
        atlas_cookie_name="AtlasClaw-Authenticate",
        host_cookie_name="Host-Token",
    )
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={"AtlasClaw-Host-Authenticate": "provider-cookie-token"},
    )

    assert deps.extra["provider_cookie_available"] is False
    assert deps.extra["provider_cookie_token"] == ""
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "user_token"
    assert smartcmp_default["user_token"] == "fallback-user-token"
    assert "cookie" not in smartcmp_default


def test_build_scoped_deps_ignores_unconfigured_authenticate_cookie(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_auth_cookie_names(
        monkeypatch,
        atlas_cookie_name="AtlasClaw-Authenticate",
        host_cookie_name="Host-Token",
    )
    ctx = _build_provider_cookie_test_context(tmp_path)
    user = _build_local_user()

    deps = build_scoped_deps(
        ctx,
        user,
        "agent:main:user:u1:web:dm:peer-1:topic:thread-42",
        request_cookies={"Provider-Authenticate": "provider-cookie-token"},
    )

    assert deps.extra["provider_cookie_available"] is False
    assert deps.extra["provider_cookie_token"] == ""
    smartcmp_default = deps.extra["provider_instances"]["smartcmp"]["default"]
    assert smartcmp_default["auth_type"] == "user_token"
    assert smartcmp_default["user_token"] == "fallback-user-token"
    assert "cookie" not in smartcmp_default


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
