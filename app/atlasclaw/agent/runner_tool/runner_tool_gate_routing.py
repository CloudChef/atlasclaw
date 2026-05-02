# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.deps import SkillDeps


class RunnerToolGateRoutingMixin:
    @staticmethod
    def _is_low_information_follow_up_text(text: str) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return True
        compact_len = len(re.sub(r"\s+", "", normalized))
        return compact_len <= 8

    @staticmethod
    def _combine_follow_up_request(previous_user_message: str, current_user_message: str) -> str:
        previous = " ".join((previous_user_message or "").split()).strip()
        current = " ".join((current_user_message or "").split()).strip()
        if not previous:
            return current
        if not current:
            return previous
        normalized_current = unicodedata.normalize("NFKC", current)
        inline_selection_pattern = re.compile(
            r"^(?:\d+|[yn]|yes|no|true|false)$",
            re.IGNORECASE,
        )
        separator = " " if inline_selection_pattern.fullmatch(normalized_current) else "\n"
        return f"{previous}{separator}{current}".strip()

    def _align_external_system_intent(
        self,
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        available_tools: list[dict[str, Any]],
        user_message: str,
        recent_history: list[dict[str, Any]],
        deps: Optional[SkillDeps] = None,
    ) -> tuple[ToolGateDecision, CapabilityMatchResult]:
        """Prioritize provider/skill tool classes for external-system requests."""
        if not decision.needs_external_system:
            return decision, match_result

        provider_skill_classes = self._collect_provider_skill_capability_classes(available_tools)
        if not provider_skill_classes:
            return decision, match_result

        requested_provider_skill_classes = [
            capability
            for capability in decision.suggested_tool_classes
            if capability == "skill" or capability.startswith("provider:")
        ]
        selected_classes = self._select_external_system_capability_classes(
            requested_provider_skill_classes=requested_provider_skill_classes,
            provider_skill_classes=provider_skill_classes,
            preferred_provider_class=self._resolve_active_provider_capability_class(
                deps=deps,
                provider_skill_classes=provider_skill_classes,
            ),
        )

        rewritten = decision.model_copy(deep=True)
        rewritten.needs_tool = True
        if rewritten.policy is ToolPolicyMode.ANSWER_DIRECT:
            rewritten.policy = ToolPolicyMode.PREFER_TOOL
        rewritten.confidence = max(
            rewritten.confidence,
            self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
        )
        rewritten.suggested_tool_classes = selected_classes
        rewritten.reason = (
            f"{rewritten.reason} External-system intent was mapped to provider/skill direct tools."
        ).strip()

        refreshed_match = CapabilityMatcher(available_tools=available_tools).match(
            rewritten.suggested_tool_classes
        )
        return rewritten, refreshed_match
    @staticmethod
    def _collect_provider_skill_capability_classes(available_tools: list[dict[str, Any]]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        for tool in available_tools:
            capability = RunnerToolGateRoutingMixin._resolve_provider_skill_capability(tool)

            if not capability:
                continue
            if capability.startswith("provider:") or capability == "skill":
                if capability in seen:
                    continue
                seen.add(capability)
                ordered.append(capability)
        return ordered
    @staticmethod
    def _has_provider_or_skill_candidates(match_result: CapabilityMatchResult) -> bool:
        for candidate in match_result.tool_candidates:
            capability = str(getattr(candidate, "capability_class", "") or "").strip()
            if capability.startswith("provider:") or capability == "skill":
                return True
        return False
    @staticmethod
    def _resolve_provider_skill_capability(tool: dict[str, Any]) -> str:
        capability = str(tool.get("capability_class", "") or "").strip().lower()
        lowered_name = str(tool.get("name", "") or "").strip().lower()
        lowered_description = str(tool.get("description", "") or "").strip().lower()
        provider_type = str(tool.get("provider_type", "") or "").strip().lower()
        category = str(tool.get("category", "") or "").strip().lower()

        if capability.startswith("provider:") or capability == "skill":
            return capability
        if provider_type and provider_type != "none":
            return f"provider:{provider_type}"
        if category.startswith("provider") or "provider:" in lowered_description:
            return "provider:generic"
        if "skill" in category or (
            "skill" in lowered_description and lowered_name not in {"web_search", "web_fetch"}
        ):
            return "skill"
        return ""
    def _select_external_system_capability_classes(
        self,
        *,
        requested_provider_skill_classes: list[str],
        provider_skill_classes: list[str],
        preferred_provider_class: Optional[str] = None,
    ) -> list[str]:
        requested = [
            capability
            for capability in requested_provider_skill_classes
            if capability in provider_skill_classes
        ]
        if requested:
            return requested
        if preferred_provider_class and preferred_provider_class in provider_skill_classes:
            return [preferred_provider_class]
        return provider_skill_classes
    @staticmethod
    def _resolve_active_provider_capability_class(
        *,
        deps: Optional[SkillDeps],
        provider_skill_classes: list[str],
    ) -> Optional[str]:
        if deps is None or not isinstance(getattr(deps, "extra", None), dict):
            return None
        extra = deps.extra
        provider_type = ""
        provider_instance = extra.get("provider_instance")
        if isinstance(provider_instance, dict):
            provider_type = str(provider_instance.get("provider_type", "") or "").strip().lower()
        if not provider_type:
            provider_type = str(extra.get("provider_type", "") or "").strip().lower()
        if not provider_type:
            provider_type = str(extra.get("provider", "") or "").strip().lower()
        if not provider_type:
            provider_instances = extra.get("provider_instances")
            if isinstance(provider_instances, dict):
                for key in sorted(provider_instances.keys()):
                    capability = f"provider:{str(key).strip().lower()}"
                    if capability in provider_skill_classes:
                        provider_type = str(key).strip().lower()
                        break
        if not provider_type:
            return None
        capability = f"provider:{provider_type}"
        if capability in provider_skill_classes:
            return capability
        return None
    @staticmethod
    def _tool_gate_has_strict_need(decision: ToolGateDecision) -> bool:
        return any(
            [
                bool(decision.needs_external_system),
                bool(decision.needs_browser_interaction),
                bool(decision.needs_private_context),
                bool(decision.needs_grounded_verification and not decision.needs_live_data),
            ]
        )
    def _resolve_contextual_tool_request(
        self,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> tuple[str, bool]:
        normalized_user_message = " ".join((user_message or "").split()).strip()
        if not normalized_user_message:
            return user_message, False

        last_assistant_index: Optional[int] = None
        last_assistant_message = ""
        last_assistant_raw_message = ""
        for index in range(len(recent_history) - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "assistant":
                continue
            content_raw = str(item.get("content", "") or "")
            content = " ".join(content_raw.split()).strip()
            if not content:
                continue
            last_assistant_index = index
            last_assistant_message = content
            last_assistant_raw_message = content_raw
            break

        if last_assistant_index is None:
            return normalized_user_message, False

        assistant_requests_follow_up = self._looks_like_follow_up_request(last_assistant_message)
        expected_field_labels = (
            self._extract_follow_up_field_labels(last_assistant_raw_message)
            if assistant_requests_follow_up
            else []
        )
        identifier_follow_up = self._contains_structured_identifier(normalized_user_message)
        structured_field_response = assistant_requests_follow_up and self._looks_like_structured_field_response(
            normalized_user_message,
            expected_labels=expected_field_labels,
        )
        if (
            identifier_follow_up
            and self._identifier_request_is_self_contained(normalized_user_message)
            and not structured_field_response
        ):
            return normalized_user_message, False
        compact_current_len = len(re.sub(r"\s+", "", normalized_user_message))
        long_structured_follow_up = compact_current_len > 32 and not identifier_follow_up
        low_information_follow_up = compact_current_len <= 8

        previous_user_message = ""
        fallback_previous_user_message = ""
        for index in range(last_assistant_index - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "user":
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if not content:
                continue
            if not fallback_previous_user_message:
                fallback_previous_user_message = content
            if self._is_low_information_follow_up_text(content):
                continue
            previous_user_message = content
            break

        if not previous_user_message:
            previous_user_message = fallback_previous_user_message

        if not previous_user_message:
            return normalized_user_message, False

        if low_information_follow_up:
            combined = self._combine_follow_up_request(
                previous_user_message,
                normalized_user_message,
            )
            return combined, combined != normalized_user_message

        if long_structured_follow_up and not assistant_requests_follow_up:
            return normalized_user_message, False

        if not identifier_follow_up and not assistant_requests_follow_up:
            return normalized_user_message, False

        combined = self._combine_follow_up_request(
            previous_user_message,
            normalized_user_message,
        )
        return combined, combined != normalized_user_message

    @classmethod
    def _identifier_request_is_self_contained(cls, text: str) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return False
        without_identifiers = cls._strip_structured_identifiers(normalized)
        compact_without_identifiers = re.sub(r"\s+", "", without_identifiers)
        if len(compact_without_identifiers) >= 4:
            return True
        return False

    @staticmethod
    def _strip_structured_identifiers(text: str) -> str:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return ""
        patterns = (
            r"(?<![a-z0-9])[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}(?![a-z0-9])",
            r"(?<![a-z0-9])(?=[a-z0-9_-]{8,})(?=[a-z0-9_-]*[a-z])(?=[a-z0-9_-]*\d)[a-z0-9_-]+(?![a-z0-9])",
            r"(?<!\d)\d{8,}(?!\d)",
        )
        stripped = normalized
        for pattern in patterns:
            stripped = re.sub(pattern, " ", stripped, flags=re.IGNORECASE)
        return " ".join(stripped.split()).strip()

    @staticmethod
    def _contains_structured_identifier(text: str) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return False
        patterns = (
            r"(?<![a-z0-9])[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}(?![a-z0-9])",
            r"(?<![a-z0-9])(?=[a-z0-9_-]{8,})(?=[a-z0-9_-]*[a-z])(?=[a-z0-9_-]*\d)[a-z0-9_-]+(?![a-z0-9])",
            r"(?<!\d)\d{8,}(?!\d)",
        )
        lowered = normalized.lower()
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _looks_like_structured_field_response(
        text: str,
        *,
        expected_labels: Optional[list[str]] = None,
    ) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return False
        normalized = unicodedata.normalize("NFKC", normalized)
        parts = [
            item.strip()
            for item in re.split(r"\s*[,，;；|]\s*", normalized)
            if item.strip()
        ]
        if len(parts) >= 2:
            informative_parts = [
                item for item in parts if len(re.sub(r"\s+", "", item)) >= 2
            ]
            if len(informative_parts) >= 2:
                return True

        normalized_labels: list[str] = []
        for item in (expected_labels or []):
            label = " ".join(str(item or "").split()).strip()
            if not label:
                continue
            folded = label.casefold()
            if folded not in normalized_labels:
                normalized_labels.append(folded)
        if len(normalized_labels) < 2:
            return False

        label_pattern = re.compile(
            r"(?<![0-9A-Za-z_\u4e00-\u9fff])(?:"
            + "|".join(re.escape(label) for label in sorted(normalized_labels, key=len, reverse=True))
            + r")(?![0-9A-Za-z_\u4e00-\u9fff])",
            flags=re.IGNORECASE,
        )
        matches = list(label_pattern.finditer(normalized))
        if len(matches) < 2:
            return False

        informative_segments = 0
        for index, match in enumerate(matches):
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            value = normalized[match.end():next_start]
            value = value.lstrip(" :=：,，;；|/-")
            value = value.strip()
            if len(re.sub(r"\s+", "", value)) >= 1:
                informative_segments += 1
            if informative_segments >= 2:
                return True
        return False

    @staticmethod
    def _extract_follow_up_field_labels(text: str, *, max_labels: int = 8) -> list[str]:
        normalized_text = unicodedata.normalize("NFKC", str(text or ""))
        labels: list[str] = []
        for raw_line in normalized_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if not re.match(r"^(?:\[\d+\]|\d[\.\)]|[-*•])\s*", line):
                continue
            candidate = re.sub(r"^(?:\[\d+\]|\d[\.\)]|[-*•])\s*", "", line)
            candidate = candidate.rstrip("：:?？").strip()
            candidate = " ".join(candidate.split())
            compact = re.sub(r"\s+", "", candidate)
            if not compact or len(compact) < 2 or len(compact) > 24:
                continue
            if len(candidate.split()) > 3:
                continue
            if re.search(r"[，,；;。.!]", candidate):
                continue
            if re.search(r"\d", candidate):
                continue
            folded = candidate.casefold()
            if folded in labels:
                continue
            labels.append(folded)
            if len(labels) >= max_labels:
                break
        return labels

    @staticmethod
    def _build_classifier_history(
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
        used_follow_up_context: bool,
        max_messages: int = 4,
        max_chars_per_message: int = 240,
    ) -> list[dict[str, Any]]:
        """Build a compact history slice for gate classification.

        The classifier should always receive a small amount of session context so
        follow-up requests (for example "show details for this ticket") can stay
        on the same provider path without shipping the full transcript.
        """
        if not isinstance(recent_history, list) or not recent_history:
            return []
        normalized_user_message = " ".join(str(user_message or "").split()).strip()
        if not used_follow_up_context:
            compact_user_len = len(re.sub(r"\s+", "", normalized_user_message))
            if compact_user_len > 8:
                return []
        tail_count = max(2, int(max_messages or 4))
        char_limit = max(80, int(max_chars_per_message or 240))
        selected = recent_history[-tail_count:]

        compact: list[dict[str, Any]] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if not content:
                continue
            if len(content) > char_limit:
                content = content[:char_limit].rstrip() + " ..."
            compact.append({"role": role, "content": content})

        if used_follow_up_context:
            return compact
        return []
    def _apply_no_classifier_follow_up_fallback(
        self,
        *,
        decision: ToolGateDecision,
        used_follow_up_context: bool,
        available_tools: list[dict[str, Any]],
    ) -> ToolGateDecision:
        # Keep follow-up turns on the same LLM-driven gate path.
        # Do not inject runtime web defaults here.
        return decision
    @staticmethod
    def _looks_like_follow_up_request(message: str) -> bool:
        text = " ".join((message or "").split())
        if not text:
            return False
        normalized_text = unicodedata.normalize("NFKC", text)
        lowered = normalized_text.lower()
        question_count = normalized_text.count("?") + normalized_text.count("？")
        numbered_choices = len(
            re.findall(r"(?:^|[\s\n])(?:\[\d+\]|\d[\)\.])", normalized_text)
        )
        enumerated_field_lines = len(
            re.findall(r"(?m)^\s*(?:\[\d+\]|\d[\.\)])\s+.+?(?::\s*)?$", normalized_text)
        )
        interaction_markers = (
            "please reply",
            "reply with",
            "choose",
            "confirm",
            "clarify",
            "specify",
            "select",
            "tell me",
            "provide",
            "请回复",
            "请提供",
            "请补充",
            "请填写",
            "请输入",
            "请选择",
            "请确认",
            "确认",
            "提供",
            "补充",
            "填写",
            "输入",
            "选择",
            "以下信息",
            "以下字段",
        )
        selection_prompt_markers = (
            "enter number",
            "input number",
            "choose a number",
            "select a number",
            "输入编号",
            "请输入编号",
            "选择编号",
            "回复编号",
            "输入序号",
        )
        marker_hits = sum(1 for marker in interaction_markers if marker in lowered)
        has_selection_prompt = any(marker in lowered for marker in selection_prompt_markers)
        has_prompt_suffix = bool(re.search(r"[:：?？]\s*$", normalized_text))
        if numbered_choices >= 2 and enumerated_field_lines >= 2:
            return True
        if numbered_choices >= 2 and marker_hits >= 1:
            return True
        if numbered_choices >= 2 and has_prompt_suffix:
            return True
        if marker_hits >= 1 and has_selection_prompt:
            return True
        if question_count >= 2 and marker_hits >= 1:
            return True
        if question_count >= 1 and marker_hits >= 2:
            return True
        return False
