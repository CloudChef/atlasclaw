# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""History normalization and long-term memory coordination for agent runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.atlasclaw.agent.compaction import CompactionPipeline
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import has_hidden_lookup_result_content
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.user_paths import user_runtime_dir


class HistoryMemoryCoordinator:
    """Encapsulates transcript conversion and long-term memory file management."""

    COMPACTION_SUMMARY_PREFIX = "[Compression Summary - Earlier conversation has been summarized]"
    MEMORY_RECALL_PREFIX = "[Long-term Memory Recall]"

    def __init__(self, session_manager: Any, compaction: CompactionPipeline) -> None:
        self.sessions = session_manager
        self.compaction = compaction

    def normalize_messages(self, messages: list[Any]) -> list[dict]:
        """Normalize agent messages into session-manager dictionaries."""
        normalized: list[dict] = []
        for msg in messages or []:
            if isinstance(msg, dict):
                item = dict(msg)
                item.setdefault("role", "assistant")
                item.setdefault("content", "")
                role = str(item.get("role", "") or "").strip().lower()
                if role == "tool":
                    tool_name = str(item.get("tool_name", "") or item.get("name", "")).strip()
                    item["content"] = self._sanitize_tool_content_for_runtime_message(
                        tool_name=tool_name,
                        content=item.get("content"),
                    )
                normalized.append(item)
                continue

            expanded = self._expand_structured_message(msg)
            if expanded:
                normalized.extend(expanded)
                continue

            role = self._extract_message_role(msg)
            content = self._extract_message_content(msg)
            item = {
                "role": str(role),
                "content": content if isinstance(content, str) else str(content),
            }
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                normalized_tool_calls = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        normalized_tool_calls.append(tc)
                    else:
                        normalized_tool_calls.append(
                            {
                                "id": getattr(tc, "id", ""),
                                "name": getattr(tc, "name", getattr(tc, "tool_name", "")),
                                "args": getattr(tc, "args", {}),
                            }
                        )
                item["tool_calls"] = normalized_tool_calls
            normalized.append(item)
        return normalized

    def _expand_structured_message(self, msg: Any) -> list[dict]:
        """Expand structured request/response messages into transcript-safe items."""
        kind = getattr(msg, "kind", "")
        parts = getattr(msg, "parts", None) or []
        if not parts:
            return []

        if kind == "request":
            expanded: list[dict] = []
            for part in parts:
                part_kind = getattr(part, "part_kind", "")
                part_content = getattr(part, "content", None)
                if part_kind == "system-prompt":
                    if not part_content:
                        continue
                    expanded.append({"role": "system", "content": str(part_content)})
                    continue
                if part_kind == "user-prompt":
                    if not part_content:
                        continue
                    expanded.append({"role": "user", "content": str(part_content)})
                    continue
                if part_kind in {"tool-return", "tool_return", "tool-result", "tool_result"}:
                    tool_name = str(getattr(part, "tool_name", getattr(part, "name", "")) or "").strip()
                    tool_call_id = str(
                        getattr(
                            part,
                            "tool_call_id",
                            getattr(part, "toolCallId", getattr(part, "id", "")),
                        )
                        or ""
                    ).strip()
                    payload = part_content
                    if payload is None:
                        payload = str(getattr(part, "text", "") or "").strip()
                    if payload is None:
                        continue
                    if isinstance(payload, str) and not payload.strip():
                        continue
                    payload = self._sanitize_tool_content_for_runtime_message(
                        tool_name=tool_name,
                        content=payload,
                    )
                    item = {
                        "role": "tool",
                        "content": payload,
                    }
                    if tool_name:
                        item["tool_name"] = tool_name
                    if tool_call_id:
                        item["tool_call_id"] = tool_call_id
                    expanded.append(item)
            return expanded

        if kind == "response":
            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for part in parts:
                part_kind = getattr(part, "part_kind", "")
                part_content = getattr(part, "content", None)
                if part_kind == "thinking":
                    continue
                if part_kind in {"text", ""}:
                    if part_content:
                        text_chunks.append(str(part_content))
                    continue
                if part_kind in {"tool-call", "tool_call"}:
                    tool_calls.append(
                        {
                            "id": str(getattr(part, "tool_call_id", getattr(part, "id", "")) or "").strip(),
                            "name": str(getattr(part, "tool_name", getattr(part, "name", "")) or "").strip(),
                            "args": getattr(part, "args", getattr(part, "arguments", {})) or {},
                        }
                    )
            content = "".join(text_chunks).strip()
            if content or tool_calls:
                item: dict[str, Any] = {"role": "assistant", "content": content}
                if tool_calls:
                    item["tool_calls"] = tool_calls
                return [item]
        return []

    def build_message_history(self, transcript: list[Any]) -> list[dict]:
        """Convert transcript entries into normalized messages."""
        messages = []
        pending_tool_calls: list[dict[str, Any]] = []
        for entry in transcript:
            msg = {
                "role": entry.role,
                "content": entry.content,
            }
            tool_name = str(getattr(entry, "tool_name", "") or "").strip()
            tool_call_id = str(getattr(entry, "tool_call_id", "") or "").strip()
            if tool_name:
                msg["tool_name"] = tool_name
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id
            if entry.tool_calls:
                msg["tool_calls"] = entry.tool_calls
                for tool_call in entry.tool_calls:
                    normalized = self._normalize_tool_call(tool_call)
                    if normalized is not None:
                        pending_tool_calls.append(normalized)
            if entry.tool_results:
                msg["tool_results"] = entry.tool_results
            if entry.metadata:
                msg["metadata"] = entry.metadata
            if str(entry.role).strip().lower() == "tool":
                inferred_tool_name, inferred_tool_call_id = self._infer_tool_message_identity(
                    message=msg,
                    pending_tool_calls=pending_tool_calls,
                )
                if inferred_tool_name and not msg.get("tool_name"):
                    msg["tool_name"] = inferred_tool_name
                if inferred_tool_call_id and not msg.get("tool_call_id"):
                    msg["tool_call_id"] = inferred_tool_call_id
                self._consume_pending_tool_call(
                    pending_tool_calls=pending_tool_calls,
                    tool_name=str(msg.get("tool_name", "") or "").strip(),
                    tool_call_id=str(msg.get("tool_call_id", "") or "").strip(),
                )
            messages.append(msg)
        return self._strip_unmatched_tool_calls(messages)

    def to_model_message_history(self, messages: list[dict]) -> list[Any]:
        """Convert normalized transcript messages into PydanticAI model messages."""
        model_messages: list[Any] = []
        pending_tool_calls: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            content = message.get("content", "")

            if role == "user":
                content_text = str(content).strip()
                if content_text:
                    model_messages.append(ModelRequest(parts=[UserPromptPart(content=content_text)]))
                continue

            if role == "system":
                content_text = str(content).strip()
                if content_text:
                    model_messages.append(ModelRequest(parts=[SystemPromptPart(content=content_text)]))
                continue

            if role == "assistant":
                response_parts: list[Any] = []
                content_text = str(content).strip()
                if content_text:
                    response_parts.append(TextPart(content=content_text))
                for tool_call in message.get("tool_calls", []) or []:
                    normalized = self._normalize_tool_call(tool_call)
                    if normalized is None:
                        continue
                    tool_name = str(normalized.get("name", "")).strip()
                    tool_args = normalized.get("args", {}) or {}
                    tool_call_id = str(normalized.get("id", "")).strip()
                    if tool_call_id:
                        response_parts.append(
                            ToolCallPart(tool_name, tool_args, tool_call_id=tool_call_id)
                        )
                    else:
                        response_parts.append(ToolCallPart(tool_name, tool_args))
                    pending_tool_calls.append(normalized)
                if response_parts:
                    model_messages.append(ModelResponse(parts=response_parts))
                continue

            if role == "tool":
                synthetic_parts = self._build_synthetic_tool_call_parts_for_orphan_returns(
                    message=message,
                    pending_tool_calls=pending_tool_calls,
                )
                if synthetic_parts:
                    model_messages.append(ModelResponse(parts=synthetic_parts))
                request_parts = self._build_tool_return_parts(message)
                if request_parts:
                    model_messages.append(ModelRequest(parts=request_parts))
        return model_messages

    @staticmethod
    def _normalize_tool_call(tool_call: Any) -> dict[str, Any] | None:
        """Normalize transcript tool-call payloads for model history replay."""
        if tool_call is None:
            return None
        if isinstance(tool_call, dict):
            tool_name = str(tool_call.get("name", tool_call.get("tool_name", "")) or "").strip()
            if not tool_name:
                return None
            normalized: dict[str, Any] = {
                "name": tool_name,
                "args": tool_call.get("args", tool_call.get("arguments", {})) or {},
            }
            tool_call_id = str(
                tool_call.get("id", tool_call.get("tool_call_id", tool_call.get("toolCallId", ""))) or ""
            ).strip()
            if tool_call_id:
                normalized["id"] = tool_call_id
            return normalized

        tool_name = str(getattr(tool_call, "name", getattr(tool_call, "tool_name", "")) or "").strip()
        if not tool_name:
            return None
        normalized = {
            "name": tool_name,
            "args": getattr(tool_call, "args", getattr(tool_call, "arguments", {})) or {},
        }
        tool_call_id = str(
            getattr(tool_call, "id", getattr(tool_call, "tool_call_id", getattr(tool_call, "toolCallId", "")))
            or ""
        ).strip()
        if tool_call_id:
            normalized["id"] = tool_call_id
        return normalized

    def _build_tool_return_parts(self, message: dict[str, Any]) -> list[ToolReturnPart]:
        """Convert persisted tool transcript messages into structured ToolReturnPart values."""
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
        tool_call_id = str(message.get("tool_call_id", "") or message.get("id", "")).strip()
        content = self._normalize_tool_content_for_model(
            tool_name=tool_name,
            content=message.get("content", ""),
        )

        parts: list[ToolReturnPart] = []
        if tool_name:
            if tool_call_id:
                parts.append(ToolReturnPart(tool_name, content, tool_call_id=tool_call_id))
            else:
                parts.append(ToolReturnPart(tool_name, content))

        for result in message.get("tool_results", []) or []:
            if not isinstance(result, dict):
                continue
            result_tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
            if not result_tool_name:
                continue
            result_call_id = str(
                result.get("tool_call_id", result.get("toolCallId", result.get("id", ""))) or ""
            ).strip()
            result_content = self._normalize_tool_content_for_model(
                tool_name=result_tool_name,
                content=result.get("content", ""),
            )
            if result_call_id:
                parts.append(ToolReturnPart(result_tool_name, result_content, tool_call_id=result_call_id))
            else:
                parts.append(ToolReturnPart(result_tool_name, result_content))
        return parts

    def _build_synthetic_tool_call_parts_for_orphan_returns(
        self,
        *,
        message: dict[str, Any],
        pending_tool_calls: list[dict[str, Any]],
    ) -> list[ToolCallPart]:
        """Insert synthetic tool-call parts when persisted history lost the assistant call row."""
        parts: list[ToolCallPart] = []
        for tool_name, tool_call_id in self._extract_tool_return_identities(message):
            if self._consume_pending_model_tool_call(
                pending_tool_calls=pending_tool_calls,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            ):
                continue
            if not tool_name:
                continue
            if tool_call_id:
                parts.append(ToolCallPart(tool_name, {}, tool_call_id=tool_call_id))
            else:
                parts.append(ToolCallPart(tool_name, {}))
        return parts

    @staticmethod
    def _extract_tool_return_identities(message: dict[str, Any]) -> list[tuple[str, str]]:
        """Collect tool return identities from one persisted transcript message."""
        identities: list[tuple[str, str]] = []
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
        tool_call_id = str(message.get("tool_call_id", "") or message.get("id", "")).strip()
        if tool_name or tool_call_id:
            identities.append((tool_name, tool_call_id))

        for result in message.get("tool_results", []) or []:
            if not isinstance(result, dict):
                continue
            result_tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
            result_call_id = str(
                result.get("tool_call_id", result.get("toolCallId", result.get("id", ""))) or ""
            ).strip()
            if result_tool_name or result_call_id:
                identities.append((result_tool_name, result_call_id))
        return identities

    @staticmethod
    def _consume_pending_model_tool_call(
        *,
        pending_tool_calls: list[dict[str, Any]],
        tool_name: str,
        tool_call_id: str,
    ) -> bool:
        """Consume a previously seen assistant tool call when a matching tool return arrives."""
        if not pending_tool_calls:
            return False
        if tool_call_id:
            for index, pending in enumerate(pending_tool_calls):
                pending_id = str(pending.get("id", "") or "").strip()
                if pending_id and pending_id == tool_call_id:
                    pending_tool_calls.pop(index)
                    return True
        if tool_name:
            for index, pending in enumerate(pending_tool_calls):
                pending_name = str(pending.get("name", "") or "").strip()
                if pending_name == tool_name:
                    pending_tool_calls.pop(index)
                    return True
        return False

    @staticmethod
    def _sanitize_tool_content_for_runtime_message(*, tool_name: str, content: Any) -> Any:
        """Hide raw lookup scaffolding while keeping workflow metadata for same-turn continuation."""
        del tool_name
        if not isinstance(content, dict):
            return content

        if not has_hidden_lookup_result_content(content):
            return content

        normalized = dict(content)
        normalized.pop("output", None)
        return normalized

    @staticmethod
    def _normalize_tool_content_for_model(*, tool_name: str, content: Any) -> Any:
        """Drop runtime-only metadata before replaying tool content to the model."""
        del tool_name
        if not isinstance(content, dict):
            return content

        if "_internal" not in content:
            return content

        normalized = dict(content)
        hide_output = has_hidden_lookup_result_content(content)
        normalized.pop("_internal", None)
        normalized.pop("_lookup_output_hidden", None)
        if hide_output:
            normalized.pop("output", None)
        return normalized

    def _infer_tool_message_identity(
        self,
        *,
        message: dict[str, Any],
        pending_tool_calls: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Best-effort repair for legacy transcripts that persisted tool rows without identity fields."""
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
        tool_call_id = str(message.get("tool_call_id", "") or message.get("id", "")).strip()
        if tool_name or tool_call_id:
            return tool_name, tool_call_id

        for result in message.get("tool_results", []) or []:
            if not isinstance(result, dict):
                continue
            result_tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
            result_call_id = str(
                result.get("tool_call_id", result.get("toolCallId", result.get("id", ""))) or ""
            ).strip()
            if result_tool_name or result_call_id:
                return result_tool_name, result_call_id

        if len(pending_tool_calls) == 1:
            only_call = pending_tool_calls[0]
            return (
                str(only_call.get("name", "") or "").strip(),
                str(only_call.get("id", "") or "").strip(),
            )

        for pending in pending_tool_calls:
            pending_name = str(pending.get("name", "") or "").strip()
            pending_id = str(pending.get("id", "") or "").strip()
            if pending_name or pending_id:
                return pending_name, pending_id
        return "", ""

    @staticmethod
    def _consume_pending_tool_call(
        *,
        pending_tool_calls: list[dict[str, Any]],
        tool_name: str,
        tool_call_id: str,
    ) -> None:
        """Remove the pending tool call satisfied by the current tool transcript row."""
        if not pending_tool_calls:
            return
        if tool_call_id:
            for index, pending in enumerate(pending_tool_calls):
                pending_id = str(pending.get("id", "") or "").strip()
                if pending_id == tool_call_id:
                    pending_tool_calls.pop(index)
                    return
        if tool_name:
            for index, pending in enumerate(pending_tool_calls):
                pending_name = str(pending.get("name", "") or "").strip()
                if pending_name == tool_name:
                    pending_tool_calls.pop(index)
                    return

    def _strip_unmatched_tool_calls(self, messages: list[dict]) -> list[dict]:
        """Remove unresolved assistant tool calls before replaying transcript to the model."""
        pending_tool_calls: list[dict[str, Any]] = []
        matched_keys: set[str] = set()
        tool_call_counter = 0

        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            if role == "assistant":
                for tool_call in message.get("tool_calls", []) or []:
                    normalized = self._normalize_tool_call(tool_call)
                    if normalized is None:
                        continue
                    normalized["match_key"] = self._tool_call_match_key(normalized, tool_call_counter)
                    pending_tool_calls.append(normalized)
                    tool_call_counter += 1

            if role == "tool":
                inferred_tool_name, inferred_tool_call_id = self._infer_tool_message_identity(
                    message=message,
                    pending_tool_calls=pending_tool_calls,
                )
                if inferred_tool_name and not message.get("tool_name"):
                    message["tool_name"] = inferred_tool_name
                if inferred_tool_call_id and not message.get("tool_call_id"):
                    message["tool_call_id"] = inferred_tool_call_id
                matched_key = self._consume_pending_tool_call_with_key(
                    pending_tool_calls=pending_tool_calls,
                    tool_name=str(message.get("tool_name", "") or "").strip(),
                    tool_call_id=str(message.get("tool_call_id", "") or "").strip(),
                )
                if matched_key:
                    matched_keys.add(matched_key)

        sanitized: list[dict] = []
        tool_call_counter = 0
        for message in messages:
            if str(message.get("role", "")).strip().lower() != "assistant":
                sanitized.append(message)
                continue

            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                if str(message.get("content", "") or "").strip():
                    sanitized.append(message)
                continue

            filtered_tool_calls: list[dict[str, Any]] = []
            for tool_call in tool_calls:
                normalized = self._normalize_tool_call(tool_call)
                if normalized is None:
                    continue
                match_key = self._tool_call_match_key(normalized, tool_call_counter)
                tool_call_counter += 1
                if match_key in matched_keys:
                    filtered_tool_calls.append(tool_call)

            if filtered_tool_calls or str(message.get("content", "") or "").strip():
                updated = dict(message)
                if filtered_tool_calls:
                    updated["tool_calls"] = filtered_tool_calls
                else:
                    updated.pop("tool_calls", None)
                sanitized.append(updated)

        return sanitized

    @staticmethod
    def _tool_call_match_key(tool_call: dict[str, Any], sequence_index: int) -> str:
        """Build a stable matching key for tool call / tool result pairing."""
        tool_call_id = str(tool_call.get("id", "") or "").strip()
        tool_name = str(tool_call.get("name", "") or "").strip()
        if tool_call_id:
            return f"id:{tool_call_id}"
        return f"seq:{sequence_index}:{tool_name}"

    @staticmethod
    def _consume_pending_tool_call_with_key(
        *,
        pending_tool_calls: list[dict[str, Any]],
        tool_name: str,
        tool_call_id: str,
    ) -> str:
        """Remove and return the pending tool-call match key satisfied by a tool row."""
        if not pending_tool_calls:
            return ""
        if tool_call_id:
            for index, pending in enumerate(pending_tool_calls):
                pending_id = str(pending.get("id", "") or "").strip()
                if pending_id == tool_call_id:
                    return str(pending_tool_calls.pop(index).get("match_key", "") or "")
        if tool_name:
            for index, pending in enumerate(pending_tool_calls):
                pending_name = str(pending.get("name", "") or "").strip()
                if pending_name == tool_name:
                    return str(pending_tool_calls.pop(index).get("match_key", "") or "")
        if len(pending_tool_calls) == 1:
            return str(pending_tool_calls.pop(0).get("match_key", "") or "")
        return ""

    def prune_summary_messages(self, messages: list[dict]) -> list[dict]:
        """Remove previously injected summary/recall system messages from session context."""
        pruned: list[dict] = []
        for msg in messages:
            if msg.get("role") != "system":
                pruned.append(msg)
                continue
            content = str(msg.get("content", ""))
            if content.startswith(self.COMPACTION_SUMMARY_PREFIX):
                continue
            if content.startswith(self.MEMORY_RECALL_PREFIX):
                continue
            pruned.append(msg)
        return pruned

    async def flush_history_to_timestamped_memory(
        self,
        *,
        session_key: str,
        messages: list[dict],
        deps: SkillDeps,
        session: Any,
        context_window: Optional[int],
        flushed_signatures: set[str],
    ) -> None:
        """Summarize overflow history and write to workspace/users/<userId>/memory/memory_<timestamp>.md."""
        summary = await self.compaction.summarize_overflow(messages)
        summary = summary.strip()
        if not summary:
            return

        signature = summary[:500]
        if signature in flushed_signatures:
            return
        flushed_signatures.add(signature)

        user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
        workspace_root = Path(str(getattr(self.sessions, "workspace_path", "."))).resolve()
        user_memory_dir = user_runtime_dir(workspace_root, user_id) / "memory"
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = user_memory_dir / f"memory_{file_timestamp}.md"

        estimated_tokens = self.compaction.estimate_tokens(messages)
        lines = [
            "# Memory Snapshot",
            "",
            f"- timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
            f"- user_id: {user_id}",
            f"- session_key: {session_key}",
            f"- estimated_tokens_before: {estimated_tokens}",
            f"- context_window: {context_window or self.compaction.config.context_window}",
            "",
            "## Summary",
            "",
            summary,
            "",
        ]
        payload = "\n".join(lines)

        def _write() -> None:
            user_memory_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write)

        if hasattr(session, "memory_flushed_this_cycle"):
            session.memory_flushed_this_cycle = True

    async def inject_memory_recall(self, messages: list[dict], deps: SkillDeps) -> list[dict]:
        """Load recent memory_*.md files and inject one recall system message."""
        user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
        workspace_root = Path(str(getattr(self.sessions, "workspace_path", "."))).resolve()
        user_memory_dir = user_runtime_dir(workspace_root, user_id) / "memory"

        def _read_recent() -> list[tuple[str, str]]:
            if not user_memory_dir.exists():
                return []
            files = sorted(user_memory_dir.glob("memory_*.md"), reverse=True)[:3]
            result: list[tuple[str, str]] = []
            for fp in files:
                try:
                    text = fp.read_text(encoding="utf-8").strip()
                except Exception:
                    continue
                if not text:
                    continue
                result.append((fp.name, text[:1200]))
            return result

        recent = await asyncio.to_thread(_read_recent)
        if not recent:
            return messages

        recall_lines = [self.MEMORY_RECALL_PREFIX, ""]
        for name, excerpt in recent:
            recall_lines.append(f"### {name}")
            recall_lines.append(excerpt)
            recall_lines.append("")

        recall_message = {
            "role": "system",
            "content": "\n".join(recall_lines).strip(),
        }

        cleaned: list[dict] = []
        for msg in messages:
            if msg.get("role") == "system" and str(msg.get("content", "")).startswith(self.MEMORY_RECALL_PREFIX):
                continue
            cleaned.append(msg)
        if cleaned and cleaned[0].get("role") == "system":
            return [cleaned[0], recall_message, *cleaned[1:]]
        return [recall_message, *cleaned]

    def _extract_message_role(self, msg: Any) -> str:
        role = getattr(msg, "role", None)
        if isinstance(role, str) and role:
            return role

        kind = getattr(msg, "kind", "")
        if kind == "request":
            parts = getattr(msg, "parts", None) or []
            if any(getattr(part, "part_kind", "") == "system-prompt" for part in parts):
                return "system"
            return "user"
        if kind == "response":
            return "assistant"
        return "assistant"

    def _extract_message_content(self, msg: Any) -> str:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content

        parts = getattr(msg, "parts", None)
        if not parts:
            return "" if content is None else str(content)

        chunks: list[str] = []
        for part in parts:
            part_kind = getattr(part, "part_kind", "")
            part_content = getattr(part, "content", None)
            if part_kind == "thinking":
                continue
            if part_kind in {"text", "user-prompt", "system-prompt", ""}:
                if isinstance(part_content, str) and part_content:
                    chunks.append(part_content)
                elif isinstance(part_content, (list, tuple)):
                    chunks.extend(str(item) for item in part_content if item)
                elif part_content:
                    chunks.append(str(part_content))
        return "".join(chunks)
