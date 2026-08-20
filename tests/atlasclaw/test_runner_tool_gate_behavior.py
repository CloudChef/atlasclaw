# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.runner_tool import runner_execution_prepare as prepare_module
from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.agent.runner_tool.runner_tool_gate_policy import (
    RunnerToolGatePolicyMixin,
)
from app.atlasclaw.agent.runner_tool.runner_execution_loop import (
    hydrate_session_provider_instance_selections,
)
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import RunnerExecutionPreparePhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    _infer_active_provider_skill_from_transcript,
    _infer_active_skill_from_transcript,
    _selected_plan_matches_active_capability,
    apply_provider_instance_selection_policy,
    filter_implicit_only_tools,
    persist_provider_instance_targets_from_intent_plan,
    prune_auto_selected_provider_instance_tools,
    toolset_has_only_coordination_support_tools,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import (
    build_llm_first_guidance_plan,
    selected_capability_ids_from_intent_plan,
)
from app.atlasclaw.agent.runner_tool.runner_tool_gate_routing import RunnerToolGateRoutingMixin
from app.atlasclaw.agent.runner_tool.runner_tool_projection import project_minimal_toolset
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
    CapabilitySelectorOutcome,
    ConversationTurnAction,
    ConversationTurnPlan,
    ConversationTurnRoute,
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)
from app.atlasclaw.core.deps import SkillDeps


class _GateRunner(RunnerToolGateModelMixin, RunnerToolGateRoutingMixin):
    TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE = 0.55
    TOOL_GATE_MUST_USE_MIN_CONFIDENCE = 0.85


class _ProviderSelectionSessionManager:
    def __init__(self, selections):
        self._session = SimpleNamespace(
            extra={"provider_instance_selections": selections}
        )
        self.updates: list[tuple[str, dict]] = []

    async def get_session(self, session_key):
        return self._session

    async def update_extra(self, session_key, updates):
        self.updates.append((session_key, dict(updates)))
        self._session.extra.update(dict(updates))


class _PrepareRunner(RunnerExecutionPreparePhaseMixin):
    pass


class _PrepareSessionManager:
    def __init__(self, transcript: list[dict] | None = None) -> None:
        self.transcript = list(transcript or [])
        self.session = SimpleNamespace(title="Existing chat", title_status="ready", extra={})

    async def get_or_create(self, session_key: str):
        return self.session

    async def load_transcript(self, session_key: str) -> list[dict]:
        return list(self.transcript)


class _PrepareHistory:
    @staticmethod
    def build_message_history(transcript: list[dict]) -> list[dict]:
        return list(transcript)

    @staticmethod
    def prune_summary_messages(messages: list[dict]) -> list[dict]:
        return list(messages)


class _PrepareRuntimeEvents:
    async def trigger_message_received(self, **kwargs) -> None:
        return None

    async def trigger_run_started(self, **kwargs) -> None:
        return None


class _PrepareActiveMemory:
    async def recall_usage_profile_for_routing(self, **kwargs):
        return SimpleNamespace(status="disabled", elapsed_ms=0, result_count=0, context="")


class _StopPrepare(Exception):
    pass


def _prepare_phase_state(*, deps: SkillDeps) -> dict:
    return {
        "session_key": deps.session_key,
        "user_message": "restart this vm",
        "deps": deps,
        "_emit_lifecycle_bounds": False,
        "start_time": time.monotonic(),
        "run_id": "run-prepare-test",
        "message_history": [],
        "context_history_for_hooks": [],
        "tool_call_summaries": [],
        "buffered_assistant_events": [],
        "tool_request_message": "restart this vm",
        "tool_gate_decision": ToolGateDecision(reason="not evaluated"),
        "all_available_tools": [],
        "tool_groups_snapshot": {},
        "available_tools": [],
        "toolset_filter_trace": [],
        "tool_projection_trace": {},
        "metadata_candidates": {},
        "ranking_trace": {},
    }


async def _run_prepare_until_tool_policy(
    runner: AgentRunner,
    *,
    state: dict,
) -> list[tuple[str, dict]]:
    logs: list[tuple[str, dict]] = []

    def _log_step(step: str, **data) -> None:
        logs.append((step, dict(data)))
        if step == "tool_policy_injected":
            raise _StopPrepare

    try:
        async for _event in runner._run_prepare_phase(state=state, _log_step=_log_step):
            pass
    except _StopPrepare:
        pass
    return logs


def _build_prepare_runner(session_manager: _PrepareSessionManager) -> AgentRunner:
    runner = AgentRunner(agent=SimpleNamespace(), session_manager=session_manager)
    runner.history = _PrepareHistory()
    runner.runtime_events = _PrepareRuntimeEvents()
    runner.active_memory = _PrepareActiveMemory()
    runner.context_pruning_settings = SimpleNamespace(mode="off")
    runner._build_turn_toolset = lambda **kwargs: (list(kwargs["all_tools"]), [], False)
    runner._build_filtered_group_map = lambda _groups, _tools: {}
    return runner


def test_prepare_preserves_original_error_before_model_message_resolution() -> None:
    """Do not mask an early runtime-token failure with an unbound local error."""

    deps = SkillDeps(session_key="session-1", channel="api", extra={})
    runner = _build_prepare_runner(_PrepareSessionManager())

    async def fail_runtime_agent(*_args, **_kwargs):
        raise RuntimeError("runtime token unavailable")

    runner._resolve_runtime_agent = fail_runtime_agent
    state = _prepare_phase_state(deps=deps)

    with pytest.raises(RuntimeError, match="runtime token unavailable"):
        asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert state["model_user_message"] == state["user_message"]


class _SelectorAgent:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages: list[str] = []

    async def run(self, user_message, *, deps):
        self.messages.append(str(user_message))
        return SimpleNamespace(output=json.dumps(self.payload))


def test_authenticated_webhook_authorizes_only_preselected_skill_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "# Automation\n\nHandle the authenticated event with the declared tools.",
        encoding="utf-8",
    )
    tools = [
        {
            "name": "example_read_event",
            "provider_type": "example",
            "provider_skill_name": "primary.automation",
            "qualified_skill_name": "example:automation",
            "skill_name": "automation",
        },
        {
            "name": "example_lookup_reference",
            "provider_type": "example",
            "provider_skill_name": "primary.automation",
            "qualified_skill_name": "example:automation",
            "skill_name": "automation",
            "routing_visibility": "internal",
        },
        {
            "name": "example_update_event",
            "provider_type": "example",
            "provider_skill_name": "primary.automation",
            "qualified_skill_name": "example:automation",
            "skill_name": "automation",
            "group_ids": ["group:mutation"],
        },
        {
            "name": "example_update_other",
            "provider_type": "example",
            "provider_skill_name": "primary.other",
            "qualified_skill_name": "example:other",
            "skill_name": "other",
            "group_ids": ["group:mutation"],
        },
    ]
    provider_skill_entry = {
        "capability_id": "provider_skill:primary.automation",
        "kind": "provider_skill",
        "name": "primary.automation",
        "provider_type": "example",
        "provider_name": "primary",
        "instance_name": "primary",
        "qualified_skill_name": "example:automation",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["primary.automation"],
        "declared_tool_names": [
            "example_read_event",
            "example_lookup_reference",
            "example_update_event",
        ],
        "locator": str(skill_path),
    }
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(provider_skill_entry)],
    )
    runner = _build_prepare_runner(_PrepareSessionManager())
    deps = SkillDeps(
        session_key="authenticated-webhook-session",
        channel="webhook",
        extra={
            "authenticated_webhook_authority": True,
            "webhook_skill": "primary.automation",
            "webhook_qualified_skill": "example:automation",
            "target_md_skill": {
                "name": "automation",
                "provider": "example",
                "qualified_name": "example:automation",
                "file_path": str(skill_path),
                "target_provider_instances": ["example.primary"],
                "target_provider_types": ["example"],
                "target_provider_skill_names": ["primary.automation"],
            },
            "provider_type": "example",
            "provider_instance_name": "primary",
            "provider_instance": {"base_url": "https://example.invalid"},
        },
    )
    state = _prepare_phase_state(deps=deps)
    state["user_message"] = "Handle authenticated event"

    logs = asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert state["selector_attempted"] is False
    assert state["tool_execution_required"] is True
    assert deps.extra["runtime_allowed_tool_names"] == [
        "example_read_event",
        "example_lookup_reference",
        "example_update_event",
    ]
    assert deps.extra["tool_policy"]["mode"] == ToolIntentAction.USE_TOOLS.value
    assert any(
        step == "capability_selector_skipped"
        and data["reason"] == "authenticated_webhook_skill"
        for step, data in logs
    )


def test_ordinary_menu_prepare_runs_main_turn_planner_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [{"name": "example_update_item", "provider_type": "example"}]
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(prepare_module, "collect_capability_index_snapshot", lambda **kwargs: [])
    manager = _PrepareSessionManager()
    runner = _build_prepare_runner(manager)
    selector_calls = 0

    async def _plan_turn(**kwargs):
        nonlocal selector_calls
        selector_calls += 1
        return ConversationTurnPlan(
            route=ConversationTurnRoute.ORDINARY,
            action=ConversationTurnAction.RESPOND,
            reason="ordinary_menu_selector",
        )

    runner._plan_conversation_turn_with_model = _plan_turn
    deps = SkillDeps(session_key="menu-session", channel="api", extra={})
    state = _prepare_phase_state(deps=deps)

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert selector_calls == 1
    assert deps.extra["runtime_allowed_tool_names"] == []
    assert state["tool_intent_plan"].reason == "ordinary_menu_selector"


def test_invalid_turn_plan_does_not_expose_runtime_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [{"name": "example_update_item", "provider_type": "example"}]
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [
            {
                "capability_id": "provider_skill:primary.item",
                "kind": "provider_skill",
                "name": "primary.item",
                "target_provider_instances": ["example.primary"],
                "target_provider_types": ["example"],
                "target_provider_skill_names": ["primary.item"],
            }
        ],
    )
    runner = _build_prepare_runner(_PrepareSessionManager())

    async def _invalid_plan(**kwargs):
        return None

    runner._plan_conversation_turn_with_model = _invalid_plan
    deps = SkillDeps(session_key="selector-with-tools-session", channel="api", extra={})
    state = _prepare_phase_state(deps=deps)

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert state["tool_intent_plan"].unavailable_runtime_capability is False
    assert (
        state["tool_intent_plan"].selector_outcome
        is CapabilitySelectorOutcome.ORDINARY_CONVERSATION
    )
    assert deps.extra["runtime_allowed_tool_names"] == []
    assert state["selector_failed"] is True


def test_continue_active_targets_continue_or_switch_within_authorized_scope() -> None:
    capability_index = [
        {
            "capability_id": "provider_skill:primary.item",
            "declared_tool_names": ["example_list_items"],
        },
        {
            "capability_id": "provider_skill:primary.report",
            "declared_tool_names": ["example_create_report"],
        },
    ]

    continued = _GateRunner._normalize_conversation_turn_plan_scope(
        plan=ConversationTurnPlan(
            route=ConversationTurnRoute.CONTINUE_ACTIVE,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["tool:example_list_items"],
        ),
        capability_index=capability_index,
        active_capability_context="provider_skill:primary.item",
    )
    switched = _GateRunner._normalize_conversation_turn_plan_scope(
        plan=ConversationTurnPlan(
            route=ConversationTurnRoute.CONTINUE_ACTIVE,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["PROVIDER_SKILL:PRIMARY.REPORT"],
        ),
        capability_index=capability_index,
        active_capability_context="provider_skill:primary.item",
    )
    rejected = _GateRunner._normalize_conversation_turn_plan_scope(
        plan=ConversationTurnPlan(
            route=ConversationTurnRoute.CONTINUE_ACTIVE,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["tool:unknown_operation"],
        ),
        capability_index=capability_index,
        active_capability_context="provider_skill:primary.item",
    )
    ambiguous = _GateRunner._normalize_conversation_turn_plan_scope(
        plan=ConversationTurnPlan(
            route=ConversationTurnRoute.CONTINUE_ACTIVE,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["tool:example_other_item"],
        ),
        capability_index=[
            {
                "capability_id": "provider_skill:Primary.Item",
                "declared_tool_names": ["example_list_items"],
            },
            {
                "capability_id": "provider_skill:primary.item",
                "declared_tool_names": ["example_other_item"],
            },
        ],
        active_capability_context="provider_skill:Primary.Item",
    )

    assert continued is not None
    assert continued.route is ConversationTurnRoute.CONTINUE_ACTIVE
    assert continued.target_capability_ids == []
    assert switched is not None
    assert switched.route is ConversationTurnRoute.START_NEW
    assert switched.target_capability_ids == ["provider_skill:primary.report"]
    assert rejected is None
    assert ambiguous is None


def test_active_preview_confirmation_projects_selected_workflow_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {
            "name": "example_list_items",
            "provider_type": "example",
            "provider_skill_name": "primary.request",
            "qualified_skill_name": "example:request",
            "skill_name": "request",
        },
        {
            "name": "example_submit_request",
            "provider_type": "example",
            "provider_skill_name": "primary.request",
            "qualified_skill_name": "example:request",
            "skill_name": "request",
            "group_ids": ["group:mutation"],
        },
    ]
    capability_entry = {
        "capability_id": "provider_skill:primary.request",
        "kind": "provider_skill",
        "name": "primary.request",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["primary.request"],
        "declared_tool_names": ["example_list_items", "example_submit_request"],
    }
    monkeypatch.setattr(
        prepare_module,
        "collect_tools_snapshot",
        lambda **kwargs: list(tools),
    )
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(capability_entry)],
    )
    monkeypatch.setattr(
        prepare_module,
        "_infer_active_provider_skill_from_transcript",
        lambda **kwargs: "primary.request",
    )
    transcript = [
        {
            "role": "tool",
            "tool_name": "example_list_items",
            "content": {
                "success": True,
                "_internal": {
                    "internal_request_trace_id": "trace-preview",
                    "provider_instance_ref": "example.primary",
                },
            },
        },
        {
            "role": "assistant",
            "content": "Request preview: example item. Confirm submission?",
        }
    ]
    runner = _build_prepare_runner(_PrepareSessionManager(transcript=transcript))
    selector_calls = 0

    async def _plan_execution(**kwargs):
        nonlocal selector_calls
        selector_calls += 1
        assert kwargs["capability_index"] == [capability_entry]
        assert kwargs["active_workflow_context"]["internal_request_trace_id"] == "trace-preview"
        return ConversationTurnPlan(
            route=ConversationTurnRoute.CONTINUE_ACTIVE,
            action=ConversationTurnAction.USE_TOOLS,
            reason="The next workflow step requires request submission.",
        )

    runner._plan_conversation_turn_with_model = _plan_execution
    deps = SkillDeps(
        session_key="preview-confirmation-session",
        channel="api",
        extra={
            "context": {
                "turn_context": {
                    "default_skill": {
                        "ref": "example:item",
                        "name": "primary.item",
                        "provider_type": "example",
                        "provider_instance": "primary",
                    },
                    "object": {"type": "item", "id": "item-1"},
                }
            },
        },
    )
    state = _prepare_phase_state(deps=deps)
    state["user_message"] = "Yes"

    logs = asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert selector_calls == 1
    assert deps.extra["runtime_allowed_tool_names"] == [
        "example_list_items",
        "example_submit_request",
    ]
    assert state["tool_execution_required"] is True
    assert next(
        data["policy"] for step, data in logs if step == "tool_gate_decided"
    ) == ToolPolicyMode.MUST_USE_TOOL.value


def test_authorized_context_prepare_loads_skill_without_exposing_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "# Item workflow\n\nCollect the requested fields and present a draft before any update.",
        encoding="utf-8",
    )
    tools = [
        {
            "name": "example_update_item",
            "provider_type": "example",
            "qualified_skill_name": "example:item",
            "skill_name": "item",
        }
    ]
    provider_skill_entry = {
        "capability_id": "provider_skill:primary.item",
        "kind": "provider_skill",
        "name": "primary.item",
        "provider_type": "example",
        "provider_name": "primary",
        "instance_name": "primary",
        "qualified_skill_name": "example:item",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["primary.item"],
        "declared_tool_names": ["example_update_item"],
        "locator": str(skill_path),
    }
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(provider_skill_entry)],
    )
    runner = _build_prepare_runner(_PrepareSessionManager())

    async def _plan_context_only(**kwargs):
        return ConversationTurnPlan(
            route=ConversationTurnRoute.START_NEW,
            action=ConversationTurnAction.RESPOND,
            target_capability_ids=["provider_skill:primary.item"],
            reason="The current turn supplies requested workflow input.",
        )

    runner._plan_conversation_turn_with_model = _plan_context_only
    deps = SkillDeps(session_key="context-only-session", channel="api", extra={})
    state = _prepare_phase_state(deps=deps)

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert deps.extra["runtime_allowed_tool_names"] == []
    assert deps.extra["tool_policy"]["mode"] == "context_only"
    assert deps.extra["target_md_skill"]["qualified_name"] == "example:item"
    assert "present a draft before any update" in deps.extra["target_md_skill"]["instructions"]
    assert state["tool_execution_required"] is False
    assert state["tool_projection_trace"]["reason"] == "projection_context_only"


def test_active_numeric_selection_skips_planner_and_projects_only_read_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "# Item workflow\n\nUse another lookup only when the next field requires it.",
        encoding="utf-8",
    )
    tools = [
        {
            "name": "example_list_items",
            "provider_type": "example",
            "provider_skill_name": "primary.item",
            "qualified_skill_name": "example:item",
            "skill_name": "item",
            "read_only": True,
        },
        {
            "name": "example_update_item",
            "provider_type": "example",
            "provider_skill_name": "primary.item",
            "qualified_skill_name": "example:item",
            "skill_name": "item",
            "group_ids": ["group:mutation"],
        },
    ]
    provider_skill_entry = {
        "capability_id": "provider_skill:primary.item",
        "kind": "provider_skill",
        "name": "primary.item",
        "provider_type": "example",
        "provider_name": "primary",
        "instance_name": "primary",
        "qualified_skill_name": "example:item",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["primary.item"],
        "declared_tool_names": ["example_list_items", "example_update_item"],
        "locator": str(skill_path),
    }
    transcript = [
        {
            "role": "tool",
            "tool_name": "example_list_items",
            "content": {
                "success": True,
                "_internal": {
                    "internal_request_trace_id": "trace-item",
                    "provider_instance_ref": "example.primary",
                    "items": [
                        {"id": "item-1", "name": "First"},
                        {"id": "item-2", "name": "Second"},
                    ],
                },
            },
        },
        {
            "role": "assistant",
            "content": "Choose an item by number:\n1. First\n2. Second",
        },
    ]
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(provider_skill_entry)],
    )
    monkeypatch.setattr(
        prepare_module,
        "_infer_active_provider_skill_from_transcript",
        lambda **kwargs: "primary.item",
    )
    runner = _build_prepare_runner(_PrepareSessionManager(transcript=transcript))

    async def _plan_active_context(**kwargs):
        raise AssertionError("exact choices must bypass conversation planning")

    runner._plan_conversation_turn_with_model = _plan_active_context
    deps = SkillDeps(session_key="active-selection-session", channel="api", extra={})
    state = _prepare_phase_state(deps=deps)
    state["user_message"] = "1"

    logs = asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert deps.extra["runtime_allowed_tool_names"] == ["example_list_items"]
    assert deps.extra["tool_policy"]["mode"] == "llm_first"
    assert state["tool_execution_required"] is False
    assert next(
        data["policy"] for step, data in logs if step == "tool_gate_decided"
    ) == ToolPolicyMode.PREFER_TOOL.value
    assert state["tool_projection_trace"]["reason"] == "projection_applied"
    assert any(
        step == "exact_choice_read_only_projection_applied"
        and data["visible_tools"] == ["example_list_items"]
        for step, data in logs
    )


def test_start_new_plan_clears_prior_workflow_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        {
            "capability_id": "provider_skill:primary.item",
            "kind": "provider_skill",
            "name": "primary.item",
            "provider_type": "example",
            "provider_name": "primary",
            "instance_name": "primary",
            "target_provider_instances": ["example.primary"],
            "target_provider_types": ["example"],
            "target_provider_skill_names": ["primary.item"],
            "declared_tool_names": ["example_lookup"],
        },
        {
            "capability_id": "provider_skill:primary.report",
            "kind": "provider_skill",
            "name": "primary.report",
            "provider_type": "example",
            "provider_name": "primary",
            "instance_name": "primary",
            "target_provider_instances": ["example.primary"],
            "target_provider_types": ["example"],
            "target_provider_skill_names": ["primary.report"],
        },
    ]
    transcript = [
        {"role": "user", "content": "Continue the item workflow."},
        {
            "role": "tool",
            "tool_name": "example_lookup",
            "content": {
                "success": True,
                "_internal": {
                    "internal_request_trace_id": "trace-old",
                    "provider_instance_ref": "example.primary",
                },
            },
        },
        {
            "role": "assistant",
            "content": "Choose the item to continue:\n1. Example item\nReply with a number.",
        },
    ]
    monkeypatch.setattr(
        prepare_module,
        "collect_tools_snapshot",
        lambda **kwargs: [
            {
                "name": "example_lookup",
                "provider_type": "example",
                "provider_instance_ref": "example.primary",
                "provider_skill_name": "primary.item",
            },
            {
                "name": "example_create_report",
                "provider_type": "example",
                "provider_instance_ref": "example.primary",
                "provider_skill_name": "primary.report",
            },
        ],
    )
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(entry) for entry in entries],
    )
    monkeypatch.setattr(
        prepare_module,
        "_infer_active_provider_skill_from_transcript",
        lambda **kwargs: "primary.item",
    )
    runner = _build_prepare_runner(_PrepareSessionManager(transcript=transcript))

    async def _plan_new_workflow(**kwargs):
        assert kwargs["user_message"] == "Create a report."
        assert kwargs["active_workflow_context"]["internal_request_trace_id"] == "trace-old"
        return ConversationTurnPlan(
            route=ConversationTurnRoute.START_NEW,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["provider_skill:primary.report"],
            reason="The user started a report workflow.",
        )

    runner._plan_conversation_turn_with_model = _plan_new_workflow
    deps = SkillDeps(session_key="new-workflow-session", channel="api", extra={})
    state = _prepare_phase_state(deps=deps)
    state["user_message"] = "Create a report."

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert state["tool_intent_plan"].target_provider_skill_names == ["primary.report"]
    assert "active_internal_request_trace_id" not in deps.extra
    assert state["model_user_message"] == "Create a report."
    assert state["tool_request_message"] == "Create a report."
    assert "current_follow_up_context" not in deps.extra


def test_authorized_context_does_not_inject_standalone_skill_runtime_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "# Draft workflow\n\nSummarize collected input and ask for confirmation.",
        encoding="utf-8",
    )
    skill_entry = {
        "capability_id": "skill:draft-workflow",
        "kind": "md_skill",
        "name": "draft-workflow",
        "qualified_skill_name": "draft-workflow",
        "description": "Prepare a draft from user input.",
        "locator": str(skill_path),
        "metadata": {},
    }
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: [])
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [dict(skill_entry)],
    )
    runner = _build_prepare_runner(_PrepareSessionManager())

    async def _plan_context_only(**kwargs):
        return ConversationTurnPlan(
            route=ConversationTurnRoute.START_NEW,
            action=ConversationTurnAction.RESPOND,
            target_capability_ids=["skill:draft-workflow"],
            reason="The current turn supplies requested workflow input.",
        )

    runner._plan_conversation_turn_with_model = _plan_context_only
    deps = SkillDeps(
        session_key="standalone-context-only-session",
        channel="api",
        extra={
            "md_skills_snapshot": [dict(skill_entry)],
            "internal_runtime_tools_snapshot": [
                {
                    "name": "internal_file_write",
                    "description": "Write a generated file.",
                    "capability_class": "skill_runtime:exec",
                }
            ],
        },
    )
    state = _prepare_phase_state(deps=deps)

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert deps.extra["runtime_allowed_tool_names"] == []
    assert deps.extra["tool_policy"]["mode"] == "context_only"
    assert deps.extra["target_md_skill"]["qualified_name"] == "draft-workflow"
    assert "ask for confirmation" in deps.extra["target_md_skill"]["instructions"]
    assert deps.extra["standard_skill_runtime_trace"]["enabled"] is False
    assert (
        deps.extra["standard_skill_runtime_trace"]["reason"]
        == "execution_not_authorized"
    )
    assert "standard_skill_runtime_enabled" not in deps.extra
    assert "standard_skill_runtime_tools_visible" not in deps.extra


def test_page_default_does_not_block_active_workflow_or_clear_capability_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {"name": "example_update_item", "provider_type": "example"},
        {"name": "example_create_report", "provider_type": "example"},
    ]
    active_provider_skill_entry = {
        "capability_id": "provider_skill:example:item",
        "kind": "provider_skill",
        "name": "example:item",
        "description": "Read and update provider items.",
        "provider_type": "example",
        "provider_name": "example",
        "provider_skill_name": "item",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["example:item"],
        "tool_names": ["example_update_item"],
    }
    report_provider_skill_entry = {
        "capability_id": "provider_skill:example:report",
        "kind": "provider_skill",
        "name": "example:report",
        "description": "Create reports from provider data.",
        "provider_type": "example",
        "provider_name": "example",
        "provider_skill_name": "report",
        "target_provider_instances": ["example.primary"],
        "target_provider_types": ["example"],
        "target_provider_skill_names": ["example:report"],
        "tool_names": ["example_create_report"],
    }
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **kwargs: list(tools))
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **kwargs: [
            dict(active_provider_skill_entry),
            dict(report_provider_skill_entry),
        ],
    )
    monkeypatch.setattr(
        prepare_module,
        "_infer_active_provider_skill_from_transcript",
        lambda **kwargs: "example:item",
    )
    manager = _PrepareSessionManager(
        transcript=[{"role": "assistant", "content": "Which item should I update?"}]
    )
    runner = _build_prepare_runner(manager)
    selector_calls = 0

    async def _plan_turn(**kwargs):
        nonlocal selector_calls
        selector_calls += 1
        assert kwargs["active_capability_context"] == ""
        return ConversationTurnPlan(
            route=ConversationTurnRoute.START_NEW,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=["provider_skill:example:report"],
            reason="switch_to_report_workflow",
        )

    runner._plan_conversation_turn_with_model = _plan_turn
    deps = SkillDeps(
        session_key="active-session",
        channel="api",
        extra={
            "context": {
                "turn_context": {
                    "default_skill": {
                        "ref": "example:item",
                        "name": "example:item",
                        "provider_type": "example",
                        "provider_instance": "default",
                    },
                    "object": {"type": "item", "id": "ITEM-1"},
                }
            }
        },
    )
    state = _prepare_phase_state(deps=deps)

    asyncio.run(_run_prepare_until_tool_policy(runner, state=state))

    assert selector_calls == 1
    assert state["tool_intent_plan"].reason == "switch_to_report_workflow"
    assert state["tool_intent_plan"].target_provider_skill_names == ["example:report"]


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


def test_implicit_only_tools_are_hidden_from_natural_language_routing() -> None:
    filtered, removed = filter_implicit_only_tools(
        [
            {
                "name": "memory_search",
                "description": "Read-only search of existing user memory",
                "capability_class": "memory",
                "group_ids": ["group:memory"],
            },
            {
                "name": "memory_get",
                "description": "Read-only memory file slice",
                "capability_class": "memory",
                "group_ids": ["group:memory"],
            },
            {
                "name": "internal_provider_lookup",
                "description": "Provider lookup selected only by another skill or slash command",
                "routing_visibility": "internal",
            },
            {
                "name": "web_search",
                "description": "Search the public web",
                "capability_class": "web_search",
                "group_ids": ["group:web"],
            },
        ]
    )

    assert removed == ["memory_search", "memory_get", "internal_provider_lookup"]
    assert [tool["name"] for tool in filtered] == ["web_search"]




def test_selected_skill_does_not_require_every_internal_tool() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_skill_names=["slides"],
        target_capability_classes=["artifact:pptx"],
    )
    decision = ToolGateDecision(
        needs_tool=True,
        suggested_tool_classes=["artifact:pptx"],
        reason="The selected skill must execute one appropriate operation.",
        policy=ToolPolicyMode.MUST_USE_TOOL,
    )
    match_result = CapabilityMatcher(
        available_tools=[
            {
                "name": "slides_create",
                "skill_name": "slides",
                "capability_class": "artifact:pptx",
            },
            {
                "name": "slides_update",
                "skill_name": "slides",
                "capability_class": "artifact:pptx",
            },
        ]
    ).match(decision.suggested_tool_classes)

    required = RunnerToolGatePolicyMixin._required_tool_names_for_decision(
        decision=decision,
        match_result=match_result,
        intent_plan=plan,
    )

    assert required == []


def test_active_capability_continuation_context_does_not_require_prompt_markers() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._build_active_capability_continuation_request(
        user_message="Submit",
        recent_history=[
            {"role": "user", "content": "I want to request a Linux VM"},
            {
                "role": "assistant",
                "content": (
                    "Request draft:\n"
                    "- Business group: Development\n"
                    "- Size: 2C4G\n"
                    "- Operating system: Linux\n"
                    "The next step is up to you."
                ),
            },
        ],
    )

    assert used_follow_up_context is True
    assert "Original user request:\nI want to request a Linux VM" in resolved
    assert "Latest assistant follow-up prompt:" in resolved
    assert "Request draft" in resolved
    assert "User reply to that prompt:\nSubmit" in resolved




def test_selected_capability_ids_use_provider_skill_not_provider_instance() -> None:
    ids = selected_capability_ids_from_intent_plan(
        ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["smartcmp.cmp"],
            target_provider_skill_names=["cmp.request"],
        )
    )

    assert ids == ["provider_skill:cmp.request"]
    assert "provider_instance:smartcmp.cmp" not in ids


def test_selected_capability_ids_skip_provider_skill_without_instance_scope() -> None:
    ids = selected_capability_ids_from_intent_plan(
        ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_skill_names=["cmp.request"],
        )
    )

    assert ids == []


def test_selected_capability_ids_keep_standalone_skill_separate_from_provider_skill() -> None:
    ids = selected_capability_ids_from_intent_plan(
        ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["smartcmp.cmp"],
            target_provider_skill_names=["cmp.request"],
            target_skill_names=["xlsx"],
        )
    )

    assert ids == ["provider_skill:cmp.request", "skill:xlsx"]
    assert "provider_skill:cmp.xlsx" not in ids


def test_repeated_selected_provider_skill_keeps_tools_for_active_follow_up() -> None:
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.cmp"],
        target_provider_types=["smartcmp"],
        target_provider_skill_names=["cmp.request"],
        target_tool_names=[
            "smartcmp_list_services",
            "smartcmp_submit_request",
        ],
    )

    assert _selected_plan_matches_active_capability(
        intent_plan=plan,
        active_provider_skill="cmp.request",
        active_skill=None,
    )

    projected, trace = project_minimal_toolset(
        allowed_tools=[
            {
                "name": "smartcmp_list_services",
                "provider_type": "smartcmp",
                "provider_skill_name": "cmp.request",
                "qualified_skill_name": "smartcmp:request",
                "skill_name": "request",
            },
            {
                "name": "smartcmp_submit_request",
                "provider_type": "smartcmp",
                "provider_skill_name": "cmp.request",
                "qualified_skill_name": "smartcmp:request",
                "skill_name": "request",
                "group_ids": ["group:cmp", "group:request", "group:mutation"],
            },
            {
                "name": "smartcmp_preapproval_get_catalog_detail",
                "provider_type": "smartcmp",
                "provider_skill_name": "cmp.preapproval-agent",
                "qualified_skill_name": "smartcmp:preapproval-agent",
                "skill_name": "preapproval-agent",
            },
        ],
        intent_plan=plan,
    )

    assert turn_action_requires_tool_execution(plan)
    assert trace["reason"] == "projection_applied"
    assert {tool["name"] for tool in projected} == {
        "smartcmp_list_services",
        "smartcmp_submit_request",
    }




def test_apply_provider_instance_selection_policy_records_explicit_instance() -> None:
    deps = SimpleNamespace(
        extra={
            "provider_instances": {
                "smartcmp": {
                    "prod": {"base_url": "https://prod.example.com"},
                    "dev": {"base_url": "https://dev.example.com"},
                }
            }
        }
    )
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["smartcmp.dev"],
    )

    updated_plan, trace = apply_provider_instance_selection_policy(
        deps=deps,
        intent_plan=plan,
    )

    assert updated_plan is not None
    assert updated_plan.target_provider_instances == ["smartcmp.dev"]
    assert updated_plan.target_provider_types == ["smartcmp"]
    assert deps.extra["provider_instance_selections"] == {"smartcmp": "dev"}
    assert deps.extra["provider_type"] == "smartcmp"
    assert deps.extra["provider_instance_name"] == "dev"
    assert deps.extra["provider_instance"]["base_url"] == "https://dev.example.com"
    assert trace["selected_provider_instances"] == ["smartcmp.dev"]


def test_apply_provider_instance_selection_policy_does_not_default_provider_type_to_instance() -> None:
    deps = SimpleNamespace(
        extra={
            "provider_instances": {
                "smartcmp": {
                    "prod": {"base_url": "https://prod.example.com"},
                    "dev": {"base_url": "https://dev.example.com"},
                }
            }
        }
    )
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_types=["smartcmp"],
    )

    updated_plan, trace = apply_provider_instance_selection_policy(
        deps=deps,
        intent_plan=plan,
    )

    assert updated_plan is plan
    assert updated_plan.target_provider_instances == []
    assert updated_plan.target_provider_types == ["smartcmp"]
    assert "provider_instance_selections" not in deps.extra
    assert "provider_instance_name" not in deps.extra
    assert trace["selected_provider_instances"] == []


def test_apply_provider_instance_selection_policy_does_not_default_provider_tool_target_to_instance() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [
                {
                    "name": "markdown_vault_search",
                    "provider_type": "markdown-vault",
                }
            ],
            "provider_instances": {
                "markdown-vault": {
                    "knowledgebase": {"vault_path": "/vault/smartcmp"},
                    "atlasclaw-docs": {"vault_path": "/vault/atlasclaw"},
                }
            },
        }
    )
    plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_tool_names=["markdown_vault_search"],
    )

    updated_plan, trace = apply_provider_instance_selection_policy(
        deps=deps,
        intent_plan=plan,
    )

    assert updated_plan is plan
    assert updated_plan.target_provider_instances == []
    assert updated_plan.target_provider_types == []
    assert updated_plan.target_tool_names == ["markdown_vault_search"]
    assert "provider_instance_name" not in deps.extra
    assert "provider_instance" not in deps.extra


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


def test_prune_auto_selected_provider_instance_tools_uses_intent_instance_target() -> None:
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
                        "prod": {"provider_type": "smartcmp"},
                        "dev": {"provider_type": "smartcmp"},
                    }
                }
            }
        ),
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["smartcmp.dev"],
            target_provider_types=["smartcmp"],
        ),
    )

    assert {tool["name"] for tool in filtered_tools} == {"smartcmp_submit_request"}
    assert trace["enabled"] is True
    assert trace["removed_tools"] == ["select_provider_instance"]
    assert trace["target_provider_instances"] == ["smartcmp.dev"]
    assert trace["explicit_selected_provider_types"] == ["smartcmp"]
    assert trace["explicit_selected_instances"] == ["dev"]


def test_prune_provider_instance_tools_keeps_selector_without_provider_target() -> None:
    filtered_tools, trace = prune_auto_selected_provider_instance_tools(
        available_tools=[
            {
                "name": "markdown_vault_search",
                "description": "Search markdown vault",
                "provider_type": "markdown-vault",
                "capability_class": "provider:markdown-vault",
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
                    "markdown-vault": {
                        "knowledgebase": {"provider_type": "markdown-vault"},
                        "atlasclaw-docs": {"provider_type": "markdown-vault"},
                    }
                }
            }
        ),
        intent_plan=ToolIntentPlan(action=ToolIntentAction.USE_TOOLS),
    )

    assert {tool["name"] for tool in filtered_tools} == {
        "markdown_vault_search",
        "select_provider_instance",
    }
    assert trace["enabled"] is False
    assert trace["removed_tools"] == []


def test_hydrate_session_provider_instance_selections_keeps_visible_selection() -> None:
    deps = SimpleNamespace(
        session_key="agent:main:user:u-1:main",
        session_manager=_ProviderSelectionSessionManager({"smartcmp": "dev"}),
        extra={
            "provider_instances": {
                "smartcmp": {
                    "prod": {"base_url": "https://cmp.example.com"},
                    "dev": {"base_url": "https://dev-cmp.example.com"},
                }
            }
        },
    )

    asyncio.run(hydrate_session_provider_instance_selections(deps))

    assert deps.extra["provider_instance_selections"] == {"smartcmp": "dev"}


def test_hydrate_session_provider_instance_selections_ignores_stale_selection() -> None:
    deps = SimpleNamespace(
        session_key="agent:main:user:u-1:main",
        session_manager=_ProviderSelectionSessionManager({"smartcmp": "prod"}),
        extra={
            "provider_instances": {
                "smartcmp": {
                    "dev": {"base_url": "https://dev-cmp.example.com"},
                }
            }
        },
    )

    asyncio.run(hydrate_session_provider_instance_selections(deps))

    assert "provider_instance_selections" not in deps.extra


def test_provider_skill_plan_persists_selected_provider_instance() -> None:
    manager = _ProviderSelectionSessionManager({})
    deps = SimpleNamespace(
        session_key="agent:main:user:u-1:main",
        session_manager=manager,
        extra={
            "provider_instances": {
                "markdown-vault": {
                    "knowledgebase": {"vault_path": "/kb"},
                    "atlasclaw-docs": {"vault_path": "/docs"},
                }
            }
        },
    )
    intent_plan, trace = apply_provider_instance_selection_policy(
        deps=deps,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["markdown-vault.knowledgebase"],
            target_provider_types=["markdown-vault"],
            target_provider_skill_names=["knowledgebase.markdown-vault-query"],
        ),
    )

    persisted = asyncio.run(
        persist_provider_instance_targets_from_intent_plan(
            deps=deps,
            intent_plan=intent_plan,
        )
    )

    assert trace["enabled"] is True
    assert persisted == ["markdown-vault.knowledgebase"]
    assert manager._session.extra["provider_instance_selections"] == {
        "markdown-vault": "knowledgebase"
    }


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
            target_provider_instances=["smartcmp.default"],
            target_provider_types=["smartcmp"],
            target_provider_skill_names=["cmp.request"],
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
        user_message="List pending CMP approvals",
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
        user_message="What is the weather in Shanghai tomorrow?",
        recent_history=[
            {"role": "user", "content": "List all current pending approvals in CMP"},
            {"role": "assistant", "content": "I will look that up."},
        ],
        used_follow_up_context=False,
    )

    assert history == []


def test_resolve_contextual_tool_request_keeps_rich_identifier_query_self_contained() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="Show details for TIC20260316000001",
        recent_history=[
            {"role": "user", "content": "List all current pending approvals in CMP"},
            {"role": "assistant", "content": "I will list them."},
        ],
    )

    assert resolved == "Show details for TIC20260316000001"
    assert used_follow_up_context is False


def test_resolve_contextual_tool_request_reuses_previous_user_message_for_low_information_follow_up() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="Shanghai",
        recent_history=[
            {"role": "user", "content": "What is the weather in Beijing tomorrow?"},
            {"role": "assistant", "content": "Weather for Beijing, China\nDaily forecast:\n| 2026-04-15 | Slight rain showers |"},
        ],
    )

    assert resolved == "What is the weather in Beijing tomorrow?\nShanghai"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_keeps_provider_route_query_self_contained() -> None:
    runner = _GateRunner()
    deps = SimpleNamespace(
        extra={
            "provider_instances": {
                "markdown-vault": {
                    "knowledgebase": {
                        "usage_hint": "Use for SmartCMP knowledge-base questions.",
                    },
                    "atlasclaw-docs": {
                        "usage_hint": "Use for AtlasClaw product documentation.",
                    },
                }
            }
        }
    )

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="How does the SmartCMP knowledge base relate service requests to deployment logs?",
        recent_history=[
            {"role": "user", "content": "According to the AtlasClaw documentation, what should a standard user check after first login?"},
            {
                "role": "assistant",
                "content": "Provide the section or specific question to search for.",
            },
        ],
        deps=deps,
    )

    assert resolved == "How does the SmartCMP knowledge base relate service requests to deployment logs?"
    assert used_follow_up_context is False


def test_provider_skill_projection_does_not_append_generic_coordination_tools() -> None:
    deps = SimpleNamespace(
        extra={
            "provider_instances": {
                "markdown-vault": {
                    "knowledgebase": {
                        "usage_hint": (
                            "Use for SmartCMP support-status questions, configuration, "
                            "integration, support status, and extension-path knowledge-base Q&A."
                        ),
                    }
                }
            }
        }
    )
    usage_plan = ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=["markdown-vault.knowledgebase"],
        target_provider_skill_names=["knowledgebase.search"],
    )
    updated_plan, selection_trace = apply_provider_instance_selection_policy(
        deps=deps,
        intent_plan=usage_plan,
    )
    projected, projection_trace = project_minimal_toolset(
        allowed_tools=[
            {
                "name": "markdown_vault_search",
                "description": "Search a Markdown knowledge vault",
                "provider_type": "markdown-vault",
                "capability_class": "provider:markdown-vault",
                "skill_name": "search",
                "qualified_skill_name": "markdown-vault:search",
            },
            {
                "name": "atlasclaw_catalog_query",
                "description": "Query the runtime catalog",
                "capability_class": "atlasclaw_catalog",
                "coordination_only": True,
            },
            {
                "name": "read",
                "description": "Read a local file",
                "capability_class": "fs_read",
                "coordination_only": True,
            },
            {
                "name": "session_status",
                "description": "Current session status",
                "capability_class": "session",
                "coordination_only": True,
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "capability_class": "provider:generic",
                "group_ids": ["group:providers"],
                "coordination_only": True,
            },
        ],
        intent_plan=updated_plan,
    )
    pruned, pruning_trace = prune_auto_selected_provider_instance_tools(
        available_tools=projected,
        deps=deps,
        intent_plan=updated_plan,
    )

    assert selection_trace["selected_provider_instances"] == ["markdown-vault.knowledgebase"]
    assert projection_trace["coordination_tools"] == []
    assert pruning_trace["removed_tools"] == []
    assert {tool["name"] for tool in pruned} == {"markdown_vault_search"}


def test_resolve_contextual_tool_request_reuses_previous_request_for_structured_follow_up_reply() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="linuxVM23, root, Passw0rd",
        recent_history=[
            {"role": "user", "content": "Request a 2C4G cloud resource"},
            {
                "role": "assistant",
                "content": (
                    "Provide the following information:\n"
                    "1. Resource name:\n"
                    "2. Username:\n"
                    "3. Password:"
                ),
            },
        ],
    )

    assert resolved == "Request a 2C4G cloud resource\nlinuxVM23, root, Passw0rd"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_reuses_previous_request_for_whitespace_separated_fields() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="Username root Password Passw0rd Name linux-test123",
        recent_history=[
            {"role": "user", "content": "Request a 2C4G Linux virtual machine"},
            {
                "role": "assistant",
                "content": (
                    "Provide the following information before submission:\n"
                    "1. Resource name\n"
                    "2. Username\n"
                    "3. Password"
                ),
            },
        ],
    )

    assert resolved == "Request a 2C4G Linux virtual machine\nUsername root Password Passw0rd Name linux-test123"
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


def test_resolve_contextual_tool_request_reuses_single_labeled_option_selection() -> None:
    runner = _GateRunner()
    assistant_prompt = (
        "Select an availability zone (available_zone_id) by number:\n"
        "1. cn-north-1a\n"
        "2. cn-north-1b\n"
        "Select the availability zone to deploy into."
    )

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="available_zone_id selection 1 (cn-north-1a)",
        recent_history=[
            {"role": "user", "content": "Request a cloud virtual machine"},
            {"role": "assistant", "content": assistant_prompt},
        ],
    )

    assert resolved == "Request a cloud virtual machine\navailable_zone_id selection 1 (cn-north-1a)"
    assert used_follow_up_context is True


def test_exact_choice_match_accepts_only_number_or_complete_visible_option() -> None:
    runner = _GateRunner()
    history = [
        {
            "role": "assistant",
            "content": (
                "Choose a compute profile by number:\n"
                "1. Tiny (1C1G)\n"
                "2. **Small** (2C2G)\n"
                "3. Medium (2C4G)"
            ),
        }
    ]

    numeric = runner._resolve_exact_choice_reply(
        user_message="2",
        recent_history=history,
    )
    complete = runner._resolve_exact_choice_reply(
        user_message="  Small(2C2G)  ",
        recent_history=history,
    )

    assert numeric is not None
    assert (numeric.ordinal, numeric.label, numeric.match_mode) == (
        2,
        "Small (2C2G)",
        "number",
    )
    assert complete is not None
    assert complete.label == "Small (2C2G)"
    assert (complete.ordinal, complete.match_mode) == (2, "full_line")
    assert runner._resolve_exact_choice_reply(
        user_message="I choose 2",
        recent_history=history,
    ) is None

    single = runner._resolve_exact_choice_reply(
        user_message="OnlyOption",
        recent_history=[
            {
                "role": "assistant",
                "content": (
                    "Please select an image:\n"
                    "1. **Only Option**\n"
                    "Reply with the option number."
                ),
            }
        ],
    )
    assert single is not None
    assert (single.ordinal, single.label) == (1, "Only Option")

    auto = runner._resolve_single_visible_choice_prompt(
        "Please select an image:\n1. Only Option\nReply with the option number."
    )
    assert auto is not None
    assert (auto.ordinal, auto.label, auto.match_mode) == (
        1,
        "Only Option",
        "single_auto",
    )
    assert runner._resolve_single_visible_choice_prompt(
        "Please confirm the operation:\n1. Execute deletion\nReply with 1."
    ) is None
    assert runner._resolve_single_visible_choice_prompt(
        "Please select an image:\n1. Redhat 8.10\n"
        "After selection, collect the previously confirmed required fields."
    ) is not None
    assert runner._resolve_single_visible_choice_prompt(
        "Please select an image:\n1. First\n2. Second\nReply with a number."
    ) is None


def test_resolve_contextual_tool_request_reuses_selection_with_returned_option_id() -> None:
    runner = _GateRunner()
    assistant_prompt = (
        "Select a network for the vSphere resource pool. One network is available:\n"
        "1. 192.168.24.0/22 — vSphere network\n"
        "Select the network by replying with 1."
    )

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="networkId select 1 (network-361)",
        recent_history=[
            {"role": "user", "content": "2 cc160480-482-finaltest"},
            {"role": "assistant", "content": assistant_prompt},
        ],
    )

    assert resolved == "2 cc160480-482-finaltest\nnetworkId select 1 (network-361)"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_keeps_unprompted_single_selection_self_contained() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="networkId selection 1 (network-361)",
        recent_history=[
            {"role": "user", "content": "Show the current network configuration."},
            {
                "role": "assistant",
                "content": "The configuration is available. Tell me what you want next.",
            },
        ],
    )

    assert resolved == "networkId selection 1 (network-361)"
    assert used_follow_up_context is False


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
            {"role": "user", "content": "Request a 2C4G cloud resource"},
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

    assert resolved == "Request a 2C4G cloud resource\nlinuxVM23, root, Passw0rd"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_recognizes_bracketed_selection_prompt() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="2",
        recent_history=[
            {"role": "user", "content": "Request a 2C4G cloud resource"},
            {
                "role": "assistant",
                "content": (
                    "[1] team1\n"
                    "[2] My business group\n"
                    "Select a business group by number:"
                ),
            },
        ],
    )

    assert "Original user request:\nRequest a 2C4G cloud resource" in resolved
    assert "Latest assistant follow-up prompt:" in resolved
    assert "[2] My business group" in resolved
    assert "User reply to that prompt:\n2" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_preserves_latest_prompt_for_repeated_numeric_choices() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="1",
        recent_history=[
            {"role": "user", "content": "I want to request a Linux VM"},
            {
                "role": "assistant",
                "content": (
                    "Select the business group for the request:\n"
                    "Development\n"
                    "Testing\n"
                    "Which business group should own the Linux VM?"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "Development selected.\n\n"
                    "Please select the required size and reply with a number:\n"
                    "Tiny — 1C1G\n"
                    "Small — 1C2G\n"
                    "Medium — 2C4G\n"
                    "Large — 4C8G\n"
                    "Which size do you need?"
                ),
            },
        ],
    )

    assert "Original user request:\nI want to request a Linux VM" in resolved
    assert "Recent follow-up context:" in resolved
    assert "User: 1" in resolved
    assert "Latest assistant follow-up prompt:" in resolved
    assert "Tiny — 1C1G" in resolved
    assert "User reply to that prompt:\n1" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert resolved != "I want to request a Linux VM 1"
    assert used_follow_up_context is True


def test_resolve_contextual_tool_request_preserves_selection_chain_for_third_numeric_choice() -> None:
    runner = _GateRunner()

    resolved, used_follow_up_context = runner._resolve_contextual_tool_request(
        user_message="1",
        recent_history=[
            {"role": "user", "content": "I want to request a Linux VM"},
            {
                "role": "assistant",
                "content": (
                    "Select the business group for the request:\n"
                    "Development\n"
                    "Testing\n"
                    "Which business group should own the Linux VM?"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "Development selected.\n\n"
                    "Please select the required size and reply with a number:\n"
                    "Tiny — 1C1G\n"
                    "Small — 1C2G\n"
                    "Which size do you need?"
                ),
            },
            {"role": "user", "content": "1"},
            {
                "role": "assistant",
                "content": (
                    "Tiny selected.\n\n"
                    "Please select the resource environment and reply with a number:\n"
                    "Development\n"
                    "Production\n"
                    "Which resource environment do you need?"
                ),
            },
        ],
    )

    assert "Original user request:\nI want to request a Linux VM" in resolved
    assert "Recent follow-up context:" in resolved
    assert "Select the business group for the request" in resolved
    assert "Please select the required size" in resolved
    assert resolved.count("User: 1") == 2
    assert "Latest assistant follow-up prompt:" in resolved
    assert "Please select the resource environment" in resolved
    assert "Development" in resolved
    assert "User reply to that prompt:\n1" in resolved
    assert "Resolved latest visible selection:" not in resolved
    assert used_follow_up_context is True


def test_transcript_active_provider_skill_infers_from_assistant_tool_calls() -> None:
    active_skill = _infer_active_provider_skill_from_transcript(
        message_history=[
            {"role": "user", "content": "I want to request a Linux VM"},
            {
                "role": "assistant",
                "content": "I will list the available business groups.",
                "tool_calls": [{"name": "smartcmp_list_business_groups"}],
            },
        ],
        capability_index=[
            {
                "kind": "provider_skill",
                "name": "cmp.request",
                "target_provider_instances": ["smartcmp.cmp"],
                "target_provider_types": ["smartcmp"],
                "target_provider_skill_names": ["cmp.request"],
                "declared_tool_names": [
                    "smartcmp_list_business_groups",
                    "smartcmp_submit_request",
                ],
            }
        ],
        active_provider_name="cmp",
    )

    assert active_skill == "cmp.request"


def test_transcript_active_provider_skill_uses_sticky_instance_to_disambiguate() -> None:
    active_skill = _infer_active_provider_skill_from_transcript(
        message_history=[
            {"role": "user", "content": "Use the knowledge base to determine whether AWS Lambda is supported"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "markdown_vault_search"}],
            },
            {"role": "tool", "tool_name": "markdown_vault_search", "content": {"ok": True}},
            {"role": "assistant", "content": "The knowledge base contains no evidence of native AWS Lambda support."},
            {"role": "user", "content": "Create an Excel workbook"},
            {"role": "assistant", "content": "", "tool_calls": [{"name": "skill_exec"}]},
            {"role": "tool", "tool_name": "skill_exec", "content": {"ok": True}},
        ],
        capability_index=[
            {
                "kind": "provider_skill",
                "name": "knowledgebase.markdown-vault-query",
                "target_provider_instances": ["markdown-vault.knowledgebase"],
                "target_provider_types": ["markdown-vault"],
                "target_provider_skill_names": ["knowledgebase.markdown-vault-query"],
                "declared_tool_names": ["markdown_vault_search", "markdown_vault_get"],
            },
            {
                "kind": "provider_skill",
                "name": "atlasclaw-docs.markdown-vault-query",
                "target_provider_instances": ["markdown-vault.atlasclaw-docs"],
                "target_provider_types": ["markdown-vault"],
                "target_provider_skill_names": ["atlasclaw-docs.markdown-vault-query"],
                "declared_tool_names": ["markdown_vault_search", "markdown_vault_get"],
            },
        ],
        active_provider_names=["knowledgebase"],
    )

    assert active_skill == "knowledgebase.markdown-vault-query"


def test_transcript_plain_skill_inference_ignores_provider_bound_markdown_skill() -> None:
    active_skill = _infer_active_skill_from_transcript(
        message_history=[
            {"role": "user", "content": "Use the knowledge base to determine whether AWS Lambda is supported"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "markdown_vault_search"}],
            },
        ],
        md_skills_snapshot=[
            {
                "name": "markdown-vault-query",
                "qualified_name": "markdown-vault:markdown-vault-query",
                "description": "Query Markdown vaults",
                "provider": "markdown-vault",
                "metadata": {
                    "provider_type": "markdown-vault",
                    "tool_search_name": "markdown_vault_search",
                },
            },
        ],
        provider_instances={
            "markdown-vault": {
                "knowledgebase": {"usage_hint": "Use for SmartCMP knowledge-base questions."},
            }
        },
    )

    assert active_skill is None


def test_transcript_active_provider_skill_infers_from_embedded_tool_results() -> None:
    active_skill = _infer_active_provider_skill_from_transcript(
        message_history=[
            {
                "role": "assistant",
                "content": "Select a business group.",
                "tool_results": [
                    {
                        "tool_name": "smartcmp_list_business_groups",
                        "content": {"ok": True},
                    }
                ],
            },
        ],
        capability_index=[
            {
                "kind": "provider_skill",
                "name": "cmp.request",
                "target_provider_instances": ["smartcmp.cmp"],
                "target_provider_types": ["smartcmp"],
                "target_provider_skill_names": ["cmp.request"],
                "declared_tool_names": [
                    "smartcmp_list_business_groups",
                    "smartcmp_submit_request",
                ],
            }
        ],
        active_provider_name="cmp",
    )

    assert active_skill == "cmp.request"


def xtest_build_recent_follow_up_tool_intent_plan_reuses_single_recent_tool() -> None:
    plan = build_recent_follow_up_tool_intent_plan(
        recent_history=[
            {"role": "user", "content": "What is the weather in Beijing tomorrow?"},
            {"role": "assistant", "content": "I will check.", "tool_calls": [{"name": "openmeteo_weather"}]},
            {"role": "tool", "tool_name": "openmeteo_weather", "content": {"ok": True}},
            {"role": "assistant", "content": "Weather for Beijing, China"},
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
                "content": "I will list the service catalog.",
                "tool_calls": [{"name": "smartcmp_list_services"}],
            },
            {"role": "tool", "tool_name": "smartcmp_list_services", "content": {"ok": True}},
            {
                "role": "assistant",
                "content": "I will retrieve the business groups next.",
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
    assert plan.target_provider_skill_names == ["cmp.request"]
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_group_ids == ["group:cmp", "group:request"]
    assert plan.target_tool_names == [
        "smartcmp_list_business_groups",
        "smartcmp_list_services",
    ]


def test_runtime_history_for_tool_turns_keeps_recent_context_even_without_follow_up_flag() -> None:
    history = _PrepareRunner._build_runtime_message_history_for_turn(
        session_message_history=[
            {"role": "user", "content": "List all pending requests in CMP"},
            {"role": "assistant", "content": "I listed three pending requests."},
        ],
        used_follow_up_context=False,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            reason="legacy tool turn",
        ),
    )

    assert history == [
        {"role": "user", "content": "List all pending requests in CMP"},
        {"role": "assistant", "content": "I listed three pending requests."},
    ]


def test_llm_first_guidance_plan_keeps_metadata_as_hints_only() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="List all pending requests in CMP",
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
        user_message="Write these requests into a new PowerPoint presentation",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=False,
    )

    assert plan is None


def test_llm_first_guidance_plan_rejects_provider_skill_without_instance_scope() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="Write these requests into a new PowerPoint presentation",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_provider_skill_names=["cmp.request"],
            target_skill_names=["pptx"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan is None


def test_llm_first_guidance_plan_keeps_explicit_artifact_targets_from_metadata_plan() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="Write these requests into a new PowerPoint presentation",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["smartcmp.cmp"],
            target_provider_types=["smartcmp"],
            target_provider_skill_names=["cmp.request"],
            target_skill_names=["pptx"],
            target_capability_classes=["artifact:pptx", "provider:smartcmp"],
            target_tool_names=["pptx_create_deck", "smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_provider_instances == ["smartcmp.cmp"]
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_provider_skill_names == ["cmp.request"]
    assert plan.target_skill_names == ["pptx"]
    assert plan.target_capability_classes == ["artifact:pptx", "provider:smartcmp"]
    assert plan.target_tool_names == ["pptx_create_deck", "smartcmp_list_pending"]


def test_llm_first_guidance_plan_supports_new_artifact_types_without_keyword_router() -> None:
    plan = build_llm_first_guidance_plan(
        user_message="Organize these requests into a new PDF document",
        metadata_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_instances=["smartcmp.cmp"],
            target_provider_types=["smartcmp"],
            target_provider_skill_names=["cmp.request"],
            target_skill_names=["pdf"],
            target_capability_classes=["artifact:pdf", "provider:smartcmp"],
            target_tool_names=["pdf_create_document", "smartcmp_list_pending"],
            reason="metadata_recall_matched",
        ),
        explicit_capability_match=True,
    )

    assert plan.action is ToolIntentAction.DIRECT_ANSWER
    assert plan.target_provider_instances == ["smartcmp.cmp"]
    assert plan.target_provider_types == ["smartcmp"]
    assert plan.target_provider_skill_names == ["cmp.request"]
    assert plan.target_skill_names == ["pdf"]
    assert plan.target_capability_classes == ["artifact:pdf", "provider:smartcmp"]
    assert plan.target_tool_names == ["pdf_create_document", "smartcmp_list_pending"]
