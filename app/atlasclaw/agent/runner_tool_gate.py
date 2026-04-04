from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from contextlib import asynccontextmanager, nullcontext
from typing import Any, Optional

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.deps import SkillDeps


logger = logging.getLogger(__name__)


class _ModelToolGateClassifier:
    """Model-backed classifier used by the runtime when a direct model call is available."""

    def __init__(
        self,
        *,
        runner: "AgentRunner",
        deps: SkillDeps,
        available_tools: list[dict[str, Any]],
        agent: Optional[Any] = None,
        agent_resolver: Optional[Any] = None,
    ) -> None:
        self._runner = runner
        self._agent = agent
        self._agent_resolver = agent_resolver
        self._deps = deps
        self._available_tools = available_tools

    async def _resolve_agent(self) -> Optional[Any]:
        if self._agent is not None:
            return self._agent
        if self._agent_resolver is None:
            return None
        resolved = self._agent_resolver()
        if inspect.isawaitable(resolved):
            resolved = await resolved
        self._agent = resolved
        return resolved

    async def classify(
        self,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> Optional[ToolGateDecision]:
        classifier_agent = await self._resolve_agent()
        if classifier_agent is None:
            return None
        return await self._runner._classify_tool_gate_with_model(
            agent=classifier_agent,
            deps=self._deps,
            user_message=user_message,
            recent_history=recent_history,
            available_tools=self._available_tools,
        )


class RunnerToolGateMixin:
    def _resolve_tool_gate_classifier(
        self,
        *,
        agent: Any,
        deps: SkillDeps,
        available_tools: list[dict[str, Any]],
    ) -> Optional[Any]:
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        explicit_classifier = extra.get("tool_gate_classifier")
        if explicit_classifier is not None:
            return explicit_classifier
        if not self.tool_gate_model_classifier_enabled:
            return None
        classifier_agent = self._select_tool_gate_classifier_agent(agent)
        if classifier_agent is None:
            return None
        return _ModelToolGateClassifier(
            runner=self,
            deps=deps,
            available_tools=available_tools,
            agent=classifier_agent if not callable(classifier_agent) else None,
            agent_resolver=classifier_agent if callable(classifier_agent) else None,
        )

    def _select_tool_gate_classifier_agent(self, runtime_agent: Any) -> Optional[Any]:
        if self.agent_factory is not None and self.token_policy is not None:
            classifier_token = self._select_tool_gate_classifier_token()
            if classifier_token is not None:
                async def _resolver() -> Any:
                    built = self.agent_factory(self.agent_id, classifier_token)
                    if inspect.isawaitable(built):
                        built = await built
                    return built if hasattr(built, "run") else None

                return _resolver
        if hasattr(runtime_agent, "run"):
            return runtime_agent
        return None

    def _select_tool_gate_classifier_token(self) -> Optional[Any]:
        if self.token_policy is None:
            return None
        pool = self.token_policy.token_pool
        ranked: list[tuple[int, int, int, Any]] = []
        for token_id, token in pool.tokens.items():
            health = pool.get_token_health(token_id)
            is_healthy = 1 if (health is None or health.is_healthy) else 0
            ranked.append((is_healthy, int(getattr(token, "priority", 0) or 0), int(getattr(token, "weight", 0) or 0), token))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return ranked[0][3]

    def _normalize_tool_gate_decision(self, decision: ToolGateDecision) -> ToolGateDecision:
        """Normalize gate output and avoid over-aggressive mandatory-tool enforcement."""
        if not isinstance(decision, ToolGateDecision):
            return ToolGateDecision(
                reason="Tool gate decision is invalid; fallback to direct-answer mode.",
                confidence=0.0,
                policy=ToolPolicyMode.ANSWER_DIRECT,
            )

        normalized = decision.model_copy(deep=True)
        normalized.suggested_tool_classes = [
            item.strip()
            for item in normalized.suggested_tool_classes
            if isinstance(item, str) and item.strip()
        ]

        has_provider_skill_hint = any(
            item == "skill" or item.startswith("provider:")
            for item in normalized.suggested_tool_classes
        )
        strict_web_grounding = bool(normalized.needs_live_data)
        strict_provider_or_skill = bool(normalized.needs_external_system) or has_provider_skill_hint
        strict_tool_enforcement = strict_web_grounding or strict_provider_or_skill

        if strict_provider_or_skill:
            normalized.needs_external_system = True
            normalized.needs_tool = True
            if normalized.policy is not ToolPolicyMode.MUST_USE_TOOL:
                normalized.policy = ToolPolicyMode.MUST_USE_TOOL
            normalized.confidence = max(
                normalized.confidence,
                self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
            )
            if "provider/skill direct tools" not in normalized.reason.lower():
                normalized.reason = (
                    f"{normalized.reason} External-system/provider-skill intent requires direct tool execution."
                ).strip()

        if strict_web_grounding and normalized.policy is ToolPolicyMode.ANSWER_DIRECT:
            normalized.policy = ToolPolicyMode.MUST_USE_TOOL
            normalized.needs_tool = True
            normalized.confidence = max(
                normalized.confidence,
                self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
            )
            normalized.reason = (
                f"{normalized.reason} Live grounded requests require tool-backed verification."
            ).strip()

        has_tool_hints = bool(normalized.suggested_tool_classes)
        strict_need = self._tool_gate_has_strict_need(normalized)
        expects_tool = normalized.needs_tool or has_tool_hints or strict_need

        if normalized.policy is ToolPolicyMode.MUST_USE_TOOL and (
            (not strict_tool_enforcement and normalized.confidence < self.TOOL_GATE_MUST_USE_MIN_CONFIDENCE)
            or not expects_tool
            or not strict_need
        ):
            normalized.policy = (
                ToolPolicyMode.PREFER_TOOL
                if expects_tool
                else ToolPolicyMode.ANSWER_DIRECT
            )
            normalized.reason = (
                f"{normalized.reason} Downgraded from must_use_tool due to insufficient confidence or strict-need signals."
            ).strip()

        if normalized.policy is ToolPolicyMode.ANSWER_DIRECT and expects_tool:
            normalized.policy = ToolPolicyMode.PREFER_TOOL

        return normalized

    def _align_external_system_intent(
        self,
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        available_tools: list[dict[str, Any]],
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
        selected_classes = requested_provider_skill_classes or provider_skill_classes
        selected_classes = [capability for capability in selected_classes if capability in provider_skill_classes]
        if not selected_classes:
            selected_classes = provider_skill_classes

        rewritten = decision.model_copy(deep=True)
        rewritten.needs_tool = True
        rewritten.policy = ToolPolicyMode.MUST_USE_TOOL
        rewritten.confidence = max(rewritten.confidence, self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE)
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
            capability = str(tool.get("capability_class", "") or "").strip()
            lowered_name = str(tool.get("name", "") or "").strip().lower()
            lowered_description = str(tool.get("description", "") or "").strip().lower()
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            category = str(tool.get("category", "") or "").strip().lower()

            if not capability:
                if provider_type:
                    capability = f"provider:{provider_type}"
                elif "jira" in lowered_name or "jira" in lowered_description:
                    capability = "provider:jira"
                elif category.startswith("provider") or "provider:" in lowered_description:
                    capability = "provider:generic"
                elif "skill" in category or (
                    "skill" in lowered_description and lowered_name not in {"web_search", "web_fetch"}
                ):
                    capability = "skill"

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
    def _tool_gate_has_strict_need(decision: ToolGateDecision) -> bool:
        return any(
            [
                bool(decision.needs_live_data),
                bool(decision.needs_grounded_verification),
                bool(decision.needs_external_system),
                bool(decision.needs_browser_interaction),
                bool(decision.needs_private_context),
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
        if len(re.sub(r"\s+", "", normalized_user_message)) > 32:
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

        if last_assistant_index is None or not self._looks_like_follow_up_request(last_assistant_message):
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

        combined = f"{previous_user_message} {normalized_user_message}".strip()
        return combined, combined != normalized_user_message

    def _apply_no_classifier_follow_up_fallback(
        self,
        *,
        decision: ToolGateDecision,
        used_follow_up_context: bool,
        available_tools: list[dict[str, Any]],
    ) -> ToolGateDecision:
        if not used_follow_up_context:
            return decision
        if decision.policy is ToolPolicyMode.MUST_USE_TOOL:
            return decision

        suggested: list[str] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            capability = str(tool.get("capability_class", "") or "").strip()
            name = str(tool.get("name", "") or "").strip()
            if capability in {"web_search", "web_fetch", "browser", "weather"}:
                if capability not in suggested:
                    suggested.append(capability)
                continue
            if name == "web_search" and "web_search" not in suggested:
                suggested.append("web_search")
            if name == "web_fetch" and "web_fetch" not in suggested:
                suggested.append("web_fetch")

        if not suggested:
            return decision

        rewritten = decision.model_copy(deep=True)
        rewritten.needs_tool = True
        rewritten.needs_grounded_verification = True
        rewritten.policy = ToolPolicyMode.PREFER_TOOL
        rewritten.confidence = max(
            rewritten.confidence,
            self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
        )
        if not rewritten.suggested_tool_classes:
            rewritten.suggested_tool_classes = suggested
        if "follow-up clarification" not in rewritten.reason.lower():
            rewritten.reason = (
                f"{rewritten.reason} Follow-up clarification context prefers tool-backed verification."
            ).strip()
        return rewritten

    def _inject_tool_policy(
        self,
        *,
        deps: SkillDeps,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
    ) -> None:
        """Inject per-run tool-policy context for prompt building."""
        if not isinstance(deps.extra, dict):
            deps.extra = {}

        required_tools: list[str] = []
        for candidate in match_result.tool_candidates:
            name = str(getattr(candidate, "name", "") or "").strip()
            if name and name not in required_tools:
                required_tools.append(name)

        if not required_tools:
            for item in decision.suggested_tool_classes:
                name = str(item or "").strip()
                if name and name not in required_tools:
                    required_tools.append(name)

        if decision.needs_external_system and required_tools:
            provider_skill_names: list[str] = []
            for candidate in match_result.tool_candidates:
                capability = str(getattr(candidate, "capability_class", "") or "").strip()
                name = str(getattr(candidate, "name", "") or "").strip()
                if not name:
                    continue
                if capability.startswith("provider:") or capability == "skill":
                    if name not in provider_skill_names:
                        provider_skill_names.append(name)
            if provider_skill_names:
                required_tools = provider_skill_names

        deps.extra["tool_policy"] = {
            "mode": decision.policy.value,
            "reason": decision.reason,
            "required_tools": required_tools,
            "missing_capabilities": list(match_result.missing_capabilities),
            "confidence": float(decision.confidence),
        }

    @staticmethod
    def _build_missing_capability_message(match_result: CapabilityMatchResult) -> str:
        missing = [item for item in match_result.missing_capabilities if item]
        if missing:
            joined = ", ".join(sorted(set(missing)))
            return (
                "Verification requires tools that are not available. Missing capabilities: "
                f"{joined}. Please enable the corresponding tools and retry."
            )
        return (
            "Verification requires tools that are not available. "
            "Please enable the required tool and retry."
        )

    @staticmethod
    def _collect_buffered_assistant_text(buffered_events: list[StreamEvent]) -> str:
        chunks: list[str] = []
        for event in buffered_events:
            if event.type != "assistant":
                continue
            content = str(getattr(event, "content", "") or "")
            if content:
                chunks.append(content)
        return "".join(chunks).strip()

    @staticmethod
    def _called_tool_names(tool_call_summaries: list[dict[str, Any]]) -> set[str]:
        called: set[str] = set()
        for item in tool_call_summaries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if name:
                called.add(name)
        return called

    @staticmethod
    def _required_tool_names_for_decision(
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
    ) -> list[str]:
        required: list[str] = []
        for candidate in match_result.tool_candidates:
            capability = str(getattr(candidate, "capability_class", "") or "").strip()
            name = str(getattr(candidate, "name", "") or "").strip()
            if not name:
                continue
            if decision.needs_external_system:
                if capability.startswith("provider:") or capability == "skill":
                    required.append(name)
                continue
            if decision.needs_live_data and decision.needs_grounded_verification:
                if capability in {"weather", "web_search", "web_fetch", "browser"}:
                    required.append(name)
                continue
            required.append(name)

        deduped: list[str] = []
        seen: set[str] = set()
        for name in required:
            if name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def _missing_required_tool_names(
        self,
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        tool_call_summaries: list[dict[str, Any]],
    ) -> list[str]:
        required = self._required_tool_names_for_decision(
            decision=decision,
            match_result=match_result,
        )
        if not required:
            return []
        called = self._called_tool_names(tool_call_summaries)
        return [name for name in required if name not in called]

    @staticmethod
    def _build_tool_evidence_required_message(
        *,
        match_result: CapabilityMatchResult,
        missing_required_tools: list[str],
    ) -> str:
        candidate_names = []
        for candidate in match_result.tool_candidates:
            name = str(getattr(candidate, "name", "") or "").strip()
            if name and name not in candidate_names:
                candidate_names.append(name)
        if missing_required_tools:
            return (
                "A grounded tool-backed answer is required for this request, but required tools were not executed: "
                f"{', '.join(missing_required_tools)}."
            )
        if candidate_names:
            return (
                "A grounded tool-backed answer is required for this request, but no usable tool "
                f"evidence was produced in this run. Required tools: {', '.join(candidate_names)}."
            )
        return (
            "A grounded tool-backed answer is required for this request, but no usable tool "
            "evidence was produced in this run."
        )

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

    async def _classify_tool_gate_with_model(
        self,
        *,
        agent: Any,
        deps: SkillDeps,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> Optional[ToolGateDecision]:
        classifier_prompt = self._build_tool_gate_classifier_prompt(available_tools)
        classifier_message = self._build_tool_gate_classifier_message(
            user_message=user_message,
            recent_history=recent_history,
        )
        try:
            raw_output = await asyncio.wait_for(
                self._run_single_with_optional_override(
                    agent=agent,
                    user_message=classifier_message,
                    deps=deps,
                    system_prompt=classifier_prompt,
                ),
                timeout=self.TOOL_GATE_CLASSIFIER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return None
        parsed = self._extract_json_object(raw_output)
        if not parsed:
            return None
        try:
            payload = json.loads(parsed)
            if not isinstance(payload, dict):
                return None
            coerced = self._coerce_tool_gate_payload(payload)
            return ToolGateDecision.model_validate(coerced)
        except Exception:
            return None

    @staticmethod
    def _coerce_tool_gate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        def _read_bool(key: str, default: bool = False) -> bool:
            value = payload.get(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "y"}:
                    return True
                if lowered in {"false", "0", "no", "n"}:
                    return False
            return default

        suggested = payload.get("suggested_tool_classes", [])
        if isinstance(suggested, str):
            suggested = [part.strip() for part in re.split(r"[,;\n]", suggested) if part.strip()]
        elif not isinstance(suggested, list):
            suggested = []
        suggested = [str(item).strip() for item in suggested if str(item).strip()]

        confidence = payload.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except Exception:
            confidence_value = 0.0
        confidence_value = max(0.0, min(1.0, confidence_value))

        policy_raw = str(payload.get("policy", ToolPolicyMode.ANSWER_DIRECT.value) or "").strip().lower()
        policy_aliases = {
            "answer": ToolPolicyMode.ANSWER_DIRECT.value,
            "direct": ToolPolicyMode.ANSWER_DIRECT.value,
            "answer_direct": ToolPolicyMode.ANSWER_DIRECT.value,
            "prefer": ToolPolicyMode.PREFER_TOOL.value,
            "prefer_tool": ToolPolicyMode.PREFER_TOOL.value,
            "tool_preferred": ToolPolicyMode.PREFER_TOOL.value,
            "must": ToolPolicyMode.MUST_USE_TOOL.value,
            "must_use": ToolPolicyMode.MUST_USE_TOOL.value,
            "must_use_tool": ToolPolicyMode.MUST_USE_TOOL.value,
            "tool_required": ToolPolicyMode.MUST_USE_TOOL.value,
        }
        policy_value = policy_aliases.get(policy_raw, ToolPolicyMode.ANSWER_DIRECT.value)

        needs_live_data = _read_bool("needs_live_data")
        needs_private_context = _read_bool("needs_private_context")
        needs_external_system = _read_bool("needs_external_system")
        needs_browser_interaction = _read_bool("needs_browser_interaction")
        needs_grounded_verification = _read_bool("needs_grounded_verification")
        needs_tool = _read_bool("needs_tool") or bool(
            suggested
            or needs_live_data
            or needs_private_context
            or needs_external_system
            or needs_browser_interaction
            or needs_grounded_verification
        )

        reason = str(payload.get("reason", "") or "").strip()
        if not reason:
            reason = "Model classifier returned a partial decision; normalized by runtime."

        return {
            "needs_tool": needs_tool,
            "needs_live_data": needs_live_data,
            "needs_private_context": needs_private_context,
            "needs_external_system": needs_external_system,
            "needs_browser_interaction": needs_browser_interaction,
            "needs_grounded_verification": needs_grounded_verification,
            "suggested_tool_classes": suggested,
            "confidence": confidence_value,
            "reason": reason,
            "policy": policy_value,
        }

    def _build_tool_gate_classifier_prompt(self, available_tools: list[dict[str, Any]]) -> str:
        capabilities: list[str] = []
        for tool in available_tools:
            name = str(tool.get("name", "")).strip()
            capability = str(tool.get("capability_class", "")).strip()
            description = str(tool.get("description", "")).strip()
            if capability:
                capabilities.append(f"- {name}: {capability} ({description})")
            else:
                capabilities.append(f"- {name}: {description}")

        capability_text = "\n".join(capabilities) if capabilities else "- no runtime tools available"
        return (
            "You are AtlasClaw's internal tool-necessity classifier.\n"
            "Your job is to decide whether the user request can be answered reliably without tools.\n"
            "Do not answer the user. Do not call tools. Return a single JSON object only.\n\n"
            "Policy rubric:\n"
            "- Use must_use_tool when reliable response requires fresh external facts, enterprise system actions, or verifiable evidence.\n"
            "- If the user asks to query/operate enterprise systems or provider-backed skills, set needs_external_system=true and prefer provider/skill classes over web classes.\n"
            "- Use web_search/web_fetch for public web real-time verification (news, prices, schedules, etc.) when no dedicated domain tool is available.\n"
            "- Do not route provider/skill requests to web_search when provider/skill capabilities are available.\n"
            "- Use prefer_tool when tools would improve confidence but a general direct answer is still acceptable.\n"
            "- Use answer_direct only when the request can be answered reliably from stable knowledge.\n\n"
            "Available runtime capabilities:\n"
            f"{capability_text}\n\n"
            "Return JSON with exactly these fields:\n"
            "{\n"
            '  "needs_tool": boolean,\n'
            '  "needs_live_data": boolean,\n'
            '  "needs_private_context": boolean,\n'
            '  "needs_external_system": boolean,\n'
            '  "needs_browser_interaction": boolean,\n'
            '  "needs_grounded_verification": boolean,\n'
            '  "suggested_tool_classes": string[],\n'
            '  "confidence": number,\n'
            '  "reason": string,\n'
            '  "policy": "answer_direct" | "prefer_tool" | "must_use_tool"\n'
            "}\n"
        )

    def _build_tool_gate_classifier_message(
        self,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> str:
        history_lines: list[str] = []
        for item in recent_history[-4:]:
            role = str(item.get("role", "")).strip() or "unknown"
            content = str(item.get("content", "")).strip().replace("\n", " ")
            if len(content) > 180:
                content = content[:177] + "..."
            history_lines.append(f"- {role}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "- none"
        return (
            "Classify the following request for runtime policy.\n\n"
            f"User request:\n{user_message}\n\n"
            f"Recent history:\n{history_text}\n"
        )

    async def _run_single_with_optional_override(
        self,
        *,
        agent: Any,
        user_message: str,
        deps: SkillDeps,
        system_prompt: Optional[str] = None,
    ) -> str:
        override_factory = getattr(agent, "override", None)
        if callable(override_factory) and system_prompt:
            try:
                override_cm = override_factory(system_prompt=system_prompt)
            except TypeError:
                override_cm = nullcontext()
        else:
            override_cm = nullcontext()

        if hasattr(override_cm, "__aenter__"):
            async with override_cm:
                result = await agent.run(user_message, deps=deps)
        else:
            with override_cm:
                result = await agent.run(user_message, deps=deps)

        output = result.output if hasattr(result, "output") else result
        return str(output).strip()

    @staticmethod
    def _extract_json_object(raw_output: str) -> str:
        text = (raw_output or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return ""
        return text[start : end + 1]

    @staticmethod
    def _extract_tool_call_arguments(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return dict(raw_args)
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

