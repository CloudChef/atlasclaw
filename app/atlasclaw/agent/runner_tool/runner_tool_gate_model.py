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
    tool_is_generic_filesystem_helper,
)
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
    def _tool_declares_explicit_artifact(tool: dict[str, Any]) -> bool:
        capability_class = str(tool.get("capability_class", "") or "").strip().lower()
        return capability_class.startswith("artifact:")

    @staticmethod
    def _tool_is_generic_filesystem_helper(tool: dict[str, Any]) -> bool:
        return tool_is_generic_filesystem_helper(tool)

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

    def _select_explicit_artifact_metadata_tools(
        self,
        *,
        metadata_candidates: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        allowed_tools = {
            str(tool.get("name", "") or "").strip(): tool
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        ranked_candidates: list[tuple[int, int, str]] = []
        for item in (metadata_candidates.get("tool_candidates", []) or []):
            if not isinstance(item, dict) or not bool(item.get("has_strong_anchor")):
                continue
            tool_name = str(
                item.get("tool_name", "")
                or next(iter(item.get("tool_names", []) or []), "")
                or ""
            ).strip()
            tool = allowed_tools.get(tool_name)
            if tool is None or not self._tool_declares_explicit_artifact(tool):
                continue
            try:
                score = int(item.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0
            try:
                priority = int(tool.get("priority", 100) or 100)
            except (TypeError, ValueError):
                priority = 100
            ranked_candidates.append((score, priority, tool_name))

        ranked_candidates.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
        return [allowed_tools[item[2]] for item in ranked_candidates]

    def _metadata_plan_represents_explicit_capability_match(
        self,
        *,
        metadata_candidates: Optional[dict[str, Any]],
        metadata_plan: Optional[ToolIntentPlan],
        available_tools: list[dict[str, Any]],
    ) -> bool:
        if metadata_plan is None:
            return False

        if not any(
            [
                list(metadata_plan.target_provider_types or []),
                list(metadata_plan.target_skill_names or []),
                list(metadata_plan.target_group_ids or []),
                list(metadata_plan.target_capability_classes or []),
                list(metadata_plan.target_tool_names or []),
            ]
        ):
            return False

        if not isinstance(metadata_candidates, dict):
            return False

        try:
            confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        min_confidence = max(
            0.0,
            float(getattr(self, "TOOL_HINT_RANKER_MIN_METADATA_CONFIDENCE", 0.3) or 0.3),
        )
        if confidence >= min_confidence:
            return True

        if self._metadata_candidates_have_single_tool_consensus(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        ):
            return True

        dominant_tool_name = self._select_dominant_metadata_tool_name(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        )
        return bool(str(dominant_tool_name or "").strip())

    def _metadata_targets_only_generic_web(
        self,
        *,
        available_tools: list[dict[str, Any]],
        target_provider_types: list[str],
        target_skill_names: list[str],
        target_capability_classes: list[str],
        target_tool_names: list[str],
    ) -> bool:
        if any(str(item or "").strip() for item in target_provider_types):
            return False
        if any(str(item or "").strip() for item in target_skill_names):
            return False
        selected_tools = self._resolve_selected_tools(
            available_tools=available_tools,
            target_provider_types=target_provider_types,
            target_skill_names=target_skill_names,
            target_capability_classes=target_capability_classes,
            target_tool_names=target_tool_names,
        )
        if not selected_tools:
            return False
        return all(self._tool_is_public_web(tool) for tool in selected_tools)

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

    def _build_metadata_fallback_tool_intent_plan(
        self,
        *,
        metadata_candidates: Optional[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> Optional[ToolIntentPlan]:
        if not isinstance(metadata_candidates, dict):
            return None

        try:
            confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        min_confidence = max(
            0.0,
            float(getattr(self, "TOOL_HINT_RANKER_MIN_METADATA_CONFIDENCE", 0.3) or 0.3),
        )
        has_provider_tool_consensus = self._metadata_candidates_have_provider_tool_consensus(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        )
        has_single_tool_consensus = self._metadata_candidates_have_single_tool_consensus(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        )
        if (
            confidence < min_confidence
            and not has_provider_tool_consensus
            and not has_single_tool_consensus
        ):
            return None

        allowed_tool_names = {
            str(tool.get("name", "") or "").strip()
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        artifact_tools = self._select_explicit_artifact_metadata_tools(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        )
        if artifact_tools:
            reason = str(metadata_candidates.get("reason", "") or "").strip()
            if reason:
                reason = (
                    f"Metadata fallback planner selected explicit artifact capability hints ({reason})."
                )
            else:
                reason = "Metadata fallback planner selected explicit artifact capability hints."
            plan = self._build_selected_tool_intent_plan(
                tools=artifact_tools,
                reason=reason,
            )
            if plan is not None:
                return plan

        dominant_tool_name = self._select_dominant_metadata_tool_name(
            metadata_candidates=metadata_candidates,
            available_tools=available_tools,
        )
        if dominant_tool_name:
            dominant_tool = next(
                (
                    tool
                    for tool in available_tools
                    if str(tool.get("name", "") or "").strip() == dominant_tool_name
                ),
                None,
            )
            if dominant_tool is not None:
                provider_type = str(dominant_tool.get("provider_type", "") or "").strip().lower()
                capability_class = str(dominant_tool.get("capability_class", "") or "").strip().lower()
                qualified_skill_name = str(
                    dominant_tool.get("qualified_skill_name", "") or dominant_tool.get("skill_name", "") or ""
                ).strip()
                target_group_ids = self._dedupe_preserve_order(
                    [
                        str(item).strip()
                        for item in (dominant_tool.get("group_ids", []) or [])
                        if str(item).strip()
                    ]
                )
                reason = str(metadata_candidates.get("reason", "") or "").strip()
                if reason:
                    reason = (
                        f"Metadata fallback planner selected a dominant explicit tool ({reason})."
                    )
                else:
                    reason = "Metadata fallback planner selected a dominant explicit tool."

                if (
                    self._metadata_targets_only_generic_web(
                        available_tools=available_tools,
                        target_provider_types=[provider_type] if provider_type else [],
                        target_skill_names=[qualified_skill_name] if qualified_skill_name else [],
                        target_capability_classes=[capability_class] if capability_class else [],
                        target_tool_names=[dominant_tool_name],
                    )
                    and not has_single_tool_consensus
                ):
                    return None

                return ToolIntentPlan(
                    action=ToolIntentAction.USE_TOOLS,
                    target_provider_types=[provider_type] if provider_type else [],
                    target_skill_names=[qualified_skill_name] if qualified_skill_name else [],
                    target_group_ids=target_group_ids,
                    target_capability_classes=[capability_class] if capability_class else [],
                    target_tool_names=[dominant_tool_name],
                    reason=reason,
                )

        target_provider_types = self._dedupe_preserve_order(
            [
                str(item).strip().lower()
                for item in (metadata_candidates.get("preferred_provider_types", []) or [])
                if str(item).strip()
            ]
        )
        target_capability_classes = self._dedupe_preserve_order(
            [
                str(item).strip().lower()
                for item in (metadata_candidates.get("preferred_capability_classes", []) or [])
                if str(item).strip()
            ]
        )
        target_tool_names = self._dedupe_preserve_order(
            [
                str(item).strip()
                for item in (metadata_candidates.get("preferred_tool_names", []) or [])
                if str(item).strip() in allowed_tool_names
            ]
        )
        target_group_ids = self._dedupe_preserve_order(
            [
                str(item).strip()
                for item in (metadata_candidates.get("preferred_group_ids", []) or [])
                if str(item).strip()
            ]
        )

        target_skill_names: list[str] = []
        for item in (metadata_candidates.get("skill_candidates", []) or []):
            if not isinstance(item, dict):
                continue
            qualified_skill_name = str(item.get("qualified_skill_name", "") or "").strip()
            skill_name = str(item.get("skill_name", "") or "").strip()
            hint_id = str(item.get("hint_id", "") or "").strip()
            selected_name = qualified_skill_name or skill_name
            if not selected_name and hint_id.startswith("skill:"):
                selected_name = hint_id.split(":", 1)[1].strip()
            if selected_name:
                target_skill_names.append(selected_name)
        target_skill_names = self._dedupe_preserve_order(target_skill_names)

        if not any(
            [
                target_provider_types,
                target_skill_names,
                target_group_ids,
                target_capability_classes,
                target_tool_names,
            ]
        ):
            return None

        if (
            self._metadata_targets_only_generic_web(
                available_tools=available_tools,
                target_provider_types=target_provider_types,
                target_skill_names=target_skill_names,
                target_capability_classes=target_capability_classes,
                target_tool_names=target_tool_names,
            )
            and not has_single_tool_consensus
        ):
            return None

        reason = str(metadata_candidates.get("reason", "") or "").strip()
        if reason:
            reason = f"Metadata fallback planner selected tool execution ({reason})."
        else:
            reason = "Metadata fallback planner selected tool execution."

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
        result_mode = str(tool.get("result_mode", "") or "").strip().lower()
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

    @staticmethod
    def _metadata_candidates_have_provider_tool_consensus(
        *,
        metadata_candidates: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> bool:
        preferred_tool_names = [
            str(item).strip()
            for item in (metadata_candidates.get("preferred_tool_names", []) or [])
            if str(item).strip()
        ]
        if not preferred_tool_names:
            return False

        tool_index: dict[str, dict[str, str]] = {}
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            tool_index[name] = {
                "provider_type": str(tool.get("provider_type", "") or "").strip().lower(),
                "capability_class": str(tool.get("capability_class", "") or "").strip().lower(),
            }

        resolved_provider_types: set[str] = set()
        resolved_provider_capabilities: set[str] = set()
        resolved_tool_count = 0
        for tool_name in preferred_tool_names:
            record = tool_index.get(tool_name)
            if not record:
                continue
            resolved_tool_count += 1
            provider_type = record.get("provider_type", "")
            capability_class = record.get("capability_class", "")
            if provider_type:
                resolved_provider_types.add(provider_type)
            if capability_class.startswith("provider:"):
                resolved_provider_capabilities.add(capability_class)

        if resolved_tool_count <= 0:
            return False
        if len(resolved_provider_types) == 1:
            return True
        return len(resolved_provider_capabilities) == 1

    @staticmethod
    def _metadata_candidates_have_single_tool_consensus(
        *,
        metadata_candidates: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> bool:
        allowed_tool_names = {
            str(tool.get("name", "") or "").strip()
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        preferred_tool_names = [
            str(item).strip()
            for item in (metadata_candidates.get("preferred_tool_names", []) or [])
            if str(item).strip() in allowed_tool_names
        ]
        preferred_tool_names = list(dict.fromkeys(preferred_tool_names))
        if len(preferred_tool_names) != 1:
            return False

        selected_tool_name = preferred_tool_names[0]
        strong_candidate_tool_sets: list[set[str]] = []
        for key in ("provider_candidates", "skill_candidates", "tool_candidates", "builtin_tool_candidates"):
            for item in (metadata_candidates.get(key, []) or []):
                if not isinstance(item, dict) or not bool(item.get("has_strong_anchor")):
                    continue
                candidate_tool_names = [
                    str(name).strip()
                    for name in (item.get("tool_names", []) or [])
                    if str(name).strip() in allowed_tool_names
                ]
                direct_tool_name = str(item.get("tool_name", "") or "").strip()
                if direct_tool_name and direct_tool_name in allowed_tool_names:
                    candidate_tool_names.append(direct_tool_name)
                candidate_tool_set = {
                    name for name in candidate_tool_names if name
                }
                if not candidate_tool_set:
                    continue
                strong_candidate_tool_sets.append(candidate_tool_set)

        if not strong_candidate_tool_sets:
            return False

        for candidate_tool_set in strong_candidate_tool_sets:
            if selected_tool_name not in candidate_tool_set:
                return False
            if candidate_tool_set.difference({selected_tool_name}):
                return False
        return True

    def _select_dominant_metadata_tool_name(
        self,
        *,
        metadata_candidates: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> str:
        allowed_tool_names = {
            str(tool.get("name", "") or "").strip()
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        ranked_candidates: list[tuple[int, str]] = []
        allowed_tool_index = {
            str(tool.get("name", "") or "").strip(): tool
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        for item in (metadata_candidates.get("tool_candidates", []) or []):
            if not isinstance(item, dict) or not bool(item.get("has_strong_anchor")):
                continue
            tool_name = str(
                item.get("tool_name", "")
                or next(iter(item.get("tool_names", []) or []), "")
                or ""
            ).strip()
            if not tool_name or tool_name not in allowed_tool_names:
                continue
            try:
                score = int(item.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0
            tool = allowed_tool_index.get(tool_name, {})
            if (
                self._tool_is_generic_filesystem_helper(tool)
                and self._select_explicit_artifact_metadata_tools(
                    metadata_candidates=metadata_candidates,
                    available_tools=available_tools,
                )
            ):
                continue
            ranked_candidates.append((score, tool_name))

        if not ranked_candidates:
            return ""

        ranked_candidates.sort(key=lambda item: (-item[0], item[1].lower()))
        top_score, top_name = ranked_candidates[0]
        if len(ranked_candidates) == 1:
            return top_name
        second_score = ranked_candidates[1][0]
        if top_score >= second_score + 3:
            return top_name
        return ""

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
    def _build_metadata_short_circuit_decision(
        self,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        metadata_candidates: Optional[dict[str, Any]] = None,
        deps: Optional[SkillDeps] = None,
    ) -> Optional[ToolGateDecision]:
        """Build a direct provider/skill gate decision from metadata when confidence is contextual.

        This path is intentionally constrained to sessions that already have an active
        provider capability selected, so generic questions do not get over-routed.
        """
        if not isinstance(metadata_candidates, dict):
            return None
        provider_skill_classes = self._collect_provider_skill_capability_classes(available_tools)
        if not provider_skill_classes:
            return None

        active_provider_class = self._resolve_active_provider_capability_class(
            deps=deps,
            provider_skill_classes=provider_skill_classes,
        )
        if not active_provider_class:
            return None

        active_provider_type = active_provider_class.split(":", 1)[1].strip().lower()
        preferred_provider_types = [
            str(item).strip().lower()
            for item in (metadata_candidates.get("preferred_provider_types") or [])
            if str(item).strip()
        ]
        preferred_capability_classes = [
            str(item).strip().lower()
            for item in (metadata_candidates.get("preferred_capability_classes") or [])
            if str(item).strip()
        ]
        provider_candidates = [
            item
            for item in (metadata_candidates.get("provider_candidates") or [])
            if isinstance(item, dict)
        ]

        has_active_provider_match = (
            active_provider_type in preferred_provider_types
            or active_provider_class in preferred_capability_classes
            or any(
                str(item.get("provider_type", "") or "").strip().lower() == active_provider_type
                for item in provider_candidates
            )
        )
        if not has_active_provider_match:
            return None

        if not self._looks_provider_or_skill_related(
            user_message=user_message,
            recent_history=recent_history,
            available_tools=available_tools,
            provider_hint_tokens=self._collect_provider_hint_tokens_from_deps(deps),
        ):
            return None

        requested_classes = [
            capability
            for capability in preferred_capability_classes
            if capability == "skill" or capability.startswith("provider:")
        ]
        if active_provider_class not in requested_classes:
            requested_classes.insert(0, active_provider_class)

        selected_classes = self._select_external_system_capability_classes(
            requested_provider_skill_classes=requested_classes,
            provider_skill_classes=provider_skill_classes,
            available_tools=available_tools,
            user_message=user_message,
            recent_history=recent_history,
            preferred_provider_class=active_provider_class,
        )
        if not selected_classes:
            return None
        if active_provider_class not in selected_classes:
            selected_classes = [active_provider_class, *selected_classes]

        metadata_confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
        return ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            needs_grounded_verification=True,
            suggested_tool_classes=self._dedupe_preserve_order(selected_classes),
            confidence=max(metadata_confidence, self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE),
            reason=(
                "Provider/skill gate short-circuited from metadata recall using "
                "the active provider context."
            ),
            policy=ToolPolicyMode.PREFER_TOOL,
        )
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
        metadata_candidates: dict[str, Any] = {}
        if isinstance(getattr(deps, "extra", None), dict):
            raw_candidates = deps.extra.get("tool_metadata_candidates")
            if isinstance(raw_candidates, dict):
                metadata_candidates = dict(raw_candidates)
        classifier_message = self._build_tool_gate_classifier_message(
            user_message=user_message,
            recent_history=recent_history,
            metadata_candidates=metadata_candidates,
        )
        try:
            raw_output = await self._run_single_with_optional_override(
                agent=agent,
                user_message=classifier_message,
                deps=deps,
                system_prompt=classifier_prompt,
                allowed_tool_names=[],
            )
        except Exception:
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
    def _build_classifier_timeout_fallback_decision(
        self,
        *,
        deps: SkillDeps,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> Optional[ToolGateDecision]:
        provider_skill_classes = self._collect_provider_skill_capability_classes(available_tools)
        if not provider_skill_classes:
            return None
        if not self._looks_provider_or_skill_related(
            user_message=user_message,
            recent_history=recent_history,
            available_tools=available_tools,
            provider_hint_tokens=self._collect_provider_hint_tokens_from_deps(deps),
        ):
            return None
        selected_classes = self._select_external_system_capability_classes(
            requested_provider_skill_classes=[],
            provider_skill_classes=provider_skill_classes,
            available_tools=available_tools,
            user_message=user_message,
            recent_history=recent_history,
            preferred_provider_class=self._resolve_active_provider_capability_class(
                deps=deps,
                provider_skill_classes=provider_skill_classes,
            ),
        )
        return ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            needs_grounded_verification=True,
            suggested_tool_classes=selected_classes,
            confidence=max(self.TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE, 0.7),
            reason=(
                "Tool-gate classifier timed out; runtime routed to provider/skill fast-path "
                "using available provider capability metadata."
            ),
            policy=ToolPolicyMode.PREFER_TOOL,
        )
    @staticmethod
    def _should_attempt_hint_ranking(
        *,
        available_tools: list[dict[str, Any]],
        provider_hint_docs: list[dict[str, Any]],
        skill_hint_docs: list[dict[str, Any]],
        metadata_candidates: Optional[dict[str, Any]] = None,
        min_confidence: float = 0.3,
    ) -> bool:
        if len(available_tools) <= 1:
            return False
        if not (provider_hint_docs or skill_hint_docs):
            return False
        if not isinstance(metadata_candidates, dict):
            return False
        has_candidates = bool(
            metadata_candidates.get("provider_candidates")
            or metadata_candidates.get("skill_candidates")
        )
        if not has_candidates:
            return False
        confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
        return confidence >= max(0.0, float(min_confidence))
    async def _rank_tools_with_hint_docs(
        self,
        *,
        agent: Any,
        deps: SkillDeps,
        user_message: str,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        provider_hint_docs: list[dict[str, Any]],
        skill_hint_docs: list[dict[str, Any]],
    ) -> tuple[Optional[dict[str, Any]], str]:
        if agent is None:
            return None, "ranking_agent_unavailable"
        if not self._should_attempt_hint_ranking(
            available_tools=available_tools,
            provider_hint_docs=provider_hint_docs,
            skill_hint_docs=skill_hint_docs,
        ):
            return None, "ranking_not_required"

        ranker_prompt = self._build_tool_hint_ranker_prompt(
            available_tools=available_tools,
            provider_hint_docs=provider_hint_docs,
            skill_hint_docs=skill_hint_docs,
        )
        ranker_message = self._build_tool_hint_ranker_message(
            user_message=user_message,
            recent_history=recent_history,
        )
        try:
            raw_output = await self._run_single_with_optional_override(
                agent=agent,
                user_message=ranker_message,
                deps=deps,
                system_prompt=ranker_prompt,
                allowed_tool_names=[],
            )
        except Exception as exc:
            return None, f"hint_ranker_error:{exc.__class__.__name__}"

        parsed = self._extract_json_object(raw_output)
        if not parsed:
            return None, "hint_ranker_invalid_json"
        try:
            payload = json.loads(parsed)
        except Exception:
            return None, "hint_ranker_parse_failed"
        if not isinstance(payload, dict):
            return None, "hint_ranker_non_object"

        normalized = self._coerce_tool_hint_ranking_payload(
            payload=payload,
            available_tools=available_tools,
        )
        if normalized is None:
            return None, "hint_ranker_empty_result"
        return normalized, ""
    def _build_tool_hint_ranker_prompt(
        self,
        *,
        available_tools: list[dict[str, Any]],
        provider_hint_docs: list[dict[str, Any]],
        skill_hint_docs: list[dict[str, Any]],
    ) -> str:
        tool_lines: list[str] = []
        for tool in available_tools:
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            description = str(tool.get("description", "") or "").strip()
            provider_type = str(tool.get("provider_type", "") or "").strip()
            capability = str(tool.get("capability_class", "") or "").strip()
            group_ids = tool.get("group_ids", [])
            group_text = ", ".join(
                str(item).strip() for item in (group_ids or []) if str(item).strip()
            )
            tool_lines.append(
                f"- {name} | provider={provider_type or '-'} | capability={capability or '-'} | "
                f"groups={group_text or '-'} | desc={description}"
            )

        provider_lines: list[str] = []
        for doc in provider_hint_docs[:16]:
            provider_lines.append(
                f"- {doc.get('hint_id', 'provider:?')} | tools={', '.join(doc.get('tool_names', [])[:6])} | "
                f"{str(doc.get('hint_text', '') or '').strip()}"
            )
        if not provider_lines:
            provider_lines.append("- none")

        skill_lines: list[str] = []
        for doc in skill_hint_docs[:24]:
            skill_lines.append(
                f"- {doc.get('hint_id', 'skill:?')} | tools={', '.join(doc.get('tool_names', [])[:6])} | "
                f"{str(doc.get('hint_text', '') or '').strip()}"
            )
        if not skill_lines:
            skill_lines.append("- none")

        return (
            "You are AtlasClaw's internal tool-ranking classifier.\n"
            "Do not answer the user and do not call tools.\n"
            "Return one JSON object only.\n\n"
            "Task:\n"
            "Given the user request, allowed runtime tools, and provider/skill metadata hints,\n"
            "rank which provider/capability/tools should be preferred FIRST.\n"
            "This is a soft ranking hint, not an execution command.\n\n"
            "Constraints:\n"
            "- Only return provider types/capabilities/tool names that exist in the allowed tool list.\n"
            "- Keep output concise.\n"
            "- If uncertain, return empty lists with low confidence.\n\n"
            "Allowed tools:\n"
            f"{chr(10).join(tool_lines) if tool_lines else '- none'}\n\n"
            "Provider hint docs:\n"
            f"{chr(10).join(provider_lines)}\n\n"
            "Skill hint docs:\n"
            f"{chr(10).join(skill_lines)}\n\n"
            "Return JSON fields exactly:\n"
            "{\n"
            '  "preferred_provider_types": string[],\n'
            '  "preferred_capability_classes": string[],\n'
            '  "preferred_tool_names": string[],\n'
            '  "confidence": number,\n'
            '  "reason": string\n'
            "}\n"
        )
    @staticmethod
    def _build_tool_hint_ranker_message(
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> str:
        history_lines: list[str] = []
        for item in recent_history[-4:]:
            role = str(item.get("role", "") or "").strip() or "unknown"
            content = str(item.get("content", "") or "").strip().replace("\n", " ")
            if len(content) > 180:
                content = content[:177] + "..."
            history_lines.append(f"- {role}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "- none"
        return (
            "Rank preferred runtime tools for this turn.\n\n"
            f"User request:\n{user_message}\n\n"
            f"Recent history:\n{history_text}\n"
        )
    @staticmethod
    def _coerce_tool_hint_ranking_payload(
        *,
        payload: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        allowed_tool_names = {
            str(item.get("name", "") or "").strip()
            for item in available_tools
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        }
        allowed_provider_types = {
            str(item.get("provider_type", "") or "").strip().lower()
            for item in available_tools
            if isinstance(item, dict) and str(item.get("provider_type", "") or "").strip()
        }
        allowed_capabilities = {
            str(item.get("capability_class", "") or "").strip().lower()
            for item in available_tools
            if isinstance(item, dict) and str(item.get("capability_class", "") or "").strip()
        }

        def _normalize_list(raw: Any) -> list[str]:
            if isinstance(raw, str):
                return [part.strip() for part in re.split(r"[,;\n]", raw) if part.strip()]
            if isinstance(raw, list):
                return [str(item).strip() for item in raw if str(item).strip()]
            return []

        provider_types = [
            item.lower()
            for item in _normalize_list(payload.get("preferred_provider_types", []))
            if item.lower() in allowed_provider_types
        ]
        capability_classes = [
            item.lower()
            for item in _normalize_list(payload.get("preferred_capability_classes", []))
            if item.lower() in allowed_capabilities
        ]
        tool_names = [
            item
            for item in _normalize_list(payload.get("preferred_tool_names", []))
            if item in allowed_tool_names
        ]
        reason = str(payload.get("reason", "") or "").strip()
        confidence_raw = payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except Exception:
            confidence = 0.0

        if not provider_types and not capability_classes and not tool_names:
            return None
        return {
            "preferred_provider_types": provider_types,
            "preferred_capability_classes": capability_classes,
            "preferred_tool_names": tool_names,
            "confidence": confidence,
            "reason": reason,
        }
    @staticmethod
    def _reorder_tools_by_hint_ranking(
        *,
        available_tools: list[dict[str, Any]],
        ranking: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not available_tools:
            return [], {}
        preferred_tools = [
            str(item).strip()
            for item in ranking.get("preferred_tool_names", [])
            if str(item).strip()
        ]
        preferred_capabilities = {
            str(item).strip().lower()
            for item in ranking.get("preferred_capability_classes", [])
            if str(item).strip()
        }
        preferred_providers = {
            str(item).strip().lower()
            for item in ranking.get("preferred_provider_types", [])
            if str(item).strip()
        }
        tool_order = {name: len(preferred_tools) - idx for idx, name in enumerate(preferred_tools)}

        scored: list[tuple[int, str, dict[str, Any]]] = []
        for tool in available_tools:
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            capability = str(tool.get("capability_class", "") or "").strip().lower()
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            try:
                priority = int(tool.get("priority", 100) or 100)
            except (TypeError, ValueError):
                priority = 100
            score = priority
            if name in tool_order:
                score += 300 + (tool_order[name] * 10)
            if capability and capability in preferred_capabilities:
                score += 220
            if provider_type and provider_type in preferred_providers:
                score += 160
            if capability.startswith("provider:"):
                provider_key = capability.split(":", 1)[1].strip().lower()
                if provider_key and provider_key in preferred_providers:
                    score += 140
            scored.append((score, name.lower(), tool))

        scored.sort(key=lambda item: (-item[0], item[1]))
        reordered = [item[2] for item in scored]
        top_tool_names = [
            str(item.get("name", "")).strip()
            for item in reordered[:3]
            if str(item.get("name", "")).strip()
        ]
        top_tool_hints = [
            f"{str(item.get('name', '')).strip()}: {str(item.get('description', '')).strip()}"
            for item in reordered[:3]
            if str(item.get("name", "")).strip()
        ]
        trace = {
            "preferred_provider_types": sorted(preferred_providers),
            "preferred_capability_classes": sorted(preferred_capabilities),
            "preferred_tool_names": preferred_tools,
            "confidence": float(ranking.get("confidence", 0.0) or 0.0),
            "reason": str(ranking.get("reason", "") or "").strip(),
            "top_tool_names": top_tool_names,
            "top_tool_hints": top_tool_hints,
        }
        return reordered, trace
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
            "- Use must_use_tool only when the request truly requires private/provider/browser execution and cannot be satisfied safely without it.\n"
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
        metadata_candidates: Optional[dict[str, Any]] = None,
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
        provider_candidates = []
        skill_candidates = []
        preferred_capabilities = []
        preferred_tools = []
        if isinstance(metadata_candidates, dict):
            confidence = float(metadata_candidates.get("confidence", 0.0) or 0.0)
            min_confidence = float(
                getattr(self, "TOOL_HINT_RANKER_MIN_METADATA_CONFIDENCE", 0.3) or 0.3
            )
            if confidence < min_confidence:
                metadata_candidates = {}
        if isinstance(metadata_candidates, dict):
            provider_candidates = [
                str(item.get("provider_type", "")).strip()
                for item in (metadata_candidates.get("provider_candidates", []) or [])[:3]
                if isinstance(item, dict) and str(item.get("provider_type", "")).strip()
            ]
            skill_candidates = [
                str(item.get("hint_id", "")).strip()
                for item in (metadata_candidates.get("skill_candidates", []) or [])[:4]
                if isinstance(item, dict) and str(item.get("hint_id", "")).strip()
            ]
            preferred_capabilities = [
                str(item).strip()
                for item in (metadata_candidates.get("preferred_capability_classes", []) or [])[:8]
                if str(item).strip()
            ]
            preferred_tools = [
                str(item).strip()
                for item in (metadata_candidates.get("preferred_tool_names", []) or [])[:8]
                if str(item).strip()
            ]
        metadata_hint_block = (
            "Metadata candidates (runtime pre-recall):\n"
            f"- provider_types: {', '.join(provider_candidates) if provider_candidates else 'none'}\n"
            f"- skill_hints: {', '.join(skill_candidates) if skill_candidates else 'none'}\n"
            f"- preferred_capabilities: {', '.join(preferred_capabilities) if preferred_capabilities else 'none'}\n"
            f"- preferred_tools: {', '.join(preferred_tools) if preferred_tools else 'none'}\n"
        )
        return (
            "Classify the following request for runtime policy.\n\n"
            f"Runtime UTC time:\n{now_utc}\n\n"
            f"User request:\n{user_message}\n\n"
            f"Recent history:\n{history_text}\n\n"
            f"{metadata_hint_block}"
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

