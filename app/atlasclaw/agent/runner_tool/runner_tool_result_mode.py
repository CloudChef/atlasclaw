# -*- coding: utf-8 -*-
"""Helpers for interpreting runtime tool result modes."""

from __future__ import annotations

import re
from typing import Any


_SILENT_RESULT_MODES = frozenset({"silent", "silent_ok", "tool_hidden", "tool_silent"})
_SILENT_ROUTING_VISIBILITIES = frozenset({"silent", "hidden", "internal"})
_SILENT_DESCRIPTION_HINTS = (
    "silent backend lookup",
    "for workflow continuation only",
    "never narrate this lookup",
    "do not display its output or metadata to the user",
    "do not mention this tool call",
    "do not mention this step to the user",
)
_SILENT_DESCRIPTION_PREFIX = (
    "For workflow continuation only. Do not mention this tool call or show its raw output to the user."
)
_CJK_BACKEND_STEP = "\u540e\u53f0\u6b65\u9aa4"
_CJK_INTERMEDIATE_LOOKUP = "\u4e2d\u95f4\u67e5\u8be2"
_CJK_BACKEND_STATE = "\u540e\u53f0\u72b6\u6001"
_CJK_INTERNAL_METADATA = "\u5185\u90e8\u5143\u6570\u636e"
_WORKFLOW_ONLY_TEXT_REPLACEMENTS = (
    (
        "Treat returned _internal metadata such as id, sourceKey, serviceCategory, "
        "instructions, and params as hidden backend state only; never display or narrate "
        "those fields.",
        "Keep returned _internal metadata for workflow use only; do not show those fields "
        "to the user.",
    ),
    ("Silent backend lookup for request workflow.", ""),
    ("Never narrate this lookup or display its output or metadata to the user.", ""),
    ("Do not mention this step to the user or display its raw output.", ""),
    ("hidden backend state", "workflow-only metadata"),
    ("Hidden backend lookup", "Internal lookup"),
    ("hidden backend lookup", "internal lookup"),
    ("hidden backend step", "intermediate lookup"),
    ("backend state", "workflow-only metadata"),
    ("backend metadata", "intermediate metadata"),
    (_CJK_BACKEND_STEP, _CJK_INTERMEDIATE_LOOKUP),
    (_CJK_BACKEND_STATE, _CJK_INTERNAL_METADATA),
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def sanitize_workflow_only_text(value: Any, *, collapse_whitespace: bool = True) -> str:
    """Remove backend/background wording while preserving workflow constraints."""
    text = _normalize_text(value) if collapse_whitespace else str(value or "")
    if not text:
        return ""

    for source, target in _WORKFLOW_ONLY_TEXT_REPLACEMENTS:
        text = text.replace(source, target)

    if collapse_whitespace:
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text.replace(". .", ".")

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def is_silent_backend_tool(tool: dict[str, Any] | None) -> bool:
    """Return whether a tool should stay invisible to the end user."""
    if not isinstance(tool, dict):
        return False

    result_mode = _normalize_text(tool.get("result_mode", "")).lower()
    if result_mode in _SILENT_RESULT_MODES:
        return True

    routing_visibility = _normalize_text(
        tool.get("routing_visibility", "") or tool.get("planner_visibility", "")
    ).lower()
    if routing_visibility in _SILENT_ROUTING_VISIBILITIES:
        return True

    description = _normalize_text(tool.get("description", "")).lower()
    return any(hint in description for hint in _SILENT_DESCRIPTION_HINTS)


def normalize_tool_result_mode(tool: dict[str, Any] | None) -> str:
    """Return the effective result mode after silent-tool normalization."""
    if not isinstance(tool, dict):
        return ""
    if is_silent_backend_tool(tool):
        return "silent_ok"
    return _normalize_text(tool.get("result_mode", "")).lower()


def normalize_tool_description(*, description: Any, silent_backend: bool) -> str:
    """Add a consistent hidden-step directive for silent backend tools."""
    normalized_description = _normalize_text(description)
    if not silent_backend:
        return normalized_description
    normalized_description = sanitize_workflow_only_text(normalized_description)
    if _SILENT_DESCRIPTION_PREFIX.lower() in normalized_description.lower():
        return normalized_description
    if normalized_description:
        return f"{_SILENT_DESCRIPTION_PREFIX} {normalized_description}"
    return _SILENT_DESCRIPTION_PREFIX
