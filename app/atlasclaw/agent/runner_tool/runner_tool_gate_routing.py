from __future__ import annotations

import re
from typing import Any, Optional

from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.deps import SkillDeps


class RunnerToolGateRoutingMixin:
    @staticmethod
    def _tool_rank_entry_declares_artifact(entry: dict[str, Any]) -> bool:
        for capability in (entry.get("capability_classes", []) or []):
            normalized = str(capability or "").strip().lower()
            if normalized.startswith("artifact:"):
                return True
        return False

    @classmethod
    def _select_tool_ranked_top_entries(
        cls,
        *,
        tool_ranked: list[dict[str, Any]],
        limit: int,
        max_artifact_extras: int = 2,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit or 1))
        selected = list(tool_ranked[:safe_limit])
        selected_names = {
            str(item.get("tool_name", "") or "").strip()
            for item in selected
            if isinstance(item, dict)
        }
        appended = 0
        for item in tool_ranked[safe_limit:]:
            if appended >= max_artifact_extras:
                break
            if not isinstance(item, dict) or not cls._tool_rank_entry_declares_artifact(item):
                continue
            tool_name = str(item.get("tool_name", "") or "").strip()
            if not tool_name or tool_name in selected_names:
                continue
            selected.append(item)
            selected_names.add(tool_name)
            appended += 1
        return selected

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
            available_tools=available_tools,
            user_message=user_message,
            recent_history=recent_history,
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
        if "jira" in lowered_name or "jira" in lowered_description:
            return "provider:jira"
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
        available_tools: list[dict[str, Any]],
        user_message: str,
        recent_history: list[dict[str, Any]],
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

        history_text = " ".join(
            str(item.get("content", "") or "").strip()
            for item in recent_history[-4:]
            if isinstance(item, dict)
        )
        request_text = f"{user_message} {history_text}".strip()
        request_text_lower = request_text.lower()
        request_tokens = self._tokenize_classifier_fallback_text(request_text)
        if not request_tokens:
            return provider_skill_classes

        class_scores: dict[str, int] = {}
        for capability in provider_skill_classes:
            class_tokens = self._tokenize_classifier_fallback_text(capability.replace("provider:", ""))
            score = len(request_tokens.intersection(class_tokens))
            if capability.startswith("provider:"):
                provider_key = capability.split(":", 1)[1].strip().lower()
                if provider_key:
                    if provider_key in request_text_lower:
                        score += 6
                    for token in request_tokens:
                        if len(token) < 3:
                            continue
                        if token in provider_key or provider_key in token:
                            score += 2
            for tool in available_tools:
                if self._resolve_provider_skill_capability(tool) != capability:
                    continue
                metadata_text = " ".join(
                    [
                        str(tool.get("name", "") or ""),
                        str(tool.get("description", "") or ""),
                        str(tool.get("provider_type", "") or ""),
                        str(tool.get("category", "") or ""),
                    ]
                ).strip().lower()
                metadata_tokens = self._tokenize_classifier_fallback_text(metadata_text)
                score += len(request_tokens.intersection(metadata_tokens))
                for token in request_tokens:
                    if len(token) < 3:
                        continue
                    if token in metadata_text:
                        score += 1
            class_scores[capability] = score

        if not class_scores:
            return provider_skill_classes
        top_score = max(class_scores.values())
        if top_score <= 0:
            return provider_skill_classes

        selected = [
            capability
            for capability in provider_skill_classes
            if class_scores.get(capability, 0) == top_score
        ]
        return selected or provider_skill_classes
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
        identifier_follow_up = self._contains_structured_identifier(normalized_user_message)
        if identifier_follow_up and self._identifier_request_is_self_contained(normalized_user_message):
            return normalized_user_message, False
        if len(re.sub(r"\s+", "", normalized_user_message)) > 32 and not identifier_follow_up:
            return normalized_user_message, False

        last_assistant_index: Optional[int] = None
        last_assistant_message = ""
        for index in range(len(recent_history) - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "assistant":
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if not content:
                continue
            last_assistant_index = index
            last_assistant_message = content
            break

        if last_assistant_index is None:
            return normalized_user_message, False
        if not identifier_follow_up and not self._looks_like_follow_up_request(last_assistant_message):
            return normalized_user_message, False

        previous_user_message = ""
        for index in range(last_assistant_index - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "user":
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if not content:
                continue
            previous_user_message = content
            break

        if not previous_user_message:
            return normalized_user_message, False

        current_tokens = self._tokenize_classifier_fallback_text(normalized_user_message)
        compact_current_len = len(re.sub(r"\s+", "", normalized_user_message))
        low_information_follow_up = compact_current_len <= 8 or len(current_tokens) <= 1
        if not low_information_follow_up and not identifier_follow_up:
            return normalized_user_message, False

        combined = f"{previous_user_message} {normalized_user_message}".strip()
        return combined, low_information_follow_up and combined != normalized_user_message

    @classmethod
    def _identifier_request_is_self_contained(cls, text: str) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return False
        without_identifiers = cls._strip_structured_identifiers(normalized)
        compact_without_identifiers = re.sub(r"\s+", "", without_identifiers)
        if len(compact_without_identifiers) >= 4:
            return True
        request_tokens = cls._tokenize_classifier_fallback_text(without_identifiers)
        return len(request_tokens) >= 2

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
    def _apply_provider_skill_intent_fallback(
        self,
        *,
        decision: ToolGateDecision,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        deps: Optional[SkillDeps] = None,
    ) -> ToolGateDecision:
        if decision.needs_external_system:
            return decision
        metadata_confidence = 0.0
        has_metadata_provider_candidates = False
        if deps is not None and isinstance(getattr(deps, "extra", None), dict):
            metadata = deps.extra.get("tool_metadata_candidates")
            if isinstance(metadata, dict):
                metadata_confidence = float(metadata.get("confidence", 0.0) or 0.0)
                has_metadata_provider_candidates = bool(
                    (metadata.get("preferred_provider_types") or [])
                    or (metadata.get("preferred_capability_classes") or [])
                    or (metadata.get("preferred_tool_names") or [])
                )
        has_contextual_provider_signal = bool(recent_history) and has_metadata_provider_candidates
        if (
            metadata_confidence < self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE
            and not has_contextual_provider_signal
        ):
            return decision
        provider_skill_classes = self._collect_provider_skill_capability_classes(available_tools)
        if not provider_skill_classes:
            return decision
        if not self._looks_provider_or_skill_related(
            user_message=user_message,
            recent_history=recent_history,
            available_tools=available_tools,
            provider_hint_tokens=self._collect_provider_hint_tokens_from_deps(deps),
        ):
            return decision

        rewritten = decision.model_copy(deep=True)
        rewritten.needs_tool = True
        rewritten.needs_external_system = True
        if rewritten.policy is ToolPolicyMode.ANSWER_DIRECT:
            rewritten.policy = ToolPolicyMode.PREFER_TOOL
        rewritten.confidence = max(
            rewritten.confidence,
            self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
        )
        requested_provider_skill_classes = [
            item
            for item in rewritten.suggested_tool_classes
            if item == "skill" or item.startswith("provider:")
        ]
        rewritten.suggested_tool_classes = self._select_external_system_capability_classes(
            requested_provider_skill_classes=requested_provider_skill_classes,
            provider_skill_classes=provider_skill_classes,
            available_tools=available_tools,
            user_message=user_message,
            recent_history=recent_history,
            preferred_provider_class=self._resolve_active_provider_capability_class(
                deps=deps,
                provider_skill_classes=provider_skill_classes,
            ),
        )
        rewritten.reason = (
            f"{rewritten.reason} Runtime mapped request to provider/skill intent using tool metadata."
        ).strip()
        return rewritten
    def _apply_tool_gate_consistency_guard(
        self,
        *,
        decision: ToolGateDecision,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        deps: Optional[SkillDeps] = None,
        metadata_candidates: Optional[dict[str, Any]] = None,
    ) -> ToolGateDecision:
        has_provider_skill_class = any(
            str(item).strip() == "skill" or str(item).strip().startswith("provider:")
            for item in decision.suggested_tool_classes
        )
        if not decision.needs_external_system and not has_provider_skill_class:
            return decision

        metadata_confidence = 0.0
        if isinstance(metadata_candidates, dict):
            metadata_confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
        meaningful_provider_overlap = self._has_meaningful_provider_overlap(
            decision=decision,
            user_message=user_message,
            recent_history=recent_history,
            available_tools=available_tools,
        )
        provider_hint_tokens = self._collect_provider_hint_tokens_from_deps(deps)
        looks_related = self._looks_provider_or_skill_related(
            user_message=user_message,
            recent_history=recent_history,
            available_tools=available_tools,
            provider_hint_tokens=provider_hint_tokens,
        )
        if (
            meaningful_provider_overlap
            or looks_related
            or metadata_confidence >= self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE
        ):
            return decision

        rewritten = decision.model_copy(deep=True)
        rewritten.needs_external_system = False
        rewritten.suggested_tool_classes = [
            item
            for item in rewritten.suggested_tool_classes
            if not (item == "skill" or item.startswith("provider:"))
        ]
        if rewritten.needs_live_data or rewritten.needs_grounded_verification:
            rewritten.needs_tool = True
            if rewritten.policy is ToolPolicyMode.ANSWER_DIRECT:
                rewritten.policy = ToolPolicyMode.PREFER_TOOL
        elif not rewritten.suggested_tool_classes:
            rewritten.needs_tool = False
            rewritten.policy = ToolPolicyMode.ANSWER_DIRECT
        rewritten.confidence = min(float(rewritten.confidence), 0.49)
        rewritten.reason = (
            f"{rewritten.reason} Consistency guard removed unsupported provider/skill routing "
            "because request-to-provider relevance was too weak."
        ).strip()
        return rewritten
    def _has_meaningful_provider_overlap(
        self,
        *,
        decision: ToolGateDecision,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> bool:
        request_text = " ".join(
            [
                str(user_message or "").strip(),
                " ".join(
                    str(item.get("content", "") or "").strip()
                    for item in recent_history[-4:]
                    if isinstance(item, dict)
                ),
            ]
        ).strip()
        request_tokens = self._tokenize_classifier_fallback_text(request_text)
        if not request_tokens:
            return False

        targeted_classes = {
            item
            for item in decision.suggested_tool_classes
            if item == "skill" or item.startswith("provider:")
        }
        provider_tools: list[dict[str, Any]] = []
        for tool in available_tools:
            capability = self._resolve_provider_skill_capability(tool)
            if not (capability == "skill" or capability.startswith("provider:")):
                continue
            if targeted_classes and capability not in targeted_classes:
                continue
            provider_tools.append(tool)
        if not provider_tools:
            return False

        score = 0
        for tool in provider_tools:
            token_bag = " ".join(
                [
                    str(tool.get("name", "") or ""),
                    str(tool.get("provider_type", "") or ""),
                    str(tool.get("capability_class", "") or ""),
                    " ".join(str(group_id or "") for group_id in (tool.get("group_ids", []) or [])),
                ]
            ).strip()
            tool_tokens = self._tokenize_classifier_fallback_text(token_bag)
            overlap = request_tokens.intersection(tool_tokens)
            if not overlap:
                continue
            score += sum(2 if len(token) >= 3 else 1 for token in overlap)
            if score >= 3:
                return True
        return False
    @staticmethod
    def _looks_like_follow_up_request(message: str) -> bool:
        text = " ".join((message or "").split())
        if not text:
            return False
        lowered = text.lower()
        question_count = text.count("?") + text.count("？")
        numbered_choices = len(re.findall(r"(?:^|[\s\n])(?:1[\)\.]|2[\)\.]|3[\)\.])", text))
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
            "\u8bf7\u56de\u590d",
            "\u56de\u590d\u6211",
            "\u8bf7\u786e\u8ba4",
            "\u786e\u8ba4\u4e00\u4e0b",
            "\u8865\u5145",
            "\u544a\u8bc9\u6211",
            "\u9009\u62e9",
            "\u6307\u5b9a",
            "\u9009\u9879",
            "\u4efb\u9009",
        )
        marker_hits = sum(1 for marker in interaction_markers if marker in lowered or marker in text)
        if numbered_choices >= 2 and marker_hits >= 1:
            return True
        if question_count >= 2 and marker_hits >= 1:
            return True
        if question_count >= 1 and marker_hits >= 2:
            return True
        return False
    @staticmethod
    def _looks_provider_or_skill_related(
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        provider_hint_tokens: Optional[set[str]] = None,
    ) -> bool:
        history_text = " ".join(
            str(item.get("content", "") or "").strip()
            for item in recent_history[-4:]
            if isinstance(item, dict)
        )
        request_text = f"{user_message} {history_text}".strip()
        request_tokens = RunnerToolGateRoutingMixin._tokenize_classifier_fallback_text(request_text)
        if not request_tokens:
            return False

        metadata_tokens: set[str] = set()
        metadata_blob_parts: list[str] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            capability = str(tool.get("capability_class", "") or "").strip()
            if not (capability.startswith("provider:") or capability == "skill"):
                continue
            metadata_text = " ".join(
                [
                    str(tool.get("name", "") or ""),
                    str(tool.get("provider_type", "") or ""),
                    capability,
                    " ".join(str(group_id or "") for group_id in (tool.get("group_ids", []) or [])),
                ]
            ).strip()
            metadata_tokens.update(
                RunnerToolGateRoutingMixin._tokenize_classifier_fallback_text(metadata_text)
            )
            if metadata_text:
                metadata_blob_parts.append(metadata_text.lower())

        if provider_hint_tokens:
            metadata_tokens.update(provider_hint_tokens)
            metadata_blob_parts.extend(str(token).lower() for token in provider_hint_tokens if token)

        if not metadata_tokens:
            return False
        if request_tokens.intersection(metadata_tokens):
            return True
        metadata_blob = " ".join(metadata_blob_parts)
        if not metadata_blob:
            return False
        for token in request_tokens:
            if len(token) < 3:
                continue
            if token in metadata_blob:
                return True
        return False
    @staticmethod
    def _tokenize_classifier_fallback_text(text: str) -> set[str]:
        normalized = " ".join((text or "").split()).strip().lower()
        if not normalized:
            return set()
        tokens: set[str] = set()
        for token in re.findall(r"[a-z0-9_:-]{2,}", normalized):
            tokens.add(token)
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            chunk = chunk.strip()
            if not chunk:
                continue
            tokens.add(chunk)
            if len(chunk) <= 2:
                continue
            # Add CJK bigrams for robust overlap checks in mixed/long Chinese queries.
            for idx in range(0, len(chunk) - 1):
                tokens.add(chunk[idx : idx + 2])
        return tokens
    def _collect_provider_hint_tokens_from_deps(self, deps: Optional[SkillDeps]) -> set[str]:
        contexts = self._collect_provider_contexts_from_deps(deps)
        tokens: set[str] = set()
        for provider_type, ctx in contexts.items():
            tokens.update(self._tokenize_classifier_fallback_text(str(provider_type or "")))
            parts = [
                str(ctx.get("display_name", "") or ""),
                " ".join(str(item) for item in (ctx.get("aliases", []) or [])),
                " ".join(str(item) for item in (ctx.get("keywords", []) or [])),
            ]
            for part in parts:
                tokens.update(self._tokenize_classifier_fallback_text(part))
        return tokens
    @staticmethod
    def _collect_provider_contexts_from_deps(deps: Optional[SkillDeps]) -> dict[str, dict[str, Any]]:
        if deps is None or not isinstance(getattr(deps, "extra", None), dict):
            return {}
        extra = deps.extra
        registry = extra.get("_service_provider_registry")
        if registry is None:
            return {}
        get_contexts = getattr(registry, "get_all_provider_contexts", None)
        if not callable(get_contexts):
            return {}
        try:
            contexts = get_contexts()
        except Exception:
            return {}
        if not isinstance(contexts, dict):
            return {}

        normalized: dict[str, dict[str, Any]] = {}
        for provider_type, ctx in contexts.items():
            provider_key = str(provider_type or "").strip().lower()
            if not provider_key:
                continue
            if hasattr(ctx, "__dict__"):
                payload = {
                    "display_name": str(getattr(ctx, "display_name", "") or ""),
                    "description": str(getattr(ctx, "description", "") or ""),
                    "aliases": list(getattr(ctx, "aliases", []) or []),
                    "keywords": list(getattr(ctx, "keywords", []) or []),
                    "capabilities": list(getattr(ctx, "capabilities", []) or []),
                    "use_when": list(getattr(ctx, "use_when", []) or []),
                    "avoid_when": list(getattr(ctx, "avoid_when", []) or []),
                }
            elif isinstance(ctx, dict):
                payload = {
                    "display_name": str(ctx.get("display_name", "") or ""),
                    "description": str(ctx.get("description", "") or ""),
                    "aliases": list(ctx.get("aliases", []) or []),
                    "keywords": list(ctx.get("keywords", []) or []),
                    "capabilities": list(ctx.get("capabilities", []) or []),
                    "use_when": list(ctx.get("use_when", []) or []),
                    "avoid_when": list(ctx.get("avoid_when", []) or []),
                }
            else:
                payload = {}
            normalized[provider_key] = payload
        return normalized
    def _build_provider_hint_docs(
        self,
        *,
        deps: Optional[SkillDeps],
        available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        contexts = self._collect_provider_contexts_from_deps(deps)
        if not contexts:
            return []

        docs: list[dict[str, Any]] = []
        for provider_type in sorted(contexts.keys()):
            ctx = contexts.get(provider_type, {})
            matched_tools = [
                tool
                for tool in available_tools
                if str(tool.get("provider_type", "") or "").strip().lower() == provider_type
                or str(tool.get("capability_class", "") or "").strip().lower() == f"provider:{provider_type}"
            ]
            if not matched_tools:
                continue

            tool_names = sorted(
                {
                    str(tool.get("name", "") or "").strip()
                    for tool in matched_tools
                    if str(tool.get("name", "") or "").strip()
                }
            )
            capability_classes = sorted(
                {
                    str(tool.get("capability_class", "") or "").strip()
                    for tool in matched_tools
                    if str(tool.get("capability_class", "") or "").strip()
                }
            )
            group_ids = sorted(
                {
                    str(group_id or "").strip()
                    for tool in matched_tools
                    for group_id in (tool.get("group_ids", []) or [])
                    if str(group_id or "").strip()
                }
            )
            provider_group = f"group:{provider_type}"
            if provider_group not in group_ids:
                group_ids.append(provider_group)

            priority_values = []
            for tool in matched_tools:
                try:
                    priority_values.append(int(tool.get("priority", 100) or 100))
                except (TypeError, ValueError):
                    continue
            priority = max(priority_values) if priority_values else 100

            hint_text = self._build_hint_text(
                display_name=str(ctx.get("display_name", "") or provider_type),
                description=str(ctx.get("description", "") or ""),
                aliases=ctx.get("aliases", []),
                keywords=ctx.get("keywords", []),
                capabilities=ctx.get("capabilities", []),
                use_when=ctx.get("use_when", []),
                avoid_when=ctx.get("avoid_when", []),
            )
            docs.append(
                {
                    "hint_id": f"provider:{provider_type}",
                    "hint_type": "provider",
                    "provider_type": provider_type,
                    "display_name": str(ctx.get("display_name", "") or provider_type),
                    "description": str(ctx.get("description", "") or ""),
                    "aliases": list(ctx.get("aliases", []) or []),
                    "keywords": list(ctx.get("keywords", []) or []),
                    "capabilities": list(ctx.get("capabilities", []) or []),
                    "use_when": list(ctx.get("use_when", []) or []),
                    "avoid_when": list(ctx.get("avoid_when", []) or []),
                    "tool_names": tool_names,
                    "group_ids": group_ids,
                    "capability_classes": capability_classes,
                    "hint_text": hint_text,
                    "priority": priority,
                }
            )
        return docs
    def _build_skill_hint_docs(
        self,
        *,
        deps: Optional[SkillDeps],
        available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if deps is None or not isinstance(getattr(deps, "extra", None), dict):
            return []
        md_skills = deps.extra.get("md_skills_snapshot")
        if not isinstance(md_skills, list):
            return []

        available_tool_by_name: dict[str, dict[str, Any]] = {}
        for tool in available_tools:
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            available_tool_by_name[name] = tool

        docs: list[dict[str, Any]] = []
        for entry in md_skills:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            declared_names = self._extract_md_declared_tool_names(metadata)
            matched_names = [
                name
                for name in declared_names
                if name in available_tool_by_name
            ]
            if not matched_names:
                continue
            matched_tools = [available_tool_by_name[name] for name in matched_names]
            provider_type = str(
                metadata.get("provider_type", "")
                or entry.get("provider", "")
                or ""
            ).strip().lower()
            capability_classes = sorted(
                {
                    str(tool.get("capability_class", "") or "").strip()
                    for tool in matched_tools
                    if str(tool.get("capability_class", "") or "").strip()
                }
            )
            group_ids = sorted(
                {
                    str(group_id or "").strip()
                    for tool in matched_tools
                    for group_id in (tool.get("group_ids", []) or [])
                    if str(group_id or "").strip()
                }
            )
            priority_values = []
            for tool in matched_tools:
                try:
                    priority_values.append(int(tool.get("priority", 100) or 100))
                except (TypeError, ValueError):
                    continue
            priority = max(priority_values) if priority_values else 100
            qualified_name = str(entry.get("qualified_name", "") or "").strip()
            skill_name = str(entry.get("name", "") or "").strip() or qualified_name or "skill"

            hint_text = self._build_hint_text(
                display_name=skill_name,
                description=str(entry.get("description", "") or ""),
                aliases=[qualified_name] if qualified_name else [],
                keywords=metadata.get("triggers", []),
                capabilities=metadata.get("examples", []),
                use_when=metadata.get("use_when", []),
                avoid_when=metadata.get("avoid_when", []),
            )
            docs.append(
                {
                    "hint_id": f"skill:{qualified_name or skill_name}",
                    "hint_type": "skill",
                    "skill_name": skill_name,
                    "qualified_skill_name": qualified_name,
                    "provider_type": provider_type,
                    "display_name": skill_name,
                    "description": str(entry.get("description", "") or ""),
                    "aliases": [qualified_name] if qualified_name else [],
                    "keywords": list(metadata.get("triggers", []) or []),
                    "capabilities": list(metadata.get("examples", []) or []),
                    "use_when": list(metadata.get("use_when", []) or []),
                    "avoid_when": list(metadata.get("avoid_when", []) or []),
                    "tool_names": sorted(matched_names),
                    "group_ids": group_ids,
                    "capability_classes": capability_classes,
                    "hint_text": hint_text,
                    "priority": priority,
                }
            )
        return docs

    def _build_tool_hint_docs(
        self,
        *,
        available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name", "") or "").strip()
            if not tool_name:
                continue
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            capability_class = str(tool.get("capability_class", "") or "").strip().lower()
            group_ids = [
                str(item).strip()
                for item in (tool.get("group_ids", []) or [])
                if str(item).strip()
            ]
            priority = 100
            try:
                priority = int(tool.get("priority", 100) or 100)
            except (TypeError, ValueError):
                priority = 100
            hint_text = self._build_hint_text(
                display_name=tool_name,
                description=str(tool.get("description", "") or ""),
                aliases=list(tool.get("aliases", []) or []),
                keywords=list(tool.get("keywords", []) or []),
                capabilities=[capability_class] if capability_class else [],
                use_when=list(tool.get("use_when", []) or []),
                avoid_when=list(tool.get("avoid_when", []) or []),
            )
            docs.append(
                {
                    "hint_id": f"tool:{tool_name}",
                    "hint_type": "tool",
                    "tool_name": tool_name,
                    "provider_type": provider_type,
                    "display_name": tool_name,
                    "description": str(tool.get("description", "") or ""),
                    "aliases": list(tool.get("aliases", []) or []),
                    "keywords": list(tool.get("keywords", []) or []),
                    "capabilities": [capability_class] if capability_class else [],
                    "use_when": list(tool.get("use_when", []) or []),
                    "avoid_when": list(tool.get("avoid_when", []) or []),
                    "tool_names": [tool_name],
                    "group_ids": group_ids,
                    "capability_classes": [capability_class] if capability_class else [],
                    "hint_text": hint_text,
                    "priority": priority,
                }
            )
        return docs

    def _build_builtin_tool_hint_docs(
        self,
        *,
        available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Backward-compatible wrapper for legacy callers."""
        return self._build_tool_hint_docs(available_tools=available_tools)
    @staticmethod
    def _extract_md_declared_tool_names(metadata: dict[str, Any]) -> list[str]:
        names: list[str] = []
        single_name = str(metadata.get("tool_name", "") or "").strip()
        if single_name:
            names.append(single_name)
        for key, value in metadata.items():
            key_text = str(key or "").strip()
            if not key_text.startswith("tool_") or not key_text.endswith("_name"):
                continue
            tool_name = str(value or "").strip()
            if tool_name:
                names.append(tool_name)
        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped
    @staticmethod
    def _build_hint_text(
        *,
        display_name: str,
        description: str,
        aliases: list[Any],
        keywords: list[Any],
        capabilities: list[Any],
        use_when: list[Any],
        avoid_when: list[Any],
    ) -> str:
        blocks: list[str] = []
        title = str(display_name or "").strip()
        if title:
            blocks.append(f"name: {title}")
        desc = str(description or "").strip()
        if desc:
            blocks.append(f"description: {desc}")

        def _append_list(label: str, values: list[Any]) -> None:
            normalized = [str(item).strip() for item in values if str(item).strip()]
            if normalized:
                blocks.append(f"{label}: " + "; ".join(normalized))

        _append_list("aliases", aliases if isinstance(aliases, list) else [])
        _append_list("keywords", keywords if isinstance(keywords, list) else [])
        _append_list("capabilities", capabilities if isinstance(capabilities, list) else [])
        _append_list("use_when", use_when if isinstance(use_when, list) else [])
        _append_list("avoid_when", avoid_when if isinstance(avoid_when, list) else [])
        return " | ".join(blocks).strip()

    @staticmethod
    def _normalize_hint_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            values = [values] if values else []
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _score_metadata_hint_doc(
        self,
        *,
        doc: dict[str, Any],
        request_tokens: set[str],
        request_text_lower: str,
        allow_weak_only: bool,
    ) -> tuple[int, list[str], bool]:
        matched_tokens: list[str] = []
        score = 0
        has_strong_anchor = False

        def _record(token: str) -> None:
            normalized = str(token or "").strip().lower()
            if normalized:
                matched_tokens.append(normalized)

        provider_type = str(doc.get("provider_type", "") or "").strip().lower()
        display_name = str(doc.get("display_name", "") or "").strip()
        aliases = self._normalize_hint_list(doc.get("aliases", []))
        keywords = self._normalize_hint_list(doc.get("keywords", []))
        capabilities = self._normalize_hint_list(doc.get("capabilities", []))
        use_when = self._normalize_hint_list(doc.get("use_when", []))
        avoid_when = self._normalize_hint_list(doc.get("avoid_when", []))
        tool_names = self._normalize_hint_list(doc.get("tool_names", []))
        capability_classes = self._normalize_hint_list(doc.get("capability_classes", []))
        group_ids = self._normalize_hint_list(doc.get("group_ids", []))

        if provider_type and provider_type in request_text_lower:
            score += 12
            has_strong_anchor = True
            _record(provider_type)

        strong_lists = [aliases, keywords, tool_names, capability_classes, group_ids]
        if display_name:
            strong_lists.append([display_name])

        for values in strong_lists:
            overlap = sorted(
                request_tokens.intersection(
                    self._tokenize_classifier_fallback_text(" ".join(values))
                )
            )
            if not overlap:
                continue
            has_strong_anchor = True
            score += len(overlap) * 6
            for token in overlap[:8]:
                _record(token)

        if not has_strong_anchor and not allow_weak_only:
            return 0, [], False

        weak_lists = [capabilities, use_when, avoid_when]
        description = str(doc.get("description", "") or "").strip()
        if description:
            weak_lists.append([description])
        hint_text = str(doc.get("hint_text", "") or "").strip()
        if hint_text:
            weak_lists.append([hint_text])

        for values in weak_lists:
            overlap = sorted(
                request_tokens.intersection(
                    self._tokenize_classifier_fallback_text(" ".join(values))
                )
            )
            if not overlap:
                continue
            score += len(overlap)
            for token in overlap[:6]:
                _record(token)

        try:
            priority = int(doc.get("priority", 100) or 100)
        except (TypeError, ValueError):
            priority = 100
        if score > 0 and priority <= 60:
            score += 1
        return score, self._dedupe_preserve_order(matched_tokens), has_strong_anchor
    def _recall_provider_skill_candidates_from_metadata(
        self,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
        used_follow_up_context: bool,
        available_tools: list[dict[str, Any]],
        provider_hint_docs: list[dict[str, Any]],
        skill_hint_docs: list[dict[str, Any]],
        tool_hint_docs: list[dict[str, Any]],
        top_k_provider: int,
        top_k_skill: int,
    ) -> dict[str, Any]:
        top_k_provider = max(1, int(top_k_provider or 1))
        top_k_skill = max(1, int(top_k_skill or 1))
        history_text = ""
        if used_follow_up_context:
            history_text = " ".join(
                str(item.get("content", "") or "").strip()
                for item in recent_history[-4:]
                if isinstance(item, dict)
            )
        request_text = " ".join([str(user_message or "").strip(), history_text]).strip()
        request_tokens = self._tokenize_classifier_fallback_text(request_text)
        if not request_tokens:
            return {
                "provider_candidates": [],
                "skill_candidates": [],
                "preferred_provider_types": [],
                "preferred_group_ids": [],
                "preferred_capability_classes": [],
                "preferred_tool_names": [],
                "confidence": 0.0,
                "reason": "metadata_recall_empty_request",
            }

        request_text_lower = request_text.lower()
        tool_name_set = {
            str(tool.get("name", "") or "").strip()
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }

        provider_ranked: list[dict[str, Any]] = []
        for doc in provider_hint_docs:
            if not isinstance(doc, dict):
                continue
            score, matched_tokens, has_strong_anchor = self._score_metadata_hint_doc(
                doc=doc,
                request_tokens=request_tokens,
                request_text_lower=request_text_lower,
                allow_weak_only=used_follow_up_context,
            )
            if score <= 0:
                continue
            tool_names = [
                str(name).strip()
                for name in (doc.get("tool_names", []) or [])
                if str(name).strip() in tool_name_set
            ]
            provider_ranked.append(
                {
                    "hint_id": str(doc.get("hint_id", "") or "").strip(),
                    "provider_type": str(doc.get("provider_type", "") or "").strip().lower(),
                    "score": score,
                    "has_strong_anchor": has_strong_anchor,
                    "matched_tokens": matched_tokens,
                    "tool_names": tool_names,
                    "group_ids": [
                        str(item).strip()
                        for item in (doc.get("group_ids", []) or [])
                        if str(item).strip()
                    ],
                    "capability_classes": [
                        str(item).strip().lower()
                        for item in (doc.get("capability_classes", []) or [])
                        if str(item).strip()
                    ],
                }
            )
        provider_ranked.sort(key=lambda item: (-int(item.get("score", 0) or 0), str(item.get("hint_id", ""))))
        provider_top = provider_ranked[:top_k_provider]

        skill_ranked: list[dict[str, Any]] = []
        for doc in skill_hint_docs:
            if not isinstance(doc, dict):
                continue
            score, matched_tokens, has_strong_anchor = self._score_metadata_hint_doc(
                doc=doc,
                request_tokens=request_tokens,
                request_text_lower=request_text_lower,
                allow_weak_only=used_follow_up_context,
            )
            if score <= 0:
                continue
            tool_names = [
                str(name).strip()
                for name in (doc.get("tool_names", []) or [])
                if str(name).strip() in tool_name_set
            ]
            skill_ranked.append(
                {
                    "hint_id": str(doc.get("hint_id", "") or "").strip(),
                    "skill_name": str(doc.get("skill_name", "") or "").strip(),
                    "qualified_skill_name": str(doc.get("qualified_skill_name", "") or "").strip(),
                    "provider_type": str(doc.get("provider_type", "") or "").strip().lower(),
                    "score": score,
                    "has_strong_anchor": has_strong_anchor,
                    "matched_tokens": matched_tokens,
                    "tool_names": tool_names,
                    "group_ids": [
                        str(item).strip()
                        for item in (doc.get("group_ids", []) or [])
                        if str(item).strip()
                    ],
                    "capability_classes": [
                        str(item).strip().lower()
                        for item in (doc.get("capability_classes", []) or [])
                        if str(item).strip()
                    ],
                }
            )
        skill_ranked.sort(key=lambda item: (-int(item.get("score", 0) or 0), str(item.get("hint_id", ""))))
        skill_top = skill_ranked[:top_k_skill]

        tool_ranked: list[dict[str, Any]] = []
        for doc in tool_hint_docs:
            if not isinstance(doc, dict):
                continue
            score, matched_tokens, has_strong_anchor = self._score_metadata_hint_doc(
                doc=doc,
                request_tokens=request_tokens,
                request_text_lower=request_text_lower,
                allow_weak_only=used_follow_up_context,
            )
            if score <= 0:
                continue
            tool_name = str(doc.get("tool_name", "") or "").strip()
            if tool_name and tool_name not in tool_name_set:
                continue
            tool_ranked.append(
                {
                    "hint_id": str(doc.get("hint_id", "") or "").strip(),
                    "tool_name": tool_name,
                    "provider_type": str(doc.get("provider_type", "") or "").strip().lower(),
                    "score": score,
                    "has_strong_anchor": has_strong_anchor,
                    "matched_tokens": matched_tokens,
                    "tool_names": [tool_name] if tool_name else [],
                    "group_ids": [
                        str(item).strip()
                        for item in (doc.get("group_ids", []) or [])
                        if str(item).strip()
                    ],
                    "capability_classes": [
                        str(item).strip().lower()
                        for item in (doc.get("capability_classes", []) or [])
                        if str(item).strip()
                    ],
                }
            )
        tool_ranked.sort(key=lambda item: (-int(item.get("score", 0) or 0), str(item.get("hint_id", ""))))
        tool_top = self._select_tool_ranked_top_entries(
            tool_ranked=tool_ranked,
            limit=max(1, min(4, top_k_skill)),
        )

        preferred_provider_types = self._dedupe_preserve_order(
            [
                str(item.get("provider_type", "") or "").strip().lower()
                for item in provider_top + skill_top + tool_top
                if str(item.get("provider_type", "") or "").strip()
            ]
        )
        preferred_group_ids = self._dedupe_preserve_order(
            [
                str(group_id).strip()
                for item in provider_top + skill_top + tool_top
                for group_id in (item.get("group_ids", []) or [])
                if str(group_id).strip()
            ]
        )
        preferred_capability_classes = self._dedupe_preserve_order(
            [
                str(capability).strip().lower()
                for item in provider_top + skill_top + tool_top
                for capability in (item.get("capability_classes", []) or [])
                if str(capability).strip()
            ]
        )
        tool_name_scores: dict[str, tuple[int, int, str]] = {}
        for item in provider_top + skill_top + tool_top:
            score = int(item.get("score", 0) or 0)
            hint_type = "tool"
            hint_id = str(item.get("hint_id", "") or "").strip()
            if hint_id.startswith("provider:"):
                hint_type = "provider"
            elif hint_id.startswith("skill:"):
                hint_type = "skill"
            source_rank = {"tool": 0, "skill": 1, "provider": 2}.get(hint_type, 3)
            for name in (item.get("tool_names", []) or []):
                tool_name = str(name).strip()
                if not tool_name or tool_name not in tool_name_set:
                    continue
                current = tool_name_scores.get(tool_name)
                candidate = (score, -source_rank, hint_id)
                if current is None or candidate > current:
                    tool_name_scores[tool_name] = candidate

        preferred_tool_names = [
            item[0]
            for item in sorted(
                tool_name_scores.items(),
                key=lambda pair: (-pair[1][0], -pair[1][1], pair[0].lower()),
            )
        ][:12]

        total_score = sum(int(item.get("score", 0) or 0) for item in provider_top + skill_top + tool_top)
        confidence_denominator = max(24, len(request_tokens) * 8)
        confidence = min(1.0, float(total_score) / float(confidence_denominator))
        reason = (
            "metadata_recall_matched"
            if (provider_top or skill_top or tool_top)
            else "metadata_recall_no_match"
        )
        return {
            "provider_candidates": provider_top,
            "skill_candidates": skill_top,
            "tool_candidates": tool_top,
            "preferred_provider_types": preferred_provider_types,
            "preferred_group_ids": preferred_group_ids,
            "preferred_capability_classes": preferred_capability_classes,
            "preferred_tool_names": preferred_tool_names,
            "confidence": confidence,
            "reason": reason,
        }
    def _apply_provider_hard_prefilter(
        self,
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        available_tools: list[dict[str, Any]],
        deps: Optional[SkillDeps] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        trace: dict[str, Any] = {
            "enabled": False,
            "before_count": len(available_tools),
            "after_count": len(available_tools),
            "target_provider_types": [],
            "target_capability_classes": [],
            "matched_provider_tool_count": 0,
            "retained_builtin_tools": [],
            "reason": "prefilter_not_enabled",
        }
        if not decision.needs_external_system:
            return available_tools, trace
        if not available_tools:
            trace["reason"] = "prefilter_empty_toolset"
            return available_tools, trace

        metadata_candidates: dict[str, Any] = {}
        if deps is not None and isinstance(getattr(deps, "extra", None), dict):
            raw = deps.extra.get("tool_metadata_candidates")
            if isinstance(raw, dict):
                metadata_candidates = dict(raw)

        target_provider_types: list[str] = []
        target_capability_classes: list[str] = []
        target_tool_names: list[str] = []
        explicit_provider_types: list[str] = []
        explicit_provider_capability_classes: list[str] = []

        target_provider_types.extend(
            str(item).strip().lower()
            for item in (metadata_candidates.get("preferred_provider_types", []) or [])
            if str(item).strip()
        )
        target_capability_classes.extend(
            str(item).strip().lower()
            for item in (metadata_candidates.get("preferred_capability_classes", []) or [])
            if str(item).strip()
        )
        target_tool_names.extend(
            str(item).strip()
            for item in (metadata_candidates.get("preferred_tool_names", []) or [])
            if str(item).strip()
        )

        for capability in decision.suggested_tool_classes:
            normalized = str(capability or "").strip().lower()
            if not normalized:
                continue
            target_capability_classes.append(normalized)
            if normalized.startswith("provider:"):
                provider_key = normalized.split(":", 1)[1].strip()
                target_provider_types.append(provider_key)
                explicit_provider_types.append(provider_key)
                explicit_provider_capability_classes.append(normalized)

        for candidate in match_result.tool_candidates:
            candidate_name = str(getattr(candidate, "name", "") or "").strip()
            candidate_capability = str(getattr(candidate, "capability_class", "") or "").strip().lower()
            if candidate_name:
                target_tool_names.append(candidate_name)
            if candidate_capability:
                target_capability_classes.append(candidate_capability)
            provider_type = ""
            if candidate_capability.startswith("provider:"):
                provider_type = candidate_capability.split(":", 1)[1].strip()
            else:
                provider_type = str(getattr(candidate, "provider_type", "") or "").strip().lower()
            if provider_type:
                target_provider_types.append(provider_type)

        active_provider_capability = self._resolve_active_provider_capability_class(
            deps=deps,
            provider_skill_classes=self._collect_provider_skill_capability_classes(available_tools),
        )
        if active_provider_capability:
            target_capability_classes.append(active_provider_capability.lower())
            if active_provider_capability.startswith("provider:"):
                target_provider_types.append(active_provider_capability.split(":", 1)[1].strip().lower())

        if explicit_provider_types:
            explicit_provider_types = self._dedupe_preserve_order(explicit_provider_types)
            explicit_provider_capability_classes = self._dedupe_preserve_order(
                explicit_provider_capability_classes
            )
            target_provider_types = list(explicit_provider_types)
            target_capability_classes = [
                capability
                for capability in target_capability_classes
                if not capability.startswith("provider:")
            ]
            target_capability_classes.extend(explicit_provider_capability_classes)

        target_provider_types = self._dedupe_preserve_order(
            [item for item in target_provider_types if item]
        )
        target_capability_classes = self._dedupe_preserve_order(
            [item for item in target_capability_classes if item]
        )
        target_tool_names = self._dedupe_preserve_order(
            [item for item in target_tool_names if item]
        )

        filtered_tools: list[dict[str, Any]] = []
        retained_builtin_tools: list[str] = []
        matched_provider_tool_count = 0
        for tool in available_tools:
            tool_name = str(tool.get("name", "") or "").strip()
            capability = str(tool.get("capability_class", "") or "").strip().lower()
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            is_provider_or_skill = self._is_provider_or_skill_tool(tool)

            if is_provider_or_skill:
                provider_match = (
                    provider_type in target_provider_types
                    if provider_type and target_provider_types
                    else False
                )
                capability_match = capability in target_capability_classes if capability else False
                name_match = tool_name in target_tool_names if tool_name else False
                implicit_provider_capability = f"provider:{provider_type}" if provider_type else ""
                implicit_match = (
                    implicit_provider_capability in target_capability_classes
                    if implicit_provider_capability
                    else False
                )
                if provider_match or capability_match or name_match or implicit_match:
                    filtered_tools.append(tool)
                    matched_provider_tool_count += 1
                continue

            if tool_name and tool_name in target_tool_names:
                filtered_tools.append(tool)
                retained_builtin_tools.append(tool_name)
                continue
            if capability and capability in target_capability_classes:
                filtered_tools.append(tool)
                retained_builtin_tools.append(tool_name or capability)

        if matched_provider_tool_count <= 0 or not filtered_tools:
            trace.update(
                {
                    "enabled": False,
                    "after_count": len(available_tools),
                    "target_provider_types": target_provider_types,
                    "target_capability_classes": target_capability_classes,
                    "matched_provider_tool_count": matched_provider_tool_count,
                    "retained_builtin_tools": retained_builtin_tools,
                    "reason": "prefilter_no_provider_match",
                }
            )
            return available_tools, trace

        trace.update(
            {
                "enabled": True,
                "after_count": len(filtered_tools),
                "target_provider_types": target_provider_types,
                "target_capability_classes": target_capability_classes,
                "matched_provider_tool_count": matched_provider_tool_count,
                "retained_builtin_tools": retained_builtin_tools,
                "reason": "prefilter_applied",
            }
        )
        return filtered_tools, trace
    @staticmethod
    def _is_provider_or_skill_tool(tool: dict[str, Any]) -> bool:
        capability = str(tool.get("capability_class", "") or "").strip().lower()
        provider_type = str(tool.get("provider_type", "") or "").strip().lower()
        category = str(tool.get("category", "") or "").strip().lower()
        if capability == "skill" or capability.startswith("provider:"):
            return True
        if provider_type:
            return True
        return category.startswith("provider") or "skill" in category
    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

