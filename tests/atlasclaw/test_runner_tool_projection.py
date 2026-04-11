# -*- coding: utf-8 -*-
from __future__ import annotations

from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    project_minimal_toolset,
    project_planner_toolset,
    tool_required_turn_has_real_execution,
)
from app.atlasclaw.agent.tool_gate_models import ToolIntentAction, ToolIntentPlan


def _allowed_tools() -> list[dict]:
    return [
        {
            "name": "cmp_list_pending",
            "description": "List SmartCMP pending approvals",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp", "group:approval"],
            "capability_class": "provider:smartcmp",
            "skill_name": "approval",
            "qualified_skill_name": "smartcmp:approval",
        },
        {
            "name": "cmp_get_request_detail",
            "description": "Get SmartCMP request detail",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp", "group:request"],
            "capability_class": "provider:smartcmp",
            "skill_name": "request",
            "qualified_skill_name": "smartcmp:request",
        },
        {
            "name": "jira_get_issue",
            "description": "Get Jira issue detail",
            "provider_type": "jira",
            "group_ids": ["group:jira"],
            "capability_class": "provider:jira",
            "skill_name": "jira-issue",
            "qualified_skill_name": "jira:jira-issue",
        },
        {
            "name": "web_search",
            "description": "Search the web",
            "group_ids": ["group:web"],
            "capability_class": "web_search",
            "planner_visibility": "general",
        },
        {
            "name": "list_provider_instances",
            "description": "List provider instances",
            "group_ids": ["group:atlasclaw"],
            "capability_class": "session",
        },
        {
            "name": "select_provider_instance",
            "description": "Select provider instance",
            "group_ids": ["group:atlasclaw"],
            "capability_class": "session",
        },
    ]


def test_project_minimal_toolset_keeps_only_targeted_provider_and_coordination_tools() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
        target_group_ids=["cmp"],
        target_capability_classes=["provider:smartcmp"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_list_pending",
        "cmp_get_request_detail",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["enabled"] is True
    assert trace["reason"] == "projection_applied"


def test_project_minimal_toolset_supports_skill_and_explicit_tool_narrowing() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
        target_capability_classes=["provider:smartcmp"],
        target_skill_names=["smartcmp:request"],
        target_tool_names=["cmp_get_request_detail"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_get_request_detail",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["after_count"] == 3


def test_project_minimal_toolset_does_not_widen_when_projection_is_empty() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["datadog"],
        target_capability_classes=["provider:datadog"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert filtered == []
    assert trace["reason"] == "projection_empty"


def test_tool_required_turn_requires_real_execution() -> None:
    plan = ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_provider_types=["smartcmp"])

    has_execution = tool_required_turn_has_real_execution(
        intent_plan=plan,
        tool_call_summaries=[],
        final_messages=[{"role": "assistant", "content": "I will query CMP now."}],
        start_index=0,
    )

    assert has_execution is False


def test_tool_required_turn_accepts_real_tool_execution_messages() -> None:
    plan = ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_provider_types=["smartcmp"])

    has_execution = tool_required_turn_has_real_execution(
        intent_plan=plan,
        tool_call_summaries=[],
        final_messages=[
            {"role": "assistant", "content": "Let me check that."},
            {"role": "tool", "tool_name": "cmp_list_pending", "content": "count=3"},
        ],
        start_index=0,
    )

    assert has_execution is True


def test_tool_required_turn_accepts_embedded_tool_results() -> None:
    plan = ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_provider_types=["smartcmp"])

    has_execution = tool_required_turn_has_real_execution(
        intent_plan=plan,
        tool_call_summaries=[],
        final_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_results": [
                    {
                        "tool_name": "cmp_list_pending",
                        "content": {"output": "count=3"},
                    }
                ],
            }
        ],
        start_index=0,
    )

    assert has_execution is True


def test_tool_required_turn_accepts_executed_tool_names_without_tool_messages() -> None:
    plan = ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_provider_types=["smartcmp"])

    has_execution = tool_required_turn_has_real_execution(
        intent_plan=plan,
        tool_call_summaries=[{"name": "smartcmp_list_pending", "args": {}}],
        final_messages=[{"role": "assistant", "content": "我来查一下。"}],
        start_index=0,
        executed_tool_names=["smartcmp_list_pending"],
    )

    assert has_execution is True


def test_project_planner_toolset_drops_provider_tools_for_new_non_follow_up_turn() -> None:
    filtered, trace = project_planner_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "planner_visibility": "general",
            },
        ],
        metadata_candidates={
            "confidence": 0.0,
            "preferred_provider_types": [],
            "preferred_group_ids": [],
            "preferred_capability_classes": [],
            "preferred_tool_names": [],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
    )

    assert [tool["name"] for tool in filtered] == [
        "web_search",
        "openmeteo_weather",
    ]
    assert trace["reason"] == "planner_general_tools_only"


def test_project_planner_toolset_keeps_metadata_matched_provider_subset() -> None:
    filtered, trace = project_planner_toolset(
        allowed_tools=_allowed_tools(),
        metadata_candidates={
            "confidence": 0.92,
            "preferred_provider_types": ["smartcmp"],
            "preferred_group_ids": ["group:cmp"],
            "preferred_capability_classes": ["provider:smartcmp"],
            "preferred_tool_names": ["cmp_list_pending"],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_list_pending",
        "cmp_get_request_detail",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "planner_metadata_subset"


def test_project_planner_toolset_prefers_single_tool_consensus_below_threshold() -> None:
    filtered, trace = project_planner_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "planner_visibility": "general",
            },
        ],
        metadata_candidates={
            "confidence": 0.08,
            "preferred_provider_types": [],
            "preferred_group_ids": ["group:web"],
            "preferred_capability_classes": ["weather"],
            "preferred_tool_names": ["openmeteo_weather"],
            "tool_candidates": [
                {
                    "hint_id": "tool:openmeteo_weather",
                    "tool_name": "openmeteo_weather",
                    "score": 8,
                    "has_strong_anchor": True,
                    "tool_names": ["openmeteo_weather"],
                    "group_ids": ["group:web"],
                    "capability_classes": ["weather"],
                }
            ],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
    )

    assert [tool["name"] for tool in filtered] == [
        "openmeteo_weather",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "planner_metadata_subset"


def test_project_planner_toolset_hides_contextual_builtin_tools_for_public_turn() -> None:
    filtered, trace = project_planner_toolset(
        allowed_tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "group_ids": ["group:web"],
                "capability_class": "web_search",
                "planner_visibility": "general",
            },
            {
                "name": "web_fetch",
                "description": "Fetch webpage content",
                "group_ids": ["group:web"],
                "capability_class": "web_fetch",
                "planner_visibility": "general",
            },
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "planner_visibility": "general",
            },
            {
                "name": "browser",
                "description": "Browser automation",
                "group_ids": ["group:ui"],
                "capability_class": "browser",
                "planner_visibility": "general",
            },
            {
                "name": "exec",
                "description": "Execute shell command",
                "group_ids": ["group:runtime"],
                "capability_class": "",
                "planner_visibility": "contextual",
            },
            {
                "name": "process",
                "description": "Manage background process",
                "group_ids": ["group:runtime"],
                "capability_class": "",
                "planner_visibility": "contextual",
            },
            {
                "name": "read",
                "description": "Read file content",
                "group_ids": ["group:fs"],
                "capability_class": "",
                "planner_visibility": "contextual",
            },
        ],
        metadata_candidates={
            "confidence": 0.0,
            "preferred_provider_types": [],
            "preferred_group_ids": [],
            "preferred_capability_classes": [],
            "preferred_tool_names": [],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
    )

    assert [tool["name"] for tool in filtered] == [
        "web_search",
        "web_fetch",
        "openmeteo_weather",
        "browser",
    ]
    assert trace["reason"] == "planner_general_tools_only"


def test_project_planner_toolset_treats_provider_type_none_string_as_non_provider() -> None:
    filtered, trace = project_planner_toolset(
        allowed_tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "provider_type": "None",
                "group_ids": ["group:web"],
                "capability_class": "web_search",
                "planner_visibility": "general",
            },
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "provider_type": "None",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "planner_visibility": "general",
            },
            {
                "name": "smartcmp_list_pending",
                "description": "List pending approvals",
                "provider_type": "smartcmp",
                "group_ids": ["group:cmp"],
                "capability_class": "provider:smartcmp",
                "planner_visibility": "contextual",
            },
        ],
        metadata_candidates={
            "confidence": 0.0,
            "preferred_provider_types": [],
            "preferred_group_ids": [],
            "preferred_capability_classes": [],
            "preferred_tool_names": [],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
    )

    assert [tool["name"] for tool in filtered] == [
        "web_search",
        "openmeteo_weather",
    ]
    assert trace["reason"] == "planner_general_tools_only"
