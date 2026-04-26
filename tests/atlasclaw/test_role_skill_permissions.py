# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Role-facing skill permission catalog translation tests."""

from __future__ import annotations

from pathlib import Path

from app.atlasclaw.skills.permission_service import skill_permission_service
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools


def _build_registry(tmp_path: Path) -> SkillRegistry:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    async def provider_tool() -> dict[str, str]:
        return {"ok": "true"}

    registry.register(
        SkillMetadata(
            name="smartcmp_create_request",
            description="Create SmartCMP request",
            provider_type="smartcmp",
            source="provider",
            capability_class="provider:smartcmp",
        ),
        provider_tool,
    )

    provider_skill_dir = tmp_path / "providers" / "smartcmp" / "skills" / "request"
    provider_skill_dir.mkdir(parents=True)
    (provider_skill_dir / "SKILL.md").write_text(
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
    registry.load_from_directory(
        str(tmp_path / "providers" / "smartcmp" / "skills"),
        location="provider",
        provider="smartcmp",
    )
    return registry


def test_expand_role_skill_permissions_expands_groups_and_drops_provider_entries(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    permissions = {
        "skills": {
            "skill_permissions": [
                {
                    "skill_id": "group:fs",
                    "skill_name": "group:fs",
                    "description": "Filesystem",
                    "authorized": True,
                    "enabled": True,
                },
                {
                    "skill_id": "smartcmp:request",
                    "skill_name": "request",
                    "authorized": True,
                    "enabled": True,
                },
                {
                    "skill_id": "smartcmp_create_request",
                    "skill_name": "smartcmp_create_request",
                    "authorized": True,
                    "enabled": True,
                },
            ],
        },
    }

    expanded = skill_permission_service.expand_role_skill_permissions_for_storage(
        permissions,
        skill_registry=registry,
    )

    entries = expanded["skills"]["skill_permissions"]
    skill_ids = {entry["skill_id"] for entry in entries}
    assert {"read", "write", "edit", "delete"}.issubset(skill_ids)
    assert "group:fs" not in skill_ids
    assert "smartcmp:request" not in skill_ids
    assert "smartcmp_create_request" not in skill_ids


def test_collapse_role_skill_permissions_groups_tools_and_hides_provider_entries(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    permissions = {
        "skills": {
            "skill_permissions": [
                {
                    "skill_id": tool_name,
                    "skill_name": tool_name,
                    "description": f"{tool_name} tool",
                    "authorized": True,
                    "enabled": True,
                }
                for tool_name in ("read", "write", "edit", "delete")
            ]
            + [
                {
                    "skill_id": "standalone-helper",
                    "skill_name": "standalone-helper",
                    "description": "Standalone helper",
                    "authorized": True,
                    "enabled": False,
                },
                {
                    "skill_id": "smartcmp:request",
                    "skill_name": "request",
                    "authorized": True,
                    "enabled": True,
                },
                {
                    "skill_id": "smartcmp_create_request",
                    "skill_name": "smartcmp_create_request",
                    "authorized": True,
                    "enabled": True,
                },
            ],
        },
    }

    collapsed = skill_permission_service.collapse_role_skill_permissions_for_response(
        permissions,
        skill_registry=registry,
    )

    entries = collapsed["skills"]["skill_permissions"]
    by_id = {entry["skill_id"]: entry for entry in entries}
    assert "group:fs" in by_id
    assert by_id["group:fs"]["type"] == "tool_group"
    assert by_id["group:fs"]["member_skill_ids"] == ["read", "write", "edit", "delete"]
    assert by_id["group:fs"]["authorized"] is True
    assert by_id["group:fs"]["enabled"] is True
    assert by_id["group:fs"]["partial"] is False
    for hidden_id in ("read", "write", "edit", "delete", "smartcmp:request", "smartcmp_create_request"):
        assert hidden_id not in by_id
    assert by_id["standalone-helper"]["enabled"] is False


def test_collapse_role_skill_permissions_marks_only_mixed_groups_partial(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    permissions = {
        "skills": {
            "skill_permissions": [
                {
                    "skill_id": tool_name,
                    "skill_name": tool_name,
                    "authorized": False,
                    "enabled": False,
                }
                for tool_name in ("read", "write", "edit", "delete")
            ]
        },
    }

    collapsed = skill_permission_service.collapse_role_skill_permissions_for_response(
        permissions,
        skill_registry=registry,
    )

    group_row = collapsed["skills"]["skill_permissions"][0]
    assert group_row["skill_id"] == "group:fs"
    assert group_row["authorized"] is False
    assert group_row["enabled"] is False
    assert group_row["partial"] is False

    permissions["skills"]["skill_permissions"][0]["enabled"] = True
    mixed = skill_permission_service.collapse_role_skill_permissions_for_response(
        permissions,
        skill_registry=registry,
    )

    mixed_group_row = mixed["skills"]["skill_permissions"][0]
    assert mixed_group_row["skill_id"] == "group:fs"
    assert mixed_group_row["partial"] is True
