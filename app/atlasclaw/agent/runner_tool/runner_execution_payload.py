from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app.atlasclaw.core.deps import SkillDeps


def build_finalize_payload(
    *,
    user_message: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, str]:
    """Build a minimal final-answer payload for a tool-backed turn."""
    evidence_lines: list[str] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip() or "tool"
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        evidence_lines.append(f"- {tool_name}: {content}")

    if not evidence_lines:
        evidence_lines.append("- tool: no tool output available")

    return {
        "system_prompt": (
            "You are AtlasClaw. Produce a concise markdown answer using only the supplied tool evidence. "
            "Do not fabricate facts or mention hidden reasoning. "
            "Do not add wrapper headings like 'Answer' or 'Result' unless the user explicitly asked for them."
        ),
        "user_prompt": (
            f"User request:\n{str(user_message or '').strip()}\n\n"
            f"Tool evidence:\n{chr(10).join(evidence_lines)}\n\n"
            "Return concise markdown. Use bullets or short paragraphs when helpful. "
            "If there are source links in the evidence, keep them as markdown links.\n"
        ),
    }


class RunnerExecutionPayloadMixin:
    @staticmethod
    def _should_surface_prompt_warning(warning_message: Any) -> bool:
        normalized = str(warning_message or "").strip().lower()
        if not normalized:
            return False
        if normalized.startswith("missing bootstrap file:"):
            return False
        return True
    @classmethod
    def _build_llm_payload_profile(
        cls,
        *,
        system_prompt: str,
        user_message: str,
        message_history: list[dict],
    ) -> dict[str, Any]:
        system_text = str(system_prompt or "")
        user_text = str(user_message or "")
        history_rows = [cls._normalize_payload_message(row) for row in (message_history or [])]

        system_chars = len(system_text)
        user_chars = len(user_text)
        history_chars = sum(len(row) for row in history_rows)

        system_bytes = len(system_text.encode("utf-8", errors="ignore"))
        user_bytes = len(user_text.encode("utf-8", errors="ignore"))
        history_bytes = sum(len(row.encode("utf-8", errors="ignore")) for row in history_rows)

        total_chars = system_chars + user_chars + history_chars
        total_bytes = system_bytes + user_bytes + history_bytes
        estimated_tokens = cls._estimate_tokens_by_chars(total_chars)

        duplicate_message_count, duplicate_group_count = cls._count_duplicate_history_messages(
            history_rows
        )
        history_count = len(history_rows)
        duplicate_ratio = (
            round(float(duplicate_message_count) / float(history_count), 4)
            if history_count > 0
            else 0.0
        )
        max_history_message_chars = max((len(row) for row in history_rows), default=0)
        user_repeated_in_history = cls._has_user_message_duplicate_in_history(
            user_text,
            history_rows,
        )

        return {
            "system_prompt_chars": system_chars,
            "system_prompt_bytes": system_bytes,
            "history_message_count": history_count,
            "history_chars": history_chars,
            "history_bytes": history_bytes,
            "history_max_message_chars": max_history_message_chars,
            "history_duplicate_messages": duplicate_message_count,
            "history_duplicate_groups": duplicate_group_count,
            "history_duplicate_ratio": duplicate_ratio,
            "user_message_chars": user_chars,
            "user_message_bytes": user_bytes,
            "user_message_repeated_in_history": user_repeated_in_history,
            "total_chars": total_chars,
            "total_bytes": total_bytes,
            "estimated_tokens": estimated_tokens,
        }
    @staticmethod
    def _normalize_payload_message(message: Any) -> str:
        if not isinstance(message, dict):
            return str(message or "")
        role = str(message.get("role", "") or "").strip()
        content = message.get("content", "")
        if isinstance(content, (dict, list)):
            content_text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        else:
            content_text = str(content or "")
        name = str(message.get("name", "") or "").strip()
        if name:
            return f"{role}:{name}:{content_text}"
        return f"{role}:{content_text}"
    @staticmethod
    def _estimate_tokens_by_chars(char_count: int) -> int:
        if char_count <= 0:
            return 0
        # Rough multilingual estimate for runtime observability.
        return max(1, int((char_count + 3) / 4))
    @staticmethod
    def _count_duplicate_history_messages(history_rows: list[str]) -> tuple[int, int]:
        if not history_rows:
            return 0, 0
        counts: dict[str, int] = {}
        for row in history_rows:
            normalized = " ".join(str(row or "").split()).strip()
            if not normalized:
                continue
            digest = hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()
            counts[digest] = counts.get(digest, 0) + 1
        duplicate_messages = sum(max(0, count - 1) for count in counts.values() if count > 1)
        duplicate_groups = sum(1 for count in counts.values() if count > 1)
        return duplicate_messages, duplicate_groups
    @staticmethod
    def _has_user_message_duplicate_in_history(user_message: str, history_rows: list[str]) -> bool:
        normalized_user = " ".join(str(user_message or "").split()).strip()
        if not normalized_user:
            return False
        user_entry = f"user:{normalized_user}"
        for row in history_rows:
            normalized_row = " ".join(str(row or "").split()).strip()
            if normalized_row == user_entry:
                return True
        return False
    @staticmethod
    def _deduplicate_message_history(messages: list[dict]) -> list[dict]:
        if len(messages) <= 1:
            return messages

        head_system: Optional[dict] = None
        core_messages = messages
        first = messages[0]
        if isinstance(first, dict) and str(first.get("role", "")).strip().lower() == "system":
            head_system = first
            core_messages = messages[1:]

        seen_signatures: set[str] = set()
        dedup_reversed: list[dict] = []
        for msg in reversed(core_messages):
            if not isinstance(msg, dict):
                dedup_reversed.append(msg)
                continue
            role = str(msg.get("role", "")).strip().lower()
            if role != "user":
                dedup_reversed.append(msg)
                continue
            if msg.get("tool_calls") or msg.get("tool_name") or msg.get("tool_call_id"):
                dedup_reversed.append(msg)
                continue
            normalized_content = " ".join(str(msg.get("content", "") or "").split()).strip()
            if not normalized_content:
                dedup_reversed.append(msg)
                continue
            user_identity = str(
                msg.get("user_id")
                or msg.get("name")
                or msg.get("sender_id")
                or "current_user"
            ).strip().lower()
            signature = f"{role}:{user_identity}:{normalized_content}"
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            dedup_reversed.append(msg)

        deduped = list(reversed(dedup_reversed))
        if head_system is not None:
            return [head_system, *deduped]
        return deduped

    @staticmethod
    def _merge_runtime_messages_with_session_prefix(
        *,
        session_message_history: list[dict],
        runtime_messages: list[dict],
        runtime_base_history_len: int,
    ) -> list[dict]:
        """Merge trimmed runtime history back onto the persisted session prefix.

        Runtime model loops may intentionally see a smaller history slice than the
        persisted session transcript. For persistence, hooks, and final answer
        extraction we reconstruct the full turn-visible transcript by keeping the
        session prefix and appending only the new suffix produced in the runtime
        loop.
        """
        session_prefix = list(session_message_history or [])
        normalized_runtime = list(runtime_messages or [])
        safe_runtime_base = max(0, min(int(runtime_base_history_len or 0), len(normalized_runtime)))
        if safe_runtime_base <= 0:
            return session_prefix + normalized_runtime
        return session_prefix + normalized_runtime[safe_runtime_base:]
    async def run_single(
        self,
        user_message: str,
        deps: SkillDeps,
        *,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Run a single non-streaming agent call."""
        # Simplified helper that bypasses the streaming session pipeline.
        try:
            result = await self.agent.run(
                user_message,
                deps=deps,
            )
            return result.output if hasattr(result, "output") else str(result)
        except Exception as e:
            return f"[Error: {str(e)}]"

