"""Filesystem delete tool.

This tool deletes a single file path. Approval is typically enforced by the
tooling policy layer.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def delete_file_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
) -> dict:
    """
    Delete a file from disk.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        file_path: File path to delete.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    path = os.path.abspath(file_path)

    if not os.path.exists(path):
        return ToolResult.error(
            f"FileNotFoundError: {file_path}",
            details={"file_path": file_path},
        ).to_dict()

    if not os.path.isfile(path):
        return ToolResult.error(
            f"Not a file (use rmdir for directories): {file_path}",
            details={"file_path": file_path},
        ).to_dict()

    try:
        os.remove(path)
        return ToolResult.text(
            f"Deleted: {file_path}",
            details={"file_path": file_path, "deleted": True},
        ).to_dict()
    except PermissionError:
        return ToolResult.error(
            f"PermissionError: cannot delete {file_path}",
            details={"file_path": file_path},
        ).to_dict()
    except Exception as e:
        return ToolResult.error(
            str(e),
            details={"file_path": file_path},
        ).to_dict()
