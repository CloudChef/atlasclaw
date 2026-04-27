# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""YAML frontmatter parsing for markdown skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


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
    try:
        loaded = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
