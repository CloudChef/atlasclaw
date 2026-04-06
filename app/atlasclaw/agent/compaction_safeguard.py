# -*- coding: utf-8 -*-
"""Safeguard helpers for compaction summary quality."""

from __future__ import annotations

from typing import Optional

MAX_TOOL_FAILURES = 8
MAX_TOOL_FAILURE_CHARS = 240
MAX_CRITICAL_HISTORY_ITEMS = 6
MAX_CRITICAL_HISTORY_CHARS = 280


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - 3)]}..."


def collect_tool_failures(messages: list[dict]) -> list[str]:
    """Collect compact tool-failure lines from historical messages."""
    failures: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if str(message.get("role", "")).strip().lower() != "tool":
            continue
        metadata = message.get("metadata", {})
        is_error = False
        if isinstance(metadata, dict):
            is_error = bool(metadata.get("is_error")) or str(metadata.get("status", "")).lower() == "error"
        if not is_error:
            continue
        tool_call_id = str(message.get("tool_call_id", "")).strip()
        if tool_call_id and tool_call_id in seen:
            continue
        if tool_call_id:
            seen.add(tool_call_id)
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip() or "tool"
        content = str(message.get("content", "") or "")
        summary = _truncate_text(content, MAX_TOOL_FAILURE_CHARS) or "failed (no output)"
        failures.append(f"- {tool_name}: {summary}")
        if len(failures) >= MAX_TOOL_FAILURES:
            break
    return failures


def collect_critical_history(messages: list[dict]) -> list[str]:
    """Collect concise critical history snippets for safeguard summaries."""
    critical: list[str] = []
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", "") or "")
        compact = _truncate_text(content, MAX_CRITICAL_HISTORY_CHARS)
        if not compact:
            continue
        critical.append(f"- [{role}] {compact}")
        if len(critical) >= MAX_CRITICAL_HISTORY_ITEMS:
            break
    return critical


def build_safeguarded_summary(*, messages: list[dict], base_summary: str) -> str:
    """Append critical history and tool-failure sections to base compaction summary."""
    summary = (base_summary or "").strip()
    sections: list[str] = [summary] if summary else []

    critical_lines = collect_critical_history(messages)
    if critical_lines:
        sections.append("## Critical History")
        sections.extend(critical_lines)

    failure_lines = collect_tool_failures(messages)
    if failure_lines:
        sections.append("## Tool Failures")
        sections.extend(failure_lines)

    return "\n".join(part for part in sections if part).strip()
