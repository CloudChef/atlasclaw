# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Optional

from app.atlasclaw.agent.plaintext_tool_calls import (
    looks_like_plaintext_tool_call_attempt,
    parse_plaintext_tool_calls,
)

def _parse_workflow_internal_metadata(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return text
    return str(value)


def _extract_workflow_candidate_items(metadata: Any) -> tuple[Optional[str], list[dict[str, Any]]]:
    if isinstance(metadata, list) and all(isinstance(item, dict) for item in metadata):
        return "__root__", [dict(item) for item in metadata]
    if not isinstance(metadata, dict):
        return None, []

    list_keys = [
        key
        for key, value in metadata.items()
        if isinstance(value, list) and all(isinstance(item, dict) for item in value)
    ]
    if len(list_keys) != 1:
        return None, []
    key = list_keys[0]
    return key, [dict(item) for item in metadata.get(key, [])]


def _replace_workflow_candidate_items(
    metadata: Any,
    *,
    container_key: str,
    items: list[dict[str, Any]],
) -> Any:
    if container_key == "__root__":
        return [dict(item) for item in items]
    if not isinstance(metadata, dict):
        return metadata
    updated = dict(metadata)
    updated[container_key] = [dict(item) for item in items]
    return updated


def _encode_workflow_internal_metadata(original: Any, narrowed: Any) -> Any:
    """Preserve the original storage shape when rewriting narrowed metadata."""
    if isinstance(original, str):
        return json.dumps(narrowed, ensure_ascii=False, separators=(",", ":"))
    return narrowed


def _workflow_candidate_selection_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("id", "entityId", "key", "code"):
        value = str(item.get(key) or "").strip()
        if value:
            tokens.add(value)
    return tokens


def _collect_explicit_selection_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, dict):
        for nested in value.values():
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, list):
        for nested in value:
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return tokens
        if text[:1] in {"{", "["}:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                tokens.update(_collect_explicit_selection_tokens(parsed))
                return tokens
        tokens.add(text)
        return tokens
    if isinstance(value, (int, float)):
        tokens.add(str(value))
        return tokens
    return tokens


def _extract_selected_candidates_from_tool_calls(
    candidates: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_lookup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for token in _workflow_candidate_selection_tokens(candidate):
            candidate_lookup.setdefault(token, candidate)

    if not candidate_lookup:
        return []

    matched: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for message in messages:
        if str(message.get("role", "")).strip().lower() != "assistant":
            continue
        for call in message.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            args = call.get("args", call.get("arguments"))
            for token in _collect_explicit_selection_tokens(args):
                candidate = candidate_lookup.get(token)
                if not candidate:
                    continue
                signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                matched.append(dict(candidate))
    return matched


def _narrow_workflow_tool_message(
    message: dict[str, Any],
    *,
    following_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a narrowed copy of a tool message when later tool calls carry an explicit selection."""
    if not following_messages:
        return None
    content = message.get("content")
    if not isinstance(content, dict) or "_internal" not in content:
        return None
    parsed_internal = _parse_workflow_internal_metadata(content.get("_internal"))
    container_key, candidates = _extract_workflow_candidate_items(parsed_internal)
    if not container_key or len(candidates) <= 1:
        return None

    narrowed_candidates = _extract_selected_candidates_from_tool_calls(candidates, following_messages)
    if len(narrowed_candidates) != 1:
        return None

    updated_content = dict(content)
    updated_content["_internal"] = _encode_workflow_internal_metadata(
        content.get("_internal"),
        _replace_workflow_candidate_items(
            parsed_internal,
            container_key=container_key,
            items=narrowed_candidates,
        ),
    )
    updated_message = dict(message)
    updated_message["content"] = updated_content
    return updated_message


class RunnerToolEvidenceMixin:
    _META_LABEL_OVERRIDES = {
        "workflowId": "Workflow ID",
        "requestId": "Request ID",
        "approvalId": "Approval ID",
        "processInstanceId": "Process Instance ID",
        "taskId": "Task ID",
        "catalogName": "Catalog",
        "applicant": "Applicant",
        "email": "Email",
        "description": "Description",
        "createdDate": "Created At",
        "updatedDate": "Updated At",
        "waitHours": "Wait Time",
        "priorityScore": "Priority Score",
        "priorityFactors": "Priority Factors",
        "approvalStep": "Approval Step",
        "currentApprover": "Current Approver",
        "costEstimate": "Cost Estimate",
        "resourceSpecs": "Resource Specs",
    }

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
            normalized_tool_calls: list[dict[str, Any]] = []
            if isinstance(tool_calls, list):
                normalized_tool_calls.extend(
                    call for call in tool_calls if isinstance(call, dict)
                )
            elif looks_like_plaintext_tool_call_attempt(str(message.get("content", "") or "")):
                normalized_tool_calls.extend(
                    parse_plaintext_tool_calls(str(message.get("content", "") or ""))
                )
            if not normalized_tool_calls:
                continue
            for call in normalized_tool_calls:
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
            compact = self._normalize_ascii_tool_output_to_markdown(compact)
            if compact:
                compact_chunks.append(compact)
        if not compact_chunks:
            return ""
        if len(compact_chunks) == 1:
            compact = compact_chunks[0]
            return compact
        return "\n\n".join(compact_chunks).strip()

    def _build_structured_tool_only_markdown_answer(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
        max_items: int = 3,
    ) -> str:
        records = self._extract_tool_result_records_from_messages(
            messages=messages,
            start_index=start_index,
            max_items=max_items,
        )
        if not records:
            return ""

        lines: list[str] = []
        for index, record in enumerate(records, start=1):
            rendered = self._render_tool_result_record_markdown(
                record=record,
                index=index,
                total=len(records),
            )
            if not rendered:
                continue
            if lines:
                lines.append("")
            lines.append(rendered)

        source_lines = self._collect_tool_result_source_lines(records)
        if source_lines:
            if lines:
                lines.append("")
            lines.extend(source_lines)
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
        structured_answer = self._build_structured_tool_only_markdown_answer(
            messages=messages,
            start_index=start_index,
            max_items=max_items,
        )
        if structured_answer:
            return structured_answer
        if not chunks:
            return ""
        return self._format_tool_chunks_as_markdown(chunks).strip()

    def _extract_tool_result_records_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
        max_items: int = 3,
    ) -> list[dict[str, Any]]:
        safe_start = max(0, min(int(start_index), len(messages)))
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            payload_items: list[tuple[str, Any]] = []
            if role in {"tool", "toolresult", "tool_result"}:
                payload_items.append(
                    (
                        str(message.get("tool_name", "") or message.get("name", "")).strip() or "tool",
                        message.get("content"),
                    )
                )
            tool_results = message.get("tool_results")
            if isinstance(tool_results, list):
                for result in tool_results:
                    if not isinstance(result, dict):
                        continue
                    payload_items.append(
                        (
                            str(result.get("tool_name", "") or result.get("name", "")).strip() or "tool",
                            result.get("content", result),
                        )
                    )
            for tool_name, payload in payload_items:
                text = self._coerce_tool_payload_to_text(payload).strip()
                if not text:
                    continue
                signature = f"{tool_name}:{text[:240]}"
                if signature in seen:
                    continue
                seen.add(signature)
                records.append(
                    {
                        "tool_name": tool_name,
                        "text": self._compact_tool_fallback_text(
                            text,
                            max_chars=5000,
                            max_lines=80,
                        ),
                        "meta_blocks": self._extract_embedded_meta_payloads(text),
                        "sources": self._extract_sources_from_tool_payload(payload),
                    }
                )
                if len(records) >= max(1, int(max_items)):
                    return records
        return records

    def _render_tool_result_record_markdown(
        self,
        *,
        record: dict[str, Any],
        index: int,
        total: int,
    ) -> str:
        meta_blocks = record.get("meta_blocks") or []
        rendered_meta_blocks: list[str] = []
        for meta_block in meta_blocks:
            rendered = self._render_embedded_meta_block(meta_block, index=index, total=total)
            if rendered:
                rendered_meta_blocks.append(rendered)
        if rendered_meta_blocks:
            return "\n\n".join(rendered_meta_blocks).strip()

        text = str(record.get("text", "") or "").strip()
        if not text:
            return ""
        return self._normalize_ascii_tool_output_to_markdown(text)

    @staticmethod
    def _looks_like_ascii_tool_layout(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        separator_count = len(re.findall(r"(?m)^[=+\-|]{8,}\s*$", normalized))
        pipe_line_count = len(re.findall(r"(?m)^\|\s*.+$", normalized))
        boxed_header_count = len(re.findall(r"(?m)^\+-\s*\[[^\]]+\].*$", normalized))
        return separator_count >= 2 or pipe_line_count >= 2 or boxed_header_count >= 1

    @staticmethod
    def _strip_tool_answer_wrapper(text: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").lstrip("\ufeff\u200b\u200c\u200d \t\r\n")
        if not normalized:
            return ""
        wrapper_pattern = re.compile(r"^(answer|result|response|回答|结果|回复)\s*[:：-]?$", re.IGNORECASE)
        while normalized:
            lines = normalized.split("\n")
            first_line = (lines[0] or "").strip()
            second_line = (lines[1] or "").strip() if len(lines) > 1 else ""
            if wrapper_pattern.fullmatch(first_line) and re.fullmatch(r"=+\s*", second_line):
                normalized = "\n".join(lines[2:]).lstrip("\ufeff\u200b\u200c\u200d \t\r\n")
                continue
            if wrapper_pattern.fullmatch(first_line):
                normalized = "\n".join(lines[1:]).lstrip("\ufeff\u200b\u200c\u200d \t\r\n")
                continue
            break
        return normalized

    def _normalize_ascii_tool_output_to_markdown(self, text: str) -> str:
        normalized = self._strip_tool_answer_wrapper(text)
        if not self._looks_like_ascii_tool_layout(normalized):
            return normalized.strip()

        markdown_lines: list[str] = []
        previous_blank = False
        promoted_heading = False
        for raw_line in normalized.splitlines():
            line = str(raw_line or "").rstrip()
            stripped = line.strip()
            if not stripped:
                if markdown_lines and not previous_blank:
                    markdown_lines.append("")
                previous_blank = True
                continue
            if re.fullmatch(r"[=+\-|]{8,}", stripped):
                continue
            if (
                not promoted_heading
                and stripped
                and not stripped.startswith(("###", "##", "#", "-", "*"))
                and not re.fullmatch(r"\+-\s*\[[^\]]+\].*", stripped)
                and not re.fullmatch(r"\|\s*.+", stripped)
            ):
                markdown_lines.append(f"## {stripped}")
                promoted_heading = True
                previous_blank = False
                continue
            boxed_header_match = re.fullmatch(r"\+-\s*(\[[^\]]+\]\s*.+?)(?:\s*[-=+|]+)?", stripped)
            if boxed_header_match:
                header_text = " ".join(boxed_header_match.group(1).split()).strip()
                if markdown_lines and markdown_lines[-1] != "":
                    markdown_lines.append("")
                markdown_lines.append(f"### {header_text}")
                previous_blank = False
                continue
            pipe_field_match = re.fullmatch(r"\|\s*(.+?)\s*[:：]\s*(.+)", stripped)
            if pipe_field_match:
                field_name = " ".join(pipe_field_match.group(1).split()).strip()
                field_value = " ".join(pipe_field_match.group(2).split()).strip()
                markdown_lines.append(f"- {field_name}: {field_value}")
                previous_blank = False
                continue
            if stripped == "|":
                if markdown_lines and not previous_blank:
                    markdown_lines.append("")
                previous_blank = True
                continue
            markdown_lines.append(" ".join(stripped.split()))
            previous_blank = False

        while markdown_lines and markdown_lines[0] == "":
            markdown_lines.pop(0)
        while markdown_lines and markdown_lines[-1] == "":
            markdown_lines.pop()
        return "\n".join(markdown_lines).strip()

    @staticmethod
    def _extract_embedded_meta_payloads(text: str) -> list[dict[str, Any]]:
        normalized = str(text or "")
        if not normalized:
            return []
        payloads: list[dict[str, Any]] = []
        for match in re.finditer(
            r"##(?P<name>[A-Z_]+)_START##\s*(?P<payload>.*?)\s*##(?P=name)_END##",
            normalized,
            flags=re.DOTALL,
        ):
            raw_payload = str(match.group("payload") or "").strip()
            if not raw_payload:
                continue
            try:
                parsed = json.loads(raw_payload)
            except Exception:
                continue
            payloads.append({"name": str(match.group("name") or "").strip(), "payload": parsed})
        return payloads

    def _render_embedded_meta_block(
        self,
        meta_block: dict[str, Any],
        *,
        index: int,
        total: int,
    ) -> str:
        payload = meta_block.get("payload")
        if isinstance(payload, list):
            rendered_items: list[str] = []
            for item_index, item in enumerate(payload, start=1):
                rendered = self._render_meta_item_markdown(item, index=item_index)
                if rendered:
                    rendered_items.append(rendered)
            return "\n\n".join(rendered_items).strip()
        if isinstance(payload, dict):
            return self._render_meta_item_markdown(payload, index=index if total > 1 else None).strip()
        return self._compact_tool_fallback_text(str(payload or ""), max_chars=1200).strip()

    def _render_meta_item_markdown(
        self,
        payload: Any,
        *,
        index: Optional[int],
    ) -> str:
        if isinstance(payload, dict):
            return self._render_meta_dict_markdown(payload, index=index)
        if isinstance(payload, list):
            scalar_items = [self._render_meta_value(item) for item in payload]
            scalar_items = [item for item in scalar_items if item]
            if not scalar_items:
                return ""
            prefix = f"{index}. " if index is not None else ""
            return "\n".join(f"- {prefix}{item}" if idx == 0 else f"- {item}" for idx, item in enumerate(scalar_items))
        rendered = self._render_meta_value(payload)
        if not rendered:
            return ""
        if index is None:
            return f"- {rendered}"
        return f"{index}. {rendered}"

    def _render_meta_dict_markdown(
        self,
        payload: dict[str, Any],
        *,
        index: Optional[int],
    ) -> str:
        title_key = ""
        title_value = ""
        for candidate in ("name", "title", "workflowId", "requestId", "approvalId", "id"):
            candidate_value = self._render_meta_value(payload.get(candidate))
            if candidate_value:
                title_key = candidate
                title_value = candidate_value
                break

        lines: list[str] = []
        if title_value:
            prefix = f"{index}. " if index is not None else ""
            lines.append(f"### {prefix}{title_value}")
        elif index is not None:
            lines.append(f"### {index}")

        for key, value in payload.items():
            if key == title_key:
                continue
            rendered_value = self._render_meta_value(value)
            if not rendered_value:
                continue
            lines.append(f"- {self._humanize_meta_key(key)}: {rendered_value}")
        return "\n".join(lines).strip()

    @staticmethod
    def _humanize_meta_key(key: str) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            return ""
        if normalized in RunnerToolEvidenceMixin._META_LABEL_OVERRIDES:
            return RunnerToolEvidenceMixin._META_LABEL_OVERRIDES[normalized]
        normalized = normalized.replace("_", " ")
        normalized = re.sub(r"(?<!^)(?=[A-Z])", " ", normalized)
        return " ".join(part.capitalize() for part in normalized.split())

    def _render_meta_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            rendered_timestamp = self._render_unix_timestamp(value)
            if rendered_timestamp:
                return rendered_timestamp
            return str(value)
        if isinstance(value, str):
            return " ".join(value.split()).strip()
        if isinstance(value, list):
            rendered_items = [self._render_meta_value(item) for item in value]
            rendered_items = [item for item in rendered_items if item]
            if not rendered_items:
                return ""
            return ", ".join(rendered_items)
        if isinstance(value, dict):
            pairs: list[str] = []
            for key, item in value.items():
                rendered_item = self._render_meta_value(item)
                if not rendered_item:
                    continue
                pairs.append(f"{self._humanize_meta_key(key)}={rendered_item}")
            return "; ".join(pairs)
        return str(value).strip()

    @staticmethod
    def _render_unix_timestamp(value: int | float) -> str:
        numeric = float(value)
        if numeric <= 0:
            return ""
        try:
            if numeric >= 1_000_000_000_000:
                dt = datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc)
            elif numeric >= 1_000_000_000:
                dt = datetime.fromtimestamp(numeric, tz=timezone.utc)
            else:
                return ""
        except Exception:
            return ""
        if dt.year < 2000 or dt.year > 2100:
            return ""
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")

    def _extract_sources_from_tool_payload(self, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            return []
        details = payload.get("details")
        if not isinstance(details, dict):
            return []

        source_items: list[dict[str, str]] = []
        for source in (details.get("sources", []) or []):
            if not isinstance(source, dict):
                continue
            label = str(source.get("label", "") or source.get("title", "") or source.get("url", "")).strip()
            url = str(source.get("url", "") or "").strip()
            if url:
                source_items.append({"label": label or url, "url": url})
        for citation in (details.get("citations", []) or []):
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url", "") or "").strip()
            title = str(citation.get("title", "") or citation.get("label", "") or url).strip()
            if url:
                source_items.append({"label": title or url, "url": url})
        for result in (details.get("results", []) or []):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url", "") or "").strip()
            title = str(result.get("title", "") or result.get("label", "") or url).strip()
            if url:
                source_items.append({"label": title or url, "url": url})
        deduped: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in source_items:
            url = str(item.get("url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduped.append({"label": str(item.get("label", "") or url).strip() or url, "url": url})
        return deduped[:5]

    @staticmethod
    def _collect_tool_result_source_lines(records: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        seen_urls: set[str] = set()
        for record in records:
            for source in (record.get("sources", []) or []):
                if not isinstance(source, dict):
                    continue
                url = str(source.get("url", "") or "").strip()
                label = str(source.get("label", "") or url).strip() or url
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                lines.append(f"- [{label}]({url})")
        return lines

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
                and (
                    had_tool_calls
                    or looks_like_plaintext_tool_call_attempt(str(item.get("content", "") or ""))
                )
            ):
                item["content"] = ""
            if (
                index >= safe_start
                and role == "assistant"
                and (
                    had_tool_calls
                    or looks_like_plaintext_tool_call_attempt(str(message.get("content", "") or ""))
                )
                and not item.get("tool_calls")
                and not str(item.get("content", "") or "").strip()
            ):
                continue
            sanitized.append(item)

        for tool_index in range(safe_start, len(sanitized)):
            tool_message = sanitized[tool_index]
            if str(tool_message.get("role", "")).strip().lower() != "tool":
                continue
            narrowed_tool_message = _narrow_workflow_tool_message(
                tool_message,
                following_messages=sanitized[tool_index + 1 :],
            )
            if narrowed_tool_message is None:
                continue
            sanitized[tool_index] = narrowed_tool_message

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
    def _compact_tool_fallback_text(
        text: str,
        max_chars: int = 1400,
        max_lines: Optional[int] = 18,
    ) -> str:
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

        raw_lines = normalized.splitlines()
        lines: list[str] = []
        total = 0
        truncated = False
        for index, raw_line in enumerate(raw_lines):
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            if line.startswith("{") and len(line) > 240:
                truncated = True
                continue
            if line.startswith("[") and len(line) > 240:
                truncated = True
                continue
            if total + len(line) + 1 > max_chars:
                truncated = True
                break
            lines.append(line)
            total += len(line) + 1
            if max_lines is not None and len(lines) >= max_lines:
                if any(" ".join(remaining.split()).strip() for remaining in raw_lines[index + 1 :]):
                    truncated = True
                break
        if not lines:
            clipped = normalized[:max_chars].strip()
            if len(normalized) > max_chars:
                clipped += " ..."
            return clipped

        compacted = "\n".join(lines).strip()
        if truncated:
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
