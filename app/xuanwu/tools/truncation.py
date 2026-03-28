"""
Result truncation

Matches decision 5 in `design.md`:tool result truncation.

Prevents large outputs from blowing up the context by keeping the head, tail, and `truncation_marker`.
"""

from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass


@dataclass
class TruncationConfig:
    """

truncation configuration

    Attributes:
        max_chars:character count
        head_lines:count
        tail_lines:count
        truncation_marker:truncate
        max_image_bytes:image base64 payload count
    
"""

    max_chars: int = 50000
    head_lines: int = 100
    tail_lines: int = 50
    truncation_marker: str = "\n... [truncated] ...\n"
    max_image_bytes: int = 100 * 1024  # 100KB


def truncate_output(text: str, config: TruncationConfig | None = None) -> str:
    """

truncate

    If the output does not exceed `max_chars`, return it in full;
    otherwise keep `head_lines + tail_lines` and insert `truncation_marker` in the middle.

    Args:
        text:raw text
        config:truncation configuration

    Returns:
        truncate
    
"""
    if config is None:
        config = TruncationConfig()

    if len(text) <= config.max_chars:
        return text

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)

    if total_lines <= config.head_lines + config.tail_lines:
        return text

    head = lines[: config.head_lines]
    tail = lines[-config.tail_lines :]

    truncated_count = total_lines - config.head_lines - config.tail_lines
    marker = config.truncation_marker.replace(
        "[truncated]",
        f"[truncated {truncated_count} lines]",
    )

    return "".join(head) + marker + "".join(tail)


def truncate_image_payload(
    base64_data: str,
    config: TruncationConfig | None = None,
) -> str:
    """

handle base64 image payload

    If the base64 payload exceeds the limit, save it to a temporary file and return its file URL.

    Args:
        base64_data:base64 imagecount
        config:truncation configuration

    Returns:
        raw base64 data URI or file path
    
"""
    if config is None:
        config = TruncationConfig()

    # calculateraw
    try:
        # data URI prefix
        pure_base64 = base64_data
        if "," in base64_data:
            pure_base64 = base64_data.split(",", 1)[1]
        raw_bytes = base64.b64decode(pure_base64)
    except Exception:
        return base64_data

    if len(raw_bytes) <= config.max_image_bytes:
        return base64_data

    # :
    suffix = ".png"
    if base64_data.startswith("data:image/jpeg"):
        suffix = ".jpg"
    elif base64_data.startswith("data:image/gif"):
        suffix = ".gif"

    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="xuanwu_img_")
    try:
        os.write(fd, raw_bytes)
    finally:
        os.close(fd)

    return tmp_path
