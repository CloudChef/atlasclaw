# -*- coding: utf-8 -*-
from __future__ import annotations

from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.agent.runner_tool.runner_tool_gate_routing import RunnerToolGateRoutingMixin
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
    TOOL_HINT_RANKER_MIN_METADATA_CONFIDENCE = 0.30


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


def test_metadata_fallback_accepts_single_provider_tool_consensus_below_threshold() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "smartcmp_list_services",
            "description": "List SmartCMP service catalogs",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
        },
        {
            "name": "smartcmp_get_request_detail",
            "description": "Get SmartCMP request detail",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
        },
    ]

    plan = runner._build_metadata_fallback_tool_intent_plan(
        metadata_candidates={
            "confidence": 0.08,
            "preferred_provider_types": ["smartcmp"],
            "preferred_capability_classes": ["provider:smartcmp"],
            "preferred_tool_names": [
                "smartcmp_list_services",
                "smartcmp_get_request_detail",
            ],
            "reason": "metadata_recall_matched",
        },
        available_tools=available_tools,
    )

    assert plan is not None
    assert plan.action.value == "use_tools"
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_tool_names == ["smartcmp_list_services", "smartcmp_get_request_detail"]


def test_projected_toolset_short_circuit_uses_single_tool_only_ok() -> None:
    runner = _GateRunner()

    plan = runner._build_projected_toolset_short_circuit_intent_plan(
        planner_available_tools=[
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
        planner_available_tools=[
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


def test_align_tool_intent_plan_with_metadata_does_not_merge_unrelated_provider_hints() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get weather forecast",
            "capability_class": "weather",
            "group_ids": ["group:web"],
        },
        {
            "name": "smartcmp_list_pending",
            "description": "List SmartCMP pending approvals",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
        },
    ]
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_capability_classes=["weather"],
        target_tool_names=["openmeteo_weather"],
        reason="weather request",
    )

    aligned = runner._align_tool_intent_plan_with_metadata(
        plan=plan,
        metadata_candidates={
            "confidence": 0.82,
            "preferred_provider_types": ["smartcmp"],
            "preferred_capability_classes": ["provider:smartcmp"],
            "preferred_tool_names": ["smartcmp_list_pending"],
            "reason": "metadata_recall_matched",
        },
        available_tools=available_tools,
    )

    assert aligned.target_provider_types == []
    assert aligned.target_capability_classes == ["weather"]
    assert aligned.target_tool_names == ["openmeteo_weather"]


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


def test_metadata_recall_ignores_history_when_not_follow_up() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get weather forecast",
            "capability_class": "weather",
            "group_ids": ["group:web"],
        },
        {
            "name": "smartcmp_list_pending",
            "description": "List SmartCMP pending approvals",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp"],
        },
    ]
    provider_hint_docs = [
        {
            "hint_id": "provider:smartcmp",
            "provider_type": "smartcmp",
            "display_name": "SmartCMP",
            "description": "List SmartCMP approvals",
            "aliases": ["cmp"],
            "keywords": ["cmp", "approval", "pending"],
            "capabilities": [],
            "use_when": [],
            "avoid_when": [],
            "tool_names": ["smartcmp_list_pending"],
            "group_ids": ["group:cmp"],
            "capability_classes": ["provider:smartcmp"],
            "hint_text": "keywords: cmp approval pending workflow request",
            "priority": 50,
        }
    ]
    skill_hint_docs = [
        {
            "hint_id": "skill:weather",
            "skill_name": "weather",
            "qualified_skill_name": "builtin:weather",
            "provider_type": "",
            "display_name": "weather",
            "description": "Get weather forecast",
            "aliases": ["builtin:weather"],
            "keywords": ["天气", "预报", "明天", "上海", "温度", "下雨"],
            "capabilities": [],
            "use_when": [],
            "avoid_when": [],
            "tool_names": ["openmeteo_weather"],
            "group_ids": ["group:web"],
            "capability_classes": ["weather"],
            "hint_text": "keywords: 天气 预报 明天 上海 温度 下雨",
            "priority": 20,
        }
    ]

    recalled = runner._recall_provider_skill_candidates_from_metadata(
        user_message="明天上海天气如何",
        recent_history=[
            {"role": "user", "content": "查下CMP 里目前所有待审批"},
            {"role": "assistant", "content": "好的，我帮你列出来。"},
        ],
        used_follow_up_context=False,
        available_tools=available_tools,
        provider_hint_docs=provider_hint_docs,
        skill_hint_docs=skill_hint_docs,
        tool_hint_docs=[],
        top_k_provider=2,
        top_k_skill=2,
    )

    assert recalled["preferred_provider_types"] == []
    assert "weather" in recalled["preferred_capability_classes"]
    assert recalled["preferred_tool_names"] == ["openmeteo_weather"]


def test_metadata_recall_requires_strong_provider_anchor_for_public_query() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "web_search",
            "description": "Search the public web",
            "capability_class": "web_search",
            "group_ids": ["group:web"],
        },
        {
            "name": "smartcmp_list_services",
            "description": "List SmartCMP services",
            "capability_class": "provider:smartcmp",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp"],
        },
    ]
    provider_hint_docs = [
        {
            "hint_id": "provider:smartcmp",
            "provider_type": "smartcmp",
            "display_name": "SmartCMP",
            "description": "List SmartCMP services",
            "aliases": ["cmp"],
            "keywords": ["cmp", "approval", "request"],
            "capabilities": [],
            "use_when": ["查看待审批和服务目录"],
            "avoid_when": [],
            "tool_names": ["smartcmp_list_services"],
            "group_ids": ["group:cmp"],
            "capability_classes": ["provider:smartcmp"],
            "hint_text": "description: 查看服务目录和请求详情 | use_when: 查看待审批和服务目录",
            "priority": 50,
        }
    ]

    recalled = runner._recall_provider_skill_candidates_from_metadata(
        user_message="查看上海周边有哪些自行车骑行公园",
        recent_history=[],
        used_follow_up_context=False,
        available_tools=available_tools,
        provider_hint_docs=provider_hint_docs,
        skill_hint_docs=[],
        tool_hint_docs=[],
        top_k_provider=2,
        top_k_skill=2,
    )

    assert recalled["preferred_provider_types"] == []
    assert recalled["preferred_tool_names"] == []


def test_tool_hint_docs_support_weather_metadata_recall() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get current and forecast weather via Open-Meteo APIs",
            "source": "builtin",
            "capability_class": "weather",
            "group_ids": ["group:web"],
            "aliases": ["weather", "forecast", "openmeteo"],
            "keywords": ["weather", "forecast", "temperature", "rain", "wind", "天气", "预报", "气温", "降雨"],
            "use_when": ["User asks for current or forecast weather conditions for a place and date"],
            "avoid_when": [],
            "priority": 100,
        }
    ]

    builtin_docs = runner._build_tool_hint_docs(available_tools=available_tools)
    recalled = runner._recall_provider_skill_candidates_from_metadata(
        user_message="明天上海天气如何",
        recent_history=[],
        used_follow_up_context=False,
        available_tools=available_tools,
        provider_hint_docs=[],
        skill_hint_docs=[],
        tool_hint_docs=builtin_docs,
        top_k_provider=2,
        top_k_skill=2,
    )

    assert "weather" in recalled["preferred_capability_classes"]
    assert recalled["preferred_tool_names"] == ["openmeteo_weather"]
    assert recalled["confidence"] > 0.0


def test_tool_hint_docs_include_provider_tools() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "smartcmp_get_request_detail",
            "description": "Get CMP request detail by identifier",
            "source": "provider",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp"],
            "capability_class": "provider:smartcmp",
            "keywords": ["detail", "request detail", "workflow"],
            "use_when": ["User asks for CMP request detail"],
            "avoid_when": [],
        }
    ]

    docs = runner._build_tool_hint_docs(available_tools=available_tools)

    assert docs == [
        {
            "hint_id": "tool:smartcmp_get_request_detail",
            "hint_type": "tool",
            "tool_name": "smartcmp_get_request_detail",
            "provider_type": "smartcmp",
            "display_name": "smartcmp_get_request_detail",
            "description": "Get CMP request detail by identifier",
            "aliases": [],
            "keywords": ["detail", "request detail", "workflow"],
            "capabilities": ["provider:smartcmp"],
            "use_when": ["User asks for CMP request detail"],
            "avoid_when": [],
            "tool_names": ["smartcmp_get_request_detail"],
            "group_ids": ["group:cmp"],
            "capability_classes": ["provider:smartcmp"],
            "hint_text": (
                "name: smartcmp_get_request_detail | description: Get CMP request detail by "
                "identifier | keywords: detail; request detail; workflow | capabilities: "
                "provider:smartcmp | use_when: User asks for CMP request detail"
            ),
            "priority": 100,
        }
    ]


def test_tool_hint_docs_support_provider_metadata_recall() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "smartcmp_get_request_detail",
            "description": "Get CMP request detail by identifier",
            "source": "provider",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp", "group:request"],
            "capability_class": "provider:smartcmp",
            "aliases": ["cmp request detail", "工单详情"],
            "keywords": ["cmp", "request detail", "workflow", "详情", "工单"],
            "use_when": ["User asks for a CMP request detail page", "User asks for 工单详情"],
            "avoid_when": [],
        }
    ]

    tool_docs = runner._build_tool_hint_docs(available_tools=available_tools)
    recalled = runner._recall_provider_skill_candidates_from_metadata(
        user_message="我要看下TIC20260316000001的详情",
        recent_history=[],
        used_follow_up_context=False,
        available_tools=available_tools,
        provider_hint_docs=[],
        skill_hint_docs=[],
        tool_hint_docs=tool_docs,
        top_k_provider=2,
        top_k_skill=2,
    )

    assert recalled["preferred_provider_types"] == ["smartcmp"]
    assert recalled["preferred_capability_classes"] == ["provider:smartcmp"]
    assert recalled["preferred_tool_names"] == ["smartcmp_get_request_detail"]


def test_metadata_fallback_builds_weather_plan_from_builtin_tool_candidates() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get current and forecast weather via Open-Meteo APIs",
            "source": "builtin",
            "capability_class": "weather",
            "group_ids": ["group:web"],
        }
    ]

    plan = runner._build_metadata_fallback_tool_intent_plan(
        metadata_candidates={
            "confidence": 0.42,
            "preferred_provider_types": [],
            "preferred_capability_classes": ["weather"],
            "preferred_tool_names": ["openmeteo_weather"],
            "reason": "metadata_recall_matched_builtin_weather",
        },
        available_tools=available_tools,
    )

    assert plan is not None
    assert plan.action.value == "use_tools"
    assert plan.target_capability_classes == ["weather"]
    assert plan.target_tool_names == ["openmeteo_weather"]


def test_metadata_fallback_accepts_single_builtin_tool_consensus_below_threshold() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get current and forecast weather via Open-Meteo APIs",
            "source": "builtin",
            "capability_class": "weather",
            "group_ids": ["group:web"],
        },
        {
            "name": "web_search",
            "description": "Search the public web",
            "source": "builtin",
            "capability_class": "web_search",
            "group_ids": ["group:web"],
        },
    ]

    plan = runner._build_metadata_fallback_tool_intent_plan(
        metadata_candidates={
            "confidence": 0.04,
            "preferred_provider_types": [],
            "preferred_capability_classes": ["weather"],
            "preferred_tool_names": ["openmeteo_weather"],
            "builtin_tool_candidates": [
                {
                    "hint_id": "builtin:openmeteo_weather",
                    "tool_name": "openmeteo_weather",
                    "score": 7,
                    "has_strong_anchor": True,
                    "tool_names": ["openmeteo_weather"],
                    "group_ids": ["group:web"],
                    "capability_classes": ["weather"],
                }
            ],
            "reason": "metadata_recall_single_builtin_tool",
        },
        available_tools=available_tools,
    )

    assert plan is not None
    assert plan.action.value == "use_tools"
    assert plan.target_capability_classes == ["weather"]
    assert plan.target_tool_names == ["openmeteo_weather"]


def test_metadata_fallback_accepts_single_tool_consensus_from_tool_candidates() -> None:
    runner = _GateRunner()
    available_tools = [
        {
            "name": "openmeteo_weather",
            "description": "Get current and forecast weather via Open-Meteo APIs",
            "source": "builtin",
            "capability_class": "weather",
            "group_ids": ["group:web"],
        },
        {
            "name": "web_search",
            "description": "Search the public web",
            "source": "builtin",
            "capability_class": "web_search",
            "group_ids": ["group:web"],
        },
    ]

    plan = runner._build_metadata_fallback_tool_intent_plan(
        metadata_candidates={
            "confidence": 0.04,
            "preferred_provider_types": [],
            "preferred_capability_classes": ["weather"],
            "preferred_tool_names": ["openmeteo_weather"],
            "tool_candidates": [
                {
                    "hint_id": "tool:openmeteo_weather",
                    "tool_name": "openmeteo_weather",
                    "score": 7,
                    "has_strong_anchor": True,
                    "tool_names": ["openmeteo_weather"],
                    "group_ids": ["group:web"],
                    "capability_classes": ["weather"],
                }
            ],
            "reason": "metadata_recall_single_tool",
        },
        available_tools=available_tools,
    )

    assert plan is not None
    assert plan.action.value == "use_tools"
    assert plan.target_capability_classes == ["weather"]
    assert plan.target_tool_names == ["openmeteo_weather"]
