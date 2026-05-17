# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from app.atlasclaw.agent.stream import StreamEvent

logger = logging.getLogger(__name__)


class RunnerExecutionFinalizePhaseMixin:
    async def _run_finalize_phase(self, *, state: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        """Finalize run lifecycle and agent-end hooks."""
        session_key = state.get("session_key")
        user_message = state.get("user_message")
        deps = state.get("deps")
        max_tool_calls = state.get("max_tool_calls")
        timeout_seconds = state.get("timeout_seconds")
        _token_failover_attempt = state.get("_token_failover_attempt")
        _emit_lifecycle_bounds = state.get("_emit_lifecycle_bounds")
        start_time = state.get("start_time")
        tool_calls_count = state.get("tool_calls_count")
        compaction_applied = state.get("compaction_applied")
        thinking_emitter = state.get("thinking_emitter")
        persist_override_messages = state.get("persist_override_messages")
        persist_override_base_len = state.get("persist_override_base_len")
        runtime_agent = state.get("runtime_agent")
        selected_token_id = state.get("selected_token_id")
        release_slot = state.get("release_slot")
        extra = state.get("extra")
        run_id = state.get("run_id")
        tool_execution_retry_count = state.get("tool_execution_retry_count")
        run_failed = state.get("run_failed")
        message_history = state.get("message_history")
        system_prompt = state.get("system_prompt")
        final_assistant = state.get("final_assistant")
        context_history_for_hooks = state.get("context_history_for_hooks")
        tool_call_summaries = state.get("tool_call_summaries")
        session_title = state.get("session_title")
        buffered_assistant_events = state.get("buffered_assistant_events")
        assistant_output_streamed = state.get("assistant_output_streamed")
        tool_request_message = state.get("tool_request_message")
        tool_intent_plan = state.get("tool_intent_plan")
        tool_gate_decision = state.get("tool_gate_decision")
        tool_match_result = state.get("tool_match_result")
        current_model_attempt = state.get("current_model_attempt")
        current_attempt_started_at = state.get("current_attempt_started_at")
        current_attempt_has_text = state.get("current_attempt_has_text")
        current_attempt_has_tool = state.get("current_attempt_has_tool")
        reasoning_retry_count = state.get("reasoning_retry_count")
        run_output_start_index = state.get("run_output_start_index")
        tool_execution_required = state.get("tool_execution_required")
        reasoning_retry_limit = state.get("reasoning_retry_limit")
        model_stream_timed_out = state.get("model_stream_timed_out")
        model_timeout_error_message = state.get("model_timeout_error_message")
        runtime_context_window_info = state.get("runtime_context_window_info")
        runtime_context_guard = state.get("runtime_context_guard")
        runtime_context_window = state.get("runtime_context_window")
        session_manager = state.get("session_manager")
        session = state.get("session")
        transcript = state.get("transcript")
        all_available_tools = state.get("all_available_tools")
        tool_groups_snapshot = state.get("tool_groups_snapshot")
        available_tools = state.get("available_tools")
        toolset_filter_trace = state.get("toolset_filter_trace")
        tool_projection_trace = state.get("tool_projection_trace")
        used_toolset_fallback = state.get("used_toolset_fallback")
        metadata_candidates = state.get("metadata_candidates")
        ranking_trace = state.get("ranking_trace")
        # -- hook:agent_end --
        if not run_failed:
            try:
                await self.runtime_events.trigger_agent_end(
                    session_key=session_key,
                    run_id=run_id,
                    tool_calls_count=tool_calls_count,
                    compaction_applied=compaction_applied,
                )
            except Exception as exc:
                logger.exception("trigger_agent_end failed")
                if bool(state.get("answer_committed")):
                    yield StreamEvent.runtime_update(
                        "warning",
                        f"Post-answer side effect failed: {exc.__class__.__name__}",
                        metadata={"phase": "post_answer_exception"},
                    )

        if _emit_lifecycle_bounds:
            yield StreamEvent.lifecycle_end()
        state.update({
            "session_key": session_key,
            "user_message": user_message,
            "deps": deps,
            "max_tool_calls": max_tool_calls,
            "timeout_seconds": timeout_seconds,
            "_token_failover_attempt": _token_failover_attempt,
            "_emit_lifecycle_bounds": _emit_lifecycle_bounds,
            "start_time": start_time,
            "tool_calls_count": tool_calls_count,
            "compaction_applied": compaction_applied,
            "thinking_emitter": thinking_emitter,
            "persist_override_messages": persist_override_messages,
            "persist_override_base_len": persist_override_base_len,
            "runtime_agent": runtime_agent,
            "selected_token_id": selected_token_id,
            "release_slot": release_slot,
            "extra": extra,
            "run_id": run_id,
            "tool_execution_retry_count": tool_execution_retry_count,
            "run_failed": run_failed,
            "message_history": message_history,
            "system_prompt": system_prompt,
            "final_assistant": final_assistant,
            "context_history_for_hooks": context_history_for_hooks,
            "tool_call_summaries": tool_call_summaries,
            "session_title": session_title,
            "buffered_assistant_events": buffered_assistant_events,
            "assistant_output_streamed": assistant_output_streamed,
            "tool_request_message": tool_request_message,
            "tool_intent_plan": tool_intent_plan,
            "tool_gate_decision": tool_gate_decision,
            "tool_match_result": tool_match_result,
            "current_model_attempt": current_model_attempt,
            "current_attempt_started_at": current_attempt_started_at,
            "current_attempt_has_text": current_attempt_has_text,
            "current_attempt_has_tool": current_attempt_has_tool,
            "reasoning_retry_count": reasoning_retry_count,
            "run_output_start_index": run_output_start_index,
            "tool_execution_required": tool_execution_required,
            "reasoning_retry_limit": reasoning_retry_limit,
            "model_stream_timed_out": model_stream_timed_out,
            "model_timeout_error_message": model_timeout_error_message,
            "runtime_context_window_info": runtime_context_window_info,
            "runtime_context_guard": runtime_context_guard,
            "runtime_context_window": runtime_context_window,
            "session_manager": session_manager,
            "session": session,
            "transcript": transcript,
            "all_available_tools": all_available_tools,
            "tool_groups_snapshot": tool_groups_snapshot,
            "available_tools": available_tools,
            "toolset_filter_trace": toolset_filter_trace,
            "tool_projection_trace": tool_projection_trace,
            "used_toolset_fallback": used_toolset_fallback,
            "metadata_candidates": metadata_candidates,
            "ranking_trace": ranking_trace,
        })
