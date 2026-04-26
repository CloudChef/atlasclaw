# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""YAML frontmatter parsing for markdown skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - fallback only used in misconfigured environments
    yaml = None


@dataclass
class FrontmatterResult:
    """Frontmatter parse result."""

    metadata: dict[str, Any] = field(default_factory=dict)
    body: str = ""


def parse_frontmatter(content: str) -> FrontmatterResult:
    """Parse Markdown YAML frontmatter.

    Returns empty metadata and the original content when:
    - the file has no leading frontmatter fence
    - the closing fence is missing
    - the frontmatter cannot be parsed as a YAML mapping
    """
    content = content.lstrip("\ufeff")
    content = content.replace("\r\n", "\n")
    lines = content.split("\n")

    if not lines or lines[0].strip() != "---":
        return FrontmatterResult(metadata={}, body=content)

    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break

    if close_idx == -1:
        return FrontmatterResult(metadata={}, body=content)

    metadata = _parse_frontmatter_mapping("\n".join(lines[1:close_idx]))
    body_lines = lines[close_idx + 1 :]
    body = "\n".join(body_lines)
    return FrontmatterResult(metadata=metadata, body=body)


def _parse_frontmatter_mapping(frontmatter: str) -> dict[str, Any]:
    """Parse the YAML mapping portion of a frontmatter block."""
    if yaml is not None:
        try:
            loaded = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return _parse_frontmatter_mapping_legacy(frontmatter)


def _parse_frontmatter_mapping_legacy(frontmatter: str) -> dict[str, Any]:
    """Fallback parser for environments without PyYAML.

    This keeps basic scalar and list support so the runtime remains usable
    even if the YAML dependency is unexpectedly unavailable.
    """
    metadata: dict[str, Any] = {}
    current_key = ""
    current_list: list[str] | None = None
    current_map: dict[str, str] | None = None

    def _strip_quotes(value: str) -> str:
        if len(value) >= 2 and (
            (value[0] == "'" and value[-1] == "'") or (value[0] == '"' and value[-1] == '"')
        ):
            return value[1:-1]
        return value

    def _flush_current() -> None:
        nonlocal current_key, current_list, current_map
        if current_key:
            if current_list is not None:
                metadata[current_key] = current_list
            elif current_map is not None:
                metadata[current_key] = current_map
        current_key = ""
        current_list = None
        current_map = None

    for line in frontmatter.splitlines():
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent > 0 and current_key and stripped.startswith("- "):
            if current_map is not None:
                continue
            if current_list is None:
                current_list = []
            current_list.append(_strip_quotes(stripped[2:].strip()))
            continue
        if indent > 0 and current_key and ":" in stripped:
            if current_list is not None:
                continue
            if current_map is None:
                current_map = {}
            nested_key, nested_value = stripped.split(":", 1)
            nested_key = nested_key.strip()
            nested_value = _strip_quotes(nested_value.strip())
            if nested_key:
                current_map[nested_key] = nested_value
            continue
        if stripped.startswith("- "):
            if current_key:
                if current_list is None:
                    current_list = []
                current_list.append(_strip_quotes(stripped[2:].strip()))
            continue
        _flush_current()
        colon_pos = stripped.find(":")
        if colon_pos == -1:
            continue
        key = stripped[:colon_pos].strip()
        value = stripped[colon_pos + 1 :].strip()
        if not value:
            current_key = key
            continue
        if key:
            metadata[key] = _strip_quotes(value)

    _flush_current()
    return metadata
