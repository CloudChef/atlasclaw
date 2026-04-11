from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.agent.runner_tool.runner_execution_finalize import RunnerExecutionFinalizePhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow import RunnerExecutionFlowPhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import RunnerExecutionPreparePhaseMixin


logger = logging.getLogger(__name__)


class RunnerExecutionLoopMixin(RunnerExecutionPreparePhaseMixin, RunnerExecutionFlowPhaseMixin, RunnerExecutionFinalizePhaseMixin):
    async def run(
        self,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        *,
        max_tool_calls: int = 50,
        timeout_seconds: int = 600,
        _token_failover_attempt: int = 0,
        _emit_lifecycle_bounds: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Execute one agent turn as a stream of runtime events."""
        start_time = time.monotonic()
        tool_calls_count = 0
        compaction_applied = False
        thinking_emitter = ThinkingStreamEmitter()
        persist_override_messages: Optional[list[dict]] = None
        persist_override_base_len: int = 0
        runtime_agent: Any = self.agent
        selected_token_id: Optional[str] = None
        release_slot: Optional[Any] = None
        flushed_memory_signatures: set[str] = set()
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        run_id = str(extra.get("run_id", "") or "")
        tool_execution_retry_count = int(extra.get("_tool_execution_retry_count", 0) or 0)
        run_failed = False
        message_history: list[dict] = []
        system_prompt = ""
        final_assistant = ""
        context_history_for_hooks: list[dict] = []
        tool_call_summaries: list[dict[str, Any]] = []
        session_title = ""
        buffered_assistant_events: list[StreamEvent] = []
        assistant_output_streamed = False
        tool_request_message = user_message
        tool_intent_plan = None
        tool_gate_decision = ToolGateDecision(reason="Tool gate not evaluated yet.")
        tool_match_result = CapabilityMatchResult(
            resolved_policy=ToolPolicyMode.ANSWER_DIRECT,
            tool_candidates=[],
            missing_capabilities=[],
            reason="Tool matcher not evaluated yet.",
        )
        current_model_attempt = 0
        current_attempt_started_at: float | None = None
        current_attempt_has_text = False
        current_attempt_has_tool = False
        reasoning_retry_count = 0
        run_output_start_index = 0
        tool_execution_required = False
        reasoning_retry_limit = self.REASONING_ONLY_MAX_RETRIES
        model_stream_timed_out = False
        model_timeout_error_message = ""

        def _log_step(step: str, **data: Any) -> None:
            payload: dict[str, Any] = {
                "session": session_key,
                "run_id": run_id,
                "step": step,
                "elapsed": round(time.monotonic() - start_time, 3),
            }
            payload.update(data)
            logger.warning("run_step %s", payload)



        phase_state: dict[str, Any] = {
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
            "flushed_memory_signatures": flushed_memory_signatures,
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
            "runtime_context_window_info": None,
            "runtime_context_guard": None,
            "runtime_context_window": None,
            "session_manager": None,
            "session": None,
            "transcript": None,
            "all_available_tools": [],
            "tool_groups_snapshot": {},
            "available_tools": [],
            "toolset_filter_trace": [],
            "tool_projection_trace": {},
            "used_toolset_fallback": False,
            "provider_hint_docs": [],
            "skill_hint_docs": [],
            "metadata_candidates": {},
            "ranking_trace": {},
            "answer_committed": False,
            "should_stop": False,
            "latest_agent_messages": [],
            "synthetic_tool_messages": [],
        }


        try:
            async for event in self._run_prepare_phase(state=phase_state, _log_step=_log_step):
                yield event
            if phase_state.get("should_stop"):
                return

            async for event in self._run_loop_phase(state=phase_state, _log_step=_log_step):
                yield event
            if phase_state.get("should_stop"):
                return

            async for event in self._run_finalize_phase(state=phase_state):
                yield event

        except Exception as e:
            logger.exception("Agent runtime outer exception")
            run_id = str(phase_state.get("run_id", "") or "")
            system_prompt = str(phase_state.get("system_prompt", "") or "")
            final_assistant = str(phase_state.get("final_assistant", "") or "")
            context_history_for_hooks = list(phase_state.get("context_history_for_hooks") or [])
            tool_call_summaries = list(phase_state.get("tool_call_summaries") or [])
            session_title = str(phase_state.get("session_title", "") or "")
            thinking_emitter = phase_state.get("thinking_emitter")
            await self.runtime_events.trigger_run_failed(
                session_key=session_key,
                run_id=run_id,
                error=str(e),
            )
            await self.runtime_events.trigger_run_context_ready(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
                system_prompt=system_prompt,
                message_history=context_history_for_hooks,
                assistant_message=final_assistant,
                tool_calls=tool_call_summaries,
                run_status="failed",
                error=str(e),
                session_title=session_title,
            )
            if thinking_emitter is not None:
                async for event in thinking_emitter.close_if_active():
                    yield event

            yield StreamEvent.runtime_update(
                "failed",
                str(e),
                metadata={"phase": "exception", "elapsed": round(time.monotonic() - start_time, 1)},
            )
            yield StreamEvent.error_event(str(e))
        finally:
            selected_token_id = phase_state.get("selected_token_id")
            release_slot = phase_state.get("release_slot")
            deps_obj = phase_state.get("deps", deps)
            if selected_token_id and self.token_interceptor is not None:
                headers = self._extract_rate_limit_headers(deps_obj)
                if headers:
                    self.token_interceptor.on_response(selected_token_id, headers)
            if release_slot is not None:
                release_slot()

