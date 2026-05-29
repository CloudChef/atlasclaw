# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
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


def test_project_minimal_toolset_does_not_project_provider_tools_without_instance_skill_scope() -> None:
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

    assert filtered == []
    assert trace["enabled"] is True
    assert trace["reason"] == "projection_empty"


def test_project_minimal_toolset_supports_skill_and_explicit_tool_narrowing() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_capability_classes=["provider:smartcmp"],
        target_provider_skill_names=["cmp.request"],
        target_tool_names=["cmp_get_request_detail"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == ["cmp_get_request_detail"]
    assert trace["after_count"] == 1


def test_project_minimal_toolset_intersects_provider_and_skill_targets() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == ["cmp_get_request_detail"]
    assert trace["reason"] == "projection_applied"


def test_project_minimal_toolset_does_not_treat_provider_qualified_skill_as_provider_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
        target_skill_names=["smartcmp:request"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert filtered == []
    assert trace["reason"] == "projection_empty"


def test_project_minimal_toolset_requires_provider_instance_for_provider_skill() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert filtered == []
    assert trace["reason"] == "projection_empty"


def test_project_minimal_toolset_does_not_widen_provider_type_without_skill_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert filtered == []
    assert trace["reason"] == "projection_empty"
    assert trace["steps"][0]["step"] == "tool_name"
    assert {"step": "provider_type", "active": False, "before_count": 6, "after_count": 0} in trace["steps"]


def test_project_minimal_toolset_hides_tools_for_direct_answer_without_targets() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.DIRECT_ANSWER,
        reason="capability selector found no runtime target",
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=_allowed_tools(),
        intent_plan=plan,
    )

    assert filtered == []
    assert trace["enabled"] is True
    assert trace["reason"] == "projection_empty"


def test_project_minimal_toolset_does_not_add_same_named_standalone_skill_for_provider_skill() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "plain_request_helper",
                "description": "Standalone request helper",
                "skill_name": "request",
                "qualified_skill_name": "request",
            },
        ],
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == ["cmp_get_request_detail"]
    assert trace["reason"] == "projection_applied"


def test_project_minimal_toolset_keeps_standalone_skill_with_provider_skill_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
        target_skill_names=["xlsx"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "xlsx_create_workbook",
                "description": "Create XLSX files",
                "skill_name": "xlsx",
                "qualified_skill_name": "xlsx",
            },
        ],
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == [
        "cmp_get_request_detail",
        "xlsx_create_workbook",
    ]
    assert trace["reason"] == "projection_applied"


def test_project_minimal_toolset_keeps_explicit_tool_and_provider_skill_target() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
        target_tool_names=["xlsx_create_workbook"],
    )

    filtered, trace = project_minimal_toolset(
        allowed_tools=[
            *_allowed_tools(),
            {
                "name": "xlsx_create_workbook",
                "description": "Create XLSX files",
                "skill_name": "xlsx",
                "qualified_skill_name": "xlsx",
            },
        ],
        intent_plan=plan,
    )

    assert [tool["name"] for tool in filtered] == [
        "xlsx_create_workbook",
        "cmp_get_request_detail",
    ]
    assert trace["explicit_target_mode"] is True
    assert trace["reason"] == "projection_applied"


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
