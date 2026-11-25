# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for file-backed RBAC defaults."""

from __future__ import annotations

import yaml

import pytest

from app.atlasclaw.core.workspace import WorkspaceInitializer
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.role import RoleService


def test_workspace_initializer_creates_default_rbac_files(tmp_path):
    workspace = tmp_path / "workspace"

    assert WorkspaceInitializer(str(workspace)).initialize() is True

    rbac_dir = workspace / "rbac"
    assert (rbac_dir / "admin.yaml").is_file()
    assert (rbac_dir / "user.yaml").is_file()
    assert (rbac_dir / "viewer.yaml").is_file()

    user_role = yaml.safe_load((rbac_dir / "user.yaml").read_text(encoding="utf-8"))
    assert user_role["identifier"] == "user"
    assert user_role["permissions"]["channels"]["view"] is True
    assert user_role["permissions"]["channels"]["create"] is True
    assert user_role["permissions"]["providers"]["module_permissions"]["manage_permissions"] is True


@pytest.mark.asyncio
async def test_builtin_roles_are_loaded_from_workspace_rbac_yaml(tmp_path):
    workspace = tmp_path / "workspace"
    WorkspaceInitializer(str(workspace)).initialize()

    user_role_path = workspace / "rbac" / "user.yaml"
    user_role = yaml.safe_load(user_role_path.read_text(encoding="utf-8"))
    user_role["permissions"]["channels"]["delete"] = False
    user_role["description"] = "Customized user role"
    user_role_path.write_text(yaml.safe_dump(user_role, allow_unicode=True, sort_keys=False), encoding="utf-8")

    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "rbac-test.db"))
    )
    await manager.create_tables()
    try:
        async with manager.get_session() as session:
            await RoleService.ensure_builtin_roles(session, rbac_dir=workspace / "rbac")
            user_role_model = await RoleService.get_by_identifier(session, "user")

        assert user_role_model is not None
        assert user_role_model.description == "Customized user role"
        assert user_role_model.permissions["channels"]["view"] is True
        assert user_role_model.permissions["channels"]["delete"] is False
    finally:
        await manager.close()
