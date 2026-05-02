# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Internal tools exposed only while executing an authorized markdown skill."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from urllib.parse import quote

from app.atlasclaw.core.security_guard import resolve_path_in_user_work_dir
from app.atlasclaw.core.workspace_downloads import is_safe_workspace_relative_path
from app.atlasclaw.tools.base import ToolResult
from app.atlasclaw.tools.truncation import truncate_output
from app.atlasclaw.tools.work_dir_guard import get_user_work_dir, resolve_file_path

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


_HOME_RELATIVE_PATH_RE = re.compile(r"(?<![\w.-])~(?:/|$)")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![:\w])/(?:[^\s'\"`<>|\\]+)")


def _safe_skill_id(value: str) -> str:
    """Return a filesystem-safe runtime directory suffix for a selected skill."""
    raw = str(value or "").strip() or "skill"
    return f"skill-{quote(raw, safe='-_.')}"


def _selected_skill(ctx: "RunContext[SkillDeps]") -> dict:
    """Return the selected skill or fail when runtime tools are not active."""
    deps = getattr(ctx, "deps", None)
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict) or not extra.get("standard_skill_runtime_enabled"):
        raise ValueError("standard skill runtime is not active for this turn")
    target = extra.get("target_md_skill")
    if not isinstance(target, dict):
        raise ValueError("standard skill runtime requires a selected skill")
    return target


def _runtime_owner_key(ctx: "RunContext[SkillDeps]") -> str:
    """Build the isolation key for long-lived processes started by one skill turn."""
    target = _selected_skill(ctx)
    deps = getattr(ctx, "deps", None)
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    session_key = getattr(deps, "session_key", "") or ""
    skill_id = str(target.get("qualified_name") or target.get("name") or "skill")
    return f"{user_id}\n{session_key}\n{skill_id}"


def _skill_dir(ctx: "RunContext[SkillDeps]") -> Path:
    """Resolve the selected skill installation directory from its SKILL.md path."""
    target = _selected_skill(ctx)
    file_path = str(target.get("file_path", "") or "").strip()
    if not file_path:
        raise ValueError("selected skill has no file path")
    return Path(file_path).expanduser().resolve().parent


def _runtime_dirs(ctx: "RunContext[SkillDeps]") -> dict[str, Path]:
    """Create per-user, per-skill runtime directories under the user's work_dir."""
    target = _selected_skill(ctx)
    skill_id = _safe_skill_id(
        str(target.get("qualified_name") or target.get("name") or "skill")
    )
    root = get_user_work_dir(ctx) / ".atlasclaw" / "skills" / skill_id
    paths = {
        "root": root,
        "home": root / "home",
        "tmp": root / "tmp",
        "config": root / "config",
        "cache": root / "cache",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _runtime_env(ctx: "RunContext[SkillDeps]") -> dict[str, str]:
    """Build the minimal environment exposed to selected-skill commands.

    The process keeps only basic locale/PATH settings from the server process.
    All writable home/config/cache/temp locations are scoped to the user's
    work_dir so standard skills cannot spill state into the service account.
    """
    dirs = _runtime_dirs(ctx)
    env = {
        key: value
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
        if (value := os.environ.get(key))
    }
    env.update({
        "ATLASCLAW_WORK_DIR": str(get_user_work_dir(ctx)),
        "ATLASCLAW_SKILL_DIR": str(_skill_dir(ctx)),
        "HOME": str(dirs["home"]),
        "TMPDIR": str(dirs["tmp"]),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return env


def _expand_runtime_arg(value: str, env: dict[str, str]) -> str:
    """Expand the runtime variables that selected skills are allowed to use."""
    expanded = str(value or "")
    for key, replacement in env.items():
        expanded = expanded.replace(f"${key}", replacement)
        expanded = expanded.replace("${" + key + "}", replacement)
    return expanded


def _runtime_command_args(command: str, env: dict[str, str]) -> list[str]:
    """Parse a command string into argv without invoking a shell."""
    raw_command = str(command or "").strip()
    if not raw_command:
        raise ValueError("command is required")
    try:
        args = shlex.split(raw_command)
    except ValueError as exc:
        raise ValueError(f"invalid command: {exc}") from exc
    if not args:
        raise ValueError("command is required")

    return [_expand_runtime_arg(arg, env) for arg in args]


def _is_relative_to(candidate: Path, root: Path) -> bool:
    """Return whether candidate is inside root after both paths are resolved."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_runtime_command_paths(
    ctx: "RunContext[SkillDeps]",
    command: str,
    args: list[str],
) -> None:
    """Reject command path arguments that escape work_dir or the selected skill.

    Commands may execute binaries by name through PATH. Explicit absolute paths
    are accepted only when they point at work_dir or the selected skill install
    directory, which keeps model-generated file operations within the selected
    runtime boundary.
    """
    if _HOME_RELATIVE_PATH_RE.search(str(command or "")):
        raise ValueError("command paths must be relative to work_dir; '~' is not allowed")

    allowed_roots = [get_user_work_dir(ctx).resolve(), _skill_dir(ctx).resolve()]
    for index, arg in enumerate(args):
        for match in _ABSOLUTE_PATH_RE.finditer(str(arg or "")):
            path_text = match.group(0).rstrip(").,;:]}")
            if not path_text or path_text == "/":
                continue
            resolved = Path(path_text).expanduser().resolve()
            if any(_is_relative_to(resolved, root) for root in allowed_roots):
                continue
            if index == 0 and str(arg).strip() == path_text:
                continue
            raise ValueError("command paths must stay inside work_dir or the selected skill")


def _validate_user_requested_runtime_path(ctx: "RunContext[SkillDeps]") -> None:
    """Reject user-requested home-relative output paths before running commands."""
    deps = getattr(ctx, "deps", None)
    user_message = str(getattr(deps, "user_message", "") or "")
    if _HOME_RELATIVE_PATH_RE.search(user_message):
        raise ValueError("home-relative output paths are not allowed; use a work_dir-relative filename")


def _resolve_runtime_read_path(ctx: "RunContext[SkillDeps]", file_path: str) -> Path:
    """Resolve a read path from work_dir first, then from the selected skill dir."""
    _selected_skill(ctx)
    requested = str(file_path or "").strip()
    if not requested:
        raise ValueError("file_path is required")

    try:
        return resolve_file_path(ctx, requested)
    except ValueError:
        pass

    skill_dir = _skill_dir(ctx)
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = skill_dir / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(skill_dir)
    except ValueError as exc:
        raise ValueError("file_path must be inside work_dir or the selected skill") from exc
    return resolved


def _resolve_runtime_cwd(ctx: "RunContext[SkillDeps]", cwd: Optional[str]) -> Path:
    """Resolve command cwd under the user's work_dir, defaulting to work_dir."""
    _selected_skill(ctx)
    if not cwd:
        return get_user_work_dir(ctx)
    deps = getattr(ctx, "deps", None)
    session_manager = getattr(deps, "session_manager", None)
    workspace_path = getattr(session_manager, "workspace_path", Path("."))
    user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
    return resolve_path_in_user_work_dir(workspace_path, user_id, cwd)


def _normalize_download_paths(
    ctx: "RunContext[SkillDeps]",
    download_paths: Optional[list[str] | str],
) -> list[str]:
    """Normalize explicit download paths into visible work_dir-relative paths.

    Runtime tools never infer downloads from all changed files. The selected
    skill must pass final user-facing files explicitly, which avoids exposing
    temporary scripts, logs, caches, or hidden runtime state.
    """
    if download_paths is None:
        return []
    raw_paths = [download_paths] if isinstance(download_paths, str) else download_paths
    work_dir = get_user_work_dir(ctx).resolve()
    results: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if not str(raw_path or "").strip():
            continue
        path = resolve_file_path(ctx, str(raw_path))
        relative_path = path.resolve().relative_to(work_dir).as_posix()
        if not is_safe_workspace_relative_path(relative_path):
            raise ValueError("download_paths must reference visible files under work_dir")
        if not path.is_file():
            continue
        if relative_path in seen:
            continue
        seen.add(relative_path)
        results.append(relative_path)
    return results


async def skill_read_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """Read a file from current work_dir or the selected skill directory."""
    try:
        path = _resolve_runtime_read_path(ctx, file_path)
    except ValueError as exc:
        return ToolResult.error(str(exc), details={"file_path": file_path}).to_dict()
    if not path.is_file():
        return ToolResult.error(f"FileNotFoundError: {file_path}").to_dict()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return ToolResult.error(str(exc), details={"file_path": file_path}).to_dict()
    start = max(0, int(offset or 1) - 1)
    end = len(lines) if limit is None else min(len(lines), start + max(0, int(limit)))
    text = "\n".join(f"{idx}\t{line}" for idx, line in enumerate(lines[start:end], start + 1))
    return ToolResult.text(
        truncate_output(text),
        details={"file_path": file_path, "total_lines": len(lines)},
    ).to_dict()


async def skill_write_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    content: str,
) -> dict:
    """Write UTF-8 text under the current user's work_dir for a selected skill."""
    try:
        _selected_skill(ctx)
        path = resolve_file_path(ctx, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content or ""), encoding="utf-8")
    except Exception as exc:
        return ToolResult.error(str(exc), details={"file_path": file_path}).to_dict()
    return ToolResult.text(
        f"File written: {file_path}",
        details={"file_path": file_path, "bytes_written": len(str(content or "").encode("utf-8"))},
    ).to_dict()


async def skill_edit_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """Replace exact text in a work_dir file for a selected skill."""
    try:
        _selected_skill(ctx)
        path = resolve_file_path(ctx, file_path)
        content = path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return ToolResult.error("old_string not found in file").to_dict()
        if count > 1 and not replace_all:
            return ToolResult.error(
                f"Multiple matches ({count}) found for old_string."
            ).to_dict()
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(updated, encoding="utf-8")
    except Exception as exc:
        return ToolResult.error(str(exc), details={"file_path": file_path}).to_dict()
    return ToolResult.text(
        f"Edited {file_path}: replaced {count} occurrence(s)",
        details={"file_path": file_path, "match_count": count},
    ).to_dict()


async def skill_delete_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
) -> dict:
    """Delete a work_dir file for a selected skill."""
    try:
        _selected_skill(ctx)
        path = resolve_file_path(ctx, file_path)
        if not path.is_file():
            return ToolResult.error(f"FileNotFoundError: {file_path}").to_dict()
        path.unlink()
    except Exception as exc:
        return ToolResult.error(str(exc), details={"file_path": file_path}).to_dict()
    return ToolResult.text(f"Deleted: {file_path}", details={"file_path": file_path}).to_dict()


async def skill_exec_tool(
    ctx: "RunContext[SkillDeps]",
    command: str,
    timeout_ms: int = 120000,
    cwd: Optional[str] = None,
    download_paths: Optional[list[str] | str] = None,
) -> dict:
    """Execute a shell command for the selected skill with cwd in work_dir.

    Use download_paths for final user-facing files that should get download links.
    Do not include scripts, logs, caches, or temporary files in download_paths.
    """
    start = time.monotonic()
    effective_cwd = ""
    status = "completed"
    exit_code = 0
    output = ""
    try:
        effective_cwd = str(_resolve_runtime_cwd(ctx, cwd))
        env = _runtime_env(ctx)
        args = _runtime_command_args(command, env)
        _validate_user_requested_runtime_path(ctx)
        _validate_runtime_command_paths(ctx, command, args)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=effective_cwd,
            env=env,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=max(1, int(timeout_ms or 0)) / 1000.0,
            )
            output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            exit_code = int(proc.returncode or 0)
            status = "completed" if exit_code == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            status = "timeout"
            exit_code = -1
    except Exception as exc:
        output = str(exc)
        status = "failed"
        exit_code = -1
    details = {
        "status": status,
        "exitCode": exit_code,
        "durationMs": int((time.monotonic() - start) * 1000),
        "cwd": effective_cwd,
    }
    if status == "completed":
        try:
            normalized_download_paths = _normalize_download_paths(ctx, download_paths)
        except Exception as exc:
            return ToolResult.error(
                str(exc),
                details={**details, "download_path": download_paths},
            ).to_dict()
        if normalized_download_paths:
            details["download_path"] = normalized_download_paths
        elif download_paths is None:
            output = (
                f"{output.rstrip()}\n" if output.strip() else ""
            ) + "No download_paths were provided; no download link was created."

    return ToolResult(
        content=[{"type": "text", "text": truncate_output(output)}],
        details=details,
        is_error=(status != "completed"),
    ).to_dict()


@dataclass
class _ManagedProcess:
    process_id: str
    owner_key: str
    proc: asyncio.subprocess.Process
    command: str
    _buffer: str = ""
    _read_offset: int = 0

    async def read_incremental(self) -> str:
        """Return unread stdout content without blocking for process completion."""
        if self.proc.stdout is None:
            return ""
        chunks: list[str] = []
        while True:
            try:
                data = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=0.1)
            except asyncio.TimeoutError:
                break
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
        self._buffer += "".join(chunks)
        result = self._buffer[self._read_offset :]
        self._read_offset = len(self._buffer)
        return result


_PROCESSES: dict[str, _ManagedProcess] = {}


def _get_process_for_ctx(
    ctx: "RunContext[SkillDeps]",
    process_id: Optional[str],
) -> _ManagedProcess | None:
    """Look up a managed process only if it belongs to this user/session/skill."""
    owner_key = _runtime_owner_key(ctx)
    managed = _PROCESSES.get(str(process_id or ""))
    if managed is None or managed.owner_key != owner_key:
        return None
    return managed


async def skill_process_tool(
    ctx: "RunContext[SkillDeps]",
    action: str,
    command: Optional[str] = None,
    process_id: Optional[str] = None,
    text: Optional[str] = None,
    cwd: Optional[str] = None,
) -> dict:
    """Start, poll, send input to, or kill a selected-skill background process."""
    normalized_action = str(action or "").strip().lower()
    try:
        owner_key = _runtime_owner_key(ctx)
    except ValueError as exc:
        return ToolResult.error(str(exc)).to_dict()
    if normalized_action == "start":
        if not command:
            return ToolResult.error("command is required for start action").to_dict()
        try:
            env = _runtime_env(ctx)
            args = _runtime_command_args(command, env)
            _validate_user_requested_runtime_path(ctx)
            _validate_runtime_command_paths(ctx, command, args)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=str(_resolve_runtime_cwd(ctx, cwd)),
                env=env,
            )
            pid = f"skill_proc_{uuid.uuid4().hex[:8]}"
            managed = _ManagedProcess(
                process_id=pid,
                owner_key=owner_key,
                proc=proc,
                command=command,
            )
            _PROCESSES[pid] = managed
            await asyncio.sleep(0.2)
            output = await managed.read_incremental()
        except Exception as exc:
            return ToolResult.error(str(exc)).to_dict()
        return ToolResult.text(
            truncate_output(output),
            details={"process_id": pid, "command": command, "status": "running"},
        ).to_dict()
    if normalized_action == "poll":
        managed = _get_process_for_ctx(ctx, process_id)
        if managed is None:
            return ToolResult.error(f"process {process_id} not found").to_dict()
        return ToolResult.text(
            truncate_output(await managed.read_incremental()),
            details={"process_id": process_id, "status": "running"},
        ).to_dict()
    if normalized_action == "send_keys":
        managed = _get_process_for_ctx(ctx, process_id)
        if managed is None or managed.proc.stdin is None:
            return ToolResult.error(f"process {process_id} not found").to_dict()
        managed.proc.stdin.write(str(text or "").encode("utf-8"))
        await managed.proc.stdin.drain()
        return ToolResult.text("keys sent", details={"process_id": process_id}).to_dict()
    if normalized_action == "kill":
        managed = _get_process_for_ctx(ctx, process_id)
        if managed is None:
            return ToolResult.error(f"process {process_id} not found").to_dict()
        _PROCESSES.pop(str(process_id or ""), None)
        managed.proc.kill()
        await managed.proc.wait()
        return ToolResult.text("process terminated", details={"process_id": process_id}).to_dict()
    return ToolResult.error(f"unknown action: {action}").to_dict()
