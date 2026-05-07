# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any
from urllib.parse import unquote

WORKSPACE_DOWNLOAD_PATH_KEYS: frozenset[str] = frozenset({"artifact_path", "download_path"})


class WorkspaceDownloadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class OpenWorkspaceDownloadFile:
    path: Path
    fd: int
    stat_result: os.stat_result


def normalize_workspace_user_id(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value or value == "anonymous" or "\x00" in value:
        return ""
    if value in {".", ".."} or "/" in value or "\\" in value:
        return ""
    return value


def is_safe_workspace_relative_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.startswith("~"):
        return False
    normalized_lower = normalized.lower()
    if normalized_lower == ".atlasclaw" or normalized_lower.startswith(".atlasclaw/"):
        return False
    if "\x00" in normalized:
        return False
    if ":" in normalized.split("/", 1)[0]:
        return False
    return all(part and part not in {".", ".."} for part in normalized.split("/"))


def coerce_workspace_download_payload(payload: Any) -> Any:
    """Parse JSON-looking tool payload strings before scanning for generated file paths."""
    if not isinstance(payload, str):
        return payload
    normalized = payload.strip()
    if not normalized or normalized[:1] not in {"{", "["}:
        return payload
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return payload


def workspace_download_payload_is_error(
    payload: dict[str, Any],
    *,
    success_false_is_error: bool = False,
) -> bool:
    """Return whether a tool payload should be ignored for workspace downloads."""
    if payload.get("is_error") is True:
        return True
    if success_false_is_error and payload.get("success") is False:
        return True
    details = payload.get("details")
    return isinstance(details, dict) and details.get("is_error") is True


def collect_workspace_download_path_candidates(
    payload: Any,
    *,
    success_false_is_error: bool = False,
) -> list[Any]:
    """Collect explicit generated file path values from a nested tool payload."""
    normalized = coerce_workspace_download_payload(payload)
    if isinstance(normalized, dict):
        if workspace_download_payload_is_error(
            normalized,
            success_false_is_error=success_false_is_error,
        ):
            return []
        candidates: list[Any] = []
        for key, value in normalized.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in WORKSPACE_DOWNLOAD_PATH_KEYS:
                if isinstance(value, list):
                    candidates.extend(value)
                else:
                    candidates.append(value)
                continue
            if isinstance(value, (dict, list)):
                candidates.extend(
                    collect_workspace_download_path_candidates(
                        value,
                        success_false_is_error=success_false_is_error,
                    )
                )
        return candidates
    if isinstance(normalized, list):
        candidates: list[Any] = []
        for item in normalized:
            candidates.extend(
                collect_workspace_download_path_candidates(
                    item,
                    success_false_is_error=success_false_is_error,
                )
            )
        return candidates
    return []


def workspace_download_root(workspace_path: str | Path, user_id: str) -> Path:
    safe_user_id = normalize_workspace_user_id(user_id)
    if not safe_user_id:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        )

    workspace_root = Path(workspace_path).expanduser().resolve()
    current = workspace_root
    for part in ("users", safe_user_id, "work_dir"):
        current = current / part
        if current.is_symlink():
            raise WorkspaceDownloadError(
                "forbidden",
                "Requested file is outside the allowed workspace area",
            )
    return workspace_root / "users" / safe_user_id / "work_dir"


def resolve_workspace_download_file(
    *,
    workspace_path: str | Path,
    user_id: str,
    requested_path: str,
) -> Path:
    raw_path = str(requested_path or "").strip()
    if not raw_path:
        raise WorkspaceDownloadError("bad_request", "Download path is required")
    if "\x00" in raw_path:
        raise WorkspaceDownloadError("bad_request", "Invalid download path")
    if Path(raw_path).is_absolute() or not is_safe_workspace_relative_path(raw_path):
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        )

    root = workspace_download_root(workspace_path, user_id)
    try:
        root_resolved = root.resolve()
        resolved = (root / raw_path).resolve()
    except WorkspaceDownloadError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkspaceDownloadError("bad_request", "Invalid download path") from exc

    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        ) from exc

    try:
        is_file = resolved.is_file()
    except (OSError, ValueError) as exc:
        raise WorkspaceDownloadError("bad_request", "Invalid download path") from exc

    if not is_file:
        raise WorkspaceDownloadError("not_found", "Download file not found")
    return resolved


def _open_directory_no_symlink(name: str | Path, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    kwargs = {"dir_fd": dir_fd} if dir_fd is not None else {}
    try:
        return os.open(name, flags, **kwargs)
    except (OSError, TypeError) as exc:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        ) from exc


def _open_file_no_symlink(name: str, *, dir_fd: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError as exc:
        raise WorkspaceDownloadError("not_found", "Download file not found") from exc
    except (OSError, TypeError) as exc:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        ) from exc

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise WorkspaceDownloadError("not_found", "Download file not found")
        return fd, file_stat
    except Exception:
        os.close(fd)
        raise


def open_workspace_download_file(
    *,
    workspace_path: str | Path,
    user_id: str,
    requested_path: str,
) -> OpenWorkspaceDownloadFile:
    raw_path = str(requested_path or "").strip()
    if not raw_path:
        raise WorkspaceDownloadError("bad_request", "Download path is required")
    if "\x00" in raw_path:
        raise WorkspaceDownloadError("bad_request", "Invalid download path")
    if Path(raw_path).is_absolute() or not is_safe_workspace_relative_path(raw_path):
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        )

    safe_user_id = normalize_workspace_user_id(user_id)
    if not safe_user_id:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        )

    workspace_root = Path(workspace_path).expanduser().resolve()
    root_fd = _open_directory_no_symlink(workspace_root)
    current_fd = root_fd
    try:
        for part in ("users", safe_user_id, "work_dir"):
            next_fd = _open_directory_no_symlink(part, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd

        parts = raw_path.replace("\\", "/").split("/")
        for part in parts[:-1]:
            next_fd = _open_directory_no_symlink(part, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd

        file_fd, file_stat = _open_file_no_symlink(parts[-1], dir_fd=current_fd)
        return OpenWorkspaceDownloadFile(
            path=workspace_root / "users" / safe_user_id / "work_dir" / raw_path,
            fd=file_fd,
            stat_result=file_stat,
        )
    finally:
        os.close(current_fd)


def _resolve_workspace_artifact_candidate(
    *,
    workspace_path: str | Path,
    user_id: str,
    candidate_path: str,
) -> Path:
    raw_path = str(candidate_path or "").strip()
    if not raw_path:
        raise WorkspaceDownloadError("bad_request", "Download path is required")

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        return resolve_workspace_download_file(
            workspace_path=workspace_path,
            user_id=user_id,
            requested_path=raw_path,
        )

    root = workspace_download_root(workspace_path, user_id)
    try:
        root_resolved = root.resolve()
        resolved = candidate.resolve()
        relative_path = resolved.relative_to(root_resolved).as_posix()
        if not is_safe_workspace_relative_path(relative_path):
            raise WorkspaceDownloadError(
                "forbidden",
                "Requested file is outside the allowed workspace area",
            )
    except ValueError as exc:
        raise WorkspaceDownloadError(
            "forbidden",
            "Requested file is outside the allowed workspace area",
        ) from exc
    except (OSError, ValueError) as exc:
        raise WorkspaceDownloadError("bad_request", "Invalid download path") from exc

    try:
        is_file = resolved.is_file()
    except (OSError, ValueError) as exc:
        raise WorkspaceDownloadError("bad_request", "Invalid download path") from exc

    if not is_file:
        raise WorkspaceDownloadError("not_found", "Download file not found")
    return resolved


def workspace_download_reference_for_path(
    raw_path: Any,
    *,
    workspace_path: str | Path,
    user_id: str,
) -> dict[str, str] | None:
    value = str(raw_path or "").strip()
    if not value or value.startswith("~") or "\x00" in value:
        return None

    candidate_path = value
    if value.lower().startswith("workspace://"):
        candidate_path = unquote(value[len("workspace://"):])

    try:
        resolved = _resolve_workspace_artifact_candidate(
            workspace_path=workspace_path,
            user_id=user_id,
            candidate_path=candidate_path,
        )
        root = workspace_download_root(workspace_path, user_id).resolve()
        return {"path": resolved.relative_to(root).as_posix()}
    except WorkspaceDownloadError:
        return None
    except ValueError:
        return None


def collect_workspace_download_references_from_payloads(
    payloads: list[Any],
    *,
    workspace_path: str | Path,
    user_id: str,
    seen_paths: set[str] | None = None,
    success_false_is_error: bool = False,
) -> list[dict[str, str]]:
    """Resolve explicit generated file paths from tool payloads into download references."""
    references: list[dict[str, str]] = []
    seen = seen_paths if seen_paths is not None else set()
    for payload in payloads:
        for candidate in collect_workspace_download_path_candidates(
            payload,
            success_false_is_error=success_false_is_error,
        ):
            reference = workspace_download_reference_for_path(
                candidate,
                workspace_path=workspace_path,
                user_id=user_id,
            )
            if not reference:
                continue
            path = reference.get("path", "")
            if not path or path in seen:
                continue
            seen.add(path)
            references.append(reference)
    return references
