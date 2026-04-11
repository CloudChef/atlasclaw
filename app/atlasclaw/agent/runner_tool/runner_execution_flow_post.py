from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from app.atlasclaw.agent.runner_tool.runner_tool_messages import overlay_synthetic_tool_messages
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    tool_required_turn_has_real_execution,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent

logger = logging.getLogger(__name__)


class RunnerExecutionFlowPostMixin:
    def _schedule_background_post_success_task(self, task: asyncio.Task[Any]) -> None:
        background_tasks = getattr(self, "_background_post_success_tasks", None)
        if background_tasks is None:
            background_tasks = set()
            setattr(self, "_background_post_success_tasks", background_tasks)
        background_tasks.add(task)
        task.add_done_callback(self._on_background_post_success_done)

    def _on_background_post_success_done(self, task: asyncio.Task[Any]) -> None:
        background_tasks = getattr(self, "_background_post_success_tasks", None)
        if isinstance(background_tasks, set):
            background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Background post-success side effect failed: %s", exc)

    async def _await_background_post_success_tasks(self) -> None:
        background_tasks = list(getattr(self, "_background_post_success_tasks", set()) or [])
        if not background_tasks:
            return
        await asyncio.gather(*background_tasks, return_exceptions=True)

    async def _process_agent_run_outcome(
        self,
        *,
        agent_run: Any,
        state: dict[str, Any],
        _log_step: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Resolve final assistant output, persist transcript, and emit terminal events."""
        start_time = float(state.get("start_time") or 0.0)
        session_key = state.get("session_key")
        session_manager = state.get("session_manager")
        session = state.get("session")
        run_id = state.get("run_id")
        user_message = state.get("user_message")
        system_prompt = state.get("system_prompt")
        deps = state.get("deps")
        max_tool_calls = int(state.get("max_tool_calls") or 0)
        timeout_seconds = float(state.get("timeout_seconds") or 0.0)
        token_failover_attempt = int(state.get("_token_failover_attempt") or 0)
        emit_lifecycle_bounds = bool(state.get("_emit_lifecycle_bounds"))
        selected_token_id = state.get("selected_token_id")
        release_slot = state.get("release_slot")
        tool_execution_retry_count = int(state.get("tool_execution_retry_count") or 0)

        try:
            raw_final_messages = agent_run.all_messages()
        except Exception:
            raw_final_messages = list(state.get("message_history") or []) + [
                {"role": "user", "content": user_message}
            ]
        runtime_final_messages = self.history.normalize_messages(raw_final_messages)
        final_messages = self._merge_runtime_messages_with_session_prefix(
            session_message_history=state.get("session_message_history") or [],
            runtime_messages=runtime_final_messages,
            runtime_base_history_len=int(state.get("runtime_base_history_len") or 0),
        )
        final_messages = overlay_synthetic_tool_messages(
            messages=final_messages,
            synthetic_tool_messages=list(state.get("synthetic_tool_messages") or []),
            start_index=int(state.get("persist_run_output_start_index") or state.get("run_output_start_index") or 0),
        )
        state["latest_agent_messages"] = list(final_messages)

        persist_override_messages = state.get("persist_override_messages")
        persist_override_base_len = int(state.get("persist_override_base_len") or 0)
        if persist_override_messages is not None:
            if len(final_messages) > persist_override_base_len > 0:
                final_messages = persist_override_messages + final_messages[persist_override_base_len:]
            else:
                final_messages = persist_override_messages
            state["run_output_start_index"] = len(persist_override_messages)
            final_messages = overlay_synthetic_tool_messages(
                messages=final_messages,
                synthetic_tool_messages=list(state.get("synthetic_tool_messages") or []),
                start_index=int(state.get("run_output_start_index") or 0),
            )

        run_output_start_index = int(state.get("run_output_start_index") or 0)
        persist_run_output_start_index = int(
            state.get("persist_run_output_start_index") or run_output_start_index
        )
        final_assistant = self._extract_latest_assistant_from_messages(
            messages=final_messages,
            start_index=persist_run_output_start_index,
        )

        buffered_assistant_events = state.get("buffered_assistant_events") or []
        tool_intent_plan = state.get("tool_intent_plan")
        tool_execution_required = bool(state.get("tool_execution_required")) or turn_action_requires_tool_execution(
            tool_intent_plan
        )
        tool_call_summaries = state.get("tool_call_summaries") or []
        if buffered_assistant_events and tool_execution_required:
            buffered_assistant_events.clear()
        elif buffered_assistant_events and not final_assistant:
            if not tool_call_summaries and not tool_execution_required:
                while buffered_assistant_events:
                    event = buffered_assistant_events.pop(0)
                    if event.type == "assistant":
                        final_assistant += event.content
                        state["assistant_output_streamed"] = True
                    yield event
                state.get("thinking_emitter").assistant_emitted = bool(final_assistant)

        if final_messages:
            inferred_tool_calls = self._collect_tool_call_summaries_from_messages(
                messages=final_messages,
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

        model_stream_timed_out = bool(state.get("model_stream_timed_out"))
        model_timeout_error_message = str(state.get("model_timeout_error_message") or "")
        current_model_attempt = int(state.get("current_model_attempt") or 0)

        tool_required_has_real_execution = tool_required_turn_has_real_execution(
            intent_plan=tool_intent_plan,
            tool_call_summaries=tool_call_summaries,
            final_messages=final_messages,
            start_index=persist_run_output_start_index,
            executed_tool_names=state.get("executed_tool_names"),
        )
        missing_required_tools = self._missing_required_tool_names(
            decision=state.get("tool_gate_decision"),
            match_result=state.get("tool_match_result"),
            tool_call_summaries=tool_call_summaries,
            available_tools=state.get("available_tools"),
            final_messages=final_messages,
            run_output_start_index=persist_run_output_start_index,
        )

        should_fail_for_missing_evidence = tool_execution_required and (
            not tool_required_has_real_execution or bool(missing_required_tools)
        )
        should_block_assistant_emit = should_fail_for_missing_evidence

        if model_stream_timed_out and not final_assistant.strip():
            failure_message = (
                model_timeout_error_message
                or "The model stream timed out before producing a usable response."
            )
            safe_messages = self._sanitize_turn_messages_for_persistence(
                messages=final_messages,
                start_index=persist_run_output_start_index,
                final_assistant="",
                clear_tool_planning_text=tool_execution_required,
            )
            await session_manager.persist_transcript(session_key, safe_messages)
            await self.runtime_events.trigger_run_context_ready(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
                system_prompt=system_prompt,
                message_history=state.get("context_history_for_hooks") or [],
                assistant_message="",
                tool_calls=tool_call_summaries,
                run_status="failed",
                error=failure_message,
                session_title=state.get("session_title"),
            )
            state["run_failed"] = True
            await self.runtime_events.trigger_llm_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            await self.runtime_events.trigger_run_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            yield StreamEvent.runtime_update(
                "failed",
                failure_message,
                metadata={
                    "phase": "final",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            yield StreamEvent.error_event(failure_message)
            buffered_assistant_events.clear()
            state["should_stop"] = True
            return

        assistant_output_streamed = bool(state.get("assistant_output_streamed"))
        allow_agent_result_fallback = not tool_execution_required
        if not assistant_output_streamed and not should_block_assistant_emit:
            if (
                allow_agent_result_fallback
                and not final_assistant
                and hasattr(agent_run, "result")
                and agent_run.result
            ):
                result = agent_run.result
                if hasattr(result, "response") and result.response:
                    response = result.response
                    if hasattr(response, "parts"):
                        for part in response.parts:
                            part_kind = getattr(part, "part_kind", "")
                            if part_kind != "thinking" and hasattr(part, "content") and part.content:
                                content = str(part.content)
                                if content:
                                    final_assistant = content
                                    break
                    elif hasattr(response, "content") and response.content:
                        final_assistant = str(response.content)
                if not final_assistant and hasattr(result, "data") and result.data:
                    final_assistant = str(result.data)

            if not final_assistant:
                final_assistant = self._extract_latest_assistant_from_messages(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                )

        persist_messages = self._sanitize_turn_messages_for_persistence(
            messages=final_messages,
            start_index=persist_run_output_start_index,
            final_assistant=final_assistant,
            clear_tool_planning_text=tool_execution_required,
        )

        if not assistant_output_streamed and final_assistant and not should_block_assistant_emit:
            state.get("thinking_emitter").assistant_emitted = True
            assistant_output_streamed = True
            yield StreamEvent.assistant_delta(final_assistant)

        if should_fail_for_missing_evidence:
            repeated_tool_failure = state.get("repeated_tool_failure")
            failure_message = (
                "This turn required real tool execution, but the model did not execute any real tool calls."
            )
            if missing_required_tools:
                build_required_message = getattr(self, "_build_tool_evidence_required_message", None)
                if callable(build_required_message):
                    failure_message = build_required_message(
                        match_result=state.get("tool_match_result"),
                        missing_required_tools=missing_required_tools,
                    )
                else:
                    failure_message = (
                        "A grounded tool-backed answer is required for this request, but required tools did not "
                        f"succeed: {', '.join(missing_required_tools)}."
                    )
            if isinstance(repeated_tool_failure, dict):
                repeated_tool_name = str(repeated_tool_failure.get("tool_name", "") or "").strip()
                repeated_error = str(repeated_tool_failure.get("error", "") or "").strip()
                if repeated_tool_name and repeated_error:
                    failure_message = (
                        f"Tool execution repeated the same failure for {repeated_tool_name}: "
                        f"{repeated_error}"
                    )
            preferred_tools = []
            if isinstance(getattr(deps, "extra", None), dict):
                preferred_tools = list(deps.extra.get("tool_policy", {}).get("preferred_tools", []) or [])
            tool_execution_retried = False
            async for retry_event in self._retry_after_missing_tool_execution(
                session_key=session_key,
                user_message=user_message,
                deps=deps,
                release_slot=release_slot,
                selected_token_id=selected_token_id,
                start_time=start_time,
                max_tool_calls=max_tool_calls,
                timeout_seconds=timeout_seconds,
                token_failover_attempt=token_failover_attempt,
                emit_lifecycle_bounds=emit_lifecycle_bounds,
                failure_message=failure_message,
                preferred_tools=preferred_tools,
                tool_execution_retry_count=tool_execution_retry_count,
                allow_retry=True,
            ):
                tool_execution_retried = True
                yield retry_event
            if tool_execution_retried:
                state["release_slot"] = None
                state["selected_token_id"] = None
                state["should_stop"] = True
                return

            safe_messages = self._sanitize_turn_messages_for_persistence(
                messages=final_messages,
                start_index=persist_run_output_start_index,
                final_assistant="",
                clear_tool_planning_text=tool_execution_required,
            )
            await session_manager.persist_transcript(session_key, safe_messages)
            await self.runtime_events.trigger_run_context_ready(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
                system_prompt=system_prompt,
                message_history=state.get("context_history_for_hooks") or [],
                assistant_message="",
                tool_calls=tool_call_summaries,
                run_status="failed",
                error=failure_message,
                session_title=state.get("session_title"),
            )
            state["run_failed"] = True
            await self.runtime_events.trigger_llm_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            await self.runtime_events.trigger_run_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            yield StreamEvent.runtime_update(
                "failed",
                failure_message,
                metadata={
                    "phase": "final",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            yield StreamEvent.error_event(failure_message)
            buffered_assistant_events.clear()
            final_assistant = ""

        else:
            if not final_assistant.strip():
                if tool_required_has_real_execution:
                    tool_only_answer = self._build_tool_only_markdown_answer_from_messages(
                        messages=final_messages,
                        start_index=persist_run_output_start_index,
                    )
                    if tool_only_answer:
                        final_assistant = tool_only_answer
                        persist_messages = self._sanitize_turn_messages_for_persistence(
                            messages=final_messages,
                            start_index=persist_run_output_start_index,
                            final_assistant=final_assistant,
                            clear_tool_planning_text=tool_execution_required,
                        )
                        if not assistant_output_streamed:
                            state.get("thinking_emitter").assistant_emitted = True
                            assistant_output_streamed = True
                            yield StreamEvent.assistant_delta(final_assistant)
            if not final_assistant.strip():
                state["run_failed"] = True
                failure_message = (
                    model_timeout_error_message
                    if model_stream_timed_out and model_timeout_error_message
                    else "The run ended without a usable final answer."
                )
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                await self.runtime_events.trigger_run_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                safe_messages = self._sanitize_turn_messages_for_persistence(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                    final_assistant="",
                    clear_tool_planning_text=tool_execution_required,
                )
                await session_manager.persist_transcript(session_key, safe_messages)
                await self.runtime_events.trigger_run_context_ready(
                    session_key=session_key,
                    run_id=run_id,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=state.get("context_history_for_hooks") or [],
                    assistant_message="",
                    tool_calls=tool_call_summaries,
                    run_status="failed",
                    error=failure_message,
                    session_title=state.get("session_title"),
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={
                        "phase": "final",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                buffered_assistant_events.clear()
                final_assistant = ""
            else:
                answered_elapsed = round(time.monotonic() - start_time, 1)
                yield StreamEvent.runtime_update(
                    "answered",
                    "Final answer ready.",
                    metadata={
                        "phase": "final",
                        "attempt": current_model_attempt,
                        "elapsed": answered_elapsed,
                    },
                )
                state["answer_committed"] = True

                async def _run_post_success_side_effects() -> None:
                    _log_step("post_success_background_side_effects_start")
                    llm_completed_task = asyncio.create_task(
                        self.runtime_events.trigger_llm_completed(
                            session_key=session_key,
                            run_id=run_id,
                            assistant_message=final_assistant,
                        )
                    )
                    persist_transcript_task = asyncio.create_task(
                        session_manager.persist_transcript(session_key, persist_messages)
                    )
                    llm_completed_result, persist_result = await asyncio.gather(
                        llm_completed_task,
                        persist_transcript_task,
                        return_exceptions=True,
                    )
                    if isinstance(llm_completed_result, Exception):
                        logger.exception("post_success_llm_completed failed")
                        _log_step("post_success_llm_completed_error", error=str(llm_completed_result))
                    else:
                        _log_step("post_success_llm_completed_done")
                    if isinstance(persist_result, Exception):
                        logger.exception("post_success_persist_transcript failed")
                        _log_step("post_success_persist_transcript_error", error=str(persist_result))
                    else:
                        _log_step("post_success_persist_transcript_done")

                    _log_step("post_success_finalize_title_start")
                    try:
                        await self._maybe_finalize_title(
                            session_manager=session_manager,
                            session_key=session_key,
                            session=session,
                            final_messages=final_messages,
                            user_message=user_message,
                        )
                        _log_step("post_success_finalize_title_done")
                        state["session_title"] = str(getattr(session, "title", "") or "")
                    except Exception as exc:
                        logger.exception("post_success_finalize_title failed")
                        _log_step("post_success_finalize_title_error", error=str(exc))

                    _log_step("post_success_run_context_ready_start")
                    try:
                        await self.runtime_events.trigger_run_context_ready(
                            session_key=session_key,
                            run_id=run_id,
                            user_message=user_message,
                            system_prompt=system_prompt,
                            message_history=state.get("context_history_for_hooks") or [],
                            assistant_message=final_assistant,
                            tool_calls=tool_call_summaries,
                            run_status="completed",
                            session_title=state.get("session_title"),
                        )
                        _log_step("post_success_run_context_ready_done")
                    except Exception as exc:
                        logger.exception("post_success_run_context_ready failed")
                        _log_step("post_success_run_context_ready_error", error=str(exc))

                self._schedule_background_post_success_task(
                    asyncio.create_task(_run_post_success_side_effects())
                )

        state["assistant_output_streamed"] = assistant_output_streamed
        state["final_assistant"] = final_assistant
        state["tool_call_summaries"] = tool_call_summaries
        state["buffered_assistant_events"] = buffered_assistant_events
        state["message_history"] = persist_messages

