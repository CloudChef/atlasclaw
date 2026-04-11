# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolCandidate,
    ToolPolicyMode,
)


_TOOL_CLASS_BY_NAME: dict[str, str] = {
    "web_search": "web_search",
    "web_fetch": "web_fetch",
    "openmeteo_weather": "weather",
    "browser": "browser",
    "memory_search": "memory",
    "memory_get": "memory",
    "session_status": "session",
    "sessions_list": "session",
    "sessions_history": "session",
    "sessions_send": "session",
    "sessions_spawn": "session",
    "subagents": "session",
}

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
            if desired_provider and inferred_class == "provider:generic":
                description = str(tool.get("description", "")).lower()
                name = str(tool.get("name", "")).lower()
                if desired_provider in description or desired_provider in name:
                    matches.append(self._build_candidate(tool, capability))

        return sorted(matches, key=lambda item: item.priority, reverse=True)

    def _infer_capability_class(self, tool: dict[str, Any]) -> str:
        name = str(tool.get("name", "")).strip()
        lowered_name = name.lower()
        if lowered_name in _TOOL_CLASS_BY_NAME:
            return _TOOL_CLASS_BY_NAME[lowered_name]

        explicit = str(tool.get("capability_class", "") or "").strip()
        if explicit:
            lowered_explicit = explicit.lower()
            # Some snapshots provide coarse classes like "builtin:web". Keep
            # known fine-grained classes and provider classes; otherwise fall
            # back to heuristic inference by tool name/description.
            if lowered_explicit.startswith("provider:"):
                return lowered_explicit
            if lowered_explicit in {
                "web_search",
                "web_fetch",
                "weather",
                "browser",
                "memory",
                "session",
                "skill",
            }:
                return lowered_explicit

        provider_type = str(tool.get("provider_type", "")).strip().lower()
        category = str(tool.get("category", "")).strip().lower()
        if provider_type:
            return f"provider:{provider_type}"

        description = str(tool.get("description", "")).lower()
        if "skill" in category and lowered_name not in _TOOL_CLASS_BY_NAME:
            return "skill"
        if "jira" in lowered_name or "jira" in description:
            return "provider:jira"
        if "browser" in lowered_name or "browser" in description:
            return "browser"
        if "search" in lowered_name and "web" in lowered_name:
            return "web_search"
        if "fetch" in lowered_name and "web" in lowered_name:
            return "web_fetch"
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

