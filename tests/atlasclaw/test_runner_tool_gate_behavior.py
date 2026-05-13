# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import RunnerExecutionPreparePhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    _infer_active_skill_from_transcript,
    build_transcript_skill_prompt_intent_plan,
    prune_auto_selected_provider_instance_tools,
    toolset_has_only_coordination_support_tools,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import build_llm_first_guidance_plan
from app.atlasclaw.agent.runner_tool.runner_tool_gate_routing import RunnerToolGateRoutingMixin
from app.atlasclaw.agent.runner_tool.runner_tool_projection import project_minimal_toolset
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)


class _GateRunner(RunnerToolGateModelMixin, RunnerToolGateRoutingMixin):
    TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE = 0.55
    TOOL_GATE_MUST_USE_MIN_CONFIDENCE = 0.85


class _PrepareRunner(RunnerExecutionPreparePhaseMixin):
    pass


class _ClassifierAgent:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def run(self, user_message, *, deps):
        self.messages.append(str(user_message))
        return SimpleNamespace(
            output=json.dumps(
                {
                    "needs_tool": False,
                    "needs_external_system": False,
                    "needs_grounded_verification": False,
                    "suggested_tool_classes": [],
                    "confidence": 0.9,
                    "reason": "Current request can be answered directly.",
                    "policy": "answer_direct",
                }
            )
        )


class _SelectorAgent:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages: list[str] = []

    async def run(self, user_message, *, deps):
        self.messages.append(str(user_message))
        return SimpleNamespace(output=json.dumps(self.payload))


def test_coordination_only_toolset_is_not_executable_runtime_capability() -> None:
    assert toolset_has_only_coordination_support_tools(
        [
            {
                "name": "atlasclaw_catalog_query",
                "capability_class": "atlasclaw_catalog",
                "coordination_only": True,
            }
        ]
    )
    assert not toolset_has_only_coordination_support_tools(
        [
            {
                "name": "skill_exec",
                "group": "skill_runtime",
                "capability_class": "skill_runtime:exec",
                "coordination_only": True,
            }
        ]
    )
    assert not toolset_has_only_coordination_support_tools(
        [
            {
                "name": "atlasclaw_catalog_query",
                "capability_class": "atlasclaw_catalog",
                "coordination_only": True,
            },
            {
                "name": "example_runtime_tool",
                "capability_class": "example",
            },
        ]
    )


def test_capability_selector_uses_authorized_xlsx_skill_without_pptx_substitution() -> None:
    runner = _GateRunner()
    selector = _SelectorAgent(
        {
            "action": "use_tools",
            "targets": ["skill:xlsx"],
            "reason": "User requested a spreadsheet artifact.",
        }
    )

    plan = asyncio.run(
        runner._select_capability_intent_plan_with_model(
            agent=selector,
            deps=SimpleNamespace(extra={}),
            user_message="生成 Excel",
            recent_history=[],
            capability_index=[
                {
                    "capability_id": "skill:xlsx",
                    "kind": "md_skill",
                    "name": "xlsx",
                    "description": "Create spreadsheet files.",
                    "declared_tool_names": [],
                },
                {
                    "capability_id": "tool:pptx_create_deck",
                    "kind": "tool",
                    "name": "pptx_create_deck",
                    "description": "Create presentation decks.",
                    "declared_tool_names": ["pptx_create_deck"],
                    "artifact_types": ["pptx"],
                },
            ],
        )
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.USE_TOOLS
    assert plan.target_skill_names == ["xlsx"]
    assert plan.target_tool_names == []
    assert "pptx_create_deck" not in plan.target_tool_names
    assert selector.messages


def test_capability_selector_prompt_uses_descriptions_only_for_capabilities() -> None:
    runner = _GateRunner()

    prompt = runner._build_capability_selector_prompt(
        capability_index=[
            {
                "capability_id": "skill:xlsx",
                "kind": "md_skill",
                "name": "xlsx",
                "description": "Create spreadsheet files.",
                "provider_type": "smartcmp",
                "artifact_types": ["xlsx"],
                "declared_tool_names": ["hidden_export_tool"],
            }
        ]
    )

    assert "skill:xlsx" in prompt
    assert "Create spreadsheet files." in prompt
    assert "provider=" not in prompt
    assert "artifact=" not in prompt
    assert "declared_tools=" not in prompt
    assert "smartcmp" not in prompt
    assert "hidden_export_tool" not in prompt


def test_capability_selector_prompt_keeps_md_routing_hints_inside_descriptions() -> None:
    runner = _GateRunner()

    prompt = runner._build_capability_selector_prompt(
        capability_index=[
            {
                "capability_id": "skill:acme:request-decomposition-agent",
                "kind": "md_skill",
                "name": "acme:request-decomposition-agent",
                "description": (
                    "Draft provider request plans. Routing hints: use when User asks for "
                    "multiple items with distinct item-level configuration; use when User "
                    "enumerates differences like item 1 / item 2 / item 3; avoid "
                    "when User has direct inputs ready for a single item."
                ),
                "declared_tool_names": ["acme_submit_request"],
                "provider_type": "acme",
            }
        ]
    )

    assert "multiple items with distinct item-level configuration" in prompt
    assert "item 1 / item 2 / item 3" in prompt
    assert "direct inputs ready for a single item" in prompt
    assert "hidden_export_tool" not in prompt
    assert "provider=acme" not in prompt


def test_capability_selector_can_select_provider_and_standard_skill_targets() -> None:
    runner = _GateRunner()
    selector = _SelectorAgent(
        {
            "action": "use_tools",
            "targets": ["provider:smartcmp", "skill:xlsx"],
            "reason": "Fetch provider data and export as spreadsheet.",
        }
    )

    plan = asyncio.run(
        runner._select_capability_intent_plan_with_model(
            agent=selector,
            deps=SimpleNamespace(extra={}),
            user_message="把待审批生成 excel",
            recent_history=[],
            capability_index=[
                {
                    "capability_id": "skill:xlsx",
                    "kind": "md_skill",
                    "name": "xlsx",
                    "description": "Create spreadsheet files.",
                    "declared_tool_names": [],
                },
                {
                    "capability_id": "provider:smartcmp",
                    "kind": "provider",
                    "name": "smartcmp",
                    "description": "Query approval data.",
                    "provider_type": "smartcmp",
                    "declared_tool_names": ["smartcmp_query_approvals"],
                },
                {
                    "capability_id": "tool:smartcmp_query_approvals",
                    "kind": "tool",
                    "name": "smartcmp_query_approvals",
                    "description": "Query approval data.",
                    "provider_type": "smartcmp",
                    "declared_tool_names": ["smartcmp_query_approvals"],
                },
            ],
        )
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.USE_TOOLS
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_skill_names == ["xlsx"]


def test_capability_selector_preserves_no_target_direct_answer() -> None:
    runner = _GateRunner()
    selector = _SelectorAgent(
        {
            "action": "direct_answer",
            "targets": [],
            "reason": "Selector chose not to target a capability.",
        }
    )

    plan = asyncio.run(
        runner._select_capability_intent_plan_with_model(
            agent=selector,
            deps=SimpleNamespace(extra={}),
            user_message="我要申请 Linux VM",
            recent_history=[],
            capability_index=[
                {
                    "capability_id": "skill:smartcmp:request",
                    "kind": "md_skill",
                    "name": "smartcmp:request",
                    "description": (
                        "Self-service request skill. Request cloud resources, "
                        "create VM, apply resources, 申请资源, 创建虚拟机."
                    ),
                    "declared_tool_names": ["smartcmp_list_services"],
                    "declares_executable_tools": True,
                },
                {
                    "capability_id": "skill:smartcmp:datasource",
                    "kind": "md_skill",
                    "name": "smartcmp:datasource",
                    "description": "Browse service catalogs and reference data.",
                    "declared_tool_names": ["smartcmp_list_components"],
                    "declares_executable_tools": True,
                },
            ],
        )
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_skill_names == []


def test_capability_selector_requires_explicit_provider_capability_id() -> None:
    runner = _GateRunner()
    selector = _SelectorAgent(
        {
            "action": "use_tools",
            "targets": ["provider:smartcmp"],
            "reason": "Provider target was not listed in the authorized capability index.",
        }
    )

    plan = asyncio.run(
        runner._select_capability_intent_plan_with_model(
            agent=selector,
            deps=SimpleNamespace(extra={}),
            user_message="查待审批",
            recent_history=[],
            capability_index=[
                {
                    "capability_id": "tool:smartcmp_query_approvals",
                    "kind": "tool",
                    "name": "smartcmp_query_approvals",
                    "description": "Query approval data.",
                    "provider_type": "smartcmp",
                    "declared_tool_names": ["smartcmp_query_approvals"],
                },
            ],
        )
    )

    assert plan is None


def test_capability_selector_drops_unauthorized_targets() -> None:
    runner = _GateRunner()
    selector = _SelectorAgent(
        {
            "action": "use_tools",
            "targets": ["skill:xlsx", "tool:pptx_create_deck"],
            "reason": "One requested target is not authorized.",
        }
    )

    plan = asyncio.run(
        runner._select_capability_intent_plan_with_model(
            agent=selector,
            deps=SimpleNamespace(extra={}),
            user_message="生成 Excel",
            recent_history=[],
            capability_index=[
                {
                    "capability_id": "skill:xlsx",
                    "kind": "md_skill",
                    "name": "xlsx",
                    "description": "Create spreadsheet files.",
                    "declared_tool_names": [],
                }
            ],
        )
    )

    assert plan is not None
    assert plan.target_skill_names == ["xlsx"]
    assert plan.target_tool_names == []


def test_tool_gate_classifier_resolves_async_agent_factory() -> None:
    runner = _GateRunner()
    classifier = _ClassifierAgent()

    async def resolver():
        return classifier

    decision = asyncio.run(
        runner._classify_tool_gate_with_model(
            agent=resolver,
            deps=SimpleNamespace(extra={}),
            user_message="hi",
            recent_history=[],
            available_tools=[],
        )
    )

    assert decision is not None
    assert decision.policy is ToolPolicyMode.ANSWER_DIRECT
    assert classifier.messages


def test_tool_gate_classifier_prefers_runtime_agent_over_factory() -> None:
    runner = _GateRunner()
    runtime_agent = _ClassifierAgent()
    runner.agent_factory = lambda *_args: pytest.fail("factory should not be used")
    runner.token_policy = SimpleNamespace(token_pool=SimpleNamespace(tokens={}))

    assert runner._select_tool_gate_classifier_agent(runtime_agent) is runtime_agent


def test_prune_auto_selected_provider_instance_tools_removes_provider_coordination_tools_by_metadata() -> None:
    filtered_tools, trace = prune_auto_selected_provider_instance_tools(
        available_tools=[
            {
                "name": "smartcmp_list_components",
                "description": "Get SmartCMP component metadata",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
            },
            {
                "name": "provider_instance_selector",
                "description": "Select provider instance",
                "capability_class": "provider:generic",
                "group_ids": ["group:providers"],
                "coordination_only": True,
            },
        ],
        deps=SimpleNamespace(
            extra={
                "provider_instances": {
                    "smartcmp": {
                        "default": {
                            "provider_type": "smartcmp",
                        }
                    }
                }
            }
        ),
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_tool_names=["smartcmp_list_components"],
            target_provider_types=["smartcmp"],
        ),
    )

    assert {tool["name"] for tool in filtered_tools} == {"smartcmp_list_components"}
    assert trace["enabled"] is True
    assert trace["removed_tools"] == ["provider_instance_selector"]
    assert trace["auto_selected_provider_types"] == ["smartcmp"]


def test_prune_selected_provider_instance_tools_removes_selector_with_multiple_instances() -> None:
    filtered_tools, trace = prune_auto_selected_provider_instance_tools(
        available_tools=[
            {
                "name": "smartcmp_submit_request",
                "description": "Submit SmartCMP request",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "capability_class": "provider:generic",
                "group_ids": ["group:providers"],
                "coordination_only": True,
            },
        ],
        deps=SimpleNamespace(
            extra={
                "provider_instances": {
                    "smartcmp": {
                        "default": {"provider_type": "smartcmp"},
                        "secondary": {"provider_type": "smartcmp"},
                    }
                },
                "_selected_capability": {
                    "kind": "provider_skill",
                    "provider_type": "smartcmp",
                    "instance_name": "default",
                    "qualified_skill_name": "smartcmp:request",
                },
            }
        ),
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_tool_names=["smartcmp_submit_request"],
            target_provider_types=["smartcmp"],
        ),
    )

    assert {tool["name"] for tool in filtered_tools} == {"smartcmp_submit_request"}
    assert trace["enabled"] is True
    assert trace["removed_tools"] == ["select_provider_instance"]
    assert trace["auto_selected_provider_types"] == []
    assert trace["explicit_selected_provider_types"] == ["smartcmp"]
    assert trace["explicit_selected_instances"] == ["default"]


def test_prune_auto_selected_provider_instance_tools_keeps_non_provider_coordination_tools() -> None:
    filtered_tools, trace = prune_auto_selected_provider_instance_tools(
        available_tools=[
            {
                "name": "smartcmp_submit_request",
                "description": "Submit SmartCMP request",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
            },
            {
                "name": "session_scope_selector",
                "description": "Pick session scope",
                "capability_class": "session",
                "coordination_only": True,
            },
        ],
        deps=SimpleNamespace(
            extra={
                "provider_instances": {
                    "smartcmp": {
                        "default": {
                            "provider_type": "smartcmp",
                        }
                    }
                }
            }
        ),
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.DIRECT_ANSWER,
            target_tool_names=["smartcmp_submit_request"],
            target_provider_types=["smartcmp"],
            target_skill_names=["smartcmp:request"],
        ),
    )

    assert {tool["name"] for tool in filtered_tools} == {
        "smartcmp_submit_request",
        "session_scope_selector",
    }
    assert trace["enabled"] is False
    assert trace["removed_tools"] == []
    assert trace["auto_selected_provider_types"] == ["smartcmp"]


def test_normalize_external_intent_does_not_force_must_use_tool() -> None:
    runner = _GateRunner()
    decision = ToolGateDecision(
        needs_tool=True,
        needs_external_system=True,
        suggested_tool_classes=["provider:smartcmp"],
        confidence=0.40,
        reason="external system request",
        policy=ToolPolicyMode.ANSWER_DIRECT,
    )

    normalized = runner._normalize_tool_gate_decision(decision)

    assert normalized.policy is ToolPolicyMode.PREFER_TOOL
    assert normalized.needs_external_system is True
    assert normalized.needs_tool is True


def test_align_external_system_intent_keeps_prefer_tool_policy() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "cmp_list_pending",
            "description": "List CMP pending requests",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
        }
    ]
    initial_decision = ToolGateDecision(
        needs_tool=True,
        needs_external_system=True,
        suggested_tool_classes=[],
        confidence=0.30,
        reason="external request",
        policy=ToolPolicyMode.ANSWER_DIRECT,
    )
    initial_match = CapabilityMatcher(available_tools=available_tools).match(["provider:smartcmp"])

    aligned_decision, _ = runner._align_external_system_intent(
        decision=initial_decision,
        match_result=initial_match,
        available_tools=available_tools,
        user_message="查下CMP待审批",
        recent_history=[],
        deps=None,
    )

    assert aligned_decision.policy is ToolPolicyMode.PREFER_TOOL
    assert aligned_decision.suggested_tool_classes == ["provider:smartcmp"]


def test_normalize_live_data_only_intent_keeps_answer_direct_without_tool_hints() -> None:
    runner = _GateRunner()
    decision = ToolGateDecision(
        needs_live_data=True,
        reason="public info request",
        policy=ToolPolicyMode.ANSWER_DIRECT,
    )

    normalized = runner._normalize_tool_gate_decision(decision)

    assert normalized.policy is ToolPolicyMode.ANSWER_DIRECT
    assert normalized.needs_external_system is False


def test_tool_gate_classifier_prompt_does_not_force_public_realtime_queries_into_tools() -> None:
    runner = _GateRunner()

    prompt = runner._build_tool_gate_classifier_prompt(
        [
            {
                "name": "web_search",
                "description": "Search the public web",
                "capability_class": "web_search",
            }
        ]
    )

    assert "Requests about current or near-future changing facts must prefer tool-backed verification" not in prompt
    assert "Use web_search/web_fetch for public web real-time verification" not in prompt
    assert "Use answer_direct when the request can be handled from model knowledge" in prompt


def test_projected_toolset_short_circuit_uses_single_tool_only_ok() -> None:
    runner = _GateRunner()

    plan = runner._build_projected_toolset_short_circuit_intent_plan(
        visible_tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "capability_class": "weather",
                "group_ids": ["group:web"],
                "result_mode": "tool_only_ok",
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "capability_class": "session",
                "group_ids": ["group:atlasclaw"],
                "coordination_only": True,
            },
        ]
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.USE_TOOLS
    assert plan.target_tool_names == ["openmeteo_weather"]
    assert plan.target_capability_classes == ["weather"]
    assert plan.target_group_ids == ["group:web"]


def test_projected_toolset_short_circuit_skips_non_tool_only_result_mode() -> None:
    runner = _GateRunner()

    plan = runner._build_projected_toolset_short_circuit_intent_plan(
        visible_tools=[
            {
                "name": "smartcmp_approve",
                "description": "Approve SmartCMP request",
                "capability_class": "provider:smartcmp",
                "provider_type": "smartcmp",
                "group_ids": ["group:cmp", "group:approval"],
                "result_mode": "llm",
            }
        ]
    )

    assert plan is None


def test_project_minimal_toolset_keeps_explicit_target_tool_even_with_provider_target() -> None:
    intent_plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
        target_tool_names=["atlasclaw_catalog_query"],
        target_capability_classes=["atlasclaw_catalog"],
        reason="platform catalog query scoped to SmartCMP",
    )

    projected, trace = project_minimal_toolset(
        allowed_tools=[
            {
                "name": "atlasclaw_catalog_query",
                "description": "Query AtlasClaw runtime catalog",
                "capability_class": "atlasclaw_catalog",
                "group_ids": ["group:catalog", "group:atlasclaw"],
                "result_mode": "tool_only_ok",
            },
            {
                "name": "smartcmp_list_pending",
                "description": "List SmartCMP pending approvals",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
                "group_ids": ["group:cmp", "group:smartcmp"],
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "capability_class": "provider:generic",
                "group_ids": ["group:providers", "group:atlasclaw"],
                "coordination_only": True,
            },
        ],
        intent_plan=intent_plan,
    )

    projected_names = {item["name"] for item in projected}
    assert "atlasclaw_catalog_query" in projected_names
    assert "smartcmp_list_pending" not in projected_names
    assert trace["reason"] == "projection_applied"


def test_direct_answer_gate_decision_keeps_hint_classes_without_requiring_tool_execution() -> None:
    runner = _GateRunner()
    decision = runner._build_tool_gate_decision_from_intent_plan(
        ToolIntentPlan(
            action=ToolIntentAction.DIRECT_ANSWER,
            target_provider_types=["smartcmp"],
            target_capability_classes=["provider:smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="hint-only smartcmp routing",
        )
    )

    assert decision.needs_tool is False
    assert decision.needs_external_system is True
    assert decision.suggested_tool_classes == ["provider:smartcmp"]


def test_classifier_history_ignores_recent_history_for_complete_new_request() -> None:
    runner = _GateRunner()

    history = runner._build_classifier_history(
        user_message="明天上海天气如何",
        recent_history=[
            {"role": "user", "content": "查下CMP 里目前所有待审批"},
            {"role": "assistant", "content": "我来帮你查。"},
        ],
        used_follow_up_context=False,
    )

    assert history == []


def test_resolve_contextual_tool_request_keeps_rich_identifier_query_self_contained() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="我要看下TIC20260316000001的详情",
        recent_history=[
            {"role": "user", "content": "查下CMP 里目前所有待审批"},
            {"role": "assistant", "content": "好的，我帮你列出来。"},
        ],
    )

    assert resolved == "我要看下TIC20260316000001的详情"
    assert used_follow_up_context is False


def test_resolve_contextual_tool_request_reuses_previous_user_message_for_low_information_follow_up() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="上海呢",
        recent_history=[
            {"role": "user", "content": "明天北京天气呢"},
            {"role": "assistant", "content": "Weather for 北京市, 北京, 中国\nDaily forecast:\n| 2026-04-15 | Slight rain showers |"},
        ],
    )

    assert resolved == "明天北京天气呢\n上海呢"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_reuses_previous_request_for_structured_follow_up_reply() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="linuxVM23, root, Passw0rd",
        recent_history=[
            {"role": "user", "content": "申请2c4g云资源"},
            {
                "role": "assistant",
                "content": (
                    "请提供以下信息：\n"
                    "1. 资源名称：\n"
                    "2. 用户名：\n"
                    "3. 密码："
                ),
            },
        ],
    )

    assert resolved == "申请2c4g云资源\nlinuxVM23, root, Passw0rd"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_reuses_previous_request_for_whitespace_separated_chinese_fields() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="用户名 root 密码 Passw0rd 名称 linux-test123",
        recent_history=[
            {"role": "user", "content": "我要申请一台 2C4G 的 Linux 虚拟机"},
            {
                "role": "assistant",
                "content": (
                    "请补充以下信息后我再提交申请：\n"
                    "1. 资源名称\n"
                    "2. 用户名\n"
                    "3. 密码"
                ),
            },
        ],
    )

    assert resolved == "我要申请一台 2C4G 的 Linux 虚拟机\n用户名 root 密码 Passw0rd 名称 linux-test123"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_reuses_previous_request_for_prompt_derived_field_labels() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="Project Code alpha-1 Owner alice Region cn-east-1",
        recent_history=[
            {"role": "user", "content": "Create an environment for analytics"},
            {
                "role": "assistant",
                "content": (
                    "Please provide the following details:\n"
                    "1. Project Code:\n"
                    "2. Owner:\n"
                    "3. Region:"
                ),
            },
        ],
    )

    assert resolved == "Create an environment for analytics\nProject Code alpha-1 Owner alice Region cn-east-1"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_does_not_merge_prompt_shaped_fields_without_follow_up_prompt() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="Project Code alpha-1 Owner alice Region cn-east-1",
        recent_history=[
            {"role": "user", "content": "Create an environment for analytics"},
            {
                "role": "assistant",
                "content": "I checked the catalog and can proceed once you tell me what you want next.",
            },
        ],
    )

    assert resolved == "Project Code alpha-1 Owner alice Region cn-east-1"
    assert used_follow_up_context is False


def test_resolve_contextual_tool_request_recognizes_enumerated_field_prompt_without_markers() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="linuxVM23, root, Passw0rd",
        recent_history=[
            {"role": "user", "content": "申请2c4g云资源"},
            {
                "role": "assistant",
                "content": (
                    "1. Resource Name:\n"
                    "2. Username:\n"
                    "3. Password:"
                ),
            },
        ],
    )

    assert resolved == "申请2c4g云资源\nlinuxVM23, root, Passw0rd"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_recognizes_bracketed_selection_prompt() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="2",
        recent_history=[
            {"role": "user", "content": "申请2c4g云资源"},
            {
                "role": "assistant",
                "content": (
                    "[1] team1\n"
                    "[2] 我的业务组\n"
                    "请选择业务组（输入编号）："
                ),
            },
        ],
    )

    assert "Original user request:\n申请2c4g云资源" in resolved
    assert "Latest assistant follow-up prompt:" in resolved
    assert "[2] 我的业务组" in resolved
    assert "User reply to that prompt:\n2" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_preserves_latest_prompt_for_repeated_numeric_choices() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="1",
        recent_history=[
            {"role": "user", "content": "我要申请 Linux VM"},
            {
                "role": "assistant",
                "content": (
                    "请选择您要申请的业务组：\n"
                    "开发部\n"
                    "测试部\n"
                    "请问您想申请哪个业务组的 Linux VM？"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "已选择开发部。\n\n"
                    "请选择您需要的规格配置：\n"
                    "Tiny — 1C1G\n"
                    "Small — 1C2G\n"
                    "Medium — 2C4G\n"
                    "Large — 4C8G\n"
                    "请问您需要哪种规格？"
                ),
            },
        ],
    )

    assert "Original user request:\n我要申请 Linux VM" in resolved
    assert "Recent follow-up context:" in resolved
    assert "User: 1" in resolved
    assert "Latest assistant follow-up prompt:" in resolved
    assert "Tiny — 1C1G" in resolved
    assert "User reply to that prompt:\n1" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert resolved != "我要申请 Linux VM 1"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_preserves_selection_chain_for_third_numeric_choice() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="1",
        recent_history=[
            {"role": "user", "content": "我要申请 Linux VM"},
            {
                "role": "assistant",
                "content": (
                    "请选择您要申请的业务组：\n"
                    "开发部\n"
                    "测试部\n"
                    "请问您想申请哪个业务组的 Linux VM？"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "已选择开发部。\n\n"
                    "请选择您需要的规格配置：\n"
                    "Tiny — 1C1G\n"
                    "Small — 1C2G\n"
                    "请问您需要哪种规格？"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "已选择 Tiny。\n\n"
                    "请选择资源环境：\n"
                    "开发\n"
                    "生产\n"
                    "请问您需要哪个资源环境？"
                ),
            },
        ],
    )

    assert "Original user request:\n我要申请 Linux VM" in resolved
    assert "Recent follow-up context:" in resolved
    assert "请选择您要申请的业务组" in resolved
    assert "请选择您需要的规格配置" in resolved
    assert resolved.count("User: 1") == 2
    assert "Latest assistant follow-up prompt:" in resolved
    assert "请选择资源环境" in resolved
    assert "开发" in resolved
    assert "User reply to that prompt:\n1" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert used_follow_up_context is True


def test_transcript_skill_prompt_plan_targets_only_skill_instructions() -> None:
    plan = build_transcript_skill_prompt_intent_plan(active_skill="smartcmp:request")

    assert plan is not None
    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_skill_names == ["smartcmp:request"]
    assert plan.target_provider_types == []
    assert plan.target_group_ids == []
    assert plan.target_tool_names == []
    assert plan.reason == "transcript_skill_continuation_prompt_only"


def test_transcript_skill_prompt_plan_ignores_missing_hint() -> None:
    assert build_transcript_skill_prompt_intent_plan(active_skill="") is None


def test_transcript_active_skill_infers_from_assistant_tool_calls() -> None:
    active_skill = _infer_active_skill_from_transcript(
        message_history=[
            {"role": "user", "content": "我要申请 Linux VM"},
            {
                "role": "assistant",
                "content": "我先查询可用业务组。",
                "tool_calls": [{"name": "smartcmp_list_business_groups"}],
            },
        ],
        md_skills_snapshot=[
            {
                "qualified_name": "smartcmp:request",
                "declared_tool_names": [
                    "smartcmp_list_business_groups",
                    "smartcmp_submit_request",
                ],
            }
        ],
    )

    assert active_skill == "smartcmp:request"


def test_transcript_active_skill_infers_from_embedded_tool_results() -> None:
    active_skill = _infer_active_skill_from_transcript(
        message_history=[
            {
                "role": "assistant",
                "content": "请选择业务组。",
                "tool_results": [
                    {
                        "tool_name": "smartcmp_list_business_groups",
                        "content": {"ok": True},
                    }
                ],
            },
        ],
        md_skills_snapshot=[
            {
                "qualified_name": "smartcmp:request",
                "declared_tool_names": [
                    "smartcmp_list_business_groups",
                    "smartcmp_submit_request",
                ],
            }
        ],
    )

    assert active_skill == "smartcmp:request"


def xtest_build_recent_follow_up_tool_intent_plan_reuses_single_recent_tool() -> None:
    plan = build_recent_follow_up_tool_intent_plan(
        recent_history=[
            {"role": "user", "content": "明天北京天气呢"},
            {"role": "assistant", "content": "我来查一下。", "tool_calls": [{"name": "openmeteo_weather"}]},
            {"role": "tool", "tool_name": "openmeteo_weather", "content": {"ok": True}},
            {"role": "assistant", "content": "Weather for 北京市, 北京, 中国"},
        ],
        available_tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "capability_class": "weather",
            }
        ],
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.USE_TOOLS
    assert plan.target_tool_names == ["openmeteo_weather"]
    assert plan.target_capability_classes == ["weather"]


def xtest_build_recent_follow_up_tool_intent_plan_recovers_recent_md_skill_scope() -> None:
    plan = build_recent_follow_up_tool_intent_plan(
        recent_history=[
            {
                "role": "assistant",
                "content": "我先列出服务目录。",
                "tool_calls": [{"name": "smartcmp_list_services"}],
            },
            {"role": "tool", "tool_name": "smartcmp_list_services", "content": {"ok": True}},
            {
                "role": "assistant",
                "content": "我再获取业务组。",
                "tool_calls": [{"name": "smartcmp_list_business_groups"}],
            },
            {"role": "tool", "tool_name": "smartcmp_list_business_groups", "content": {"ok": True}},
        ],
        available_tools=[
            {
                "name": "smartcmp_list_services",
                "description": "List SmartCMP service catalogs",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
                "group_ids": ["group:cmp", "group:request"],
                "qualified_skill_name": "smartcmp:request",
            },
            {
                "name": "smartcmp_list_business_groups",
                "description": "List SmartCMP business groups",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
                "group_ids": ["group:cmp", "group:request"],
                "qualified_skill_name": "smartcmp:request",
            },
            {
                "name": "smartcmp_submit_request",
                "description": "Submit SmartCMP request",
                "provider_type": "smartcmp",
                "capability_class": "provider:smartcmp",
                "group_ids": ["group:cmp", "group:request"],
                "qualified_skill_name": "smartcmp:request",
            },
        ],
    )

    assert plan is not None
    assert plan.action is ToolIntentAction.USE_TOOLS
    assert plan.target_skill_names == ["smartcmp:request"]
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_group_ids == ["group:cmp", "group:request"]
    assert plan.target_tool_names == [
        "smartcmp_list_business_groups",
        "smartcmp_list_services",
    ]


def test_runtime_history_for_tool_turns_keeps_recent_context_even_without_follow_up_flag() -> None:
    history = _PrepareRunner._build_runtime_message_history_for_turn(
        session_message_history=[
            {"role": "user", "content": "查一个 cmp 所有待审批的申请"},
            {"role": "assistant", "content": "我已经列出了 3 条待审批申请。"},
        ],
        used_follow_up_context=False,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            reason="legacy tool turn",
        ),
    )

    assert history == [
        {"role": "user", "content": "查一个 cmp 所有待审批的申请"},
        {"role": "assistant", "content": "我已经列出了 3 条待审批申请。"},
    ]


def test_llm_first_guidance_plan_keeps_metadata_as_hints_only() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="查一个 cmp 所有待审批的申请",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_tool_names == ["smartcmp_list_pending"]
    assert "does not decide the turn action" in plan.reason


def test_llm_first_guidance_plan_does_not_force_artifact_without_matching_capability() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="将这些申请写入一个新的PPT",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=False,
    )

    assert plan is None


def test_llm_first_guidance_plan_keeps_explicit_artifact_targets_from_metadata_plan() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="将这些申请写入一个新的PPT",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_skill_names=["pptx", "smartcmp:request"],
            target_capability_classes=["artifact:pptx", "provider:smartcmp"],
            target_tool_names=["pptx_create_deck", "smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_skill_names == ["pptx", "smartcmp:request"]
    assert plan.target_capability_classes == ["artifact:pptx", "provider:smartcmp"]
    assert plan.target_tool_names == ["pptx_create_deck", "smartcmp_list_pending"]


def test_llm_first_guidance_plan_supports_new_artifact_types_without_keyword_router() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="将这些申请整理成一个新的PDF文件",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_skill_names=["pdf", "smartcmp:request"],
            target_capability_classes=["artifact:pdf", "provider:smartcmp"],
            target_tool_names=["pdf_create_document", "smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_skill_names == ["pdf", "smartcmp:request"]
    assert plan.target_capability_classes == ["artifact:pdf", "provider:smartcmp"]
    assert plan.target_tool_names == ["pdf_create_document", "smartcmp_list_pending"]
