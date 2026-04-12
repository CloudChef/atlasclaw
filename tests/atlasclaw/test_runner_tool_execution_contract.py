# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import time

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.atlasclaw.agent.runner_tool.runner_execution_flow_error import RunnerExecutionFlowErrorMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow import RunnerExecutionFlowPhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_post import RunnerExecutionFlowPostMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import RunnerExecutionFlowStreamMixin
from app.atlasclaw.agent.runner_tool.runner_execution_payload import (
    RunnerExecutionPayloadMixin,
    build_finalize_payload,
)
from app.atlasclaw.agent.runner_tool.runner_execution_retry import RunnerExecutionRetryMixin
from app.atlasclaw.agent.runner_tool.runner_tool_messages import (
    extract_synthetic_tool_messages_from_next_node,
    overlay_synthetic_tool_messages,
)
from app.atlasclaw.agent.runner_tool.runner_tool_gate_policy import RunnerToolGatePolicyMixin
from app.atlasclaw.agent.runner_tool_evidence import RunnerToolEvidenceMixin
from app.atlasclaw.agent.tool_gate_models import ToolGateDecision, ToolIntentAction, ToolIntentPlan, ToolPolicyMode


class _History:
    @staticmethod
    def normalize_messages(messages):
        return list(messages)


class _RuntimeEvents:
    def __init__(self) -> None:
        self.context_ready_calls = []

    async def trigger_llm_completed(self, **kwargs):
        return None

    async def trigger_run_context_ready(self, **kwargs):
        self.context_ready_calls.append(kwargs)
        return None

    async def trigger_llm_failed(self, **kwargs):
        return None

    async def trigger_run_failed(self, **kwargs):
        return None


class _SessionManager:
    def __init__(self) -> None:
        self.persisted_messages = None

    async def persist_transcript(self, session_key, messages):
        self.persisted_messages = list(messages)
        return None


class _PostRunner(
    RunnerExecutionPayloadMixin,
    RunnerToolEvidenceMixin,
    RunnerExecutionFlowPostMixin,
):
    def __init__(self) -> None:
        self.history = _History()
        self.runtime_events = _RuntimeEvents()

    @staticmethod
    def _collect_buffered_assistant_text(buffered_events):
        return ""

    @staticmethod
    def _missing_required_tool_names(**kwargs):
        return []

    @staticmethod
    def _build_tool_evidence_required_message(**kwargs):
        missing = kwargs.get("missing_required_tools", []) or []
        return "Missing successful tool evidence: " + ", ".join(missing)

    @staticmethod
    def _tool_gate_has_strict_need(decision):
        return bool(
            getattr(decision, "needs_tool", False)
            or getattr(decision, "needs_external_system", False)
            or getattr(decision, "needs_live_data", False)
            or getattr(decision, "needs_grounded_verification", False)
        )

    async def _retry_after_missing_tool_execution(self, **kwargs):
        if False:
            yield None
        return

    async def _maybe_finalize_title(self, **kwargs):
        return None


class _SlowRuntimeEvents(_RuntimeEvents):
    async def trigger_llm_completed(self, **kwargs):
        await asyncio.sleep(0.2)
        return None

    async def trigger_run_context_ready(self, **kwargs):
        await asyncio.sleep(0.2)
        self.context_ready_calls.append(kwargs)
        return None


class _SlowSessionManager(_SessionManager):
    async def persist_transcript(self, session_key, messages):
        await asyncio.sleep(0.2)
        self.persisted_messages = list(messages)
        return None


class _SlowPostRunner(_PostRunner):
    def __init__(self) -> None:
        super().__init__()
        self.runtime_events = _SlowRuntimeEvents()

    async def _maybe_finalize_title(self, **kwargs):
        await asyncio.sleep(0.2)
        return None


class _AgentRun:
    def __init__(self, messages, result=None):
        self._messages = list(messages)
        self.result = result

    def all_messages(self):
        return list(self._messages)


class _FlowHistory:
    def to_model_message_history(self, messages):
        return list(messages)

    def normalize_messages(self, messages):
        return list(messages)

    def prune_summary_messages(self, messages):
        return list(messages)


class _LoopRunner(RunnerExecutionFlowPhaseMixin):
    def __init__(self) -> None:
        self.history = _FlowHistory()
        self.compaction = SimpleNamespace(
            should_memory_flush=lambda *args, **kwargs: False,
            should_compact=lambda *args, **kwargs: False,
        )
        self.context_pruning_settings = SimpleNamespace(enabled=False)
        self.hooks = None
        self.runtime_events = SimpleNamespace(trigger_llm_input=_noop_async)
        self.captured_message_history = None

    @asynccontextmanager
    async def _run_iter_with_optional_override(self, **kwargs):
        self.captured_message_history = list(kwargs.get("message_history") or [])
        yield _AgentRun([])

    async def _process_agent_run_outcome(self, **kwargs):
        if False:
            yield None
        return

    async def _handle_loop_phase_exception(self, **kwargs):
        if False:
            yield None
        return

    async def _iter_agent_nodes(self, agent_run):
        if False:
            yield None
        return


class _ErrorRunner(RunnerToolEvidenceMixin, RunnerExecutionRetryMixin, RunnerExecutionFlowErrorMixin):
    def __init__(self) -> None:
        self.runtime_events = _RuntimeEvents()
        self.token_policy = None
        self.token_interceptor = None

    async def _retry_after_hard_token_failure(self, **kwargs):
        if False:
            yield None
        return


class _StreamRunner(RunnerExecutionFlowStreamMixin):
    pass


class _StreamRunnerWithEvidence(RunnerToolGatePolicyMixin, RunnerToolEvidenceMixin, RunnerExecutionFlowStreamMixin):
    pass


class _RefreshingHistory:
    def normalize_messages(self, messages):
        return list(messages)

    def prune_summary_messages(self, messages):
        return list(messages)


class _ToolAwareHistory(_RefreshingHistory):
    def normalize_messages(self, messages):
        normalized = []
        for message in messages:
            if isinstance(message, dict):
                normalized.append(dict(message))
                continue
            parts = getattr(message, "parts", None)
            if not isinstance(parts, list):
                continue
            for part in parts:
                tool_name = str(getattr(part, "tool_name", "") or "").strip()
                if not tool_name:
                    continue
                item = {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": getattr(part, "content", ""),
                }
                tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                if tool_call_id:
                    item["tool_call_id"] = tool_call_id
                normalized.append(item)
        return normalized


class _SequencedAgentRun:
    def __init__(self, snapshots):
        self._snapshots = [list(item) for item in snapshots]
        self._index = 0

    def all_messages(self):
        if self._index >= len(self._snapshots):
            return list(self._snapshots[-1])
        current = list(self._snapshots[self._index])
        self._index += 1
        return current


class _RefreshingStreamRunner(RunnerExecutionFlowStreamMixin):
    def __init__(self) -> None:
        self.history = _RefreshingHistory()

    @staticmethod
    def _deduplicate_message_history(messages):
        return list(messages)

    @staticmethod
    def _merge_runtime_messages_with_session_prefix(
        *,
        session_message_history,
        runtime_messages,
        runtime_base_history_len,
    ):
        return list(runtime_messages)


class _PayloadRunner(RunnerExecutionPayloadMixin):
    pass


@pytest.mark.asyncio
async def test_tool_required_turn_does_not_accept_fast_path_text_without_real_tool_execution() -> None:
    runner = _PostRunner()
    state = {
        "start_time": 0.0,
        "session_key": "s-1",
        "session_manager": _SessionManager(),
        "session": SimpleNamespace(title=""),
        "run_id": "run-1",
        "user_message": "查下CMP待审批",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            reason="provider request",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            reason="provider action",
        ),
    }

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun([{"role": "user", "content": "查下CMP待审批"}]),
        state=state,
        _log_step=lambda *args, **kwargs: None,
    ):
        events.append(event)

    answered_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "answered"
    ]
    assistant_chunks = [event for event in events if event.type == "assistant"]
    assert answered_states == []
    assert assistant_chunks == []


@pytest.mark.asyncio
async def test_tool_required_turn_without_final_assistant_uses_tool_only_fallback() -> None:
    runner = _PostRunner()
    session_manager = _SessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-2",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-2",
        "user_message": "查下CMP待审批",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            reason="provider request",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "smartcmp_list_pending", "args": {}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="provider action",
        ),
    }

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun(
            [
                {"role": "user", "content": "查下CMP待审批"},
                {
                    "role": "assistant",
                    "content": "我来帮您查询。",
                    "tool_calls": [{"id": "tc-1", "name": "smartcmp_list_pending", "args": {}}],
                },
                {
                    "role": "tool",
                    "tool_name": "smartcmp_list_pending",
                    "content": {"output": "count=3"},
                },
            ]
        ),
        state=state,
        _log_step=lambda *args, **kwargs: None,
        ):
        events.append(event)

    assistant_chunks = [event.content for event in events if event.type == "assistant"]
    answered_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "answered"
    ]
    failed_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "failed"
    ]

    assert answered_states
    assert failed_states == []
    assert any("count=3" in chunk for chunk in assistant_chunks)
    await runner._await_background_post_success_tasks()
    assert session_manager.persisted_messages is not None
    persisted_assistants = [
        message
        for message in session_manager.persisted_messages
        if str(message.get("role", "")).strip() == "assistant"
    ]
    assert any("count=3" in str(message.get("content", "")) for message in persisted_assistants)


@pytest.mark.asyncio
async def test_tool_required_turn_ignores_agent_result_text_and_uses_tool_only_fallback() -> None:
    runner = _PostRunner()
    session_manager = _SessionManager()
    stale_result = SimpleNamespace(response=SimpleNamespace(content="上一轮天气答案"))
    state = {
        "start_time": 0.0,
        "session_key": "s-2b",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-2b",
        "user_message": "上海周边有哪些自行车骑行公园",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_live_data=True,
            reason="public web search",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "persist_run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "web_search", "args": {"query": "上海周边 自行车骑行公园"}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_group_ids=["group:web"],
            target_capability_classes=["web_search"],
            target_tool_names=["web_search"],
            reason="public search",
        ),
    }

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun(
            [
                {"role": "assistant", "content": "上一轮天气答案"},
                {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "tc-1", "name": "web_search", "args": {"query": "上海周边 自行车骑行公园"}}],
                },
                {
                    "role": "tool",
                    "tool_name": "web_search",
                    "content": {
                        "output": "- 崇明岛环岛绿道\n- 滴水湖环湖骑行道\n- 淀山湖环湖骑行线"
                    },
                },
            ],
            result=stale_result,
        ),
        state=state,
        _log_step=lambda *args, **kwargs: None,
    ):
        events.append(event)

    assistant_chunks = [event.content for event in events if event.type == "assistant"]
    assert assistant_chunks
    assert all("上一轮天气答案" not in chunk for chunk in assistant_chunks)
    assert any("崇明岛" in chunk for chunk in assistant_chunks)


@pytest.mark.asyncio
async def test_tool_only_finalize_prefers_structured_tool_answer_over_model_plaintext() -> None:
    runner = _PostRunner()
    session_manager = _SessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-2c",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-2c",
        "user_message": "CMP里面有多少待审批的",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            reason="provider request",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [
            {
                "name": "smartcmp_list_pending",
                "capability_class": "provider:smartcmp",
                "result_mode": "tool_only_ok",
            }
        ],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "persist_run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "smartcmp_list_pending", "args": {}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_list_pending"],
            reason="provider action",
        ),
        "executed_tool_names": ["smartcmp_list_pending"],
        "force_tool_only_finalize": True,
    }

    meta_output = "\n".join(
        [
            "Answer",
            "=====",
            "+- [1] 高 ---------------------------------------------",
            "| 名称: Test ticket for build verification",
            "##APPROVAL_META_START##",
            '[{"index":1,"id":"A-1","requestId":"TIC20260316000001","name":"Test ticket for build verification","catalogName":"Incident Ticket","approvalStep":"一级审批","currentApprover":"待分配","waitHours":645.1}]',
            "##APPROVAL_META_END##",
        ]
    )

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun(
            [
                {"role": "user", "content": "CMP里面有多少待审批的"},
                {
                    "role": "assistant",
                    "content": "Answer\n=====\n+- [1] 高 ---------------------------------------------",
                    "tool_calls": [{"id": "cmp-1", "name": "smartcmp_list_pending", "args": {}}],
                },
                {
                    "role": "tool",
                    "tool_name": "smartcmp_list_pending",
                    "content": {"output": meta_output},
                },
            ]
        ),
        state=state,
        _log_step=lambda *args, **kwargs: None,
    ):
        events.append(event)

    assistant_chunks = [event.content for event in events if event.type == "assistant"]
    assert assistant_chunks
    final_chunk = "".join(assistant_chunks)
    assert "Answer" not in final_chunk
    assert "=====" not in final_chunk
    assert "### 1. Test ticket for build verification" in final_chunk


@pytest.mark.asyncio
async def test_run_loop_phase_preserves_explicit_empty_runtime_history() -> None:
    runner = _LoopRunner()
    state = {
        "deps": SimpleNamespace(user_message="", is_aborted=lambda: False),
        "user_message": "上海周边有哪些自行车骑行公园",
        "runtime_message_history": [],
        "message_history": [
            {"role": "user", "content": "明天上海天气如何"},
            {"role": "assistant", "content": "明天有小雨。"},
        ],
        "runtime_agent": object(),
        "system_prompt": "system",
        "run_output_start_index": 0,
        "thinking_emitter": SimpleNamespace(close_if_active=_empty_async_iter),
    }

    events = []
    async for event in runner._run_loop_phase(state=state, _log_step=lambda *args, **kwargs: None):
        events.append(event)

    runtime_messages = [
        event.content
        for event in events
        if event.type == "runtime"
    ]
    assert runtime_messages[:2] == [
        "Preparing model request context.",
        "Starting model session.",
    ]
    assert runner.captured_message_history == []
    assert state["run_output_start_index"] == 0


def test_repeated_tool_loop_limit_detects_same_tool_before_dispatch() -> None:
    exceeded = _StreamRunner._collect_repeated_tool_names(
        planned_tool_calls=[{"name": "web_search"}],
        executed_tool_names=["web_search", "web_search"],
        repeat_limit=2,
    )

    assert exceeded == ["web_search"]


def test_merge_runtime_messages_with_session_prefix_restores_full_turn_view() -> None:
    merged = _PayloadRunner._merge_runtime_messages_with_session_prefix(
        session_message_history=[
            {"role": "user", "content": "查下CMP待审批"},
            {"role": "assistant", "content": "好的，我来查。"},
        ],
        runtime_messages=[
            {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
            {"role": "assistant", "content": "我来帮你找。"},
        ],
        runtime_base_history_len=0,
    )

    assert merged == [
        {"role": "user", "content": "查下CMP待审批"},
        {"role": "assistant", "content": "好的，我来查。"},
        {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
        {"role": "assistant", "content": "我来帮你找。"},
    ]


def test_sanitize_turn_messages_for_persistence_drops_unmatched_tool_calls() -> None:
    runner = _PostRunner()
    sanitized = runner._sanitize_turn_messages_for_persistence(
        messages=[
            {"role": "user", "content": "查下CMP详情"},
            {
                "role": "assistant",
                "content": "我来查一下",
                "tool_calls": [{"id": "tc-1", "name": "smartcmp_get_request_detail", "args": {"identifier": "TIC-1"}}],
            },
        ],
        start_index=1,
        final_assistant="",
        clear_tool_planning_text=True,
    )

    assert sanitized == [{"role": "user", "content": "查下CMP详情"}]


@pytest.mark.asyncio
async def test_tool_required_turn_fails_when_required_tool_only_returns_errors() -> None:
    runner = _PostRunner()
    session_manager = _SessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-3",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-3",
        "user_message": "我要看下TIC20260316000001的详情",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_external_system=True,
            reason="provider request",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [{"name": "smartcmp_get_request_detail", "capability_class": "provider:smartcmp"}],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "smartcmp_get_request_detail", "args": {"identifier": "TIC20260316000001"}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_provider_types=["smartcmp"],
            target_tool_names=["smartcmp_get_request_detail"],
            reason="provider action",
        ),
        "repeated_tool_failure": {
            "tool_name": "smartcmp_get_request_detail",
            "error": "[ERROR] Missing required identifier argument.",
            "count": 2,
        },
    }

    def _missing_required_tool_names(**kwargs):
        return ["smartcmp_get_request_detail"]

    runner._missing_required_tool_names = _missing_required_tool_names  # type: ignore[method-assign]

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun(
            [
                {"role": "user", "content": "我要看下TIC20260316000001的详情"},
                {
                    "role": "assistant",
                    "content": "我来查一下。",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "smartcmp_get_request_detail",
                            "args": {"workflowId": "TIC20260316000001"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "smartcmp_get_request_detail",
                    "content": "[ERROR] Missing required identifier argument.",
                },
            ]
        ),
        state=state,
        _log_step=lambda *args, **kwargs: None,
    ):
        events.append(event)

    failed_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "failed"
    ]
    answered_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "answered"
    ]
    assert failed_states
    assert answered_states == []


@pytest.mark.asyncio
async def test_tool_required_exception_returns_tool_only_fallback_answer() -> None:
    runner = _ErrorRunner()
    session_manager = _SessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-4",
        "session_manager": session_manager,
        "run_id": "run-4",
        "user_message": "明天上海天气如何",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_call_summaries": [{"name": "openmeteo_weather", "args": {"location": "上海"}}],
        "latest_agent_messages": [
            {"role": "user", "content": "明天上海天气如何"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
            },
            {
                "role": "tool",
                "tool_name": "openmeteo_weather",
                "content": {
                    "output": "明天（2026-04-11）上海：小雨，13.8°C - 18.6°C，降水概率 63%。"
                },
            },
        ],
        "message_history": [],
        "run_output_start_index": 1,
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_capability_classes=["weather"],
            target_tool_names=["openmeteo_weather"],
            reason="weather tool required",
        ),
        "executed_tool_names": ["openmeteo_weather"],
        "thinking_emitter": SimpleNamespace(close_if_active=lambda: _empty_async_iter()),
        "session_title": "",
        "context_history_for_hooks": [],
        "final_assistant": "",
        "answer_committed": False,
        "assistant_output_streamed": False,
        "buffered_assistant_events": [],
    }

    events = []
    async for event in runner._handle_loop_phase_exception(
        error=RuntimeError("Invalid response from openai chat completions endpoint"),
        state=state,
    ):
        events.append(event)

    warning_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "warning"
    ]
    answered_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "answered"
    ]
    failed_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "failed"
    ]
    assistant_chunks = [event.content for event in events if event.type == "assistant"]

    assert warning_states
    assert answered_states
    assert failed_states == []
    assert assistant_chunks
    assert "上海" in assistant_chunks[0]
    assert state["answer_committed"] is True
    assert session_manager.persisted_messages is not None


def test_invalid_chat_completion_validation_error_is_hard_failure() -> None:
    runner = _ErrorRunner()

    assert runner._is_hard_token_failure(
        RuntimeError(
            "Invalid response from openai chat completions endpoint: 4 validation errors for ChatCompletion"
        )
    )


def test_detect_repeated_tool_no_progress_for_same_empty_search_results() -> None:
    runner = _StreamRunner()

    repeated = runner._detect_repeated_tool_no_progress(
        messages=[
            {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "query": "上海周边自行车骑行公园推荐",
                    "results": [],
                    "summary": "",
                    "citations": [],
                },
            },
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "query": "上海骑行公园 自行车道 推荐",
                    "results": [],
                    "summary": "",
                    "citations": [],
                },
            },
        ],
        start_index=1,
        target_tool_names=["web_search"],
        threshold=2,
    )

    assert repeated is not None
    assert repeated["tool_name"] == "web_search"
    assert repeated["count"] == 2


def test_detect_repeated_tool_no_progress_for_live_web_search_empty_payloads() -> None:
    runner = _StreamRunner()

    repeated = runner._detect_repeated_tool_no_progress(
        messages=[
            {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "content": [{"type": "text", "text": "Search '上海周边自行车骑行公园推荐' returned no results"}],
                    "details": {
                        "provider": "bing_html_fallback",
                        "query": "上海周边自行车骑行公园推荐",
                        "summary": "",
                        "results": [],
                        "citations": [],
                        "expanded_queries": ["上海周边自行车骑行公园推荐"],
                        "retrieved_at": "2026-04-11T01:32:58.968198",
                    },
                    "is_error": False,
                },
            },
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "content": [{"type": "text", "text": "Search '上海自行车骑行公园 骑行道 推荐' returned no results"}],
                    "details": {
                        "provider": "bing_html_fallback",
                        "query": "上海自行车骑行公园 骑行道 推荐",
                        "summary": "",
                        "results": [],
                        "citations": [],
                        "expanded_queries": ["上海自行车骑行公园 骑行道 推荐"],
                        "retrieved_at": "2026-04-11T01:33:13.663424",
                    },
                    "is_error": False,
                },
            },
        ],
        start_index=1,
        target_tool_names=["web_search"],
        threshold=2,
    )

    assert repeated is not None
    assert repeated["tool_name"] == "web_search"
    assert repeated["count"] == 2


def test_should_finalize_from_tool_results_when_tool_is_tool_only_ok() -> None:
    runner = _StreamRunnerWithEvidence()

    should_finalize = runner._should_finalize_from_tool_results(
        messages=[
            {"role": "user", "content": "明天上海天气如何"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
            },
            {
                "role": "tool",
                "tool_name": "openmeteo_weather",
                "content": {
                    "output": "明天（2026-04-11）上海：小雨，13.8°C - 18.6°C，降水概率 63%。"
                },
            },
        ],
        start_index=1,
        planned_tool_names=["openmeteo_weather"],
        available_tools=[
            {
                "name": "openmeteo_weather",
                "capability_class": "weather",
                "result_mode": "tool_only_ok",
            }
        ],
    )

    assert should_finalize is True


def test_should_finalize_from_embedded_tool_results_when_tool_is_tool_only_ok() -> None:
    runner = _StreamRunnerWithEvidence()

    should_finalize = runner._should_finalize_from_tool_results(
        messages=[
            {"role": "user", "content": "明天上海天气如何"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_results": [
                    {
                        "tool_name": "openmeteo_weather",
                        "content": {
                            "output": "明天（2026-04-11）上海：小雨，13.8°C - 18.6°C，降水概率 63%。"
                        },
                    }
                ],
            },
        ],
        start_index=1,
        planned_tool_names=["openmeteo_weather"],
        available_tools=[
            {
                "name": "openmeteo_weather",
                "capability_class": "weather",
                "result_mode": "tool_only_ok",
            }
        ],
    )

    assert should_finalize is True


def test_should_not_finalize_from_single_terminal_no_results_tool_payload() -> None:
    runner = _StreamRunnerWithEvidence()

    should_finalize = runner._should_finalize_from_tool_results(
        messages=[
            {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "web_search", "args": {"query": "上海周边 自行车骑行公园 推荐"}}],
            },
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "content": [{"type": "text", "text": "Search '上海周边 自行车骑行公园 推荐' returned no results"}],
                    "details": {
                        "provider": "bing_html_fallback",
                        "query": "上海周边 自行车骑行公园 推荐",
                        "summary": "",
                        "results": [],
                        "citations": [],
                    },
                    "is_error": False,
                },
            },
        ],
        start_index=1,
        planned_tool_names=["web_search"],
        available_tools=[
            {
                "name": "web_search",
                "capability_class": "web_search",
            }
        ],
    )

    assert should_finalize is False


def test_should_finalize_from_repeated_terminal_no_results_tool_payload() -> None:
    runner = _StreamRunnerWithEvidence()

    should_finalize = runner._should_finalize_from_tool_results(
        messages=[
            {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "web_search", "args": {"query": "上海周边 自行车骑行公园 推荐"}}],
            },
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "content": [{"type": "text", "text": "Search '上海周边 自行车骑行公园 推荐' returned no results"}],
                    "details": {
                        "provider": "bing_html_fallback",
                        "query": "上海周边 自行车骑行公园 推荐",
                        "summary": "",
                        "results": [],
                        "citations": [],
                    },
                    "is_error": False,
                },
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-2", "name": "web_search", "args": {"query": "上海自行车公园推荐"}}],
            },
            {
                "role": "tool",
                "tool_name": "web_search",
                "content": {
                    "content": [{"type": "text", "text": "Search '上海自行车公园推荐' returned no results"}],
                    "details": {
                        "provider": "bing_html_fallback",
                        "query": "上海自行车公园推荐",
                        "summary": "",
                        "results": [],
                        "citations": [],
                    },
                    "is_error": False,
                },
            },
        ],
        start_index=1,
        planned_tool_names=["web_search"],
        available_tools=[
            {
                "name": "web_search",
                "capability_class": "web_search",
            }
        ],
    )

    assert should_finalize is True


@pytest.mark.asyncio
async def test_refresh_messages_after_tool_dispatch_waits_for_new_tool_results() -> None:
    runner = _RefreshingStreamRunner()
    before_messages = [
        {"role": "user", "content": "上海周边有哪些自行车骑行公园"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc-1", "name": "web_search", "args": {"query": "上海周边自行车骑行公园推荐"}}],
        },
        {
            "role": "tool",
            "tool_name": "web_search",
            "content": {
                "content": [{"type": "text", "text": "Search '上海周边自行车骑行公园推荐' returned no results"}],
                "details": {"results": [], "citations": [], "summary": "", "query": "上海周边自行车骑行公园推荐"},
                "is_error": False,
            },
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc-2", "name": "web_search", "args": {"query": "上海自行车骑行公园 骑行绿道推荐"}}],
        },
    ]
    after_messages = before_messages + [
        {
            "role": "tool",
            "tool_name": "web_search",
            "content": {
                "content": [{"type": "text", "text": "Search '上海自行车骑行公园 骑行绿道推荐' returned no results"}],
                "details": {"results": [], "citations": [], "summary": "", "query": "上海自行车骑行公园 骑行绿道推荐"},
                "is_error": False,
            },
        }
    ]

    agent_run = _SequencedAgentRun([before_messages, after_messages])
    latest_runtime_messages, latest_messages = await runner._refresh_messages_after_tool_dispatch(
        agent_run=agent_run,
        session_message_history=[],
        runtime_base_history_len=0,
        start_index=1,
        target_tool_names=["web_search"],
        previous_result_count=1,
    )

    assert latest_runtime_messages == after_messages
    repeated = runner._detect_repeated_tool_no_progress(
        messages=latest_messages,
        start_index=1,
        target_tool_names=["web_search"],
        threshold=2,
    )
    assert repeated is not None
    assert repeated["tool_name"] == "web_search"


def test_extract_synthetic_tool_messages_from_next_node_returns_tool_rows() -> None:
    next_node = SimpleNamespace(
        request=ModelRequest(
            parts=[
                ToolReturnPart(
                    "openmeteo_weather",
                    {"output": "明天上海小雨，13.8-18.6°C"},
                    tool_call_id="weather-1",
                )
            ]
        )
    )

    synthetic_messages = extract_synthetic_tool_messages_from_next_node(
        history=_ToolAwareHistory(),
        next_node=next_node,
    )

    assert synthetic_messages == [
        {
            "role": "tool",
            "tool_name": "openmeteo_weather",
            "tool_call_id": "weather-1",
            "content": {"output": "明天上海小雨，13.8-18.6°C"},
        }
    ]


def test_overlay_synthetic_tool_messages_inserts_before_final_assistant() -> None:
    merged = overlay_synthetic_tool_messages(
        messages=[
            {"role": "user", "content": "明天上海天气如何"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
            },
            {"role": "assistant", "content": "明天上海小雨，13.8-18.6°C。"},
        ],
        synthetic_tool_messages=[
            {
                "role": "tool",
                "tool_name": "openmeteo_weather",
                "tool_call_id": "tc-1",
                "content": {"output": "明天上海小雨，13.8-18.6°C"},
            }
        ],
        start_index=1,
    )

    assert [message.get("role") for message in merged] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_tool_required_turn_uses_synthetic_tool_messages_for_tool_only_fallback() -> None:
    runner = _PostRunner()
    runner.history = _ToolAwareHistory()
    session_manager = _SessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-synth",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-synth",
        "user_message": "明天上海天气如何",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_live_data=True,
            reason="weather tool required",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [
            {"name": "openmeteo_weather", "capability_class": "weather", "result_mode": "tool_only_ok"}
        ],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "persist_run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "openmeteo_weather", "args": {"location": "上海"}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "synthetic_tool_messages": [
            {
                "role": "tool",
                "tool_name": "openmeteo_weather",
                "tool_call_id": "weather-1",
                "content": {"output": "明天（2026-04-12）上海：小雨，13.8-18.6°C，降水概率 63%。"},
            }
        ],
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_group_ids=["group:web"],
            target_capability_classes=["weather"],
            target_tool_names=["openmeteo_weather"],
            reason="weather",
        ),
    }

    events = []
    async for event in runner._process_agent_run_outcome(
        agent_run=_AgentRun(
            [
                {"role": "user", "content": "明天上海天气如何"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "weather-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
                },
            ]
        ),
        state=state,
        _log_step=lambda *args, **kwargs: None,
    ):
        events.append(event)

    assistant_chunks = [event.content for event in events if event.type == "assistant"]
    failed_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "failed"
    ]

    assert failed_states == []
    assert any("13.8-18.6" in chunk for chunk in assistant_chunks)
    await runner._await_background_post_success_tasks()
    assert session_manager.persisted_messages is not None
    assert any(
        str(message.get("role", "")).strip() == "tool"
        and str(message.get("tool_name", "")).strip() == "openmeteo_weather"
        for message in session_manager.persisted_messages
    )


async def _empty_async_iter():
    if False:
        yield None


async def _noop_async(*args, **kwargs):
    return None


def test_build_finalize_payload_is_minimal_for_tool_backed_answer() -> None:
    payload = build_finalize_payload(
        user_message="明天上海天气如何",
        tool_results=[
            {
                "tool_name": "openmeteo_weather",
                "content": "明天（2026-04-12）上海：小雨，13.8°C - 18.6°C，降水概率 63%。",
            }
        ],
    )

    assert "bootstrap" not in payload["system_prompt"].lower()
    assert "明天上海天气如何" in payload["user_prompt"]
    assert "openmeteo_weather" in payload["user_prompt"]


def test_build_tool_only_markdown_answer_includes_sources_for_structured_tool_result() -> None:
    runner = _PostRunner()

    answer = runner._build_tool_only_markdown_answer_from_messages(
        messages=[
            {
                "role": "tool",
                "tool_name": "openmeteo_weather",
                "content": {
                    "output": "明天（2026-04-12）上海：小雨，13.8°C - 18.6°C，降水概率 63%。",
                    "details": {
                        "sources": [
                            {
                                "label": "Open-Meteo Forecast API",
                                "url": "https://api.open-meteo.com/v1/forecast",
                            }
                        ]
                    },
                },
            }
        ],
        start_index=0,
    )

    assert "## Answer" not in answer
    assert "## Result" not in answer
    assert "13.8°C - 18.6°C" in answer
    assert "https://api.open-meteo.com/v1/forecast" in answer


def test_build_tool_only_markdown_answer_keeps_multi_item_structured_provider_output() -> None:
    runner = _PostRunner()
    pending_output = "\n".join(
        [
            "CMP pending approvals (3)",
            "1) TIC20260316000001",
            "title: Test ticket for build verification",
            "catalog: Incident Ticket",
            "stage: 一级审批",
            "assignee: 待分配",
            "wait_hours: 507.3",
            "priority_factor: 等待超3天",
            "2) TIC20260313000006",
            "title: 加急加急",
            "catalog: 问题工单",
            "stage: 一级审批",
            "assignee: 待分配",
            "wait_hours: 579.3",
            "priority_factor: 等待超3天",
            "3) TIC20260313000004",
            "title: (名称为空)",
            "catalog: 问题工单",
            "stage: 一级审批",
            "assignee: 待分配",
            "wait_hours: 580.7",
            "priority_factor: 等待超3天",
        ]
    )

    answer = runner._build_tool_only_markdown_answer_from_messages(
        messages=[
            {
                "role": "tool",
                "tool_name": "smartcmp_list_pending",
                "content": {
                    "output": pending_output,
                    "details": {
                        "sources": [
                            {
                                "label": "SmartCMP approvals",
                                "url": "https://smartcmp.example.local/pending",
                            }
                        ]
                    },
                },
            }
        ],
        start_index=0,
    )

    assert "TIC20260316000001" in answer
    assert "TIC20260313000006" in answer
    assert "TIC20260313000004" in answer
    assert "\n..." not in answer


def test_build_tool_only_markdown_answer_prefers_meta_block_over_ascii_layout() -> None:
    runner = _PostRunner()

    meta_output = "\n".join(
        [
            "===============================================================",
            "待审批列表 - 共 3 项（按优先级排序）",
            "===============================================================",
            "+- [1] 高 ---------------------------------------------",
            "| 名称: Test ticket for build verification",
            "| 工单号: TIC20260316000001",
            "##APPROVAL_META_START##",
            '[{"index":1,"id":"A-1","requestId":"TIC20260316000001","name":"Test ticket for build verification","catalogName":"Incident Ticket","approvalStep":"一级审批","currentApprover":"待分配","waitHours":645.1},{"index":2,"id":"A-2","requestId":"TIC20260313000006","name":"加急加急","catalogName":"问题工单","approvalStep":"一级审批","currentApprover":"待分配","waitHours":619.2}]',
            "##APPROVAL_META_END##",
        ]
    )

    answer = runner._build_tool_only_markdown_answer_from_messages(
        messages=[
            {
                "role": "tool",
                "tool_name": "smartcmp_list_pending",
                "content": {"output": meta_output},
            }
        ],
        start_index=0,
    )

    assert "## Answer" not in answer
    assert "## Result" not in answer
    assert "TIC20260316000001" in answer
    assert "Test ticket for build verification" in answer
    assert "加急加急" in answer
    assert "===============================================================" not in answer
    assert "+- [1]" not in answer


@pytest.mark.asyncio
async def test_post_success_side_effects_do_not_block_answer_completion() -> None:
    runner = _SlowPostRunner()
    session_manager = _SlowSessionManager()
    state = {
        "start_time": 0.0,
        "session_key": "s-fast-return",
        "session_manager": session_manager,
        "session": SimpleNamespace(title=""),
        "run_id": "run-fast-return",
        "user_message": "明天上海天气如何",
        "system_prompt": "system",
        "deps": SimpleNamespace(extra={}),
        "tool_gate_decision": ToolGateDecision(
            needs_tool=True,
            needs_live_data=True,
            reason="weather tool required",
            policy=ToolPolicyMode.PREFER_TOOL,
        ),
        "tool_match_result": SimpleNamespace(missing_capabilities=[], tool_candidates=[]),
        "available_tools": [
            {"name": "openmeteo_weather", "capability_class": "weather", "result_mode": "tool_only_ok"}
        ],
        "tool_execution_required": True,
        "max_tool_calls": 5,
        "timeout_seconds": 60.0,
        "_token_failover_attempt": 0,
        "_emit_lifecycle_bounds": False,
        "selected_token_id": None,
        "release_slot": None,
        "tool_execution_retry_count": 0,
        "persist_override_messages": None,
        "persist_override_base_len": 0,
        "run_output_start_index": 1,
        "persist_run_output_start_index": 1,
        "buffered_assistant_events": [],
        "tool_call_summaries": [{"name": "openmeteo_weather", "args": {"location": "上海"}}],
        "assistant_output_streamed": False,
        "model_stream_timed_out": False,
        "model_timeout_error_message": "",
        "current_model_attempt": 1,
        "thinking_emitter": SimpleNamespace(assistant_emitted=False),
        "context_history_for_hooks": [],
        "session_title": "",
        "tool_intent_plan": ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_group_ids=["group:web"],
            target_capability_classes=["weather"],
            target_tool_names=["openmeteo_weather"],
            reason="weather",
        ),
        "executed_tool_names": ["openmeteo_weather"],
    }

    async def _collect():
        events = []
        async for event in runner._process_agent_run_outcome(
            agent_run=_AgentRun(
                [
                    {"role": "user", "content": "明天上海天气如何"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "weather-1", "name": "openmeteo_weather", "args": {"location": "上海"}}],
                    },
                    {
                        "role": "tool",
                        "tool_name": "openmeteo_weather",
                        "content": {
                            "output": "明天（2026-04-12）上海：小雨，13.8°C - 18.6°C，降水概率 63%。"
                        },
                    },
                ]
            ),
            state=state,
            _log_step=lambda *args, **kwargs: None,
        ):
            events.append(event)
        return events

    started_at = time.monotonic()
    events = await asyncio.wait_for(_collect(), timeout=0.1)
    elapsed = time.monotonic() - started_at

    answered_states = [
        event
        for event in events
        if event.type == "runtime" and str(event.metadata.get("state", "")).strip() == "answered"
    ]
    assert answered_states
    assert elapsed < 0.1

    await runner._await_background_post_success_tasks()
    assert session_manager.persisted_messages is not None
    assert runner.runtime_events.context_ready_calls
