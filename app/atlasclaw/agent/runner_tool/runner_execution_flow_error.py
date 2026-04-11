from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from app.atlasclaw.agent.runner_tool.runner_tool_messages import overlay_synthetic_tool_messages
from app.atlasclaw.agent.runner_tool.runner_tool_projection import tool_required_turn_has_real_execution
from app.atlasclaw.agent.stream import StreamEvent


logger = logging.getLogger(__name__)


class RunnerExecutionFlowErrorMixin:
    async def _handle_loop_phase_exception(
        self,
        *,
        error: Exception,
        state: dict[str, Any],
    ) -> AsyncIterator[StreamEvent]:
        """Handle runtime exceptions within loop phase and attempt token failover."""
        logger.exception("Agent runtime exception during streaming run")
        if bool(state.get("answer_committed")):
            error_text = str(error).strip() or error.__class__.__name__
            yield StreamEvent.runtime_update(
                "warning",
                f"Post-answer side effect failed: {error_text}",
                metadata={
                    "phase": "post_answer_exception",
                    "elapsed": round(time.monotonic() - float(state.get("start_time") or 0.0), 1),
                },
            )
            return

        error_text = str(error).strip()
        if not error_text:
            if state.get("model_stream_timed_out") and state.get("model_timeout_error_message"):
                error_text = str(state.get("model_timeout_error_message"))
            else:
                error_text = error.__class__.__name__

        retry_error: Exception = error
        if not str(error).strip():
            retry_error = RuntimeError(error_text)

        latest_messages = list(state.get("latest_agent_messages") or state.get("message_history") or [])
        run_output_start_index = int(state.get("run_output_start_index") or 0)
        persist_run_output_start_index = int(
            state.get("persist_run_output_start_index") or run_output_start_index
        )
        latest_messages = overlay_synthetic_tool_messages(
            messages=latest_messages,
            synthetic_tool_messages=list(state.get("synthetic_tool_messages") or []),
            start_index=persist_run_output_start_index,
        )
        tool_call_summaries = list(state.get("tool_call_summaries") or [])
        inferred_tool_calls = self._collect_tool_call_summaries_from_messages(
            messages=latest_messages,
            start_index=persist_run_output_start_index,
        )
        if inferred_tool_calls:
            existing_signatures: set[tuple[str, str]] = set()
            for item in tool_call_summaries:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if not name:
                    continue
                args = item.get("args")
                args_signature = str(sorted(args.items())) if isinstance(args, dict) else ""
                existing_signatures.add((name, args_signature))
            for item in inferred_tool_calls:
                name = str(item.get("name", "") or "").strip()
                if not name:
                    continue
                args = item.get("args")
                args_signature = str(sorted(args.items())) if isinstance(args, dict) else ""
                signature = (name, args_signature)
                if signature in existing_signatures:
                    continue
                existing_signatures.add(signature)
                tool_call_summaries.append(item)

        tool_required_has_real_execution = tool_required_turn_has_real_execution(
            intent_plan=state.get("tool_intent_plan"),
            tool_call_summaries=tool_call_summaries,
            final_messages=latest_messages,
            start_index=persist_run_output_start_index,
            executed_tool_names=state.get("executed_tool_names"),
        )
        if tool_required_has_real_execution:
            tool_only_answer = self._build_tool_only_markdown_answer_from_messages(
                messages=latest_messages,
                start_index=persist_run_output_start_index,
            )
            if tool_only_answer:
                logger.warning(
                    "tool-required turn hit model-finalization exception; returning tool-backed fallback answer"
                )
                session_key = state.get("session_key")
                run_id = state.get("run_id")
                session_manager = state.get("session_manager")
                user_message = state.get("user_message")
                system_prompt = state.get("system_prompt")
                session_title = state.get("session_title")
                thinking_emitter = state.get("thinking_emitter")
                safe_messages = self._sanitize_turn_messages_for_persistence(
                    messages=latest_messages,
                    start_index=persist_run_output_start_index,
                    final_assistant=tool_only_answer,
                    clear_tool_planning_text=True,
                )
                if thinking_emitter is not None:
                    async for event in thinking_emitter.close_if_active():
                        yield event
                yield StreamEvent.runtime_update(
                    "warning",
                    "Model finalization failed after successful tool execution. Returning a tool-backed fallback answer.",
                    metadata={
                        "phase": "tool_only_fallback",
                        "elapsed": round(time.monotonic() - float(state.get("start_time") or 0.0), 1),
                        "error": error_text,
                    },
                )
                yield StreamEvent.assistant_delta(tool_only_answer)
                try:
                    await self.runtime_events.trigger_llm_completed(
                        session_key=session_key,
                        run_id=run_id,
                        assistant_message=tool_only_answer,
                    )
                except Exception:
                    logger.exception("tool_only_fallback llm_completed failed")
                try:
                    if session_manager is not None:
                        await session_manager.persist_transcript(session_key, safe_messages)
                except Exception:
                    logger.exception("tool_only_fallback persist_transcript failed")
                try:
                    await self.runtime_events.trigger_run_context_ready(
                        session_key=session_key,
                        run_id=run_id,
                        user_message=user_message,
                        system_prompt=system_prompt,
                        message_history=state.get("context_history_for_hooks") or [],
                        assistant_message=tool_only_answer,
                        tool_calls=tool_call_summaries,
                        run_status="completed",
                        session_title=session_title,
                    )
                except Exception:
                    logger.exception("tool_only_fallback run_context_ready failed")
                yield StreamEvent.runtime_update(
                    "answered",
                    "Final answer ready.",
                    metadata={
                        "phase": "tool_only_fallback",
                        "elapsed": round(time.monotonic() - float(state.get("start_time") or 0.0), 1),
                    },
                )
                state["answer_committed"] = True
                state["assistant_output_streamed"] = True
                state["final_assistant"] = tool_only_answer
                state["tool_call_summaries"] = tool_call_summaries
                state["message_history"] = safe_messages
                state["buffered_assistant_events"] = []
                return

        hard_failure_retried = False
        async for retry_event in self._retry_after_hard_token_failure(
            error=retry_error,
            session_key=state.get("session_key"),
            user_message=state.get("user_message"),
            deps=state.get("deps"),
            selected_token_id=state.get("selected_token_id"),
            release_slot=state.get("release_slot"),
            thinking_emitter=state.get("thinking_emitter"),
            start_time=state.get("start_time"),
            max_tool_calls=state.get("max_tool_calls"),
            timeout_seconds=state.get("timeout_seconds"),
            token_failover_attempt=state.get("_token_failover_attempt"),
            emit_lifecycle_bounds=state.get("_emit_lifecycle_bounds"),
        ):
            hard_failure_retried = True
            yield retry_event

        if hard_failure_retried:
            state["release_slot"] = None
            state["selected_token_id"] = None
            state["should_stop"] = True
            return

        if tool_required_has_real_execution:
            logger.warning(
                "tool-required turn failed before final assistant output; tool-only recovery was unavailable"
            )

        state["run_failed"] = True
        await self.runtime_events.trigger_llm_failed(
            session_key=state.get("session_key"),
            run_id=state.get("run_id"),
            error=error_text,
        )
        await self.runtime_events.trigger_run_failed(
            session_key=state.get("session_key"),
            run_id=state.get("run_id"),
            error=error_text,
        )
        await self.runtime_events.trigger_run_context_ready(
            session_key=state.get("session_key"),
            run_id=state.get("run_id"),
            user_message=state.get("user_message"),
            system_prompt=state.get("system_prompt"),
            message_history=state.get("context_history_for_hooks") or [],
            assistant_message=state.get("final_assistant") or "",
            tool_calls=state.get("tool_call_summaries") or [],
            run_status="failed",
            error=error_text,
            session_title=state.get("session_title"),
        )

        thinking_emitter = state.get("thinking_emitter")
        if thinking_emitter is not None:
            async for event in thinking_emitter.close_if_active():
                yield event

        yield StreamEvent.runtime_update(
            "failed",
            f"Agent runtime error: {error_text}",
            metadata={"phase": "exception", "elapsed": round(time.monotonic() - float(state.get('start_time') or 0.0), 1)},
        )
        yield StreamEvent.error_event(f"agent_error: {error_text}")

