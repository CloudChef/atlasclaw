"""Filesystem write tool.

This tool writes text content to disk and creates parent directories when
needed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult
from app.xuanwu.tools.work_dir_guard import resolve_file_path

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def write_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    content: str,
) -> dict:
    """
    Write text content to a file.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        file_path: File path to write.
        content: Text content to persist.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    try:
        path = str(resolve_file_path(ctx, file_path))
    except ValueError as e:
        return ToolResult.error(
            str(e),
            details={"file_path": file_path},
        ).to_dict()

    try:
        # Create parent directories automatically when needed.
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        bytes_written = len(content.encode("utf-8"))

        return ToolResult.text(
            f"File written: {file_path}",
            details={
                "file_path": file_path,
                "bytes_written": bytes_written,
                "created": True,
            },
        ).to_dict()

    except PermissionError:
        return ToolResult.error(
            f"PermissionError: cannot write to {file_path}",
            details={"file_path": file_path},
        ).to_dict()
    except Exception as e:
        return ToolResult.error(
            str(e),
            details={"file_path": file_path},
        ).to_dict()
