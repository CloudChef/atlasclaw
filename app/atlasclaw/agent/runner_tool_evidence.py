from __future__ import annotations

import json
import re
from typing import Any


class RunnerToolEvidenceMixin:
    def _collect_tool_call_summaries_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "")).strip().lower() != "assistant":
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name", "") or call.get("tool_name", "")).strip()
                if not name:
                    continue
                args_raw = call.get("args", call.get("arguments"))
                args: dict[str, Any] = {}
                if isinstance(args_raw, dict):
                    args = dict(args_raw)
                elif isinstance(args_raw, str):
                    payload = args_raw.strip()
                    if payload.startswith("{"):
                        try:
                            parsed = json.loads(payload)
                            if isinstance(parsed, dict):
                                args = parsed
                        except Exception:
                            args = {}
                signature = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                if signature in seen:
                    continue
                seen.add(signature)
                summary: dict[str, Any] = {"name": name}
                if args:
                    summary["args"] = args
                summaries.append(summary)
        return summaries

    def _extract_tool_text_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
        max_chars: int = 6000,
    ) -> str:
        chunks = self._extract_tool_text_chunks_from_messages(
            messages=messages,
            start_index=start_index,
            max_items=1,
            max_chars_per_item=max_chars,
        )
        if not chunks:
            return ""
        return chunks[0]

    def _extract_tool_text_chunks_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
        max_items: int = 3,
        max_chars_per_item: int = 3000,
    ) -> list[str]:
        safe_start = max(0, min(int(start_index), len(messages)))
        chunks: list[str] = []
        seen: set[str] = set()
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            for normalized in self._extract_tool_payload_strings_from_message(
                message=message,
                max_chars_per_item=max_chars_per_item,
            ):
                compact_signature = normalized[:400]
                if compact_signature in seen:
                    continue
                seen.add(compact_signature)
                chunks.append(normalized)
                if len(chunks) >= max(1, int(max_items)):
                    return chunks
        return chunks

    def _extract_tool_payload_strings_from_message(
        self,
        *,
        message: dict[str, Any],
        max_chars_per_item: int,
    ) -> list[str]:
        role = str(message.get("role", "")).strip().lower()
        payloads: list[Any] = []
        if role in {"tool", "toolresult", "tool_result"}:
            payloads.append(message.get("content"))
        tool_results = message.get("tool_results")
        if isinstance(tool_results, list):
            for result in tool_results:
                if isinstance(result, dict):
                    payloads.append(result.get("content", result))
                else:
                    payloads.append(result)

        chunks: list[str] = []
        for payload in payloads:
            text = self._coerce_tool_payload_to_text(payload)
            if not text:
                continue
            normalized = text.strip()
            if not normalized:
                continue
            chunks.append(normalized[:max_chars_per_item])
        return chunks

    def _coerce_tool_payload_to_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            chunks: list[str] = []
            for item in payload:
                block = self._coerce_tool_payload_to_text(item)
                if block:
                    chunks.append(block)
            return "\n".join(chunks).strip()
        if isinstance(payload, dict):
            for key in ("output", "text", "summary", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            if "content" in payload:
                return self._coerce_tool_payload_to_text(payload.get("content"))
            if "results" in payload:
                return self._coerce_tool_payload_to_text(payload.get("results"))
            if "data" in payload:
                return self._coerce_tool_payload_to_text(payload.get("data"))
            try:
                return json.dumps(payload, ensure_ascii=False)
            except Exception:
                return str(payload)
        try:
            return str(payload)
        except Exception:
            return ""

    def _format_tool_chunks_as_markdown(self, chunks: list[str]) -> str:
        compact_chunks: list[str] = []
        for chunk in chunks:
            compact = self._compact_tool_fallback_text(chunk, max_chars=1200).strip()
            if compact:
                compact_chunks.append(compact)
        if not compact_chunks:
            return ""
        if len(compact_chunks) == 1:
            compact = compact_chunks[0]
            if self._looks_like_markdown(compact):
                return compact
            return f"## Result\n\n{compact}"

        lines: list[str] = ["## Result"]
        for index, compact in enumerate(compact_chunks, start=1):
            lines.append("")
            lines.append(f"### Evidence {index}")
            lines.append(compact)
        return "\n".join(lines).strip()

    def _build_tool_only_markdown_answer_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
        max_items: int = 3,
        max_chars_per_item: int = 3000,
    ) -> str:
        chunks = self._extract_tool_text_chunks_from_messages(
            messages=messages,
            start_index=start_index,
            max_items=max_items,
            max_chars_per_item=max_chars_per_item,
        )
        if not chunks:
            return ""
        return self._format_tool_chunks_as_markdown(chunks).strip()

    def _sanitize_turn_messages_for_persistence(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        final_assistant: str = "",
        clear_tool_planning_text: bool = False,
    ) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        safe_start = max(0, min(int(start_index), len(messages)))
        final_assistant_text = str(final_assistant or "").strip()
        matched_tool_call_keys = self._collect_matched_tool_call_keys(
            messages=messages,
            start_index=safe_start,
        )
        tool_call_counter = 0

        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            item = dict(message)
            role = str(item.get("role", "")).strip().lower()
            original_tool_calls = item.get("tool_calls")
            had_tool_calls = isinstance(original_tool_calls, list) and bool(original_tool_calls)
            if had_tool_calls and index >= safe_start:
                filtered_tool_calls: list[dict[str, Any]] = []
                for call in original_tool_calls:
                    if not isinstance(call, dict):
                        continue
                    record = self._normalize_tool_call_match_record(
                        call=call,
                        sequence_index=tool_call_counter,
                    )
                    tool_call_counter += 1
                    if record["match_key"] in matched_tool_call_keys:
                        filtered_tool_calls.append(call)
                if filtered_tool_calls:
                    item["tool_calls"] = filtered_tool_calls
                else:
                    item.pop("tool_calls", None)
            if (
                clear_tool_planning_text
                and index >= safe_start
                and role == "assistant"
                and had_tool_calls
            ):
                item["content"] = ""
            if (
                index >= safe_start
                and role == "assistant"
                and had_tool_calls
                and not item.get("tool_calls")
                and not str(item.get("content", "") or "").strip()
            ):
                continue
            sanitized.append(item)

        if not final_assistant_text:
            return sanitized

        last_plain_assistant_index: int | None = None
        for index in range(len(sanitized) - 1, safe_start - 1, -1):
            item = sanitized[index]
            if str(item.get("role", "")).strip().lower() != "assistant":
                continue
            if isinstance(item.get("tool_calls"), list) and item.get("tool_calls"):
                continue
            last_plain_assistant_index = index
            break

        if last_plain_assistant_index is None:
            sanitized.append({"role": "assistant", "content": final_assistant_text})
        else:
            updated = dict(sanitized[last_plain_assistant_index])
            updated["content"] = final_assistant_text
            sanitized[last_plain_assistant_index] = updated
        return sanitized

    def _collect_matched_tool_call_keys(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int,
    ) -> set[str]:
        """Return tool-call keys that have a matching tool return later in the turn."""
        safe_start = max(0, min(int(start_index), len(messages)))
        pending_tool_calls: list[dict[str, Any]] = []
        matched_keys: set[str] = set()
        tool_call_counter = 0

        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            if role == "assistant":
                for call in message.get("tool_calls", []) or []:
                    if not isinstance(call, dict):
                        continue
                    record = self._normalize_tool_call_match_record(
                        call=call,
                        sequence_index=tool_call_counter,
                    )
                    tool_call_counter += 1
                    pending_tool_calls.append(record)

            for tool_name, tool_call_id in self._extract_completed_tool_identities(
                message=message,
                pending_tool_calls=pending_tool_calls,
            ):
                match_key = self._consume_matching_tool_call(
                    pending_tool_calls=pending_tool_calls,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )
                if match_key:
                    matched_keys.add(match_key)

        return matched_keys

    def _extract_completed_tool_identities(
        self,
        *,
        message: dict[str, Any],
        pending_tool_calls: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Extract tool result identities from persisted transcript messages."""
        identities: list[tuple[str, str]] = []
        role = str(message.get("role", "")).strip().lower()
        if role in {"tool", "toolresult", "tool_result"}:
            tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
            tool_call_id = str(message.get("tool_call_id", "") or message.get("id", "")).strip()
            if tool_name or tool_call_id:
                identities.append((tool_name, tool_call_id))

        for result in message.get("tool_results", []) or []:
            if not isinstance(result, dict):
                continue
            tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
            tool_call_id = str(
                result.get("tool_call_id", result.get("toolCallId", result.get("id", ""))) or ""
            ).strip()
            if tool_name or tool_call_id:
                identities.append((tool_name, tool_call_id))

        if identities:
            return identities

        if role in {"tool", "toolresult", "tool_result"} and len(pending_tool_calls) == 1:
            pending = pending_tool_calls[0]
            return [(pending.get("name", ""), pending.get("id", ""))]
        return []

    @staticmethod
    def _normalize_tool_call_match_record(
        *,
        call: dict[str, Any],
        sequence_index: int,
    ) -> dict[str, str]:
        """Normalize one assistant tool-call record for later return matching."""
        tool_name = str(call.get("name", "") or call.get("tool_name", "")).strip()
        tool_call_id = str(
            call.get("id", call.get("tool_call_id", call.get("toolCallId", ""))) or ""
        ).strip()
        if tool_call_id:
            match_key = f"id:{tool_call_id}"
        else:
            match_key = f"seq:{sequence_index}:{tool_name}"
        return {
            "name": tool_name,
            "id": tool_call_id,
            "match_key": match_key,
        }

    @staticmethod
    def _consume_matching_tool_call(
        *,
        pending_tool_calls: list[dict[str, Any]],
        tool_name: str,
        tool_call_id: str,
    ) -> str:
        """Consume and return the matched pending tool-call key, if any."""
        if not pending_tool_calls:
            return ""
        if tool_call_id:
            for index, pending in enumerate(pending_tool_calls):
                pending_id = str(pending.get("id", "") or "").strip()
                if pending_id and pending_id == tool_call_id:
                    return pending_tool_calls.pop(index).get("match_key", "")
        if tool_name:
            for index, pending in enumerate(pending_tool_calls):
                pending_name = str(pending.get("name", "") or "").strip()
                if pending_name == tool_name:
                    return pending_tool_calls.pop(index).get("match_key", "")
        if len(pending_tool_calls) == 1:
            return pending_tool_calls.pop(0).get("match_key", "")
        return ""

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        return bool(
            re.search(r"(^#|\n#|^\* |\n\* |^- |\n- |^\d+\.\s|\n\d+\.\s|```|\[[^\]]+\]\([^)]+\))", normalized)
        )

    @staticmethod
    def _compact_tool_fallback_text(text: str, max_chars: int = 1400) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return ""
        normalized = re.sub(
            r"##[A-Z_]+_META_START##.*?##[A-Z_]+_META_END##",
            "",
            normalized,
            flags=re.DOTALL,
        ).strip()
        if not normalized:
            return ""

        lines: list[str] = []
        total = 0
        for raw_line in normalized.splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            if line.startswith("{") and len(line) > 240:
                continue
            if line.startswith("[") and len(line) > 240:
                continue
            if total + len(line) + 1 > max_chars:
                break
            lines.append(line)
            total += len(line) + 1
            if len(lines) >= 18:
                break
        if not lines:
            clipped = normalized[:max_chars].strip()
            if len(normalized) > max_chars:
                clipped += " ..."
            return clipped

        compacted = "\n".join(lines).strip()
        if len(compacted) < len(normalized):
            compacted += "\n..."
        return compacted

    @staticmethod
    def _replace_last_assistant_message(
        *,
        messages: list[dict[str, Any]],
        content: str,
    ) -> list[dict[str, Any]]:
        updated = list(messages)
        for index in range(len(updated) - 1, -1, -1):
            item = updated[index]
            if str(item.get("role", "")).strip() != "assistant":
                continue
            replaced = dict(item)
            replaced["content"] = content
            updated[index] = replaced
            return updated
        updated.append({"role": "assistant", "content": content})
        return updated

    @staticmethod
    def _extract_latest_assistant_from_messages(
        *,
        messages: list[dict[str, Any]],
        start_index: int,
    ) -> str:
        if not isinstance(messages, list) or not messages:
            return ""
        safe_start = max(0, min(int(start_index), len(messages)))
        for item in reversed(messages[safe_start:]):
            if not isinstance(item, dict):
                continue
            if str(item.get("role", "")).strip() != "assistant":
                continue
            if isinstance(item.get("tool_calls"), list) and item.get("tool_calls"):
                continue
            content = str(item.get("content", "") or "").strip()
            if content:
                return content
        return ""

    @staticmethod
    def _remove_last_assistant_from_run(
        *,
        messages: list[dict[str, Any]],
        start_index: int,
    ) -> list[dict[str, Any]]:
        updated = list(messages)
        safe_start = max(0, min(int(start_index), len(updated)))
        for index in range(len(updated) - 1, safe_start - 1, -1):
            item = updated[index]
            if not isinstance(item, dict):
                continue
            if str(item.get("role", "")).strip() != "assistant":
                continue
            return updated[:index] + updated[index + 1 :]
        return updated

