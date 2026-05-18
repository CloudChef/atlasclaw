# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.api.agent_capabilities import (
    build_agent_capabilities,
    resolve_auto_selected_capability,
    resolve_selected_capability,
)
from app.atlasclaw.agent.selected_capability import (
    get_selected_capability_from_extra,
    selected_capability_provider_instance_ref,
    selected_capability_targets,
)
from app.atlasclaw.api.deps_context import APIContext
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import MdSkillEntry, SkillMetadata, SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools


def _handler():
    return "ok"


def _build_context(tmp_path) -> APIContext:
    registry = SkillRegistry()
    registry._md_skills["smartcmp:linux-vm-request"] = MdSkillEntry(
        name="linux-vm-request",
        description="Request a Linux VM through a provider instance.",
        file_path=str(tmp_path / "smartcmp" / "linux-vm-request" / "SKILL.md"),
        provider="smartcmp",
        qualified_name="smartcmp:linux-vm-request",
        location="workspace",
        metadata={"provider_type": "smartcmp"},
    )
    registry._md_skill_tools["smartcmp:linux-vm-request"] = {"smartcmp_linux_vm_request"}
    registry.register(
        SkillMetadata(
            name="no-provider-vm-request",
            description="Request a dry-run VM without a provider.",
            source="md_skill",
        ),
        _handler,
    )
    return APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )


def _build_markdown_vault_context(tmp_path) -> APIContext:
    registry = SkillRegistry()
    registry._md_skills["markdown-vault:markdown-vault-query"] = MdSkillEntry(
        name="markdown-vault-query",
        description="Search and retrieve configured Markdown vault knowledge.",
        file_path=str(tmp_path / "markdown-vault" / "markdown-vault-query" / "SKILL.md"),
        provider="markdown-vault",
        qualified_name="markdown-vault:markdown-vault-query",
        location="provider",
        metadata={
            "provider_type": "markdown-vault",
            "auto_select": True,
            "auto_select_triggers": [
                "knowledge base",
                "wiki",
            ],
        },
    )
    registry._md_skill_tools["markdown-vault:markdown-vault-query"] = {
        "markdown_vault_search",
        "markdown_vault_get",
    }
    return APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )


def _authz(
    *,
    skill_view: bool = False,
    provider_allowed: bool = True,
    provider_skill_enabled: bool = True,
    standalone_skill_enabled: bool = True,
) -> AuthorizationContext:
    skill_permissions = [
        {
            "skill_id": "smartcmp:linux-vm-request",
            "skill_name": "linux-vm-request",
            "authorized": True,
            "enabled": provider_skill_enabled,
        },
        {
            "skill_id": "no-provider-vm-request",
            "skill_name": "no-provider-vm-request",
            "authorized": True,
            "enabled": standalone_skill_enabled,
        },
    ]
    return AuthorizationContext(
        user=UserInfo(user_id="user"),
        permissions={
            "skills": {
                "module_permissions": {"view": skill_view},
                "skill_permissions": skill_permissions,
            },
            "providers": {
                "provider_permissions": [
                    {
                        "provider_type": "smartcmp",
                        "instance_name": "default",
                        "allowed": provider_allowed,
                    }
                ],
            },
        },
    )


def test_agent_capabilities_include_provider_skill_command_and_direct_skill(tmp_path):
    ctx = _build_context(tmp_path)

    payload = build_agent_capabilities(
        ctx=ctx,
        authz=_authz(),
        provider_instances={"smartcmp": {"default": {"base_url": "https://example.test"}}},
    )

    commands = {item["command"]: item for item in payload["capabilities"]}
    assert "/default.linux-vm-request" in commands
    assert commands["/default.linux-vm-request"]["kind"] == "provider_skill"
    assert commands["/default.linux-vm-request"]["provider_type"] == "smartcmp"
    assert commands["/default.linux-vm-request"]["instance_name"] == "default"
    assert commands["/default.linux-vm-request"]["target_skill_names"] == [
        "smartcmp:linux-vm-request",
        "linux-vm-request",
    ]
    assert "/no-provider-vm-request" in commands
    assert commands["/no-provider-vm-request"]["kind"] == "skill"


def test_agent_capabilities_hide_denied_provider_instance(tmp_path):
    ctx = _build_context(tmp_path)

    payload = build_agent_capabilities(
        ctx=ctx,
        authz=_authz(provider_allowed=False),
        provider_instances={"smartcmp": {"default": {"base_url": "https://example.test"}}},
    )

    commands = {item["command"] for item in payload["capabilities"]}
    assert "/default.linux-vm-request" not in commands
    assert "/no-provider-vm-request" in commands


def test_agent_capabilities_hide_internal_catalog_and_show_authorized_artifact_tools(tmp_path):
    registry = SkillRegistry()
    register_builtin_tools(registry)
    registry.register(
        SkillMetadata(
            name="txt_create_document",
            description="Create a TXT artifact from explicit content.",
            source="md_skill",
            capability_class="artifact:txt",
            group_ids=["group:txt"],
        ),
        _handler,
    )
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )
    authz = AuthorizationContext(
        user=UserInfo(user_id="user"),
        permissions={
            "skills": {
                "module_permissions": {"view": True},
                "skill_permissions": [
                    {
                        "skill_id": "txt_create_document",
                        "skill_name": "txt_create_document",
                        "authorized": True,
                        "enabled": True,
                    }
                ],
            },
            "providers": {"provider_permissions": []},
        },
    )

    payload = build_agent_capabilities(ctx=ctx, authz=authz, provider_instances={})

    commands = {item["command"]: item for item in payload["capabilities"]}
    assert "/txt_create_document" in commands
    assert commands["/txt_create_document"]["target_tool_names"] == ["txt_create_document"]
    assert "/atlasclaw_catalog_query" not in commands


def test_resolve_selected_capability_rejects_disabled_standalone_skill(tmp_path):
    ctx = _build_context(tmp_path)
    selected = {
        "kind": "skill",
        "command": "/no-provider-vm-request",
        "qualified_skill_name": "no-provider-vm-request",
    }

    resolved = resolve_selected_capability(
        ctx=ctx,
        selected=selected,
        authz=_authz(standalone_skill_enabled=False),
        provider_instances={"smartcmp": {"default": {"base_url": "https://example.test"}}},
    )

    assert resolved is None


def test_resolve_selected_provider_capability_uses_provider_permission(tmp_path):
    ctx = _build_context(tmp_path)
    selected = {
        "kind": "provider_skill",
        "command": "/default.linux-vm-request",
        "provider_type": "smartcmp",
        "instance_name": "default",
        "qualified_skill_name": "smartcmp:linux-vm-request",
    }

    resolved = resolve_selected_capability(
        ctx=ctx,
        selected=selected,
        authz=_authz(provider_skill_enabled=False),
        provider_instances={"smartcmp": {"default": {"base_url": "https://example.test"}}},
    )

    assert resolved is not None
    assert resolved["provider_type"] == "smartcmp"

    resolved = resolve_selected_capability(
        ctx=ctx,
        selected=selected,
        authz=_authz(provider_allowed=False, provider_skill_enabled=False),
        provider_instances={"smartcmp": {"default": {"base_url": "https://example.test"}}},
    )

    assert resolved is None


def test_scoped_deps_only_reads_server_validated_selected_capability():
    unvalidated = {"id": "client-supplied"}
    validated = {"id": "server-validated"}

    assert get_selected_capability_from_extra({"selected_capability": unvalidated}) is None
    assert (
        get_selected_capability_from_extra(
            {"context": {"selected_capability": unvalidated}}
        )
        is None
    )
    assert get_selected_capability_from_extra({"_selected_capability": validated}) == validated
    assert (
        get_selected_capability_from_extra(
            {"context": {"_selected_capability": validated}}
        )
        == validated
    )


def test_selected_capability_targets_normalize_for_reusable_permission_checks():
    selected = {
        "provider_type": "SmartCMP",
        "instance_name": "default",
        "qualified_skill_name": "smartcmp:linux-vm-request",
        "skill_name": "linux-vm-request",
        "target_skill_names": [
            "smartcmp:linux-vm-request",
            "Linux-VM-Request",
            "linux-vm-request",
        ],
        "target_tool_names": ["request_vm", "REQUEST_VM", ""],
        "target_group_ids": ["group:smartcmp", "GROUP:SMARTCMP"],
    }

    targets = selected_capability_targets(selected)

    assert targets.provider_types == ["SmartCMP"]
    assert targets.skill_names == ["smartcmp:linux-vm-request", "Linux-VM-Request"]
    assert targets.tool_names == ["request_vm"]
    assert targets.group_ids == ["group:smartcmp"]
    assert targets.has_any() is True
    assert selected_capability_provider_instance_ref(selected) == ("SmartCMP", "default")


def test_auto_selects_unique_provider_capability_from_declared_trigger(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="Based on the knowledge base, what are the VM request steps",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is not None
    assert selected["provider_type"] == "markdown-vault"
    assert selected["instance_name"] == "default"
    assert set(selected["target_tool_names"]) == {
        "markdown_vault_search",
        "markdown_vault_get",
    }


def test_auto_select_ignores_question_without_declared_trigger(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="How do I request a VM?",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is None


def test_auto_select_does_not_infer_cross_language_trigger_in_api_router(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="\u57fa\u4e8e\u77e5\u8bc6\u5e93\uff0c"
        "\u7533\u8bf7\u865a\u62df\u673a\u9700\u8981\u54ea\u4e9b\u6b65\u9aa4\uff1f",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is None


def test_auto_select_trigger_uses_word_boundaries_for_short_ascii_terms(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="What does the wiki say about release approvals?",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is not None

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="What is Wikipedia?",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is None


def test_auto_select_markdown_vault_ignores_ordinary_chat(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="你好",
        provider_instances={
            "markdown-vault": {
                "default": {"vault_path": str(tmp_path / "vault")},
            }
        },
    )

    assert selected is None


def test_auto_select_markdown_vault_does_not_guess_between_instances(tmp_path):
    ctx = _build_markdown_vault_context(tmp_path)

    selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="Based on the knowledge base, what are the VM request steps",
        provider_instances={
            "markdown-vault": {
                "team-a": {"vault_path": str(tmp_path / "a")},
                "team-b": {"vault_path": str(tmp_path / "b")},
            }
        },
    )

    assert selected is None
