"""Filesystem edit tool.

This tool performs exact string replacement within a file and optionally
replaces all matching occurrences.
"""

from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def edit_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """
    Edit a file by replacing an exact string.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        file_path: File path to modify.
        old_string: Exact text to replace.
        new_string: Replacement text.
        replace_all: Whether to replace every match instead of only the first.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    path = os.path.abspath(file_path)

    if not os.path.exists(path):
        return ToolResult.error(
            f"FileNotFoundError: {file_path}",
            details={"file_path": file_path},
        ).to_dict()

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return ToolResult.error(str(e), details={"file_path": file_path}).to_dict()

    # Count matching occurrences before modifying the file.
    match_count = content.count(old_string)

    if match_count == 0:
        return ToolResult.error(
            "old_string not found in file",
            details={"file_path": file_path, "old_string": old_string},
        ).to_dict()

    if match_count > 1 and not replace_all:
        return ToolResult.error(
            f"Multiple matches ({match_count}) found for old_string. "
            f"Use replace_all=True to replace all, or provide a more specific old_string.",
            details={
                "file_path": file_path,
                "match_count": match_count,
            },
        ).to_dict()

    # Record affected line numbers for reporting.
    lines_changed: list[int] = []
    lines = content.split("\n")
    for i, line in enumerate(lines, start=1):
        if old_string in line:
            lines_changed.append(i)

    # Perform the replacement.
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return ToolResult.error(str(e), details={"file_path": file_path}).to_dict()

    return ToolResult.text(
        f"Edited {file_path}: replaced {match_count} occurrence(s)",
        details={
            "file_path": file_path,
            "lines_changed": lines_changed,
            "match_count": match_count,
            "replace_all": replace_all,
        },
    ).to_dict()
