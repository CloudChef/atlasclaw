# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.xuanwu.core.security_guard import (
    ensure_user_work_dir,
    resolve_cwd_in_user_work_dir,
    resolve_path_in_user_work_dir,
)

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


def get_user_work_dir(ctx: "RunContext[SkillDeps]") -> Path:
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)
    workspace_path = getattr(session_manager, "workspace_path", Path("."))
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    return ensure_user_work_dir(workspace_path, user_id)


def resolve_file_path(ctx: "RunContext[SkillDeps]", file_path: str) -> Path:
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)
    workspace_path = getattr(session_manager, "workspace_path", Path("."))
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    return resolve_path_in_user_work_dir(workspace_path, user_id, file_path)


def resolve_cwd(ctx: "RunContext[SkillDeps]", cwd: Optional[str]) -> Path:
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)
    workspace_path = getattr(session_manager, "workspace_path", Path("."))
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    return resolve_cwd_in_user_work_dir(workspace_path, user_id, cwd)
