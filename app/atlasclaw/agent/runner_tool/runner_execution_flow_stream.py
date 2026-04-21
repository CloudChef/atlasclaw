# Copyright 2021  Qianyun, Inc. All rights reserved.


from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from app.atlasclaw.agent.context_pruning import prune_context_messages, should_apply_context_pruning
from app.atlasclaw.agent.runner_tool.runner_llm_routing import (
    messages_satisfy_artifact_goal,
    selected_capability_ids_from_intent_plan,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import normalize_tool_result_mode
from app.atlasclaw.agent.runner_tool.runner_tool_messages import (
    extract_synthetic_tool_messages_from_next_node,
    merge_synthetic_tool_messages,
    overlay_synthetic_tool_messages,
)
from app.atlasclaw.agent.runner_tool.runner_tool_projection import turn_action_requires_tool_execution
from app.atlasclaw.agent.stream import StreamEvent


class RunnerExecutionFlowStreamMixin:
    async def _run_agent_node_stream(
        self,
        *,
        agent_run: Any,
        state: dict[str, Any],
        _log_step: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream model/tool nodes and update run state in-place."""
        deps = state.get("deps")
        start_time = float(state.get("start_time") or 0.0)
        session = state.get("session")
        session_key = state.get("session_key")
        session_manager = state.get("session_manager")
        run_id = state.get("run_id")
        user_message = state.get("user_message")
        system_prompt = state.get("system_prompt")
        max_tool_calls = int(state.get("max_tool_calls") or 0)
        runtime_context_window = state.get("runtime_context_window")
        flushed_memory_signatures = state.get("flushed_memory_signatures")
        session_message_history = list(state.get("session_message_history") or [])
        runtime_base_history_len = int(state.get("runtime_base_history_len") or 0)
        persist_run_output_start_index = int(state.get("persist_run_output_start_index") or 0)
        synthetic_tool_messages = list(state.get("synthetic_tool_messages") or [])
        first_node_seen = False
        first_node_wait_tick = 0

        _log_step("agent_first_node_wait_start")
        yield StreamEvent.runtime_update(
            "reasoning",
            "Waiting for model tool decision.",
            metadata={
                "phase": "agent_first_node_wait",
                "elapsed": round(time.monotonic() - start_time, 1),
            },
        )
        node_iterator = self._iter_agent_nodes(agent_run).__aiter__()
        first_node: Any | None = None
        first_node_task = asyncio.create_task(node_iterator.__anext__())
        while not first_node_seen:
            done, _ = await asyncio.wait({first_node_task}, timeout=5.0)
            if first_node_task not in done:
                first_node_wait_tick += 1
                yield StreamEvent.runtime_update(
                    "reasoning",
                    "Still waiting for model tool decision."
                    if first_node_wait_tick >= 1
                    else "Waiting for model tool decision.",
                    metadata={
                        "phase": (
                            "agent_first_node_wait_progress"
                            if first_node_wait_tick >= 1
                            else "agent_first_node_wait"
                        ),
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                continue

            try:
                first_node = first_node_task.result()
                first_node_seen = True
                _log_step(
                    "agent_first_node_wait_done",
                    node_type=type(first_node).__name__,
                )
            except StopAsyncIteration:
                break

        if not first_node_seen and not first_node_task.done():
            first_node_task.cancel()

        if not first_node_seen:
            return

        async def _iter_nodes_with_first() -> AsyncIterator[Any]:
            if first_node is not None:
                yield first_node
            async for next_node in node_iterator:
                yield next_node

        async for node in _iter_nodes_with_first():
            if deps.is_aborted():
                yield StreamEvent.lifecycle_aborted()
                break

            runtime_current_messages = self.history.normalize_messages(agent_run.all_messages())
            runtime_current_messages = self.history.prune_summary_messages(runtime_current_messages)
            if should_apply_context_pruning(
                settings=self.context_pruning_settings,
                session=session,
            ):
                runtime_current_messages = prune_context_messages(
                    messages=runtime_current_messages,
                    settings=self.context_pruning_settings,
                    context_window_tokens=runtime_context_window,
                )
            runtime_current_messages = self._deduplicate_message_history(runtime_current_messages)
            merged_current_messages = self._merge_runtime_messages_with_session_prefix(
                session_message_history=session_message_history,
                runtime_messages=runtime_current_messages,
                runtime_base_history_len=runtime_base_history_len,
            )
            state["latest_runtime_messages"] = list(runtime_current_messages)
            state["latest_agent_messages"] = list(merged_current_messages)
            state["context_history_for_hooks"] = list(merged_current_messages)

            if self.compaction.should_memory_flush(
                merged_current_messages,
                session,
                context_window_override=runtime_context_window,
            ):
                await self.history.flush_history_to_timestamped_memory(
                    session_key=session_key,
                    messages=merged_current_messages,
                    deps=deps,
                    session=session,
                    context_window=runtime_context_window,
                    flushed_signatures=flushed_memory_signatures,
                )

            if self.compaction.should_compact(
                merged_current_messages,
                session,
                context_window_override=runtime_context_window,
            ):
                if self.hooks:
                    await self.hooks.trigger(
                        "before_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(merged_current_messages),
                        },
                    )
                yield StreamEvent.compaction_start()
                compressed = await self.compaction.compact(merged_current_messages, session)
                persist_override_messages = self.history.normalize_messages(compressed)
                persist_override_messages = await self.history.inject_memory_recall(
                    persist_override_messages,
                    deps,
                )
                state["context_history_for_hooks"] = list(persist_override_messages)
                state["persist_override_messages"] = persist_override_messages
                state["persist_override_base_len"] = len(merged_current_messages)
                await session_manager.mark_compacted(session_key)
                state["compaction_applied"] = True
                yield StreamEvent.compaction_end()
                if self.hooks:
                    await self.hooks.trigger(
                        "after_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(persist_override_messages),
                        },
                    )

            if self._is_model_request_node(node):
                current_model_attempt = int(state.get("current_model_attempt") or 0) + 1
                state["current_model_attempt"] = current_model_attempt
                state["current_attempt_started_at"] = time.monotonic()
                state["current_attempt_has_text"] = False
                state["current_attempt_has_tool"] = False
                thinking_emitter = state.get("thinking_emitter")
                thinking_emitter.reset_cycle_flags()
                prior_tool_call_summaries = list(state.get("tool_call_summaries") or [])
                selected_capability_ids = selected_capability_ids_from_intent_plan(
                    state.get("tool_intent_plan")
                )
                loop_message, loop_reason = self._describe_llm_reentry(
                    attempt=current_model_attempt,
                    tool_call_summaries=prior_tool_call_summaries,
                )
                loop_metadata = {
                    "phase": "model_request",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                    "loop_index": current_model_attempt,
                    "loop_reason": loop_reason,
                    "selected_capability_ids": list(selected_capability_ids),
                }
                if prior_tool_call_summaries:
                    loop_metadata["tool_result_count"] = len(prior_tool_call_summaries)
                _log_step(
                    "llm_input_dispatch_start",
                    attempt=current_model_attempt,
                    loop_index=current_model_attempt,
                    loop_reason=loop_reason,
                )
                await self.runtime_events.trigger_llm_input(
                    session_key=session_key,
                    run_id=run_id,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=runtime_current_messages,
                    loop_index=current_model_attempt,
                    loop_reason=loop_reason,
                    selected_capability_ids=selected_capability_ids,
                )
                _log_step(
                    "llm_input_dispatch_done",
                    attempt=current_model_attempt,
                    loop_index=current_model_attempt,
                    loop_reason=loop_reason,
                )
                payload_profile = self._build_llm_payload_profile(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    message_history=runtime_current_messages,
                )
                _log_step(
                    "llm_payload_profile",
                    stage=f"attempt_{current_model_attempt}",
                    attempt=current_model_attempt,
                    loop_index=current_model_attempt,
                    loop_reason=loop_reason,
                    **payload_profile,
                )
                if isinstance(deps.extra, dict):
                    existing_profiles = deps.extra.get("_llm_payload_profiles")
                    entry = {
                        "stage": f"attempt_{current_model_attempt}",
                        "attempt": current_model_attempt,
                        "loop_index": current_model_attempt,
                        "loop_reason": loop_reason,
                        **payload_profile,
                    }
                    if isinstance(existing_profiles, list):
                        existing_profiles.append(entry)
                    else:
                        deps.extra["_llm_payload_profiles"] = [entry]
                yield StreamEvent.runtime_update(
                    "reasoning",
                    loop_message,
                    metadata=loop_metadata,
                )

            thinking_emitter = state.get("thinking_emitter")
            tool_intent_plan = state.get("tool_intent_plan")
            tool_execution_required = bool(state.get("tool_execution_required")) or turn_action_requires_tool_execution(
                tool_intent_plan
            )
            tool_calls_in_node = self.runtime_events.collect_tool_calls(node)
            plaintext_tool_calls_in_node = []
            if not tool_calls_in_node:
                plaintext_tool_calls_in_node = self.runtime_events.collect_plaintext_tool_calls(node)
            buffer_assistant_output = bool(
                tool_execution_required
                or tool_calls_in_node
                or plaintext_tool_calls_in_node
                or state.get("buffer_direct_answer_output")
            )
            tool_call_summaries = state.get("tool_call_summaries") or []
            if hasattr(node, "model_response") and node.model_response:
                async for event in thinking_emitter.emit_from_model_response(
                    model_response=node.model_response,
                    hooks=self.hooks,
                    session_key=session_key,
                ):
                    if event.type == "assistant" and buffer_assistant_output:
                        state.get("buffered_assistant_events").append(event)
                        state["current_attempt_has_text"] = True
                    else:
                        if event.type == "assistant":
                            state["current_attempt_has_text"] = True
                            state["assistant_output_streamed"] = True
                        yield event
            elif hasattr(node, "content") and node.content:
                content = str(node.content)
                async for event in thinking_emitter.emit_plain_content(
                    content=content,
                    hooks=self.hooks,
                    session_key=session_key,
                ):
                    if event.type == "assistant" and buffer_assistant_output:
                        state.get("buffered_assistant_events").append(event)
                        state["current_attempt_has_text"] = True
                    else:
                        if event.type == "assistant":
                            state["current_attempt_has_text"] = True
                            state["assistant_output_streamed"] = True
                        yield event

            for tool_call in tool_calls_in_node:
                if isinstance(tool_call, dict):
                    tool_name = tool_call.get("name", tool_call.get("tool_name", "unknown_tool"))
                    raw_args = tool_call.get("args", tool_call.get("arguments"))
                else:
                    tool_name = getattr(tool_call, "tool_name", getattr(tool_call, "name", "unknown_tool"))
                    raw_args = getattr(tool_call, "args", getattr(tool_call, "arguments", None))
                normalized_tool_name = str(tool_name)
                parsed_args = self._extract_tool_call_arguments(raw_args)
                summary: dict[str, Any] = {"name": normalized_tool_name}
                if parsed_args:
                    summary["args"] = parsed_args
                tool_call_summaries.append(summary)
            for tool_call in plaintext_tool_calls_in_node:
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(tool_call.get("name", "") or tool_call.get("tool_name", "")).strip()
                if not tool_name:
                    continue
                parsed_args = self._extract_tool_call_arguments(
                    tool_call.get("args", tool_call.get("arguments"))
                )
                summary = {"name": tool_name}
                if parsed_args:
                    summary["args"] = parsed_args
                tool_call_summaries.append(summary)
            state["tool_call_summaries"] = tool_call_summaries

            if plaintext_tool_calls_in_node and not tool_calls_in_node:
                state["current_attempt_has_tool"] = True
                state["plaintext_tool_call_attempt"] = True
                state["plaintext_tool_call_summaries"] = list(plaintext_tool_calls_in_node)
                yield StreamEvent.runtime_update(
                    "warning",
                    "Model returned plaintext tool-call markup. Recovering with structured tool execution.",
                    metadata={
                        "phase": "plaintext_tool_call_attempt",
                        "attempt": state.get("current_model_attempt"),
                        "elapsed": round(time.monotonic() - start_time, 1),
                        "tools": [
                            str(item.get("name", "") or item.get("tool_name", "")).strip()
                            for item in plaintext_tool_calls_in_node
                            if isinstance(item, dict)
                        ],
                    },
                )
                break

            if tool_calls_in_node:
                current_node_tool_names = [
                    name
                    for name in (
                        self._normalize_tool_call_name(tool_call)
                        for tool_call in tool_calls_in_node
                    )
                    if name
                ]
                tool_result_count_before_dispatch = self._count_tool_result_records(
                    messages=state.get("latest_agent_messages") or merged_current_messages,
                    start_index=persist_run_output_start_index,
                    target_tool_names=current_node_tool_names,
                )
                repeat_limit = int(
                    (
                        deps.extra.get("tool_policy", {}).get("max_same_tool_calls_per_turn", 0)
                        if isinstance(getattr(deps, "extra", None), dict)
                        else 0
                    )
                    or 0
                )
                repeated_tool_names = self._collect_repeated_tool_names(
                    planned_tool_calls=tool_calls_in_node,
                    executed_tool_names=list(state.get("executed_tool_names") or []),
                    repeat_limit=repeat_limit,
                )
                if repeated_tool_names:
                    repeated_tool_name = repeated_tool_names[0]
                    state["repeated_tool_loop"] = {
                        "tool_name": repeated_tool_name,
                        "count": len(
                            [
                                name
                                for name in list(state.get("executed_tool_names") or [])
                                if str(name or "").strip() == repeated_tool_name
                            ]
                        ),
                        "limit": repeat_limit,
                    }
                    yield StreamEvent.runtime_update(
                        "warning",
                        (
                            f"Stopping repeated tool loop for {repeated_tool_name}. "
                            "The current tool evidence did not converge after repeated calls."
                        ),
                        metadata={
                            "phase": "tool_repeat_limit",
                            "attempt": state.get("current_model_attempt"),
                            "elapsed": round(time.monotonic() - start_time, 1),
                            "tool_name": repeated_tool_name,
                            "repeat_limit": repeat_limit,
                        },
                    )
                    break
                state["current_attempt_has_tool"] = True
                yield StreamEvent.runtime_update(
                    "waiting_for_tool",
                    "Preparing tool execution.",
                    metadata={
                        "phase": "planned",
                        "attempt": state.get("current_model_attempt"),
                        "elapsed": round(time.monotonic() - start_time, 1),
                        "tools": [
                            (
                                tool_call.get("name", tool_call.get("tool_name", "unknown_tool"))
                                if isinstance(tool_call, dict)
                                else getattr(
                                    tool_call,
                                    "tool_name",
                                    getattr(tool_call, "name", "unknown_tool"),
                                )
                            )
                            for tool_call in tool_calls_in_node
                        ],
                    },
                )

            tool_dispatch = await self.runtime_events.dispatch_tool_calls(
                tool_calls_in_node,
                tool_calls_count=int(state.get("tool_calls_count") or 0),
                max_tool_calls=max_tool_calls,
                deps=deps,
                session_key=session_key,
                run_id=run_id,
            )
            state["tool_calls_count"] = tool_dispatch.tool_calls_count
            executed_tool_names = list(state.get("executed_tool_names") or [])
            for event in tool_dispatch.events:
                if event.type == "tool" and event.phase == "end" and str(event.tool or "").strip():
                    executed_tool_names.append(str(event.tool).strip())
            if executed_tool_names:
                state["executed_tool_names"] = executed_tool_names
            for event in tool_dispatch.events:
                if event.type == "assistant":
                    state["assistant_output_streamed"] = True
                yield event

            if tool_calls_in_node:
                next_node = getattr(node, "_atlas_next_node", None)
                synthetic_tool_messages = merge_synthetic_tool_messages(
                    existing=synthetic_tool_messages,
                    new_messages=extract_synthetic_tool_messages_from_next_node(
                        history=self.history,
                        next_node=next_node,
                    ),
                )
                state["synthetic_tool_messages"] = list(synthetic_tool_messages)
                (
                    latest_runtime_messages,
                    latest_messages,
                ) = await self._refresh_messages_after_tool_dispatch(
                    agent_run=agent_run,
                    session_message_history=session_message_history,
                    runtime_base_history_len=runtime_base_history_len,
                    start_index=persist_run_output_start_index,
                    target_tool_names=current_node_tool_names,
                    previous_result_count=tool_result_count_before_dispatch,
                    synthetic_tool_messages=synthetic_tool_messages,
                )
                state["latest_runtime_messages"] = list(latest_runtime_messages)
                state["latest_agent_messages"] = list(latest_messages)
                state["message_history"] = list(latest_messages)
                repeated_failure = self._detect_repeated_tool_failure(
                    messages=latest_messages,
                    start_index=persist_run_output_start_index,
                    threshold=max(
                        2,
                        int(getattr(self, "REPEATED_TOOL_FAILURE_THRESHOLD", 2) or 2),
                    ),
                )
                if repeated_failure is not None:
                    state["repeated_tool_failure"] = repeated_failure
                    yield StreamEvent.runtime_update(
                        "warning",
                        (
                            "Repeated tool failure detected for "
                            f"{repeated_failure['tool_name']}. Stopping this loop "
                            "to avoid ungrounded retries."
                        ),
                        metadata={
                            "phase": "tool_failure_repeat",
                            "attempt": state.get("current_model_attempt"),
                            "elapsed": round(time.monotonic() - start_time, 1),
                            "tool_name": repeated_failure["tool_name"],
                            "error": repeated_failure["error"],
                            "count": repeated_failure["count"],
                        },
                    )
                    break
                repeated_no_progress = self._detect_repeated_tool_no_progress(
                    messages=latest_messages,
                    start_index=persist_run_output_start_index,
                    target_tool_names=current_node_tool_names,
                    threshold=2,
                )
                if repeated_no_progress is not None:
                    state["repeated_tool_no_progress"] = repeated_no_progress
                    yield StreamEvent.runtime_update(
                        "warning",
                        (
                            f"Stopping repeated tool loop for {repeated_no_progress['tool_name']}. "
                            "The latest tool execution did not add new evidence."
                        ),
                        metadata={
                            "phase": "tool_no_progress",
                            "attempt": state.get("current_model_attempt"),
                            "elapsed": round(time.monotonic() - start_time, 1),
                            "tool_name": repeated_no_progress["tool_name"],
                            "count": repeated_no_progress["count"],
                        },
                    )
                    break
                if self._should_finalize_from_tool_results(
                    messages=latest_messages,
                    start_index=persist_run_output_start_index,
                    planned_tool_names=current_node_tool_names,
                    available_tools=list(state.get("available_tools") or []),
                    artifact_goal=state.get("artifact_goal"),
                ):
                    state["force_tool_only_finalize"] = True
                    yield StreamEvent.runtime_update(
                        "reasoning",
                        "Structured tool results are sufficient. Finalizing directly from tool output.",
                        metadata={
                            "phase": "tool_only_finalize",
                            "attempt": state.get("current_model_attempt"),
                            "elapsed": round(time.monotonic() - start_time, 1),
                            "tools": current_node_tool_names,
                        },
                    )
                    break
                yield StreamEvent.runtime_update(
                    "reasoning",
                    "Tool results received. Continuing reasoning with tool evidence.",
                    metadata={
                        "phase": "tool_result_continue",
                        "attempt": state.get("current_model_attempt"),
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )

            if (
                self._is_call_tools_node(node)
                and not state.get("current_attempt_has_text")
                and not state.get("current_attempt_has_tool")
                and thinking_emitter.current_cycle_had_thinking
            ):
                elapsed_total = round(time.monotonic() - start_time, 1)
                current_attempt_started_at = state.get("current_attempt_started_at")
                attempt_elapsed = (
                    round(time.monotonic() - current_attempt_started_at, 1)
                    if current_attempt_started_at is not None
                    else elapsed_total
                )
                reasoning_retry_count = int(state.get("reasoning_retry_count") or 0)
                reasoning_retry_limit = int(state.get("reasoning_retry_limit") or 0)
                should_escalate = (
                    elapsed_total >= self.REASONING_ONLY_ESCALATION_SECONDS
                    or reasoning_retry_count >= reasoning_retry_limit
                )
                if should_escalate:
                    if tool_execution_required:
                        yield StreamEvent.runtime_update(
                            "warning",
                            "This turn required a real tool call, but the model ended the cycle without executing one.",
                            metadata={
                                "phase": "tool_execution",
                                "attempt": state.get("current_model_attempt"),
                                "elapsed": elapsed_total,
                                "attempt_elapsed": attempt_elapsed,
                            },
                        )
                        break
                    raise RuntimeError(
                        "The model did not produce a usable answer after bounded reasoning retries."
                    )

                reasoning_retry_count += 1
                state["reasoning_retry_count"] = reasoning_retry_count
                yield StreamEvent.runtime_update(
                    "retrying",
                    "Reasoning finished without a usable answer. Retrying with a stricter response policy.",
                    metadata={
                        "phase": "retry",
                        "attempt": reasoning_retry_count,
                        "elapsed": elapsed_total,
                        "attempt_elapsed": attempt_elapsed,
                        "reason": "reasoning_only",
                    },
                )
                if tool_dispatch.should_break:
                    break

    @staticmethod
    def _detect_repeated_tool_failure(
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        threshold: int,
    ) -> dict[str, Any] | None:
        counts: dict[tuple[str, str], int] = {}
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role not in {"tool", "toolresult", "tool_result"}:
                continue
            tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
            if not tool_name:
                continue
            error_signature = RunnerExecutionFlowStreamMixin._extract_tool_error_signature(
                message.get("content")
            )
            if not error_signature:
                continue
            key = (tool_name, error_signature)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] >= threshold:
                return {
                    "tool_name": tool_name,
                    "error": error_signature,
                    "count": counts[key],
                }
        return None

    @staticmethod
    def _collect_repeated_tool_names(
        *,
        planned_tool_calls: list[Any],
        executed_tool_names: list[str],
        repeat_limit: int,
    ) -> list[str]:
        if repeat_limit <= 0:
            return []
        prior_counts: dict[str, int] = {}
        for name in executed_tool_names or []:
            normalized = str(name or "").strip()
            if not normalized:
                continue
            prior_counts[normalized] = prior_counts.get(normalized, 0) + 1
        exceeded: list[str] = []
        for tool_call in planned_tool_calls or []:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name", tool_call.get("tool_name", ""))
            else:
                tool_name = getattr(tool_call, "tool_name", getattr(tool_call, "name", ""))
            normalized_name = str(tool_name or "").strip()
            if not normalized_name:
                continue
            prior_counts[normalized_name] = prior_counts.get(normalized_name, 0) + 1
            if prior_counts[normalized_name] > repeat_limit and normalized_name not in exceeded:
                exceeded.append(normalized_name)
        return exceeded

    @staticmethod
    def _normalize_tool_call_name(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("name", "") or tool_call.get("tool_name", "")).strip()
        return str(getattr(tool_call, "tool_name", getattr(tool_call, "name", "")) or "").strip()

    def _detect_repeated_tool_no_progress(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        target_tool_names: list[str],
        threshold: int,
    ) -> dict[str, Any] | None:
        if threshold <= 1:
            return None
        target_names = {str(name).strip() for name in target_tool_names if str(name).strip()}
        if not target_names:
            return None
        signatures: dict[str, list[str]] = {}
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
                if tool_name in target_names:
                    signature = self._build_tool_result_progress_signature(message.get("content"))
                    if signature:
                        signatures.setdefault(tool_name, []).append(signature)
            tool_results = message.get("tool_results")
            if not isinstance(tool_results, list):
                continue
            for result in tool_results:
                if not isinstance(result, dict):
                    continue
                tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
                if tool_name not in target_names:
                    continue
                signature = self._build_tool_result_progress_signature(
                    result.get("content", result)
                )
                if not signature:
                    continue
                signatures.setdefault(tool_name, []).append(signature)

        for tool_name in target_tool_names:
            normalized_tool_name = str(tool_name).strip()
            if not normalized_tool_name:
                continue
            tool_signatures = signatures.get(normalized_tool_name, [])
            if len(tool_signatures) < threshold:
                continue
            trailing = tool_signatures[-threshold:]
            if len(set(trailing)) == 1:
                return {
                    "tool_name": normalized_tool_name,
                    "count": len(tool_signatures),
                    "signature": trailing[-1],
                }
        return None

    def _should_finalize_from_tool_results(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        planned_tool_names: list[str],
        available_tools: list[dict[str, Any]],
        artifact_goal: dict[str, Any] | None = None,
    ) -> bool:
        normalized_planned = [
            str(name).strip()
            for name in planned_tool_names
            if str(name).strip()
        ]
        if not normalized_planned:
            return False
        if artifact_goal and not messages_satisfy_artifact_goal(
            messages=messages,
            start_index=start_index,
            target_tool_names=normalized_planned,
            artifact_goal=artifact_goal,
        ):
            return False
        tool_index = {
            str(tool.get("name", "") or "").strip(): tool
            for tool in available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        if self._tool_results_are_terminal_no_evidence(
            messages=messages,
            start_index=start_index,
            target_tool_names=normalized_planned,
        ):
            repeated_no_progress = self._detect_repeated_tool_no_progress(
                messages=messages,
                start_index=start_index,
                target_tool_names=normalized_planned,
                threshold=2,
            )
            return repeated_no_progress is not None
        for tool_name in normalized_planned:
            tool_meta = tool_index.get(tool_name)
            result_mode = normalize_tool_result_mode(tool_meta or {})
            if result_mode != "tool_only_ok":
                return False
        successful_tool_names = set()
        collect_successful = getattr(self, "_collect_successful_tool_names", None)
        if callable(collect_successful):
            successful_tool_names = set(
                collect_successful(
                    messages=messages,
                    start_index=start_index,
                    available_tools=available_tools,
                )
                or set()
            )
        return all(tool_name in successful_tool_names for tool_name in normalized_planned)

    def _tool_results_are_terminal_no_evidence(
        self,
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        target_tool_names: list[str],
    ) -> bool:
        target_names = {str(name).strip() for name in target_tool_names if str(name).strip()}
        if not target_names:
            return False

        latest_payloads: dict[str, Any] = {}
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
                if tool_name in target_names:
                    latest_payloads[tool_name] = self._normalize_tool_result_progress_payload(
                        message.get("content")
                    )
            tool_results = message.get("tool_results")
            if not isinstance(tool_results, list):
                continue
            for result in tool_results:
                if not isinstance(result, dict):
                    continue
                tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
                if tool_name not in target_names:
                    continue
                latest_payloads[tool_name] = self._normalize_tool_result_progress_payload(
                    result.get("content", result)
                )

        if len(latest_payloads) != len(target_names):
            return False

        for payload in latest_payloads.values():
            if not isinstance(payload, dict):
                return False
            if str(payload.get("outcome", "") or "").strip().lower() != "no_results":
                return False
            if bool(payload.get("is_error")):
                return False
        return True

    async def _refresh_messages_after_tool_dispatch(
        self,
        *,
        agent_run: Any,
        session_message_history: list[dict[str, Any]],
        runtime_base_history_len: int,
        start_index: int,
        target_tool_names: list[str],
        previous_result_count: int,
        synthetic_tool_messages: list[dict[str, Any]] | None = None,
        max_attempts: int = 6,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        latest_runtime_messages: list[dict[str, Any]] = []
        latest_messages: list[dict[str, Any]] = []
        safe_attempts = max(1, int(max_attempts or 0))
        for attempt in range(safe_attempts):
            latest_runtime_messages = self.history.normalize_messages(agent_run.all_messages())
            latest_runtime_messages = self.history.prune_summary_messages(latest_runtime_messages)
            latest_runtime_messages = self._deduplicate_message_history(latest_runtime_messages)
            latest_messages = self._merge_runtime_messages_with_session_prefix(
                session_message_history=session_message_history,
                runtime_messages=latest_runtime_messages,
                runtime_base_history_len=runtime_base_history_len,
            )
            latest_messages = overlay_synthetic_tool_messages(
                messages=latest_messages,
                synthetic_tool_messages=list(synthetic_tool_messages or []),
                start_index=start_index,
            )
            refreshed_result_count = self._count_tool_result_records(
                messages=latest_messages,
                start_index=start_index,
                target_tool_names=target_tool_names,
            )
            if refreshed_result_count > previous_result_count:
                break
            if attempt + 1 < safe_attempts:
                await asyncio.sleep(0.01)
        return latest_runtime_messages, latest_messages

    @staticmethod
    def _count_tool_result_records(
        *,
        messages: list[dict[str, Any]],
        start_index: int,
        target_tool_names: list[str],
    ) -> int:
        target_names = {str(name).strip() for name in target_tool_names if str(name).strip()}
        if not target_names:
            return 0
        count = 0
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip()
                if tool_name in target_names:
                    count += 1
            tool_results = message.get("tool_results")
            if not isinstance(tool_results, list):
                continue
            for result in tool_results:
                if not isinstance(result, dict):
                    continue
                tool_name = str(result.get("tool_name", "") or result.get("name", "")).strip()
                if tool_name in target_names:
                    count += 1
        return count

    def _build_tool_result_progress_signature(self, payload: Any) -> str:
        normalized = self._normalize_tool_result_progress_payload(payload)
        if normalized in (None, "", [], {}):
            return ""
        try:
            return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(normalized)

    def _normalize_tool_result_progress_payload(self, payload: Any) -> Any:
        volatile_keys = {
            "query",
            "provider",
            "requested_url",
            "requested_query",
            "retrieved_at",
            "expanded_queries",
            "cached",
            "elapsed_ms",
            "duration_ms",
            "latency_ms",
            "timing_ms",
        }
        if payload is None:
            return None
        if isinstance(payload, str):
            normalized = payload.strip()
            if not normalized:
                return ""
            if normalized.startswith("{") or normalized.startswith("["):
                return normalized
            try:
                parsed = json.loads(normalized)
            except Exception:
                return normalized
            return self._normalize_tool_result_progress_payload(parsed)
            return normalized
        if isinstance(payload, list):
            return [
                self._normalize_tool_result_progress_payload(item)
                for item in payload[:8]
            ]
        if isinstance(payload, dict):
            details = payload.get("details")
            if (
                isinstance(details, dict)
                and isinstance(details.get("results"), list)
                and not details.get("results")
                and isinstance(details.get("citations"), list)
                and not details.get("citations")
                and not str(details.get("summary", "") or "").strip()
                and not bool(payload.get("is_error"))
            ):
                return {
                    "details": {
                        "results": [],
                        "citations": [],
                        "summary": "",
                    },
                    "is_error": False,
                    "outcome": "no_results",
                }
            normalized: dict[str, Any] = {}
            for key, value in payload.items():
                normalized_key = str(key or "").strip()
                if not normalized_key or normalized_key in volatile_keys:
                    continue
                normalized[normalized_key] = self._normalize_tool_result_progress_payload(value)
            return normalized
        return payload

    @staticmethod
    def _extract_tool_error_signature(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            normalized = payload.strip()
            if not normalized:
                return ""
            if normalized.startswith("{") or normalized.startswith("["):
                try:
                    parsed = json.loads(normalized)
                except Exception:
                    return normalized[:240]
                return RunnerExecutionFlowStreamMixin._extract_tool_error_signature(parsed)
            lowered = normalized.lower()
            if lowered.startswith("[error]") or "missing required" in lowered or "error" in lowered:
                return normalized[:240]
            return ""
        if isinstance(payload, dict):
            if bool(payload.get("is_error")):
                error_text = str(payload.get("error", "") or "").strip()
                if error_text:
                    return error_text[:240]
            error_value = payload.get("error")
            if isinstance(error_value, str) and error_value.strip():
                return error_value.strip()[:240]
            if isinstance(error_value, dict) and error_value:
                return json.dumps(error_value, ensure_ascii=False, sort_keys=True)[:240]
            if isinstance(error_value, list) and error_value:
                return json.dumps(error_value, ensure_ascii=False)[:240]
            if "content" in payload:
                return RunnerExecutionFlowStreamMixin._extract_tool_error_signature(payload.get("content"))
            return ""
        if isinstance(payload, list):
            for item in payload:
                signature = RunnerExecutionFlowStreamMixin._extract_tool_error_signature(item)
                if signature:
                    return signature
            return ""
        return ""

    @staticmethod
    def _describe_llm_reentry(
        *,
        attempt: int,
        tool_call_summaries: list[dict[str, Any]],
    ) -> tuple[str, str]:
        if attempt <= 1:
            return "Analyzing request.", "initial_request"
        if tool_call_summaries:
            return "Re-entering model loop with tool evidence.", "tool_result_continuation"
        return "Re-entering model loop to continue reasoning.", "reasoning_retry"
