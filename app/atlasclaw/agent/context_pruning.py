# -*- coding: utf-8 -*-
"""Runtime context pruning for long conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

CHARS_PER_TOKEN_ESTIMATE = 4
IMAGE_CHAR_ESTIMATE = 8_000


@dataclass
class SoftTrimConfig:
    max_chars: int = 4_000
    head_chars: int = 1_500
    tail_chars: int = 1_500


@dataclass
class HardClearConfig:
    enabled: bool = True
    placeholder: str = "[Tool result cleared to save context space]"


@dataclass
class ContextPruningSettings:
    keep_last_assistants: int = 3
    soft_trim_ratio: float = 0.30
    hard_clear_ratio: float = 0.50
    min_prunable_tool_chars: int = 50_000
    soft_trim: SoftTrimConfig = field(default_factory=SoftTrimConfig)
    hard_clear: HardClearConfig = field(default_factory=HardClearConfig)


DEFAULT_CONTEXT_PRUNING_SETTINGS = ContextPruningSettings()


def _estimate_message_chars(message: dict) -> int:
    role = str(message.get("role", "")).strip().lower()
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                total += len(str(part))
                continue
            part_type = str(part.get("type", "")).strip().lower()
            if part_type == "text":
                total += len(str(part.get("text", "")))
            elif part_type == "image":
                total += IMAGE_CHAR_ESTIMATE
            else:
                total += len(str(part))
        return total
    if role == "assistant":
        return len(str(message))
    return len(str(content))


def _estimate_context_chars(messages: list[dict]) -> int:
    return sum(_estimate_message_chars(msg) for msg in messages)


def _find_assistant_cutoff_index(messages: list[dict], keep_last_assistants: int) -> Optional[int]:
    if keep_last_assistants <= 0:
        return len(messages)
    remaining = keep_last_assistants
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role", "")).strip().lower() != "assistant":
            continue
        remaining -= 1
        if remaining == 0:
            return index
    return None


def _first_user_index(messages: list[dict]) -> int:
    for index, message in enumerate(messages):
        if str(message.get("role", "")).strip().lower() == "user":
            return index
    return len(messages)


def _has_image_blocks(content: object) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and str(part.get("type", "")).strip().lower() == "image":
            return True
    return False


def _soft_trim_text(content: str, cfg: SoftTrimConfig) -> Optional[str]:
    if len(content) <= cfg.max_chars:
        return None
    if cfg.head_chars + cfg.tail_chars >= len(content):
        return None
    head = content[: cfg.head_chars]
    tail = content[-cfg.tail_chars :]
    return (
        f"{head}\n...\n{tail}\n"
        f"[Tool result trimmed: kept first {cfg.head_chars} chars and last {cfg.tail_chars} chars "
        f"of {len(content)} chars.]"
    )


def prune_context_messages(
    *,
    messages: list[dict],
    settings: Optional[ContextPruningSettings] = None,
    context_window_tokens: Optional[int] = None,
    is_tool_prunable: Optional[Callable[[str], bool]] = None,
) -> list[dict]:
    """Prune low-value tool payloads when context window pressure is high."""

    active_settings = settings or DEFAULT_CONTEXT_PRUNING_SETTINGS
    if not messages:
        return messages
    if context_window_tokens is None or context_window_tokens <= 0:
        return messages

    char_window = max(1, int(context_window_tokens) * CHARS_PER_TOKEN_ESTIMATE)
    total_chars = _estimate_context_chars(messages)
    ratio = float(total_chars) / float(char_window)
    if ratio < active_settings.soft_trim_ratio:
        return messages

    cutoff = _find_assistant_cutoff_index(messages, active_settings.keep_last_assistants)
    if cutoff is None:
        return messages

    prune_start = _first_user_index(messages)
    next_messages = list(messages)
    prunable_indexes: list[int] = []

    def _tool_allowed(tool_name: str) -> bool:
        if is_tool_prunable is None:
            return True
        return bool(is_tool_prunable(tool_name))

    for idx in range(prune_start, cutoff):
        msg = next_messages[idx]
        role = str(msg.get("role", "")).strip().lower()
        if role != "tool":
            continue
        metadata = msg.get("metadata", {})
        if isinstance(metadata, dict):
            if bool(metadata.get("is_error")) or str(metadata.get("status", "")).strip().lower() == "error":
                # Preserve failed tool context for postmortem and compaction safeguard.
                continue
        tool_name = str(msg.get("tool_name", "") or msg.get("name", "")).strip()
        if not _tool_allowed(tool_name):
            continue
        content = msg.get("content", "")
        if _has_image_blocks(content):
            continue
        if not isinstance(content, str):
            continue
        prunable_indexes.append(idx)
        trimmed = _soft_trim_text(content, active_settings.soft_trim)
        if trimmed is None:
            continue
        before_chars = len(content)
        msg_copy = dict(msg)
        msg_copy["content"] = trimmed
        next_messages[idx] = msg_copy
        total_chars += len(trimmed) - before_chars

    ratio = float(total_chars) / float(char_window)
    if ratio < active_settings.hard_clear_ratio:
        return next_messages
    if not active_settings.hard_clear.enabled:
        return next_messages

    total_prunable_chars = 0
    for idx in prunable_indexes:
        content = next_messages[idx].get("content", "")
        if isinstance(content, str):
            total_prunable_chars += len(content)
    if total_prunable_chars < active_settings.min_prunable_tool_chars:
        return next_messages

    for idx in prunable_indexes:
        msg = next_messages[idx]
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        msg_copy = dict(msg)
        msg_copy["content"] = active_settings.hard_clear.placeholder
        next_messages[idx] = msg_copy

    return next_messages
