# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Automatic transcript compaction pipeline.

The pipeline monitors estimated context usage and compacts older transcript
segments when the conversation approaches the configured token budget.

Compaction flow:
1. Optionally trigger a memory-flush reminder before compaction
2. Select the older portion of the transcript for compression
3. Generate a summary with an LLM or fallback summarizer
4. Rebuild the message list from the system prompt, summary, and recent turns

Related pruning concepts:
- soft trim: keep the head and tail while dropping part of the middle
- hard clear: aggressively clear transcript state when limits are exceeded
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Optional, Any, Callable, Awaitable

from app.atlasclaw.agent.compaction_safeguard import (
    build_safeguarded_summary,
    load_workspace_critical_rules,
)

BASE_CHUNK_RATIO = 0.4
MIN_CHUNK_RATIO = 0.15
SAFETY_MARGIN = 1.2
DEFAULT_STAGED_SUMMARY_PARTS = 2
DEFAULT_STAGED_SUMMARY_MIN_MESSAGES = 4
DEFAULT_SUMMARY_MERGE_INSTRUCTIONS = (
    "Merge partial summaries into one cohesive summary. "
    "Preserve decisions, constraints, unresolved issues, and tool failures."
)
TOOL_RESULT_ROLES = {"tool", "toolresult", "tool_result"}
TOOL_DETAIL_FIELD_MARKERS = {
    "detail",
    "details",
    "raw",
    "payload",
    "body",
    "html",
    "full_text",
    "fulltext",
    "trace",
    "stack",
    "stdout",
    "stderr",
}
TOOL_CALL_ID_FIELDS = ("id", "tool_call_id", "toolCallId", "call_id", "callId")
TOOL_RESULT_CALL_ID_FIELDS = ("tool_call_id", "toolCallId", "call_id", "callId", "id")
MAX_TOOL_SUMMARY_TEXT_CHARS = 900
MAX_TOOL_SUMMARY_CONTAINER_ITEMS = 12
MAX_TOOL_SUMMARY_DEPTH = 2


@dataclass
class CompactionConfig:
    """Configuration for automatic transcript compaction.

    Attributes:
        reserve_tokens_floor: Token budget reserved as a hard floor.
        soft_threshold_tokens: Buffer before the hard floor used by callers
            that need an early compaction warning.
        context_window: Total model context window size.
        keep_recent_turns: Number of recent user/assistant turns to preserve.
        keep_last_assistants: Number of recent assistant messages to preserve.
        soft_trim_enabled: Whether soft-trim behavior is enabled.
        hard_clear_threshold: Character threshold for aggressive clearing.
    """
    reserve_tokens_floor: int = 20000
    soft_threshold_tokens: int = 4000
    context_window: int = 128000
    keep_recent_turns: int = 3
    keep_last_assistants: int = 3
    soft_trim_enabled: bool = True
    hard_clear_threshold: int = 10000
    safeguard_enabled: bool = True
    max_history_share: float = 0.5
    staged_summary_parts: int = DEFAULT_STAGED_SUMMARY_PARTS
    staged_summary_min_messages: int = DEFAULT_STAGED_SUMMARY_MIN_MESSAGES
    staged_summary_overhead_tokens: int = 512
    staged_summary_min_chunk_tokens: int = 256
    workspace_path: Optional[str] = None
    safeguard_workspace_sections: tuple[str, ...] = ("Session Startup", "Red Lines")
    safeguard_workspace_max_chars: int = 2_000


@dataclass
class HistorySharePruningResult:
    """Result of history-share pruning before summary generation."""

    messages: list[dict]
    dropped_messages: list[dict]
    dropped_chunks: int
    budget_tokens: int


class CompactionPipeline:
    """Compact older transcript segments when context usage grows too large.

    Example:
        ```python
        pipeline = CompactionPipeline(config, summarizer=llm_summarize)

        if pipeline.should_compact(messages, session):
            new_messages = await pipeline.compact(messages, session)
        ```
    """
    
    def __init__(
        self,
        config: CompactionConfig,
        summarizer: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    ):
        """Initialize the compaction pipeline.

        Args:
            config: Compaction configuration.
            summarizer: Optional async summary generator for message batches.
        """
        self.config = config
        self._summarizer = summarizer
    
    def estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token usage for a normalized message list.

        This heuristic uses roughly four characters per token and includes
        textual tool-call payloads.
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Count text blocks inside multimodal content arrays.
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])
            
            # Include serialized tool-call payloads in the estimate.
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                total_chars += len(str(tc))
        
        return total_chars // 4
    
    def _resolve_context_window(self, context_window_override: Optional[int] = None) -> int:
        """Resolve effective context window for the current evaluation."""
        if context_window_override and context_window_override > 0:
            return context_window_override
        return self.config.context_window

    def get_available_tokens(self, context_window_override: Optional[int] = None) -> int:
        """Return the token budget available before the soft threshold."""
        context_window = self._resolve_context_window(context_window_override)
        return (
            context_window
            - self.config.reserve_tokens_floor
            - self.config.soft_threshold_tokens
        )
    
    def should_compact(
        self,
        messages: list[dict],
        session: Any = None,
        *,
        context_window_override: Optional[int] = None,
    ) -> bool:
        """Return whether the transcript should be compacted now."""
        estimated = self.estimate_tokens(messages)
        context_window = self._resolve_context_window(context_window_override)
        threshold = context_window - self.config.reserve_tokens_floor
        return estimated > threshold
    
    def _split_for_compaction(self, messages: list[dict]) -> tuple[Optional[dict], list[dict], list[dict]]:
        """Split messages into system prompt, recent messages, and compressible history."""
        system_prompt = messages[0] if messages and messages[0].get("role") == "system" else None

        keep_count = self.config.keep_recent_turns * 2
        recent_messages = messages[-keep_count:] if keep_count > 0 else []

        start_idx = 1 if system_prompt else 0
        end_idx = len(messages) - keep_count if keep_count > 0 else len(messages)
        to_compress = messages[start_idx:end_idx]
        return system_prompt, recent_messages, to_compress

    async def summarize_overflow(self, messages: list[dict]) -> str:
        """Generate a summary for the overflow section only (without rebuilding transcript)."""
        if len(messages) <= self.config.keep_recent_turns * 2 + 1:
            return ""
        _, _, to_compress = self._split_for_compaction(messages)
        if not to_compress:
            return ""
        return await self._generate_summary(to_compress)
    
    async def compact(
        self,
        messages: list[dict],
        session: Any = None,
    ) -> list[dict]:
        """Compact the transcript and return a rebuilt message list."""
        if len(messages) <= self.config.keep_recent_turns * 2 + 1:
            # Not enough history to compact meaningfully.
            return messages

        # 1. Separate the system prompt from compressible history.
        system_prompt, recent_messages, to_compress = self._split_for_compaction(messages)
        
        if not to_compress:
            return messages
        
        # 2. Generate a summary for the older portion.
        try:
            summary = await self._generate_summary(to_compress)
        except Exception:
            # Fail-safe: keep original transcript if compaction summarization fails.
            return messages

        # 3. Rebuild the transcript from the summary and recent turns.
        result = []
        if system_prompt:
            result.append(system_prompt)
        
        # Insert the generated summary as a synthetic system message.
        result.append({
            "role": "system",
            "content": f"[Compression Summary - Earlier conversation has been summarized]\n{summary}",
        })

        # Preserve the recent conversation verbatim.
        result.extend(recent_messages)
        
        return result
    
    async def _generate_summary(self, messages: list[dict]) -> str:
        """Generate a summary for the provided message batch."""
        if not messages:
            return "(No content)"

        summary_messages = self._prepare_messages_for_summary(messages)
        pruning = self._prune_history_for_context_share(summary_messages)
        retained_messages = pruning.messages

        dropped_summary = ""
        if pruning.dropped_messages:
            dropped_summary = await self._summarize_in_stages(pruning.dropped_messages)

        retained_summary = (
            await self._summarize_in_stages(retained_messages)
            if retained_messages
            else "(No retained history)"
        )
        if dropped_summary:
            summary = (
                "## Older History\n"
                f"{dropped_summary}\n\n"
                "## Retained History\n"
                f"{retained_summary}"
            )
        else:
            summary = retained_summary

        if self.config.safeguard_enabled:
            workspace_rules = self._load_workspace_rules_for_safeguard()
            return build_safeguarded_summary(
                messages=summary_messages,
                base_summary=summary,
                workspace_critical_rules=workspace_rules,
            )
        return summary

    def _load_workspace_rules_for_safeguard(self) -> str:
        return load_workspace_critical_rules(
            workspace_path=self.config.workspace_path,
            section_titles=self.config.safeguard_workspace_sections,
            max_chars=self.config.safeguard_workspace_max_chars,
        )

    async def _summarize_in_stages(self, messages: list[dict]) -> str:
        if not messages:
            return "(No content)"

        total_tokens = self.estimate_tokens(messages)
        parts = self._normalize_parts(self.config.staged_summary_parts, len(messages))
        min_messages = max(2, int(self.config.staged_summary_min_messages or 0))
        max_chunk_tokens = self._resolve_max_chunk_tokens(messages)

        if parts <= 1 or len(messages) < min_messages or total_tokens <= max_chunk_tokens:
            return await self._summarize_once(messages)

        split_chunks = [chunk for chunk in self._split_messages_by_token_share(messages, parts=parts) if chunk]
        if len(split_chunks) <= 1:
            return await self._summarize_once(messages)

        partial_summaries: list[str] = []
        for chunk in split_chunks:
            chunk_blocks = self._chunk_messages_by_max_tokens(chunk, max_chunk_tokens=max_chunk_tokens)
            if len(chunk_blocks) <= 1:
                partial_summaries.append(await self._summarize_once(chunk))
                continue

            block_summaries: list[str] = []
            for block in chunk_blocks:
                block_summaries.append(await self._summarize_once(block))
            merged_chunk = await self._summarize_once(self._build_merge_messages(block_summaries))
            partial_summaries.append(merged_chunk)

        if len(partial_summaries) == 1:
            return partial_summaries[0]
        return await self._summarize_once(self._build_merge_messages(partial_summaries))

    async def _summarize_once(self, messages: list[dict]) -> str:
        if self._summarizer:
            summary = await self._summarizer(messages)
            normalized = str(summary or "").strip()
            if normalized:
                return normalized

        summary_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                preview = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"- [{role}]: {preview}")
        return "\n".join(summary_parts) if summary_parts else "(No content)"

    def _resolve_max_chunk_tokens(self, messages: list[dict]) -> int:
        context_window = max(1, int(self.config.context_window or 1))
        adaptive_ratio = self._compute_adaptive_chunk_ratio(messages, context_window)
        base_chunk_tokens = max(1, int(context_window * adaptive_ratio))
        overhead = max(0, int(self.config.staged_summary_overhead_tokens or 0))
        effective_overhead = min(overhead, max(0, base_chunk_tokens // 2))
        return max(
            max(1, int(self.config.staged_summary_min_chunk_tokens or 1)),
            base_chunk_tokens - effective_overhead,
        )

    def _compute_adaptive_chunk_ratio(self, messages: list[dict], context_window: int) -> float:
        if not messages or context_window <= 0:
            return BASE_CHUNK_RATIO
        total_tokens = self.estimate_tokens(messages)
        if total_tokens <= 0:
            return BASE_CHUNK_RATIO
        avg_tokens = float(total_tokens) / float(len(messages))
        avg_ratio = (avg_tokens * SAFETY_MARGIN) / float(context_window)
        if avg_ratio <= 0.1:
            return BASE_CHUNK_RATIO
        reduction = min(avg_ratio * 2.0, BASE_CHUNK_RATIO - MIN_CHUNK_RATIO)
        return max(MIN_CHUNK_RATIO, BASE_CHUNK_RATIO - reduction)

    def _split_messages_by_token_share(self, messages: list[dict], *, parts: int) -> list[list[dict]]:
        if not messages:
            return []
        normalized_parts = self._normalize_parts(parts, len(messages))
        if normalized_parts <= 1:
            return [list(messages)]

        total_tokens = max(1, self.estimate_tokens(messages))
        target_tokens = float(total_tokens) / float(normalized_parts)
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_tokens = 0

        for message in messages:
            message_tokens = max(1, self.estimate_tokens([message]))
            if (
                len(chunks) < normalized_parts - 1
                and current_chunk
                and float(current_tokens + message_tokens) > target_tokens
            ):
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            current_chunk.append(message)
            current_tokens += message_tokens

        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _chunk_messages_by_max_tokens(self, messages: list[dict], *, max_chunk_tokens: int) -> list[list[dict]]:
        if not messages:
            return []
        effective_max = max(1, int(float(max_chunk_tokens) / SAFETY_MARGIN))
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_tokens = 0

        for message in messages:
            message_tokens = max(1, self.estimate_tokens([message]))
            if current_chunk and current_tokens + message_tokens > effective_max:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            current_chunk.append(message)
            current_tokens += message_tokens

        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _normalize_parts(self, parts: int, message_count: int) -> int:
        if message_count <= 0:
            return 1
        if not isinstance(parts, int) or parts <= 1:
            return 1
        return min(max(1, parts), max(1, message_count))

    def _build_merge_messages(self, partial_summaries: list[str]) -> list[dict]:
        merge_messages: list[dict] = [
            {"role": "system", "content": DEFAULT_SUMMARY_MERGE_INSTRUCTIONS},
        ]
        for partial in partial_summaries:
            text = str(partial or "").strip()
            if not text:
                continue
            merge_messages.append({"role": "user", "content": text})
        return merge_messages

    def _prepare_messages_for_summary(self, messages: list[dict]) -> list[dict]:
        prepared: list[dict] = []
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            if role in TOOL_RESULT_ROLES:
                prepared.append(self._strip_tool_result_details_for_summary(message))
                continue
            prepared.append(message)
        return prepared

    def _strip_tool_result_details_for_summary(self, message: dict) -> dict:
        stripped: dict = {}
        for key in ("role", "tool_name", "name"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                stripped[key] = value
        call_id = self._extract_tool_result_call_id(message)
        if call_id:
            stripped["tool_call_id"] = call_id

        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            compact_metadata: dict = {}
            for key in ("status", "is_error", "error", "code", "reason"):
                if key in metadata:
                    compact_metadata[key] = metadata.get(key)
            if compact_metadata:
                stripped["metadata"] = compact_metadata

        stripped["content"] = self._compact_tool_payload_for_summary(message.get("content"))
        return stripped

    def _compact_tool_payload_for_summary(self, payload: Any) -> str:
        if isinstance(payload, str):
            normalized = " ".join(payload.split()).strip()
            if len(normalized) <= MAX_TOOL_SUMMARY_TEXT_CHARS:
                return normalized
            return f"{normalized[: MAX_TOOL_SUMMARY_TEXT_CHARS - 3]}..."

        compact = self._compact_tool_payload_structure(payload, depth=0)
        try:
            serialized = json.dumps(compact, ensure_ascii=False)
        except Exception:
            serialized = str(compact)
        if len(serialized) <= MAX_TOOL_SUMMARY_TEXT_CHARS:
            return serialized
        return f"{serialized[: MAX_TOOL_SUMMARY_TEXT_CHARS - 3]}..."

    def _compact_tool_payload_structure(self, value: Any, *, depth: int) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            normalized = " ".join(value.split()).strip()
            if len(normalized) <= MAX_TOOL_SUMMARY_TEXT_CHARS:
                return normalized
            return f"{normalized[: MAX_TOOL_SUMMARY_TEXT_CHARS - 3]}..."

        if depth >= MAX_TOOL_SUMMARY_DEPTH:
            if isinstance(value, dict):
                return f"{{{len(value)} fields omitted}}"
            if isinstance(value, list):
                return f"[{len(value)} items omitted]"
            return str(value)

        if isinstance(value, list):
            compact_list: list[Any] = []
            for item in value[:MAX_TOOL_SUMMARY_CONTAINER_ITEMS]:
                compact_list.append(self._compact_tool_payload_structure(item, depth=depth + 1))
            remaining = len(value) - len(compact_list)
            if remaining > 0:
                compact_list.append(f"... ({remaining} more items)")
            return compact_list

        if isinstance(value, dict):
            compact_dict: dict[str, Any] = {}
            for idx, (raw_key, raw_value) in enumerate(value.items()):
                if idx >= MAX_TOOL_SUMMARY_CONTAINER_ITEMS:
                    compact_dict["..."] = f"{len(value) - MAX_TOOL_SUMMARY_CONTAINER_ITEMS} more fields"
                    break
                key = str(raw_key)
                if key.strip().lower() in TOOL_DETAIL_FIELD_MARKERS:
                    compact_dict[key] = "[omitted]"
                    continue
                compact_dict[key] = self._compact_tool_payload_structure(raw_value, depth=depth + 1)
            return compact_dict

        return str(value)

    def _repair_tool_result_pairing(self, messages: list[dict]) -> list[dict]:
        if not messages:
            return messages

        known_tool_call_ids = self._collect_tool_call_ids(messages)
        if not known_tool_call_ids:
            return messages

        repaired: list[dict] = []
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            if role not in TOOL_RESULT_ROLES:
                repaired.append(message)
                continue
            tool_call_id = self._extract_tool_result_call_id(message)
            if tool_call_id and tool_call_id not in known_tool_call_ids:
                continue
            repaired.append(message)
        return repaired

    def _collect_tool_call_ids(self, messages: list[dict]) -> set[str]:
        call_ids: set[str] = set()
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            if role != "assistant":
                continue
            tool_calls = message.get("tool_calls", [])
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                call_id = self._extract_tool_call_id(tool_call)
                if call_id:
                    call_ids.add(call_id)
        return call_ids

    def _extract_tool_call_id(self, tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            for field in TOOL_CALL_ID_FIELDS:
                value = tool_call.get(field)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        for field in TOOL_CALL_ID_FIELDS:
            value = getattr(tool_call, field, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _extract_tool_result_call_id(self, message: dict) -> str:
        for field in TOOL_RESULT_CALL_ID_FIELDS:
            value = message.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _prune_history_for_context_share(self, messages: list[dict]) -> HistorySharePruningResult:
        if not messages:
            return HistorySharePruningResult(
                messages=[],
                dropped_messages=[],
                dropped_chunks=0,
                budget_tokens=0,
            )

        raw_share = float(self.config.max_history_share)
        max_history_share = min(1.0, max(0.1, raw_share))
        budget_tokens = max(1, int(float(self.config.context_window) * max_history_share))
        kept_messages = list(messages)
        dropped_messages: list[dict] = []
        dropped_chunks = 0

        while kept_messages and self.estimate_tokens(kept_messages) > budget_tokens:
            chunks = self._split_messages_by_token_share(
                kept_messages,
                parts=max(2, self._normalize_parts(self.config.staged_summary_parts, len(kept_messages))),
            )
            if len(chunks) <= 1:
                break
            dropped_chunk = chunks[0]
            if not dropped_chunk:
                break
            dropped_messages.extend(dropped_chunk)
            kept_messages = [item for chunk in chunks[1:] for item in chunk]
            dropped_chunks += 1

        kept_messages = self._repair_tool_result_pairing(kept_messages)

        return HistorySharePruningResult(
            messages=kept_messages,
            dropped_messages=dropped_messages,
            dropped_chunks=dropped_chunks,
            budget_tokens=budget_tokens,
        )
    
    def prune_tool_results(
        self,
        messages: list[dict],
        mode: str = "soft",
    ) -> list[dict]:
        """Prune tool results to save context space.
        
        Args:
            messages: Message list
            mode: Pruning mode (soft/hard)
            
        Returns:
            Pruned message list
        """
        result = []
        assistant_count = 0
        
        # Count assistant messages from the end
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                assistant_count += 1
        
        current_assistant = assistant_count
        
        for msg in messages:
            if msg.get("role") == "assistant":
                current_assistant -= 1
            
            # Keep recent messages
            if current_assistant < self.config.keep_last_assistants:
                result.append(msg)
                continue
            
            # Handle tool results
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                
                # Check if content contains images
                if isinstance(content, list):
                    has_image = any(
                        isinstance(p, dict) and p.get("type") == "image"
                        for p in content
                    )
                    if has_image:
                        result.append(msg)
                        continue
                
                # Prune large tool results
                if isinstance(content, str) and len(content) > self.config.hard_clear_threshold:
                    if mode == "hard":
                        # Hard clear: remove content entirely
                        msg = msg.copy()
                        msg["content"] = "[Tool result cleared to save context space]"
                    else:
                        # Soft trim: keep head and tail
                        msg = msg.copy()
                        head = content[:500]
                        tail = content[-200:]
                        original_size = len(content)
                        msg["content"] = f"{head}\n...\n{tail}\n[Original size: {original_size} characters]"
            
            result.append(msg)

        return result
