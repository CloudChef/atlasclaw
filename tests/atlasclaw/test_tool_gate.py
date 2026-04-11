# -*- coding: utf-8 -*-
from __future__ import annotations

from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import ToolPolicyMode


def test_matcher_prefers_provider_for_private_provider_question() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {"name": "web_search", "description": "Web search"},
            {"name": "jira_search", "description": "Search Jira issues", "capability_class": "provider:jira"},
        ]
    )
    result = matcher.match(["provider:jira"])
    assert result.tool_candidates[0].name == "jira_search"


def test_matcher_reports_partial_missing_capabilities() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {"name": "web_search", "description": "Web search"},
        ]
    )

    result = matcher.match(["web_search", "browser"])

    assert [candidate.name for candidate in result.tool_candidates] == ["web_search"]
    assert result.missing_capabilities == ["browser"]
    assert result.resolved_policy is ToolPolicyMode.MUST_USE_TOOL


def test_matcher_infers_skill_capability_from_category() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {
                "name": "custom_skill_runner",
                "description": "Run one workflow skill",
                "category": "skill",
            }
        ]
    )

    result = matcher.match(["skill"])

    assert result.missing_capabilities == []
    assert [candidate.name for candidate in result.tool_candidates] == ["custom_skill_runner"]
    assert result.tool_candidates[0].capability_class == "skill"


def test_matcher_resolves_openmeteo_weather_capability() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get forecast weather from Open-Meteo APIs",
            }
        ]
    )

    result = matcher.match(["weather"])

    assert result.missing_capabilities == []
    assert [candidate.name for candidate in result.tool_candidates] == ["openmeteo_weather"]
    assert result.tool_candidates[0].capability_class == "weather"


def test_matcher_prefers_name_mapping_over_coarse_explicit_capability_class() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {
                "name": "web_search",
                "description": "Web search",
                "capability_class": "builtin:web",
            }
        ]
    )

    result = matcher.match(["web_search"])

    assert result.missing_capabilities == []
    assert [candidate.name for candidate in result.tool_candidates] == ["web_search"]
    assert result.tool_candidates[0].capability_class == "web_search"
