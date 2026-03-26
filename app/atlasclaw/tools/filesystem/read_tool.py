"""Filesystem read tool.

This tool reads text files with optional line slicing and line numbers, and it
can also return image files as base64 data URIs.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Optional, TYPE_CHECKING

from app.atlasclaw.tools.base import ToolResult
from app.atlasclaw.tools.truncation import truncate_output, TruncationConfig
from app.atlasclaw.tools.work_dir_guard import resolve_file_path

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


# Supported image extensions that should be returned as image payloads.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}


async def read_tool(
    ctx: "RunContext[SkillDeps]",
    file_path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """
    Read file content from disk.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        file_path: File path to read.
        offset: Optional starting line number, using 1-based indexing.
        limit: Optional maximum number of lines to return.

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

    if not os.path.exists(path):
        return ToolResult.error(
            f"FileNotFoundError: {file_path}",
            details={"file_path": file_path},
        ).to_dict()

    if not os.path.isfile(path):
        return ToolResult.error(
            f"Not a file: {file_path}",
            details={"file_path": file_path},
        ).to_dict()

    # Return image files as image payloads rather than as text.
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return _read_image(path, file_path)

    # Read text content from disk.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except PermissionError:
        return ToolResult.error(
            f"PermissionError: cannot read {file_path}",
            details={"file_path": file_path},
        ).to_dict()
    except Exception as e:
        return ToolResult.error(
            str(e),
            details={"file_path": file_path},
        ).to_dict()

    total_lines = len(all_lines)

    # Compute the selected line range.
    start = 0
    end = total_lines
    if offset is not None:
        start = max(0, offset - 1)  # Offset is 1-based.
    if limit is not None:
        end = min(total_lines, start + limit)

    selected = all_lines[start:end]

    # Prefix each returned line with its original line number.
    numbered_lines: list[str] = []
    for i, line in enumerate(selected, start=start + 1):
        line_text = line.rstrip("\n").rstrip("\r")
        numbered_lines.append(f"{i}\t{line_text}")

    text = "\n".join(numbered_lines)

    # Truncate oversized output to protect the model context window.
    text = truncate_output(text, TruncationConfig())

    return ToolResult.text(
        text,
        details={
            "file_path": file_path,
            "total_lines": total_lines,
            "start_line": start + 1,
            "end_line": end,
            "lines_returned": len(selected),
        },
    ).to_dict()


def _read_image(path: str, file_path: str) -> dict:
    """Read an image file and return it as a base64 data URI."""
    try:
        mime_type = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"

        return ToolResult(
            content=[{"type": "image", "url": data_uri}],
            details={
                "file_path": file_path,
                "size_bytes": len(raw),
                "mime_type": mime_type,
            },
        ).to_dict()
    except Exception as e:
        return ToolResult.error(
            str(e), details={"file_path": file_path}
        ).to_dict()
