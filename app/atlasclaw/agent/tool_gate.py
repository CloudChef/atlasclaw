# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolCandidate, ToolPolicyMode

_TOOL_PRIORITY: dict[str, int] = {
    "provider": 120,
    "skill": 115,
    "weather": 112,
    "browser": 110,
    "web_search": 100,
    "web_fetch": 90,
    "memory": 50,
    "session": 40,
    "hooks_context": 30,
}


@dataclass
class CapabilityMatcher:
    """Resolve required capability classes against currently available runtime tools."""

    available_tools: list[dict[str, Any]]

    def match(self, suggested_tool_classes: list[str]) -> CapabilityMatchResult:
        if not suggested_tool_classes:
            return CapabilityMatchResult(
                resolved_policy=ToolPolicyMode.ANSWER_DIRECT,
                tool_candidates=[],
                missing_capabilities=[],
                reason="No tool-backed capability is required for this request.",
            )

        matched: list[ToolCandidate] = []
        missing: list[str] = []

        for capability in suggested_tool_classes:
            candidates = self._find_candidates(capability)
            if candidates:
                matched.extend(candidates)
            else:
                missing.append(capability)

        matched = self._dedupe_candidates(matched)
        resolved_policy = ToolPolicyMode.MUST_USE_TOOL if matched or missing else ToolPolicyMode.ANSWER_DIRECT
        reason = (
            "Matched required capabilities to available runtime tools."
            if matched and not missing
            else "Matched some required capabilities, but additional tool classes are still missing."
            if matched and missing
            else "No available tools satisfy the required capabilities."
        )
        return CapabilityMatchResult(
            resolved_policy=resolved_policy,
            tool_candidates=matched,
            missing_capabilities=missing,
            reason=reason,
        )

    def _find_candidates(self, capability: str) -> list[ToolCandidate]:
        provider_prefix = "provider:"
        desired_provider = capability[len(provider_prefix):] if capability.startswith(provider_prefix) else None
        matches: list[ToolCandidate] = []

        for tool in self.available_tools:
            inferred_class = self._infer_capability_class(tool)
            if inferred_class == capability:
                matches.append(self._build_candidate(tool, inferred_class))
                continue
            if self._tool_name_matches_capability(tool, capability):
                matches.append(self._build_candidate(tool, capability))
                continue
            if desired_provider and inferred_class == "provider:generic":
                description = str(tool.get("description", "")).lower()
                name = str(tool.get("name", "")).lower()
                if desired_provider in description or desired_provider in name:
                    matches.append(self._build_candidate(tool, capability))

        return sorted(matches, key=lambda item: item.priority, reverse=True)

    def _infer_capability_class(self, tool: dict[str, Any]) -> str:
        lowered_name = str(tool.get("name", "")).strip().lower()
        explicit = str(tool.get("capability_class", "") or "").strip()
        if explicit:
            lowered_explicit = explicit.lower()
            if lowered_explicit.startswith("provider:"):
                return lowered_explicit
            return lowered_explicit

        provider_type = str(tool.get("provider_type", "")).strip().lower()
        category = str(tool.get("category", "")).strip().lower()
        if provider_type:
            return f"provider:{provider_type}"

        description = str(tool.get("description", "")).lower()
        if "skill" in category:
            return "skill"
        if "skill" in lowered_name or "skill" in description:
            return "skill"
        return "provider:generic" if "provider" in description else lowered_name

    def _build_candidate(self, tool: dict[str, Any], capability_class: str) -> ToolCandidate:
        explicit_priority = tool.get("priority")
        if isinstance(explicit_priority, int):
            priority = explicit_priority
        else:
            key = capability_class.split(":", 1)[0]
            priority = _TOOL_PRIORITY.get(key, 0)
        return ToolCandidate(
            name=str(tool.get("name", "")),
            capability_class=capability_class,
            priority=priority,
            metadata={
                "description": str(tool.get("description", "")).strip(),
            },
        )

    @staticmethod
    def _tool_name_matches_capability(tool: dict[str, Any], capability: str) -> bool:
        normalized_name = str(tool.get("name", "") or "").strip().lower()
        normalized_capability = str(capability or "").strip().lower()
        if not normalized_name or not normalized_capability:
            return False
        if normalized_capability.startswith("provider:"):
            return False
        return (
            normalized_name == normalized_capability
            or normalized_name.startswith(f"{normalized_capability}_")
            or normalized_name.endswith(f"_{normalized_capability}")
        )

    @staticmethod
    def _dedupe_candidates(candidates: list[ToolCandidate]) -> list[ToolCandidate]:
        seen: set[tuple[str, str]] = set()
        deduped: list[ToolCandidate] = []
        for candidate in candidates:
            key = (candidate.name, candidate.capability_class)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped
