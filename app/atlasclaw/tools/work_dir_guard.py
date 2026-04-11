# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.atlasclaw.core.security_guard import (
    ensure_user_work_dir,
    resolve_cwd_in_user_work_dir,
    resolve_path_in_user_work_dir,
)

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


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


def resolve_read_file_path(ctx: "RunContext[SkillDeps]", file_path: str) -> Path:
    """Resolve a readable path from either user work_dir or registered skill paths."""
    try:
        return resolve_file_path(ctx, file_path)
    except ValueError as error:
        resolved = _resolve_registered_skill_path(ctx, file_path)
        if resolved is not None:
            return resolved
        raise error


def resolve_cwd(ctx: "RunContext[SkillDeps]", cwd: Optional[str]) -> Path:
    deps = ctx.deps
    session_manager = getattr(deps, "session_manager", None)
    workspace_path = getattr(session_manager, "workspace_path", Path("."))
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    return resolve_cwd_in_user_work_dir(workspace_path, user_id, cwd)


def _resolve_registered_skill_path(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
) -> Optional[Path]:
    deps = ctx.deps
    extra = deps.extra if isinstance(getattr(deps, "extra", None), dict) else {}
    requested = str(file_path or "").strip()
    if not requested:
        return None

    target = Path(requested).expanduser()
    if not target.is_absolute():
        target_md_skill = extra.get("target_md_skill")
        if isinstance(target_md_skill, dict):
            anchor = str(target_md_skill.get("file_path", "") or "").strip()
            if anchor:
                target = Path(anchor).expanduser().resolve().parent / target
    try:
        resolved_target = target.resolve()
    except Exception:
        return None

    allowed_files: set[Path] = set()
    allowed_roots: set[Path] = set()
    md_skills_snapshot = extra.get("md_skills_snapshot")
    if isinstance(md_skills_snapshot, list):
        for entry in md_skills_snapshot:
            if not isinstance(entry, dict):
                continue
            raw_path = str(entry.get("file_path", "") or "").strip()
            if not raw_path:
                continue
            try:
                resolved_file = Path(raw_path).expanduser().resolve()
            except Exception:
                continue
            allowed_files.add(resolved_file)
            allowed_roots.add(resolved_file.parent)

    if resolved_target in allowed_files:
        return resolved_target
    for root in allowed_roots:
        if _is_relative_to(resolved_target, root):
            return resolved_target
    return None


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
