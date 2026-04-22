# -*- coding: utf-8 -*-
"""Helpers for interpreting runtime tool result modes."""

from __future__ import annotations

import re
from typing import Any


_SILENT_RESULT_MODES = frozenset({"silent", "silent_ok", "tool_hidden", "tool_silent"})
_SILENT_ROUTING_VISIBILITIES = frozenset({"silent", "hidden", "internal"})
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

    return False


def normalize_tool_result_mode(tool: dict[str, Any] | None) -> str:
    """Return the effective result mode after silent-tool normalization."""
    if not isinstance(tool, dict):
        return ""
    if is_silent_backend_tool(tool):
        return "silent_ok"
    return _normalize_text(tool.get("result_mode", "")).lower()


def normalize_tool_description(*, description: Any, silent_backend: bool) -> str:
    """Normalize workflow-only wording without inferring tool behavior from text."""
    normalized_description = _normalize_text(description)
    if not silent_backend:
        return normalized_description
    return sanitize_workflow_only_text(normalized_description)


def should_hide_lookup_output(
    *,
    result_mode: str,
    success_contract: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> bool:
    """Return whether a silent lookup result should hide its raw scaffold output."""
    if not isinstance(result, dict):
        return False

    normalized_result_mode = _normalize_text(result_mode).lower()
    if normalized_result_mode not in _SILENT_RESULT_MODES:
        return False

    normalized_success_contract = success_contract if isinstance(success_contract, dict) else {}
    if normalized_success_contract:
        return False

    if not bool(result.get("success")):
        return False

    if "_internal" not in result:
        return False

    output = result.get("output")
    return isinstance(output, str) and bool(output.strip())


def has_hidden_lookup_result_content(content: Any) -> bool:
    """Return whether persisted tool content represents a hidden silent lookup result."""
    if not isinstance(content, dict):
        return False

    if "_internal" not in content:
        return False

    if bool(content.get("_lookup_output_hidden")):
        return True

    output = content.get("output")
    if output is None:
        return True
    if isinstance(output, str):
        return not bool(output.strip())
    return False
