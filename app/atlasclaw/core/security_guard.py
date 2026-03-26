# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Optional

_COMMAND_PATTERN = re.compile(
    r"^\s*(?:sudo\s+)?(?:"
    r"rm|mv|cp|cat|ls|cd|chmod|chown|curl|wget|bash|sh|python|node|npm|pnpm|yarn|"
    r"git|docker|kubectl|pwsh|powershell|cmd|del|rd|mkdir|rmdir|type|copy|move|echo"
    r")\b",
    re.IGNORECASE,
)

_SCRIPT_PATTERN = re.compile(
    r"(?:<script\b|</script>|javascript:|^\s*#!|console\.log\(|eval\(|new Function\()",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_command_or_script(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    return bool(_COMMAND_PATTERN.search(value) or _SCRIPT_PATTERN.search(value))


def encode_untrusted_text(text: str) -> str:
    raw = str(text or "")
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"[encoded_input:base64:{encoded}]"


def encode_if_untrusted(text: str) -> tuple[str, bool]:
    if looks_like_command_or_script(text):
        return encode_untrusted_text(text), True
    return str(text or ""), False


def ensure_user_work_dir(workspace_path: str | Path, user_id: str) -> Path:
    root = Path(workspace_path).resolve() / "users" / (user_id or "default") / "work_dir"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_path_in_user_work_dir(
    workspace_path: str | Path,
    user_id: str,
    candidate_path: str,
) -> Path:
    work_dir = ensure_user_work_dir(workspace_path, user_id)
    candidate = Path(candidate_path)
    resolved = (candidate if candidate.is_absolute() else (work_dir / candidate)).resolve()
    try:
        resolved.relative_to(work_dir)
    except ValueError as exc:
        raise ValueError(f"path must be inside user work_dir: {work_dir}") from exc
    return resolved


def resolve_cwd_in_user_work_dir(
    workspace_path: str | Path,
    user_id: str,
    cwd: Optional[str],
) -> Path:
    work_dir = ensure_user_work_dir(workspace_path, user_id)
    if not cwd:
        return work_dir
    return resolve_path_in_user_work_dir(workspace_path, user_id, cwd)
