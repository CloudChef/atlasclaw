# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import inspect
import json
import logging
import re
from typing import Any, Optional

from app.atlasclaw.agent.runner_tool.runner_agent_override import resolve_override_tools
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    tool_is_coordination_support,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import normalize_tool_result_mode
from app.atlasclaw.agent.tool_gate_models import (
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)
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
        """Run the tool-gate classifier with the lazily resolved model agent."""
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

class RunnerToolGateModelMixin:
    @staticmethod
    def _dedupe_selector_values(values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    async def _select_capability_intent_plan_with_model(
        self,
        *,
        agent: Any,
        deps: SkillDeps,
        user_message: str,
        recent_history: list[dict[str, Any]],
        capability_index: list[dict[str, Any]],
    ) -> Optional[ToolIntentPlan]:
        """Ask the model to select authorized capabilities for a natural-language turn."""
        if agent is None:
            return None

        selector_prompt = self._build_capability_selector_prompt(capability_index=capability_index)
        selector_message = self._build_capability_selector_message(
            user_message=user_message,
            recent_history=recent_history,
        )
        try:
            raw_output = await self._run_single_with_optional_override(
                agent=agent,
                user_message=selector_message,
                deps=deps,
                system_prompt=selector_prompt,
                purpose="capability_selector_model_pass",
                allowed_tool_names=[],
            )
        except Exception as exc:
            logger.warning("capability_selector_failed: %s", exc)
            return None

        parsed = self._extract_json_object(raw_output)
        if not parsed:
            return None
        try:
            payload = json.loads(parsed)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None

        return self._coerce_capability_selector_payload(
            payload=payload,
            capability_index=capability_index,
        )

    def _build_capability_selector_prompt(
        self,
        *,
        capability_index: list[dict[str, Any]],
    ) -> str:
        """Build the LLM selector prompt from authorized capability descriptions only."""
        capability_lines: list[str] = []
        for entry in capability_index[:96]:
            if not isinstance(entry, dict):
                continue
            capability_id = str(entry.get("capability_id", "") or "").strip()
            if not capability_id:
                continue
            kind = str(entry.get("kind", "") or "").strip() or "capability"
            name = str(entry.get("name", "") or "").strip() or capability_id
            description = str(entry.get("description", "") or "").strip().replace("\n", " ")
            if len(description) > 280:
                description = description[:277] + "..."
            capability_lines.append(
                f"- {capability_id} | kind={kind} | name={name} | desc={description or '-'}"
            )

        return (
            "You are AtlasClaw's internal capability selector.\n"
            "Do not answer the user and do not call tools. Return one JSON object only.\n\n"
            "Task:\n"
            "Select which authorized capability targets, if any, should handle this turn.\n"
            "Slash-selected capabilities are handled before you run; this selector is only for "
            "ordinary natural-language requests.\n\n"
            "Rules:\n"
            "- Choose only capability IDs listed below.\n"
            "- Use direct_answer when the request does not need an authorized runtime capability.\n"
            "- Use ask_clarification when the user intent or required target is ambiguous.\n"
            "- Use use_tools when the request needs provider data, private context, artifact creation, "
            "or an authorized skill/tool.\n"
            "- Do not substitute artifact formats; preserve the requested file type or choose no "
            "artifact target.\n"
            "- For file deliverables, include a matching file-creation capability. A data/query "
            "capability alone is not enough.\n"
            "- If the request needs data and a file deliverable, include both targets in execution order.\n"
            "- Standard markdown skills may have no declared public tool; selecting the skill is enough "
            "for the runtime to provide controlled internal execution tools.\n"
            "Authorized capabilities:\n"
            f"{chr(10).join(capability_lines) if capability_lines else '- none'}\n\n"
            "Return JSON fields exactly:\n"
            "{\n"
            '  "action": "direct_answer" | "use_tools" | "ask_clarification",\n'
            '  "targets": string[],\n'
            '  "reason": string\n'
            "}\n"
        )

    @staticmethod
    def _build_capability_selector_message(
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> str:
        history_lines: list[str] = []
        for item in recent_history[-6:]:
            role = str(item.get("role", "") or "").strip() or "unknown"
            content = str(item.get("content", "") or "").strip().replace("\n", " ")
            if len(content) > 220:
                content = content[:217] + "..."
            history_lines.append(f"- {role}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "- none"
        return (
            "Select authorized capability targets for this turn.\n\n"
            f"User request:\n{user_message}\n\n"
            f"Recent history:\n{history_text}\n"
        )

    def _coerce_capability_selector_payload(
        self,
        *,
        payload: dict[str, Any],
        capability_index: list[dict[str, Any]],
    ) -> Optional[ToolIntentPlan]:
        """Validate the selector JSON and convert authorized targets into an intent plan."""
        allowed_targets: dict[str, tuple[str, str]] = {}

        for entry in capability_index:
            if not isinstance(entry, dict):
                continue
            capability_id = str(entry.get("capability_id", "") or "").strip()
            if not capability_id or ":" not in capability_id:
                continue
            prefix, raw_name = capability_id.split(":", 1)
            prefix = prefix.strip().lower()
            raw_name = raw_name.strip()
            if not raw_name:
                continue
            if prefix not in {"tool", "skill", "provider", "capability", "group"}:
                continue
            allowed_targets[capability_id] = (prefix, raw_name)

        action_raw = str(payload.get("action", "") or "").strip().lower()
        action_map = {
            ToolIntentAction.DIRECT_ANSWER.value: ToolIntentAction.DIRECT_ANSWER,
            ToolIntentAction.USE_TOOLS.value: ToolIntentAction.USE_TOOLS,
            ToolIntentAction.ASK_CLARIFICATION.value: ToolIntentAction.ASK_CLARIFICATION,
        }
        action = action_map.get(action_raw)
        if action is None:
            return None

        raw_targets = payload.get("targets", [])
        if not isinstance(raw_targets, list):
            return None

        target_skill_names: list[str] = []
        target_tool_names: list[str] = []
        target_provider_types: list[str] = []
        target_capability_classes: list[str] = []
        target_group_ids: list[str] = []

        for raw_target in raw_targets:
            if not isinstance(raw_target, str):
                continue
            normalized = raw_target.strip()
            if not normalized:
                continue
            resolved = allowed_targets.get(normalized)
            if resolved is None:
                continue
            prefix, value = resolved
            if prefix == "skill":
                target_skill_names.append(value)
            elif prefix == "tool":
                target_tool_names.append(value)
            elif prefix == "provider":
                target_provider_types.append(value)
            elif prefix == "capability":
                target_capability_classes.append(value)
            elif prefix == "group":
                target_group_ids.append(value)

        target_skill_names = self._dedupe_selector_values(target_skill_names)
        target_tool_names = self._dedupe_selector_values(target_tool_names)
        target_provider_types = self._dedupe_selector_values(target_provider_types)
        target_capability_classes = self._dedupe_selector_values(target_capability_classes)
        target_group_ids = self._dedupe_selector_values(target_group_ids)
        has_targets = any(
            [
                target_skill_names,
                target_tool_names,
                target_provider_types,
                target_capability_classes,
                target_group_ids,
            ]
        )
        if action is ToolIntentAction.USE_TOOLS and not has_targets:
            return None
        if has_targets and action is not ToolIntentAction.USE_TOOLS:
            return None

        reason = str(payload.get("reason", "") or "").strip()
        if not reason:
            reason = "LLM capability selector produced a routing decision."

        return ToolIntentPlan(
            action=action,
            target_provider_types=target_provider_types,
            target_skill_names=target_skill_names,
            target_group_ids=target_group_ids,
            target_capability_classes=target_capability_classes,
            target_tool_names=target_tool_names,
            reason=reason,
        )

    @staticmethod
    def _build_selected_tool_intent_plan(
        *,
        tools: list[dict[str, Any]],
        reason: str,
    ) -> Optional[ToolIntentPlan]:
        normalized_tools = [
            tool
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        ]
        if not normalized_tools:
            return None

        def _dedupe(values: list[str]) -> list[str]:
            deduped: list[str] = []
            seen: set[str] = set()
            for value in values:
                normalized = str(value or "").strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                deduped.append(normalized)
            return deduped

        target_provider_types = _dedupe(
            [
                str(tool.get("provider_type", "") or "").strip().lower()
                for tool in normalized_tools
            ]
        )
        target_skill_names = _dedupe(
            [
                str(
                    tool.get("qualified_skill_name", "") or tool.get("skill_name", "") or ""
                ).strip()
                for tool in normalized_tools
            ]
        )
        target_group_ids = _dedupe(
            [
                str(group_id).strip()
                for tool in normalized_tools
                for group_id in (tool.get("group_ids", []) or [])
            ]
        )
        target_capability_classes = _dedupe(
            [
                str(tool.get("capability_class", "") or "").strip().lower()
                for tool in normalized_tools
            ]
        )
        target_tool_names = _dedupe(
            [str(tool.get("name", "") or "").strip() for tool in normalized_tools]
        )
        return ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=target_provider_types,
            target_skill_names=target_skill_names,
            target_group_ids=target_group_ids,
            target_capability_classes=target_capability_classes,
            target_tool_names=target_tool_names,
            reason=reason,
        )

    @staticmethod
    def _tool_is_public_web(tool: dict[str, Any]) -> bool:
        return bool(tool.get("public_web"))

    @staticmethod
    def _tool_needs_live_data(tool: dict[str, Any]) -> bool:
        return bool(tool.get("live_data"))

    @staticmethod
    def _tool_needs_browser_interaction(tool: dict[str, Any]) -> bool:
        return bool(tool.get("browser_interaction"))

    def _resolve_selected_tools(
        self,
        *,
        available_tools: list[dict[str, Any]],
        target_provider_types: list[str],
        target_skill_names: list[str],
        target_capability_classes: list[str],
        target_tool_names: list[str],
    ) -> list[dict[str, Any]]:
        normalized_provider_types = {
            str(item or "").strip().lower()
            for item in target_provider_types
            if str(item or "").strip()
        }
        normalized_skill_names = {
            str(item or "").strip().lower()
            for item in target_skill_names
            if str(item or "").strip()
        }
        normalized_capability_classes = {
            str(item or "").strip().lower()
            for item in target_capability_classes
            if str(item or "").strip()
        }
        normalized_tool_names = {
            str(item or "").strip()
            for item in target_tool_names
            if str(item or "").strip()
        }
        selected: list[dict[str, Any]] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            capability_class = str(tool.get("capability_class", "") or "").strip().lower()
            qualified_skill_name = str(
                tool.get("qualified_skill_name", "") or tool.get("skill_name", "") or ""
            ).strip().lower()
            if normalized_tool_names and name in normalized_tool_names:
                selected.append(tool)
                continue
            if normalized_provider_types and provider_type in normalized_provider_types:
                selected.append(tool)
                continue
            if normalized_skill_names and qualified_skill_name in normalized_skill_names:
                selected.append(tool)
                continue
            if normalized_capability_classes and capability_class in normalized_capability_classes:
                selected.append(tool)
                continue
        return selected

    def _build_tool_gate_decision_from_intent_plan(
        self,
        plan: ToolIntentPlan,
        available_tools: Optional[list[dict[str, Any]]] = None,
    ) -> ToolGateDecision:
        selected_tools = self._resolve_selected_tools(
            available_tools=list(available_tools or []),
            target_provider_types=list(plan.target_provider_types or []),
            target_skill_names=list(plan.target_skill_names or []),
            target_capability_classes=list(plan.target_capability_classes or []),
            target_tool_names=list(plan.target_tool_names or []),
        )
        suggested_classes: list[str] = []
        for provider_type in plan.target_provider_types:
            normalized = str(provider_type or "").strip().lower()
            if normalized:
                suggested_classes.append(f"provider:{normalized}")
        for capability in plan.target_capability_classes:
            normalized = str(capability or "").strip().lower()
            if normalized and normalized not in suggested_classes:
                suggested_classes.append(normalized)
        needs_external_system = bool(
            plan.target_provider_types
            or any(
                str(item or "").strip().lower().startswith("provider:")
                for item in plan.target_capability_classes
            )
        )
        needs_live_data = any(self._tool_needs_live_data(tool) for tool in selected_tools)
        needs_browser_interaction = any(
            self._tool_needs_browser_interaction(tool) for tool in selected_tools
        )
        if plan.action is ToolIntentAction.CREATE_ARTIFACT:
            explicit_artifact_target = bool(
                plan.target_tool_names
                or plan.target_skill_names
                or any(
                    str(item or "").strip().lower().startswith("artifact:")
                    for item in plan.target_capability_classes
                )
            )
            if explicit_artifact_target:
                return ToolGateDecision(
                    needs_tool=True,
                    needs_external_system=needs_external_system,
                    needs_live_data=needs_live_data,
                    needs_browser_interaction=needs_browser_interaction,
                    suggested_tool_classes=suggested_classes,
                    confidence=0.8,
                    reason=plan.reason or "Planner selected explicit artifact execution.",
                    policy=ToolPolicyMode.PREFER_TOOL,
                )
            return ToolGateDecision(
                reason=plan.reason or "Planner selected artifact creation.",
                confidence=0.7,
                policy=ToolPolicyMode.ANSWER_DIRECT,
            )
        if plan.action is ToolIntentAction.DIRECT_ANSWER:
            return ToolGateDecision(
                needs_external_system=needs_external_system,
                needs_live_data=needs_live_data,
                needs_browser_interaction=needs_browser_interaction,
                suggested_tool_classes=suggested_classes,
                reason=plan.reason or "Planner selected direct answer.",
                confidence=0.7,
                policy=ToolPolicyMode.ANSWER_DIRECT,
            )
        if plan.action is ToolIntentAction.ASK_CLARIFICATION:
            return ToolGateDecision(
                needs_external_system=needs_external_system,
                needs_live_data=needs_live_data,
                needs_browser_interaction=needs_browser_interaction,
                suggested_tool_classes=suggested_classes,
                reason=plan.reason or "Planner requested clarification before tool execution.",
                confidence=0.7,
                policy=ToolPolicyMode.ANSWER_DIRECT,
            )
        return ToolGateDecision(
            needs_tool=True,
            needs_live_data=needs_live_data,
            needs_browser_interaction=needs_browser_interaction,
            needs_external_system=needs_external_system,
            needs_grounded_verification=bool(needs_external_system),
            suggested_tool_classes=suggested_classes,
            confidence=0.8,
            reason=plan.reason or "Planner selected tool execution.",
            policy=ToolPolicyMode.PREFER_TOOL,
        )

    @staticmethod
    def _build_projected_toolset_short_circuit_intent_plan(
        *,
        visible_tools: list[dict[str, Any]],
    ) -> Optional[ToolIntentPlan]:
        candidate_tools: list[dict[str, Any]] = []
        for tool in visible_tools or []:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name", "") or "").strip()
            if not tool_name or tool_is_coordination_support(tool):
                continue
            candidate_tools.append(tool)

        if len(candidate_tools) != 1:
            return None

        tool = candidate_tools[0]
        result_mode = normalize_tool_result_mode(tool)
        if result_mode != "tool_only_ok":
            return None

        tool_name = str(tool.get("name", "") or "").strip()
        provider_type = str(tool.get("provider_type", "") or "").strip().lower()
        capability_class = str(tool.get("capability_class", "") or "").strip().lower()
        group_ids = [
            str(item).strip()
            for item in (tool.get("group_ids", []) or [])
            if str(item).strip()
        ]
        qualified_skill_name = str(tool.get("qualified_skill_name", "") or "").strip()
        skill_name = str(tool.get("skill_name", "") or "").strip()
        target_skill_names = [qualified_skill_name or skill_name] if (qualified_skill_name or skill_name) else []

        reason = f"Visible runtime toolset converged to a single tool-only tool: {tool_name}."
        return ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=[provider_type] if provider_type else [],
            target_skill_names=target_skill_names,
            target_group_ids=group_ids,
            target_capability_classes=[capability_class] if capability_class else [],
            target_tool_names=[tool_name],
            reason=reason,
        )

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
        return None
    def _select_tool_gate_classifier_agent(self, runtime_agent: Any) -> Optional[Any]:
        if hasattr(runtime_agent, "run"):
            return runtime_agent
        if self.agent_factory is not None and self.token_policy is not None:
            classifier_token = self._select_tool_gate_classifier_token()
            if classifier_token is not None:
                async def _resolver() -> Any:
                    built = self.agent_factory(self.agent_id, classifier_token)
                    if inspect.isawaitable(built):
                        built = await built
                    return built if hasattr(built, "run") else None

                return _resolver
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
        strict_provider_or_skill = bool(normalized.needs_external_system) or has_provider_skill_hint
        strict_tool_enforcement = strict_provider_or_skill or bool(
            normalized.needs_browser_interaction or normalized.needs_private_context
        )

        if strict_provider_or_skill:
            normalized.needs_external_system = True
            normalized.needs_tool = True
            normalized.confidence = max(
                normalized.confidence,
                self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE,
            )
            if normalized.policy is ToolPolicyMode.ANSWER_DIRECT:
                normalized.policy = ToolPolicyMode.PREFER_TOOL
            if "provider/skill intent" not in normalized.reason.lower():
                normalized.reason = (
                    f"{normalized.reason} External-system/provider-skill intent detected from tool metadata."
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
            raw_output = await self._run_single_with_optional_override(
                agent=agent,
                user_message=classifier_message,
                deps=deps,
                system_prompt=classifier_prompt,
                allowed_tool_names=[],
            )
        except Exception as exc:
            logger.warning("tool_gate_classifier_failed: %s", exc)
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
            or needs_private_context
            or needs_external_system
            or needs_browser_interaction
            or (needs_grounded_verification and not needs_live_data)
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
            "- Decide based on clear capability fit, not freshness alone.\n"
            "- Classify the current User request. Use Recent history only when the current request explicitly continues, confirms, answers requested fields for, or modifies that prior task.\n"
            "- Do not require tools solely because Recent history contains an unresolved provider or tool request.\n"
            "- When no runtime tools are available, still use answer_direct for ordinary conversation or requests that can be answered without runtime capabilities.\n"
            "- Do not set must_use_tool unless needs_external_system, needs_private_context, needs_browser_interaction, or suggested_tool_classes is also true/non-empty.\n"
            "- Use must_use_tool only when the request truly requires private/provider/browser execution and cannot be satisfied safely without it.\n"
            "- Classify intent across languages. If the user asks AtlasClaw to perform, submit, request, provision, modify, approve, delete, start, stop, or verify an operation in an external environment, set needs_external_system=true even when no matching tools are listed.\n"
            "- If there are no runtime tools and the request is an external-system operation, keep policy=must_use_tool; the no-tools prompt must explain that the capability is unavailable.\n"
            "- For status checks, verification, audit evidence, records, or other facts that live in a private or provider-backed system, set needs_external_system=true or needs_private_context=true instead of only needs_grounded_verification=true.\n"
            "- If the user asks to query or operate enterprise systems or provider-backed skills, set needs_external_system=true and prefer provider/skill classes over web classes.\n"
            "- Use prefer_tool when the request clearly matches available tools and trying them first would materially help.\n"
            "- Public questions about prices, schedules, recommendations, or opening status may still use answer_direct when no clear capability match is required.\n"
            "- Use web_search/web_fetch only when those tools are themselves the best matching available capability.\n"
            "- Do not route provider/skill requests to web_search when provider/skill capabilities are available.\n"
            "- Use answer_direct when the request can be handled from model knowledge, even if certainty should be expressed cautiously.\n\n"
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
        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
            f"Runtime UTC time:\n{now_utc}\n\n"
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
        purpose: str = "tool_gate_model_pass",
        allowed_tool_names: Optional[list[str]] = None,
    ) -> str:
        if callable(agent) and not hasattr(agent, "run"):
            agent = agent()
            if inspect.isawaitable(agent):
                agent = await agent
        if agent is None or not hasattr(agent, "run"):
            return ""

        override_factory = getattr(agent, "override", None)
        override_tools = resolve_override_tools(
            agent=agent,
            allowed_tool_names=allowed_tool_names,
        )
        if callable(override_factory) and system_prompt:
            override_cm = nullcontext()
            override_candidates = []
            if override_tools is not None:
                override_candidates.append({"instructions": system_prompt, "tools": override_tools})
                override_candidates.append({"system_prompt": system_prompt, "tools": override_tools})
            else:
                override_candidates.append({"instructions": system_prompt})
                override_candidates.append({"system_prompt": system_prompt})
            for override_kwargs in override_candidates:
                try:
                    override_cm = override_factory(**override_kwargs)
                    break
                except TypeError:
                    continue
        elif callable(override_factory) and override_tools is not None:
            try:
                override_cm = override_factory(tools=override_tools)
            except TypeError:
                override_cm = nullcontext()
        else:
            override_cm = nullcontext()

        async def _execute() -> str:
            if hasattr(override_cm, "__aenter__"):
                async with override_cm:
                    result = await agent.run(user_message, deps=deps)
            else:
                with override_cm:
                    result = await agent.run(user_message, deps=deps)

            output = result.output if hasattr(result, "output") else result
            return str(output).strip()

        timeout_seconds = self._resolve_tool_gate_model_timeout_seconds()
        try:
            return await asyncio.wait_for(_execute(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "%s timed out after %.3fs",
                str(purpose or "tool_gate_model_pass"),
                timeout_seconds,
            )
            raise

    def _resolve_tool_gate_model_timeout_seconds(self) -> float:
        raw_value = getattr(self, "TOOL_GATE_MODEL_TIMEOUT_SECONDS", 8.0)
        try:
            timeout_seconds = float(raw_value)
        except Exception:
            timeout_seconds = 8.0
        return max(0.5, timeout_seconds)
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
