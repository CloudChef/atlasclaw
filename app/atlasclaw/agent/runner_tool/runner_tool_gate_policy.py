# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)
from app.atlasclaw.core.deps import SkillDeps


class RunnerToolGatePolicyMixin:
    def _inject_tool_policy(
        self,
        *,
        deps: SkillDeps,
        intent_plan: Optional[ToolIntentPlan],
        available_tools: list[dict[str, Any]],
    ) -> None:
        """Inject per-run turn guidance for prompt building."""
        if not isinstance(deps.extra, dict):
            deps.extra = {}
        retry_count = int(deps.extra.get("_tool_execution_retry_count", 0) or 0)
        retry_missing_tools = deps.extra.get("tool_execution_retry_missing_tools")
        if not isinstance(retry_missing_tools, list):
            retry_missing_tools = []
        if intent_plan is None:
            preferred_tools = self._preferred_tools_from_available_tools(available_tools=available_tools)
        else:
            preferred_tools = self._preferred_tools_for_intent_plan(
                intent_plan=intent_plan,
                available_tools=available_tools,
            )
        policy_mode = "llm_first"
        policy_reason = (
            "The main model decides this turn after capability pruning and prompt construction."
        )
        target_provider_types: list[str] = []
        target_skill_names: list[str] = []
        target_group_ids: list[str] = []
        target_capability_classes: list[str] = []
        if intent_plan is not None:
            if intent_plan.action is ToolIntentAction.CREATE_ARTIFACT:
                policy_mode = intent_plan.action.value
            elif intent_plan.action is ToolIntentAction.USE_TOOLS:
                policy_mode = intent_plan.action.value
            elif intent_plan.action is ToolIntentAction.ASK_CLARIFICATION:
                policy_mode = intent_plan.action.value
            policy_reason = intent_plan.reason or policy_reason
            target_provider_types = list(intent_plan.target_provider_types)
            target_skill_names = list(intent_plan.target_skill_names)
            target_group_ids = list(intent_plan.target_group_ids)
            target_capability_classes = list(intent_plan.target_capability_classes)
        execution_hint = (
            "provider_tool_first"
            if (
                intent_plan is not None
                and bool(intent_plan.target_provider_types or intent_plan.target_skill_names)
            )
            else "default"
        )
        artifact_goal = None
        if isinstance(deps.extra, dict):
            candidate_goal = deps.extra.get("artifact_goal")
            if isinstance(candidate_goal, dict):
                artifact_goal = dict(candidate_goal)
        deps.extra["tool_policy"] = {
            "mode": policy_mode,
            "reason": policy_reason,
            "preferred_tools": preferred_tools,
            "execution_hint": execution_hint,
            "max_same_tool_calls_per_turn": int(
                getattr(self, "MAX_IDENTICAL_TOOL_CALLS_PER_TURN", 2) or 2
            ),
            "retry_count": retry_count,
            "retry_missing_tools": [
                str(name).strip() for name in retry_missing_tools if str(name).strip()
            ],
            "target_provider_types": target_provider_types,
            "target_skill_names": target_skill_names,
            "target_group_ids": target_group_ids,
            "target_capability_classes": target_capability_classes,
            "artifact_goal": artifact_goal,
        }

    @staticmethod
    def _preferred_tools_from_available_tools(
        *,
        available_tools: list[dict[str, Any]],
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            try:
                priority = int(tool.get("priority", 100) or 100)
            except (TypeError, ValueError):
                priority = 100
            ranked.append((priority, name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        result: list[str] = []
        for _, name in ranked:
            if name in result:
                continue
            result.append(name)
            if len(result) >= 8:
                break
        return result

    @staticmethod
    def _preferred_tools_for_intent_plan(
        *,
        intent_plan: ToolIntentPlan,
        available_tools: list[dict[str, Any]],
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            try:
                priority = int(tool.get("priority", 100) or 100)
            except (TypeError, ValueError):
                priority = 100
            ranked.append((priority, name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        names: list[str] = []
        for _, name in ranked:
            if name in names:
                continue
            names.append(name)
            if len(names) >= 8:
                break
        if names:
            return names
        return list(intent_plan.target_tool_names)
    @staticmethod
    def _preferred_tool_names_for_prompt(
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        required_tools: list[str],
    ) -> list[str]:
        if decision.needs_external_system:
            provider_skill_candidates: list[str] = []
            for candidate in match_result.tool_candidates:
                capability = str(getattr(candidate, "capability_class", "") or "").strip()
                name = str(getattr(candidate, "name", "") or "").strip()
                if not name:
                    continue
                if not (capability.startswith("provider:") or capability == "skill"):
                    continue
                if name in provider_skill_candidates:
                    continue
                provider_skill_candidates.append(name)
                if len(provider_skill_candidates) >= 5:
                    break
            if provider_skill_candidates:
                return provider_skill_candidates
        return list(required_tools)
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
        intent_plan: Optional[ToolIntentPlan] = None,
    ) -> list[str]:
        explicit_target_tools = [
            str(name or "").strip()
            for name in list(getattr(intent_plan, "target_tool_names", []) or [])
            if str(name or "").strip()
        ]
        if explicit_target_tools:
            deduped_targets: list[str] = []
            seen_targets: set[str] = set()
            for name in explicit_target_tools:
                if name in seen_targets:
                    continue
                seen_targets.add(name)
                deduped_targets.append(name)
            return deduped_targets

        required: list[str] = []
        if decision.needs_external_system:
            required_by_capability: dict[str, str] = {}
            for candidate in match_result.tool_candidates:
                capability = str(getattr(candidate, "capability_class", "") or "").strip()
                name = str(getattr(candidate, "name", "") or "").strip()
                if not name:
                    continue
                if not (capability.startswith("provider:") or capability == "skill"):
                    continue
                if capability in required_by_capability:
                    continue
                required_by_capability[capability] = name
            if required_by_capability:
                return list(required_by_capability.values())

        for candidate in match_result.tool_candidates:
            capability = str(getattr(candidate, "capability_class", "") or "").strip()
            name = str(getattr(candidate, "name", "") or "").strip()
            if not name:
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
        intent_plan: Optional[ToolIntentPlan] = None,
        tool_call_summaries: list[dict[str, Any]],
        available_tools: Optional[list[dict[str, Any]]] = None,
        final_messages: Optional[list[dict[str, Any]]] = None,
        run_output_start_index: int = 0,
    ) -> list[str]:
        required = self._required_tool_names_for_decision(
            decision=decision,
            match_result=match_result,
            intent_plan=intent_plan,
        )
        if not required:
            return []
        called = self._called_tool_names(tool_call_summaries)
        if final_messages:
            called.update(
                self._collect_called_tool_names_from_messages(
                    messages=final_messages,
                    start_index=run_output_start_index,
                )
            )
        missing = [name for name in required if name not in called]
        if not missing:
            return []
        if not called:
            return missing

        capability_map = self._build_tool_capability_map(available_tools or [])
        called_capabilities = {
            self._resolve_tool_capability(name=name, capability_map=capability_map)
            for name in called
        }
        required_capabilities = {
            self._resolve_tool_capability(name=name, capability_map=capability_map)
            for name in required
        }

        if self._called_capabilities_satisfy_required(
            decision=decision,
            called_capabilities=called_capabilities,
            required_capabilities=required_capabilities,
        ):
            missing = []

        if final_messages:
            successful_tools = self._collect_successful_tool_names(
                messages=final_messages,
                start_index=run_output_start_index,
            )
            if required:
                missing_success = [name for name in required if name not in successful_tools]
                if missing_success:
                    successful_capabilities = {
                        self._resolve_tool_capability(name=name, capability_map=capability_map)
                        for name in successful_tools
                    }
                    if not self._called_capabilities_satisfy_required(
                        decision=decision,
                        called_capabilities=successful_capabilities,
                        required_capabilities=required_capabilities,
                    ):
                        return missing_success

        return missing
    def _collect_called_tool_names_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
    ) -> set[str]:
        names: set[str] = set()
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                tool_name = str(
                    message.get("tool_name", "") or message.get("name", "")
                ).strip()
                if tool_name:
                    names.add(tool_name)
                continue
            if role != "assistant":
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                tool_name = str(
                    call.get("name", "") or call.get("tool_name", "")
                ).strip()
                if tool_name:
                    names.add(tool_name)
            tool_results = message.get("tool_results")
            if not isinstance(tool_results, list):
                continue
            for result in tool_results:
                if not isinstance(result, dict):
                    continue
                tool_name = str(
                    result.get("name", "") or result.get("tool_name", "")
                ).strip()
                if tool_name:
                    names.add(tool_name)
        return names
    def _should_enforce_web_tool_verification(
        self,
        *,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        available_tools: list[dict[str, Any]],
    ) -> bool:
        """Return whether hard/soft tool verification should be enforced for this run.

        Enforcement stays on the unified model tool loop for all tool classes.
        """
        if decision.policy not in {ToolPolicyMode.MUST_USE_TOOL, ToolPolicyMode.PREFER_TOOL}:
            return False
        strict_need = self._tool_gate_has_strict_need(decision)
        required_tools = self._required_tool_names_for_decision(
            decision=decision,
            match_result=match_result,
        )
        if decision.policy is ToolPolicyMode.MUST_USE_TOOL and required_tools:
            return True
        if decision.policy is ToolPolicyMode.PREFER_TOOL:
            return strict_need and bool(required_tools)

        suggested = [
            str(item or "").strip()
            for item in decision.suggested_tool_classes
            if isinstance(item, str) and str(item or "").strip()
        ]
        if decision.policy is ToolPolicyMode.MUST_USE_TOOL and suggested:
            return True
        if decision.policy is ToolPolicyMode.PREFER_TOOL:
            return strict_need and bool(suggested)

        if decision.policy is ToolPolicyMode.MUST_USE_TOOL:
            return bool(
                decision.needs_tool
                or decision.needs_external_system
                or decision.needs_live_data
                or decision.needs_grounded_verification
            )
        return strict_need and bool(decision.needs_tool)
    def _collect_successful_tool_names(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int = 0,
    ) -> set[str]:
        successful: set[str] = set()
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
                if tool_name and self._is_tool_result_success(message):
                    successful.add(tool_name)
            tool_results = message.get("tool_results")
            if not isinstance(tool_results, list):
                continue
            for result in tool_results:
                if not isinstance(result, dict):
                    continue
                tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
                if tool_name and self._is_tool_result_success(result):
                    successful.add(tool_name)
        return successful
    @staticmethod
    def _tool_requires_identifier_success_contract(tool_name: str) -> bool:
        # Request submission is only considered complete once the backend returns
        # a concrete request/workflow identifier that the user can track later.
        return str(tool_name or "").strip().lower() == "smartcmp_submit_request"

    @classmethod
    def _payload_contains_required_identifier(cls, payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, dict):
            for key in ("id", "requestId", "request_id", "workflowId", "workflow_id"):
                value = payload.get(key)
                normalized = str(value or "").strip()
                if normalized and normalized.lower() not in {"n/a", "na", "none", "null"}:
                    return True
            for key in ("output", "content", "details", "results", "data", "summary", "text", "message"):
                if key in payload and cls._payload_contains_required_identifier(payload.get(key)):
                    return True
            return False
        if isinstance(payload, list):
            return any(cls._payload_contains_required_identifier(item) for item in payload)
        if isinstance(payload, str):
            normalized = payload.strip()
            if not normalized:
                return False
            if normalized.startswith("{") or normalized.startswith("["):
                try:
                    parsed = json.loads(normalized)
                except Exception:
                    parsed = None
                if parsed is not None:
                    return cls._payload_contains_required_identifier(parsed)
            # Provider scripts often print plain text instead of structured JSON.
            # Accept the result only when it clearly surfaces a request/workflow ID.
            return bool(
                re.search(
                    r"\b(?:request|workflow)\s*id\s*:\s*(?!n/?a\b|none\b|null\b)([A-Za-z0-9][A-Za-z0-9._:-]*)",
                    normalized,
                    flags=re.IGNORECASE,
                )
            )
        return False

    @classmethod
    def _is_tool_result_success(cls, message: dict[str, Any]) -> bool:
        if bool(message.get("is_error")):
            return False
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
        content = message.get("content")
        if isinstance(content, str):
            payload = content.strip()
            if not payload:
                return False
            if payload.startswith("{") or payload.startswith("["):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    return cls._is_tool_payload_success(payload, tool_name=tool_name)
                return cls._is_tool_payload_success(parsed, tool_name=tool_name)
            return cls._is_tool_payload_success(payload, tool_name=tool_name)
        return cls._is_tool_payload_success(content, tool_name=tool_name)

    @classmethod
    def _is_tool_payload_success(cls, payload: Any, *, tool_name: str = "") -> bool:
        requires_identifier = cls._tool_requires_identifier_success_contract(tool_name)
        if payload is None:
            return False
        if isinstance(payload, dict):
            if bool(payload.get("is_error")):
                return False
            if requires_identifier:
                error_value = payload.get("error")
                if isinstance(error_value, str) and error_value.strip():
                    return False
                if isinstance(error_value, (dict, list)) and error_value:
                    return False
                return cls._payload_contains_required_identifier(payload)
            if payload.get("success") is True:
                output_value = payload.get("output")
                if isinstance(output_value, str) and output_value.strip():
                    return True
                if output_value not in (None, "", [], {}):
                    return True
                return True
            if "returncode" in payload and payload.get("returncode") == 0:
                output_value = payload.get("output")
                if isinstance(output_value, str):
                    return bool(output_value.strip())
                if output_value not in (None, "", [], {}):
                    return True
            error_value = payload.get("error")
            if isinstance(error_value, str) and error_value.strip():
                return False
            if isinstance(error_value, dict) and error_value:
                return False
            if isinstance(error_value, list) and error_value:
                return False
            if "content" in payload:
                return cls._is_tool_payload_success(payload.get("content"), tool_name=tool_name)
            if "details" in payload:
                return True
            if "results" in payload:
                return cls._is_tool_payload_success(payload.get("results"), tool_name=tool_name)
            if "data" in payload:
                return cls._is_tool_payload_success(payload.get("data"), tool_name=tool_name)
            if "summary" in payload and str(payload.get("summary", "")).strip():
                return True
            if "text" in payload and str(payload.get("text", "")).strip():
                return True
            if "output" in payload and str(payload.get("output", "")).strip():
                return True
            return bool(payload)
        if isinstance(payload, list):
            if not payload:
                return False
            for item in payload:
                if cls._is_tool_payload_success(item, tool_name=tool_name):
                    return True
            return False
        if isinstance(payload, (int, float, bool)):
            if requires_identifier:
                return False
            return True
        if isinstance(payload, str):
            if requires_identifier:
                return cls._payload_contains_required_identifier(payload)
            return bool(payload.strip())
        return bool(payload)
    @staticmethod
    def _build_tool_capability_map(available_tools: list[dict[str, Any]]) -> dict[str, str]:
        capability_map: dict[str, str] = {}
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            capability = str(tool.get("capability_class", "") or "").strip()
            if capability:
                capability_map[name] = capability
        return capability_map
    def _resolve_tool_capability(self, *, name: str, capability_map: dict[str, str]) -> str:
        direct = capability_map.get(name, "")
        if direct:
            return direct
        return ""
    @staticmethod
    def _called_capabilities_satisfy_required(
        *,
        decision: ToolGateDecision,
        called_capabilities: set[str],
        required_capabilities: set[str],
    ) -> bool:
        normalized_called = {item for item in called_capabilities if item}
        normalized_required = {item for item in required_capabilities if item}
        if not normalized_called:
            return False

        provider_skill_called = any(
            capability.startswith("provider:") or capability == "skill"
            for capability in normalized_called
        )
        web_required_only = bool(normalized_required) and all(
            capability in {"web_search", "web_fetch", "browser", "weather"}
            for capability in normalized_required
        )

        if decision.needs_external_system and provider_skill_called:
            return True
        if normalized_required.intersection(normalized_called):
            return True
        if provider_skill_called and web_required_only:
            # Guard against classifier mismatch: when provider/skill tools actually ran,
            # do not force an unrelated web-search requirement.
            return True
        return False
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
            display_missing = list(missing_required_tools)
            if len(display_missing) > 5:
                extra_count = len(display_missing) - 5
                display_missing = [*display_missing[:5], f"...({extra_count} more)"]
            return (
                "A grounded tool-backed answer is required for this request, but required tools were not executed: "
                f"{', '.join(display_missing)}."
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
