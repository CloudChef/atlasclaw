# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any


_TOOL_RESULT_ROLES = {"tool", "toolresult", "tool_result"}


def extract_synthetic_tool_messages_from_next_node(*, history: Any, next_node: Any) -> list[dict[str, Any]]:
    """Extract immediate tool-return transcript rows from the node that follows tool execution."""
    request = getattr(next_node, "request", None)
    if request is None:
        return []
    try:
        normalized = history.normalize_messages([request])
    except Exception:
        return []
    synthetic: list[dict[str, Any]] = []
    for message in normalized:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").strip().lower()
        if role not in _TOOL_RESULT_ROLES:
            continue
        synthetic.append(dict(message))
    return synthetic


def merge_synthetic_tool_messages(
    *,
    existing: list[dict[str, Any]],
    new_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(message) for message in existing if isinstance(message, dict)]
    seen = {
        _tool_message_identity_key(message)
        for message in merged
        if _tool_message_identity_key(message)
    }
    for message in new_messages:
        if not isinstance(message, dict):
            continue
        normalized = dict(message)
        message_key = _tool_message_identity_key(normalized)
        if message_key and message_key in seen:
            continue
        if message_key:
            seen.add(message_key)
        merged.append(normalized)
    return merged


def overlay_synthetic_tool_messages(
    *,
    messages: list[dict[str, Any]],
    synthetic_tool_messages: list[dict[str, Any]],
    start_index: int = 0,
) -> list[dict[str, Any]]:
    base_messages = [dict(message) for message in messages if isinstance(message, dict)]
    if not synthetic_tool_messages:
        return base_messages

    seen = {
        _tool_message_identity_key(message)
        for message in base_messages
        if _tool_message_identity_key(message)
    }
    pending: list[dict[str, Any]] = []
    for message in synthetic_tool_messages:
        if not isinstance(message, dict):
            continue
        normalized = dict(message)
        message_key = _tool_message_identity_key(normalized)
        if message_key and message_key in seen:
            continue
        if message_key:
            seen.add(message_key)
        pending.append(normalized)
    if not pending:
        return base_messages

    insertion_index = len(base_messages)
    safe_start = max(0, min(int(start_index), len(base_messages)))
    for index in range(safe_start, len(base_messages)):
        message = base_messages[index]
        role = str(message.get("role", "") or "").strip().lower()
        if role != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            continue
        insertion_index = index
        break

    return [
        *base_messages[:insertion_index],
        *pending,
        *base_messages[insertion_index:],
    ]


def _tool_message_identity_key(message: dict[str, Any]) -> str:
    role = str(message.get("role", "") or "").strip().lower()
    if role not in _TOOL_RESULT_ROLES:
        return ""
    tool_call_id = str(message.get("tool_call_id", "") or message.get("id", "")).strip()
    if tool_call_id:
        return f"id:{tool_call_id}"
    tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
    content_signature = _stable_payload_signature(message.get("content"))
    if tool_name or content_signature:
        return f"name:{tool_name}|content:{content_signature}"
    return ""


def _stable_payload_signature(payload: Any) -> str:
    if payload is None:
        return ""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(payload)
