# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.api.agent_capabilities import (
    build_agent_capabilities,
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
