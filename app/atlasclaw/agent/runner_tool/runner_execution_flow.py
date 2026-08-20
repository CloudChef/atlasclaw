# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from app.atlasclaw.agent.runner_tool.runner_execution_flow_error import RunnerExecutionFlowErrorMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_post import RunnerExecutionFlowPostMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import RunnerExecutionFlowStreamMixin
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    project_explicit_read_only_tools,
)
from app.atlasclaw.agent.stream import StreamEvent


class RunnerExecutionFlowPhaseMixin(
    RunnerExecutionFlowStreamMixin,
    RunnerExecutionFlowPostMixin,
    RunnerExecutionFlowErrorMixin,
):
    _MAX_SINGLE_CHOICE_CONTINUATIONS = 8

    def _latest_unconsumed_read_only_result_for_single_choice(
        self,
        *,
        state: dict[str, Any],
        messages: list[dict[str, Any]],
        visible_label: str,
    ) -> str | None:
        """Return the latest causal result only when it proves one visible choice."""
        choice_source_names = {
            str(tool.get("name", "") or "").strip()
            for tool in list(state.get("available_tools") or [])
            if isinstance(tool, dict)
            and tool.get("read_only") is True
            and tool.get("auto_select_single_option") is True
            and str(tool.get("name", "") or "").strip()
        }
        executed_names = {
            str(name or "").strip()
            for name in list(state.get("executed_tool_names") or [])
            if str(name or "").strip()
        }
        target_names = choice_source_names & executed_names
        if not target_names:
            return None
        consumed_result_ids = {
            str(item or "").strip()
            for item in list(state.get("consumed_single_choice_result_ids") or [])
            if str(item or "").strip()
        }

        safe_start = max(
            0,
            min(
                int(state.get("persist_run_output_start_index") or 0),
                len(messages),
            ),
        )
        for message_index in range(len(messages) - 1, safe_start - 1, -1):
            message = messages[message_index]
            if not isinstance(message, dict):
                continue
            results: list[tuple[int, dict[str, Any]]] = []
            role = str(message.get("role", "") or "").strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                results.append((0, message))
            embedded = message.get("tool_results")
            if isinstance(embedded, list):
                results.extend(
                    (result_index + 1, item)
                    for result_index, item in enumerate(embedded)
                    if isinstance(item, dict)
                )
            for result_index, result in reversed(results):
                tool_name = str(
                    result.get("tool_name", "") or result.get("name", "") or ""
                ).strip()
                if not tool_name:
                    continue
                if tool_name not in target_names:
                    return None
                payload = result if result.get("is_error") is True else result.get(
                    "content", result
                )
                result_id = str(result.get("tool_call_id", "") or "").strip()
                if not result_id:
                    result_id = f"{message_index}:{result_index}:{tool_name}"
                if result_id in consumed_result_ids:
                    return None
                if self._tool_payload_reports_failure(payload):
                    return None
                if self._single_choice_candidate_identity(
                    payload,
                    visible_label=visible_label,
                ) is None:
                    return None
                return result_id
        return None

    def _single_choice_candidate_identity(
        self,
        payload: Any,
        *,
        visible_label: str,
    ) -> str | None:
        """Bind one visible row to a sole top-level candidate collection."""

        value = payload
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        expected_label = self._normalize_visible_choice_text(visible_label).casefold()
        if not expected_label:
            return None

        top_level_lists: list[list[Any]] = []
        if isinstance(value, list):
            top_level_lists.append(value)
        elif isinstance(value, dict):
            top_level_lists.extend(
                nested
                for key, nested in value.items()
                if str(key).strip() != "_internal" and isinstance(nested, list)
            )
        else:
            return None

        candidate_collections: list[list[Any]] = []
        for collection in top_level_lists:
            if not collection or not all(isinstance(item, dict) for item in collection):
                continue
            if any(
                any(str(item.get(key) or "").strip() for key in ("id", "key", "code"))
                and any(
                    str(item.get(key) or "").strip()
                    for key in (
                        "name",
                        "label",
                        "title",
                        "displayName",
                        "display_name",
                    )
                )
                for item in collection
            ):
                candidate_collections.append(collection)

        if len(candidate_collections) != 1 or len(candidate_collections[0]) != 1:
            return None
        candidate = candidate_collections[0][0]
        label = next(
            (
                str(candidate.get(key) or "").strip()
                for key in (
                    "name",
                    "label",
                    "title",
                    "displayName",
                    "display_name",
                )
                if str(candidate.get(key) or "").strip()
            ),
            "",
        )
        identity = next(
            (
                str(candidate.get(key) or "").strip()
                for key in ("id", "key", "code")
                if str(candidate.get(key) or "").strip()
            ),
            "",
        )
        if (
            identity
            and self._normalize_visible_choice_text(label).casefold() == expected_label
        ):
            return identity
        return None

    def _prepare_single_choice_continuation(
        self,
        *,
        agent_run: Any,
        state: dict[str, Any],
        continuation_index: int,
        _log_step: Any,
    ) -> tuple[list[dict[str, Any]], str] | None:
        """Prepare one hidden continuation for an explicit single-choice source."""
        if continuation_index >= self._MAX_SINGLE_CHOICE_CONTINUATIONS:
            return None
        if state.get("persist_override_messages") is not None:
            return None
        raw_runtime_messages = self.history.normalize_messages(agent_run.all_messages())
        runtime_messages = list(
            state.get("latest_runtime_messages") or raw_runtime_messages
        )
        merged_messages = self._merge_runtime_messages_with_session_prefix(
            session_message_history=state.get("session_message_history") or [],
            runtime_messages=raw_runtime_messages,
            runtime_base_history_len=int(state.get("runtime_base_history_len") or 0),
            current_turn_user_message=(
                state.get("model_user_message") or state.get("user_message")
            ),
        )
        prompt = self._extract_latest_assistant_from_messages(
            messages=merged_messages,
            start_index=int(state.get("persist_run_output_start_index") or 0),
        )
        choice = self._resolve_single_visible_choice_prompt(prompt)
        if choice is None:
            return None
        result_id = self._latest_unconsumed_read_only_result_for_single_choice(
            state=state,
            messages=merged_messages,
            visible_label=choice.label,
        )
        if result_id is None:
            return None

        read_only_tools, removed_tools = project_explicit_read_only_tools(
            list(state.get("available_tools") or [])
        )
        allowed_names = [
            str(tool.get("name", "") or "").strip()
            for tool in read_only_tools
            if str(tool.get("name", "") or "").strip()
        ]
        state["available_tools"] = read_only_tools
        deps = state.get("deps")
        if isinstance(getattr(deps, "extra", None), dict):
            deps.extra["tools_snapshot"] = list(read_only_tools)
            deps.extra["tools_snapshot_authoritative"] = True
            deps.extra["runtime_allowed_tool_names"] = list(allowed_names)

        internal_reply = (
            "[AtlasClaw internal single-choice continuation]\n"
            f"Selected the only visible option number: {choice.ordinal}.\n"
            "Continue the active workflow from this exact value."
        )
        consumed_result_ids = list(state.get("consumed_single_choice_result_ids") or [])
        consumed_result_ids.append(result_id)
        state["consumed_single_choice_result_ids"] = consumed_result_ids
        hidden_pairs = list(state.get("hidden_single_choice_pairs") or [])
        hidden_pairs.append({"assistant": prompt, "user": internal_reply})
        state["hidden_single_choice_pairs"] = hidden_pairs
        state["buffered_assistant_events"] = []
        state["assistant_output_streamed"] = False
        state["session_message_history"] = list(merged_messages)
        state["runtime_message_history"] = list(runtime_messages)
        state["runtime_base_history_len"] = len(runtime_messages)
        state["model_user_message"] = internal_reply
        state["latest_runtime_messages"] = list(runtime_messages)
        state["latest_agent_messages"] = list(merged_messages)
        state["message_history"] = list(merged_messages)
        _log_step(
            "single_choice_auto_continuation",
            ordinal=choice.ordinal,
            match_mode=choice.match_mode,
            remaining_read_only_tool_count=len(read_only_tools),
            removed_tool_count=len(removed_tools),
            continuation_index=continuation_index + 1,
        )
        return runtime_messages, internal_reply

    async def _run_loop_phase(self, *, state: dict[str, Any], _log_step: Any) -> AsyncIterator[StreamEvent]:
        """Main model/tool streaming loop phase."""
        deps = state.get("deps")
        user_message = state.get("user_message")
        model_user_message = state.get("model_user_message") or user_message
        raw_runtime_message_history = state.get("runtime_message_history")
        if raw_runtime_message_history is None:
            raw_runtime_message_history = state.get("message_history") or []
        runtime_message_history = list(raw_runtime_message_history)
        agent_run = None

        deps.user_message = user_message
        if model_user_message != user_message:
            _log_step("model_user_message_contextualized")
        state["run_output_start_index"] = len(runtime_message_history)

        try:
            _log_step(
                "model_message_history_build_start",
                runtime_history_count=len(runtime_message_history),
            )
            yield StreamEvent.runtime_update(
                "reasoning",
                "Preparing model request context.",
                metadata={
                    "phase": "model_message_history_build",
                    "elapsed": round(time.monotonic() - float(state.get("start_time") or 0.0), 1),
                },
            )
            continuation_index = 0
            while True:
                model_message_history = self.history.to_model_message_history(
                    runtime_message_history
                )
                _log_step(
                    "model_message_history_build_done",
                    model_history_count=len(model_message_history),
                )
                _log_step("agent_iter_open_start")
                yield StreamEvent.runtime_update(
                    "reasoning",
                    "Starting model session.",
                    metadata={
                        "phase": "agent_iter_open",
                        "elapsed": round(
                            time.monotonic() - float(state.get("start_time") or 0.0),
                            1,
                        ),
                    },
                )
                async with self._run_iter_with_optional_override(
                    agent=state.get("runtime_agent"),
                    user_message=model_user_message,
                    deps=deps,
                    message_history=model_message_history,
                    system_prompt=state.get("system_prompt"),
                ) as agent_run:
                    _log_step("agent_iter_open_done")
                    async for event in self._run_agent_node_stream(
                        agent_run=agent_run,
                        state=state,
                        _log_step=_log_step,
                    ):
                        yield event

                    thinking_emitter = state.get("thinking_emitter")
                    if thinking_emitter is not None:
                        async for event in thinking_emitter.close_if_active():
                            yield event

                    continuation = self._prepare_single_choice_continuation(
                        agent_run=agent_run,
                        state=state,
                        continuation_index=continuation_index,
                        _log_step=_log_step,
                    )
                    if continuation is not None:
                        runtime_message_history, model_user_message = continuation
                        continuation_index += 1
                        yield StreamEvent.runtime_update(
                            "reasoning",
                            "Continuing with the only available option.",
                            metadata={
                                "phase": "single_choice_auto_continuation",
                                "continuation_index": continuation_index,
                                "elapsed": round(
                                    time.monotonic()
                                    - float(state.get("start_time") or 0.0),
                                    1,
                                ),
                            },
                        )
                        continue

                    async for event in self._process_agent_run_outcome(
                        agent_run=agent_run,
                        state=state,
                        _log_step=_log_step,
                    ):
                        yield event
                    break

        except Exception as error:
            if agent_run is not None:
                try:
                    runtime_messages = self.history.normalize_messages(agent_run.all_messages())
                    merged_messages = self._merge_runtime_messages_with_session_prefix(
                        session_message_history=state.get("session_message_history") or [],
                        runtime_messages=runtime_messages,
                        runtime_base_history_len=int(state.get("runtime_base_history_len") or 0),
                        current_turn_user_message=(
                            state.get("model_user_message") or state.get("user_message")
                        ),
                    )
                    merged_messages = self._remove_hidden_single_choice_pairs(
                        messages=merged_messages,
                        hidden_pairs=list(state.get("hidden_single_choice_pairs") or []),
                    )
                    state["latest_runtime_messages"] = runtime_messages
                    state["latest_agent_messages"] = merged_messages
                    state["message_history"] = merged_messages
                    state["context_history_for_hooks"] = list(merged_messages)
                except Exception:
                    pass
            async for event in self._handle_loop_phase_exception(error=error, state=state):
                yield event
