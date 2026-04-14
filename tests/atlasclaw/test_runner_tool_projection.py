# -*- coding: utf-8 -*-
from __future__ import annotations

from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    compress_candidate_toolset,
    project_minimal_toolset,
    tool_required_turn_has_real_execution,
    turn_action_requires_tool_execution,
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
            "routing_visibility": "general",
        },
        {
            "name": "list_provider_instances",
            "description": "List provider instances",
            "group_ids": ["group:atlasclaw"],
            "capability_class": "session",
            "coordination_only": True,
        },
        {
            "name": "select_provider_instance",
            "description": "Select provider instance",
            "group_ids": ["group:atlasclaw"],
            "capability_class": "session",
            "coordination_only": True,
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


def test_project_minimal_toolset_supports_explicit_create_artifact_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.CREATE_ARTIFACT,
        target_tool_names=["pptx_create_deck"],
        target_capability_classes=["artifact:pptx"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "pptx_create_deck",
                "description": "Create PPTX deck",
                "group_ids": ["group:pptx"],
                "capability_class": "artifact:pptx",
                "result_mode": "tool_only_ok",
            },
        ],
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == ["pptx_create_deck"]
    assert trace["enabled"] is True
    assert trace["reason"] == "projection_applied"


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


def test_tool_required_turn_requires_real_execution_for_explicit_create_artifact_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.CREATE_ARTIFACT,
        target_tool_names=["pptx_create_deck"],
        target_capability_classes=["artifact:pptx"],
    )

    has_execution = tool_required_turn_has_real_execution(
        intent_plan=plan,
        tool_call_summaries=[],
        final_messages=[{"role": "assistant", "content": "我来帮你生成 PPT。"}],
        start_index=0,
    )

    assert has_execution is False


def test_turn_action_requires_tool_execution_for_explicit_create_artifact_target() -> None:
    assert turn_action_requires_tool_execution(
        ToolIntentPlan(
            action=ToolIntentAction.CREATE_ARTIFACT,
            target_tool_names=["pptx_create_deck"],
            target_capability_classes=["artifact:pptx"],
        )
    )
    assert turn_action_requires_tool_execution(
        ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_tool_names=["cmp_list_pending"])
    )
    assert not turn_action_requires_tool_execution(
        ToolIntentPlan(action=ToolIntentAction.CREATE_ARTIFACT)
    )


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


def test_compress_candidate_toolset_keeps_full_surface_without_metadata_subset() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "routing_visibility": "general",
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
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_list_pending",
        "cmp_get_request_detail",
        "jira_get_issue",
        "web_search",
        "list_provider_instances",
        "select_provider_instance",
        "openmeteo_weather",
    ]
    assert trace["reason"] == "candidate_compression_not_required"


def test_compress_candidate_toolset_keeps_metadata_matched_provider_subset() -> None:
    filtered, trace = compress_candidate_toolset(
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
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_list_pending",
        "cmp_get_request_detail",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "candidate_compression_applied"


def test_compress_candidate_toolset_prefers_single_tool_consensus_below_threshold() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "routing_visibility": "general",
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
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "openmeteo_weather",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "candidate_compression_applied"


def test_compress_candidate_toolset_keeps_full_surface_without_metadata_hint() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "group_ids": ["group:web"],
                "capability_class": "web_search",
                "routing_visibility": "general",
            },
            {
                "name": "web_fetch",
                "description": "Fetch webpage content",
                "group_ids": ["group:web"],
                "capability_class": "web_fetch",
                "routing_visibility": "general",
            },
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "routing_visibility": "general",
            },
            {
                "name": "browser",
                "description": "Browser automation",
                "group_ids": ["group:ui"],
                "capability_class": "browser",
                "routing_visibility": "general",
            },
            {
                "name": "exec",
                "description": "Execute shell command",
                "group_ids": ["group:runtime"],
                "capability_class": "",
                "routing_visibility": "contextual",
            },
            {
                "name": "process",
                "description": "Manage background process",
                "group_ids": ["group:runtime"],
                "capability_class": "",
                "routing_visibility": "contextual",
            },
            {
                "name": "read",
                "description": "Read file content",
                "group_ids": ["group:fs"],
                "capability_class": "",
                "routing_visibility": "contextual",
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
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "web_search",
        "web_fetch",
        "openmeteo_weather",
        "browser",
        "exec",
        "process",
        "read",
    ]
    assert trace["reason"] == "candidate_compression_not_required"


def test_compress_candidate_toolset_treats_provider_type_none_string_as_non_provider() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "provider_type": "None",
                "group_ids": ["group:web"],
                "capability_class": "web_search",
                "routing_visibility": "general",
            },
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "provider_type": "None",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "routing_visibility": "general",
            },
            {
                "name": "smartcmp_list_pending",
                "description": "List pending approvals",
                "provider_type": "smartcmp",
                "group_ids": ["group:cmp"],
                "capability_class": "provider:smartcmp",
                "routing_visibility": "contextual",
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
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "web_search",
        "openmeteo_weather",
        "smartcmp_list_pending",
    ]
    assert trace["reason"] == "candidate_compression_not_required"


def test_compress_candidate_toolset_applies_metadata_subset() -> None:
    filtered, trace = compress_candidate_toolset(
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
        compression_threshold=1,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_list_pending",
        "cmp_get_request_detail",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "candidate_compression_applied"


def test_compress_candidate_toolset_prefers_explicit_artifact_tools_over_generic_fs_helpers() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            {
                "name": "write",
                "description": "Write file content",
                "group_ids": ["group:fs", "group:atlasclaw"],
                "capability_class": "fs_write",
                "priority": 100,
            },
            {
                "name": "pptx_create_deck",
                "description": "Create a .pptx presentation",
                "group_ids": ["group:pptx", "group:fs"],
                "capability_class": "artifact:pptx",
                "priority": 120,
            },
            {
                "name": "smartcmp_get_request_detail",
                "description": "Get SmartCMP request detail",
                "provider_type": "smartcmp",
                "group_ids": ["group:cmp", "group:request"],
                "capability_class": "provider:smartcmp",
                "priority": 100,
            },
            {
                "name": "list_provider_instances",
                "description": "List provider instances",
                "group_ids": ["group:atlasclaw"],
                "capability_class": "",
                "coordination_only": True,
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "group_ids": ["group:atlasclaw"],
                "capability_class": "",
                "coordination_only": True,
            },
        ],
        metadata_candidates={
            "confidence": 1.0,
            "preferred_provider_types": ["smartcmp"],
            "preferred_group_ids": [
                "group:fs",
                "group:atlasclaw",
                "group:pptx",
                "group:cmp",
                "group:request",
            ],
            "preferred_capability_classes": ["fs_write", "artifact:pptx", "provider:smartcmp"],
            "preferred_tool_names": ["write", "pptx_create_deck", "smartcmp_get_request_detail"],
            "tool_candidates": [
                {
                    "hint_id": "tool:write",
                    "tool_name": "write",
                    "score": 27,
                    "has_strong_anchor": True,
                    "tool_names": ["write"],
                    "group_ids": ["group:fs", "group:atlasclaw"],
                    "capability_classes": ["fs_write"],
                },
                {
                    "hint_id": "tool:pptx_create_deck",
                    "tool_name": "pptx_create_deck",
                    "score": 13,
                    "has_strong_anchor": True,
                    "tool_names": ["pptx_create_deck"],
                    "group_ids": ["group:pptx", "group:fs"],
                    "capability_classes": ["artifact:pptx"],
                },
                {
                    "hint_id": "tool:smartcmp_get_request_detail",
                    "tool_name": "smartcmp_get_request_detail",
                    "score": 9,
                    "has_strong_anchor": True,
                    "tool_names": ["smartcmp_get_request_detail"],
                    "group_ids": ["group:cmp", "group:request"],
                    "capability_classes": ["provider:smartcmp"],
                },
            ],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "pptx_create_deck",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "candidate_compression_applied"


def test_compress_candidate_toolset_prefers_explicit_artifact_tool_over_generic_write() -> None:
    filtered, trace = compress_candidate_toolset(
        allowed_tools=[
            {
                "name": "write",
                "description": "Write file content",
                "group_ids": ["group:fs", "group:atlasclaw"],
                "capability_class": "fs_write",
                "priority": 100,
            },
            {
                "name": "pptx_create_deck",
                "description": "Create a PPTX deck",
                "group_ids": ["group:pptx", "group:fs"],
                "capability_class": "artifact:pptx",
                "priority": 120,
            },
            {
                "name": "smartcmp_get_request_detail",
                "description": "Get SmartCMP request detail",
                "provider_type": "smartcmp",
                "group_ids": ["group:cmp", "group:request"],
                "capability_class": "provider:smartcmp",
                "priority": 100,
            },
            {
                "name": "list_provider_instances",
                "description": "List provider instances",
                "group_ids": ["group:atlasclaw"],
                "capability_class": "",
                "coordination_only": True,
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "group_ids": ["group:atlasclaw"],
                "capability_class": "",
                "coordination_only": True,
            },
        ],
        metadata_candidates={
            "confidence": 1.0,
            "preferred_provider_types": ["smartcmp"],
            "preferred_group_ids": [
                "group:fs",
                "group:atlasclaw",
                "group:pptx",
                "group:cmp",
                "group:request",
            ],
            "preferred_capability_classes": ["fs_write", "artifact:pptx", "provider:smartcmp"],
            "preferred_tool_names": ["write", "pptx_create_deck", "smartcmp_get_request_detail"],
            "tool_candidates": [
                {
                    "tool_name": "write",
                    "score": 27,
                    "has_strong_anchor": True,
                    "tool_names": ["write"],
                    "group_ids": ["group:fs", "group:atlasclaw"],
                    "capability_classes": ["fs_write"],
                },
                {
                    "tool_name": "pptx_create_deck",
                    "score": 13,
                    "has_strong_anchor": True,
                    "tool_names": ["pptx_create_deck"],
                    "group_ids": ["group:pptx", "group:fs"],
                    "capability_classes": ["artifact:pptx"],
                },
                {
                    "tool_name": "smartcmp_get_request_detail",
                    "score": 9,
                    "has_strong_anchor": True,
                    "tool_names": ["smartcmp_get_request_detail"],
                    "group_ids": ["group:cmp", "group:request"],
                    "capability_classes": ["provider:smartcmp"],
                },
            ],
        },
        used_follow_up_context=False,
        min_metadata_confidence=0.3,
        compression_threshold=2,
    )

    assert [tool["name"] for tool in filtered] == [
        "pptx_create_deck",
        "list_provider_instances",
        "select_provider_instance",
    ]
    assert trace["reason"] == "candidate_compression_applied"
