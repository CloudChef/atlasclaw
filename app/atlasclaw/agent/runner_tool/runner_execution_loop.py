# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.skill_stage_context import (
    build_stage_context_message,
    new_stage_state,
    record_completed_stage,
)
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.trace import resolve_trace_context
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.agent.runner_tool.runner_execution_finalize import RunnerExecutionFinalizePhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow import RunnerExecutionFlowPhaseMixin
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import RunnerExecutionPreparePhaseMixin
from app.atlasclaw.tools.providers.instance_tools import (
    PROVIDER_INSTANCE_SELECTIONS_KEY,
    clear_recorded_provider_instance_selections,
)


logger = logging.getLogger(__name__)


async def hydrate_session_provider_instance_selections(deps: SkillDeps) -> None:
    """Load valid session-sticky provider instance selections into request extras."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    session_manager = getattr(deps, "session_manager", None)
    session_key = str(getattr(deps, "session_key", "") or "").strip()
    if session_manager is None or not session_key:
        return

    get_session = getattr(session_manager, "get_session", None)
    if not callable(get_session):
        return

    session = await get_session(session_key)
    session_extra = getattr(session, "extra", {}) if session is not None else {}
    raw_selections = {}
    if isinstance(session_extra, dict) and isinstance(
        session_extra.get(PROVIDER_INSTANCE_SELECTIONS_KEY),
        dict,
    ):
        raw_selections = dict(session_extra[PROVIDER_INSTANCE_SELECTIONS_KEY])

    provider_instances = extra.get("provider_instances")
    if not isinstance(provider_instances, dict) or not raw_selections:
        return

    valid_selections: dict[str, str] = {}
    for provider_type, instance_name in raw_selections.items():
        normalized_provider_type = str(provider_type or "").strip()
        normalized_instance_name = str(instance_name or "").strip()
        instances = provider_instances.get(normalized_provider_type)
        if not isinstance(instances, dict):
            instances = provider_instances.get(normalized_provider_type.lower())
        if isinstance(instances, dict) and normalized_instance_name in instances:
            valid_selections[normalized_provider_type] = normalized_instance_name

    if valid_selections:
        current = extra.get(PROVIDER_INSTANCE_SELECTIONS_KEY)
        merged = dict(valid_selections)
        if isinstance(current, dict):
            merged.update(current)
        extra[PROVIDER_INSTANCE_SELECTIONS_KEY] = merged


class RunnerExecutionLoopMixin(RunnerExecutionPreparePhaseMixin, RunnerExecutionFlowPhaseMixin, RunnerExecutionFinalizePhaseMixin):
    async def run(
        self,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        *,
        max_tool_calls: int = 50,
        timeout_seconds: int | None = 600,
        _token_failover_attempt: int = 0,
        _emit_lifecycle_bounds: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Run one task, continuing through validated model-bound Skill stages."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        run_id = str(extra.get("run_id") or uuid.uuid4().hex)
        extra["run_id"] = run_id
        storage_key = f"skill_stage_workflow:{run_id}"
        session_manager = self._resolve_session_manager(session_key, deps)
        session = await session_manager.get_or_create(session_key)
        session_extra = getattr(session, "extra", {}) or {}
        stored_state = session_extra.get(storage_key)
        if (
            isinstance(stored_state, dict)
            and stored_state.get("run_id") == run_id
            and isinstance(stored_state.get("completed_stages"), list)
        ):
            stage_state = dict(stored_state)
            stage_state["completed_stages"] = list(stored_state["completed_stages"])
        else:
            stage_state = new_stage_state(run_id=run_id, original_request=user_message)
            candidate_run_id = str(
                session_extra.get("active_skill_stage_workflow_run_id") or ""
            ).strip()
            candidate_state = session_extra.get(
                f"skill_stage_workflow:{candidate_run_id}"
            ) if candidate_run_id else None
            if (
                isinstance(candidate_state, dict)
                and candidate_state.get("run_id") == candidate_run_id
                and candidate_state.get("status") in {"running", "failed", "unknown"}
                and isinstance(candidate_state.get("completed_stages"), list)
                and (candidate_state.get("completed_stages") or candidate_state.get("active_stage"))
            ):
                candidate_state = deepcopy(candidate_state)
                candidate_state["recovery_outcome_unknown"] = bool(
                    candidate_state.get("active_stage")
                )
                extra["_skill_stage_candidate_state"] = candidate_state
                extra["_skill_stage_candidate_run_id"] = candidate_run_id
                extra["_skill_stage_candidate_context_message"] = build_stage_context_message(
                    candidate_state
                )
            else:
                extra.pop("_skill_stage_candidate_state", None)
                extra.pop("_skill_stage_candidate_run_id", None)
                extra.pop("_skill_stage_candidate_context_message", None)
        extra["_skill_stage_state"] = stage_state
        extra["_skill_stage_original_request"] = str(stage_state.get("original_request") or user_message)
        extra["_skill_stage_continuation"] = False
        extra.pop("_skill_stage_stop", None)
        initial_request = str(stage_state.get("original_request") or user_message)
        stage_user_message = user_message
        continuation = bool(stage_state.get("completed_stages"))
        failed = False
        final_answer_committed = False
        failure_message = ""

        if _emit_lifecycle_bounds:
            yield StreamEvent.lifecycle_start()

        stored_status = str(stage_state.get("status") or "").strip().lower()
        if stored_state is not None and stored_status == "completed":
            final_output = str(stage_state.get("final_output") or "")
            if final_output:
                yield StreamEvent.assistant_delta(final_output)
            yield StreamEvent.runtime_update("answered", "Final answer ready.")
            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_end()
            return
        if stored_state is not None and stored_state.get("active_stage"):
            # A previous process stopped after dispatch began. Its side effects
            # cannot be inferred safely, so retain prior stages and surface the
            # outcome as unknown instead of replaying the same Skill.
            failure_message = "A Skill stage was interrupted after dispatch; its outcome is unknown and it was not replayed."
            stage_state["status"] = "unknown"
            stage_state["failure"] = failure_message
            await session_manager.update_extra(session_key, {storage_key: stage_state})
            yield StreamEvent.runtime_update(
                "failed",
                failure_message,
                metadata={"phase": "skill_stage_outcome_unknown"},
            )
            yield StreamEvent.error_event(failure_message)
            if _emit_lifecycle_bounds:
                yield StreamEvent(type="lifecycle", phase="error")
            return
        if stored_state is not None and stored_status in {"failed", "unknown"}:
            failure_message = str(stage_state.get("failure") or "Skill workflow previously failed")
            yield StreamEvent.runtime_update(
                "failed", failure_message, metadata={"phase": "skill_stage_recovery"}
            )
            yield StreamEvent.error_event(failure_message)
            if _emit_lifecycle_bounds:
                yield StreamEvent(type="lifecycle", phase="error")
            return

        while True:
            extra["_skill_stage_continuation"] = continuation
            extra["_skill_stage_active"] = True
            extra["_skill_stage_tool_dispatched"] = False
            extra["_skill_stage_side_effect_started"] = False
            extra["_skill_stage_usage_row_ids"] = []
            extra["_chat_tool_started"] = False
            extra.pop("_skill_stage_retry_snapshot", None)
            extra.pop("_binding_retry_token", None)
            extra.pop("_skill_stage_descriptor", None)
            extra.pop("_skill_stage_latest_messages", None)
            extra.pop("_skill_stage_final_output", None)
            extra.pop("_skill_stage_stop", None)
            if continuation:
                extra["_skill_stage_context_message"] = build_stage_context_message(stage_state)
                extra["_skill_stage_internal_turn"] = True
                extra.pop("_current_binding_selection", None)
                extra.pop("_current_binding_target", None)
                stage_user_message = ""
            else:
                extra.pop("_skill_stage_context_message", None)
                extra.pop("_skill_stage_internal_turn", None)
                stage_user_message = initial_request

            buffered_assistant: list[StreamEvent] = []
            stage_assistant_output: list[str] = []
            stage_answer_committed = False
            stage_failed = False
            stage_failure = ""
            async for event in self._run_single_stage(
                session_key=session_key,
                user_message=stage_user_message,
                deps=deps,
                max_tool_calls=max_tool_calls,
                timeout_seconds=timeout_seconds,
                _token_failover_attempt=_token_failover_attempt,
                _emit_lifecycle_bounds=False,
            ):
                if event.type == "assistant":
                    stage_assistant_output.append(str(event.content or ""))
                    descriptor = extra.get("_skill_stage_descriptor")
                    if isinstance(descriptor, dict) and descriptor.get("continue_after_success"):
                        buffered_assistant.append(event)
                    else:
                        yield event
                else:
                    withhold_stage_answer = False
                    if event.type == "error":
                        stage_failed = True
                        stage_failure = str(event.error or "")
                    elif event.type == "runtime":
                        runtime_state = str((event.metadata or {}).get("state") or "").strip().lower()
                        if runtime_state == "failed":
                            stage_failed = True
                            stage_failure = str(event.content or "")
                        elif (
                            runtime_state == "answered"
                            and str(event.content or "").strip() == "Final answer ready."
                        ):
                            stage_answer_committed = True
                            descriptor = extra.get("_skill_stage_descriptor")
                            withhold_stage_answer = bool(
                                isinstance(descriptor, dict)
                                and descriptor.get("continue_after_success")
                            )
                    if not withhold_stage_answer:
                        yield event

            adopted_state = extra.pop("_skill_stage_adopted_state", None)
            extra["_skill_stage_active"] = False
            if isinstance(adopted_state, dict):
                stage_state = adopted_state
                extra["_skill_stage_state"] = stage_state
                initial_request = str(stage_state.get("original_request") or initial_request)
                continuation = True

            descriptor = extra.get("_skill_stage_descriptor")
            stage_stop = str(extra.get("_skill_stage_stop") or "").strip()
            if stage_failed or stage_stop:
                failed = True
                failure_message = stage_failure or stage_stop
                if buffered_assistant:
                    for event in buffered_assistant:
                        yield event
                has_unresolved_side_effect = bool(
                    extra.get("_skill_stage_side_effect_started")
                )
                stage_state["status"] = "unknown" if has_unresolved_side_effect else "failed"
                if has_unresolved_side_effect:
                    descriptor = extra.get("_skill_stage_descriptor")
                    stage_messages = extra.get("_skill_stage_latest_messages")
                    active_stage = dict(stage_state.get("active_stage") or {})
                    if isinstance(descriptor, dict):
                        active_stage.update(
                            {
                                "identity": active_stage.get("identity") or str(
                                    descriptor.get("target_skill_id") or ""
                                ),
                                "target_skill_id": str(
                                    descriptor.get("target_skill_id") or active_stage.get("target_skill_id") or ""
                                ),
                                "planned_input": str(
                                    descriptor.get("planned_input") or active_stage.get("planned_input") or ""
                                ),
                            }
                        )
                    active_stage["status"] = "outcome_unknown"
                    active_stage["failure"] = failure_message
                    active_stage["normalized_messages"] = [
                        dict(item) for item in stage_messages or [] if isinstance(item, dict)
                    ]
                    stage_state["active_stage"] = active_stage
                    await session_manager.update_extra(
                        session_key,
                        {
                            storage_key: stage_state,
                            "active_skill_stage_workflow_run_id": run_id,
                        },
                    )
                else:
                    stage_state.pop("active_stage", None)
                if failure_message:
                    stage_state["failure"] = failure_message
                if not has_unresolved_side_effect:
                    await session_manager.update_extra(session_key, {storage_key: stage_state})
                break

            should_continue = bool(
                stage_answer_committed
                and isinstance(descriptor, dict)
                and descriptor.get("continue_after_success")
                and str(descriptor.get("target_skill_id") or "").strip()
            )
            if should_continue:
                stage_input = str(descriptor.get("planned_input") or initial_request)
                stage_messages = extra.get("_skill_stage_latest_messages")
                if not isinstance(stage_messages, list):
                    stage_messages = []
                stage_metadata = dict(descriptor.get("metadata") or {})
                stage_metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
                stage_metadata["usage_record_ids"] = list(
                    extra.get("_skill_stage_usage_row_ids") or []
                )
                record_completed_stage(
                    stage_state,
                    target_skill_id=str(descriptor["target_skill_id"]),
                    planned_input=stage_input,
                    messages=stage_messages,
                    assistant_output=str(extra.get("_skill_stage_final_output") or ""),
                    stage_metadata=stage_metadata,
                )
                stage_state["status"] = "running"
                stage_state.pop("active_stage", None)
                stage_state.pop("failure", None)
                await session_manager.update_extra(
                    session_key,
                    {
                        storage_key: stage_state,
                        "active_skill_stage_workflow_run_id": run_id,
                    },
                )
                yield StreamEvent.runtime_update(
                    "skill_execution_completed",
                    f"Completed Skill {descriptor['target_skill_id']}.",
                    metadata={
                        **{
                            key: value
                            for key, value in stage_metadata.items()
                            if key != "usage_record_ids"
                        },
                        "state": "skill_execution_completed",
                    },
                )
                continuation = True
                continue

            completed_event_metadata = None
            if buffered_assistant:
                for event in buffered_assistant:
                    yield event
            if isinstance(descriptor, dict) and str(descriptor.get("target_skill_id") or "").strip():
                stage_messages = extra.get("_skill_stage_latest_messages")
                if not isinstance(stage_messages, list):
                    stage_messages = []
                final_output = str(
                    extra.get("_skill_stage_final_output")
                    or "".join(stage_assistant_output)
                )
                stage_metadata = dict(descriptor.get("metadata") or {})
                stage_metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
                stage_metadata["usage_record_ids"] = list(
                    extra.get("_skill_stage_usage_row_ids") or []
                )
                record_completed_stage(
                    stage_state,
                    target_skill_id=str(descriptor["target_skill_id"]),
                    planned_input=str(descriptor.get("planned_input") or initial_request),
                    messages=stage_messages,
                    assistant_output=final_output,
                    stage_metadata=stage_metadata,
                )
                stage_state["final_output"] = final_output
                completed_event_metadata = stage_metadata
            stage_state["status"] = "completed" if stage_answer_committed else "failed"
            stage_state.pop("active_stage", None)
            if not stage_answer_committed:
                failed = True
                failure_message = "Stage ended without a committed answer"
                stage_state["failure"] = failure_message
            else:
                if not stage_state.get("final_output"):
                    stage_state["final_output"] = "".join(stage_assistant_output)
                stage_state.pop("failure", None)
                final_answer_committed = True
            if final_answer_committed:
                await session_manager.update_extra(session_key, {storage_key: stage_state})
                if str((getattr(session, "extra", {}) or {}).get(
                    "active_skill_stage_workflow_run_id"
                ) or "") == run_id:
                    await session_manager.update_extra(
                        session_key,
                        {"active_skill_stage_workflow_run_id": ""},
                    )
                if completed_event_metadata is not None:
                    yield StreamEvent.runtime_update(
                        "skill_execution_completed",
                        f"Completed Skill {descriptor['target_skill_id']}.",
                        metadata={
                            **{
                                key: value
                                for key, value in completed_event_metadata.items()
                                if key != "usage_record_ids"
                            },
                            "state": "skill_execution_completed",
                        },
                    )
            elif (
                not (
                    failed
                    and stage_state.get("status") == "failed"
                    and stage_state.get("completed_stages")
                )
                and str((getattr(session, "extra", {}) or {}).get(
                    "active_skill_stage_workflow_run_id"
                ) or "") == run_id
            ):
                await session_manager.update_extra(
                    session_key,
                    {"active_skill_stage_workflow_run_id": ""},
                )
            break

        extra["_skill_stage_continuation"] = False
        extra.pop("_skill_stage_internal_turn", None)
        if failed and not failure_message:
            failure_message = "Skill stage failed"
        if _emit_lifecycle_bounds:
            if failed:
                yield StreamEvent(type="lifecycle", phase="error")
            else:
                yield StreamEvent.lifecycle_end()

    async def _run_single_stage(
        self,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        *,
        max_tool_calls: int = 50,
        timeout_seconds: int | None = 600,
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
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        run_id = str(extra.get("run_id", "") or uuid.uuid4().hex)
        if isinstance(extra, dict):
            extra["run_id"] = run_id
        trace_context = resolve_trace_context(session_key, run_id=run_id, deps=deps)
        if isinstance(extra, dict):
            extra["trace_id"] = trace_context.trace_id
            extra["thread_id"] = trace_context.thread_id
        await hydrate_session_provider_instance_selections(deps)
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
                "trace_id": trace_context.trace_id,
                "thread_id": trace_context.thread_id,
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
            "extra": extra,
            "run_id": run_id,
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
            "conversation_turn_plan": None,
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
            "metadata_candidates": {},
            "ranking_trace": {},
            "artifact_goal": None,
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
            if phase_state.get("selector_attempted"):
                _log_step(
                    "capability_selector_audit",
                    selector_outcome=str(
                        phase_state.get("selector_outcome") or ""
                    ),
                    selector_failed=bool(
                        phase_state.get("selector_failed")
                    ),
                    selector_elapsed_ms=int(
                        phase_state.get("selector_elapsed_ms") or 0
                    ),
                    main_model_calls=int(
                        phase_state.get("current_model_attempt") or 0
                    ),
                )
            selected_token_id = phase_state.get("selected_token_id")
            release_slot = phase_state.get("release_slot")
            deps_obj = phase_state.get("deps", deps)
            clear_recorded_provider_instance_selections(deps_obj)
            if selected_token_id and self.token_interceptor is not None:
                headers = self._extract_rate_limit_headers(deps_obj)
                if headers:
                    extra = deps_obj.extra if isinstance(deps_obj.extra, dict) else {}
                    selection = extra.get("_current_binding_selection")
                    health_token_id = selected_token_id
                    fingerprint = extra.get("_selected_token_fingerprint")
                    selected = getattr(selection, "token", None)
                    if selected is not None and selected.token_id == selected_token_id:
                        pool_token_ids = getattr(selection, "pool_token_ids", {}) or {}
                        health_token_id = str(
                            pool_token_ids.get(selected_token_id, selected_token_id)
                            or selected_token_id
                        )
                        fingerprint = selected.credential_fingerprint
                        pool = getattr(self.token_policy, "token_pool", None)
                        pool_entry = getattr(pool, "tokens", {}).get(health_token_id)
                        if (
                            pool_entry is not None
                            and pool_entry.credential_fingerprint != fingerprint
                        ):
                            # Do not apply a source-qualified credential's health
                            # headers to a colliding ID from another credential source.
                            health_token_id = ""
                    if health_token_id:
                        guard = {"expected_fingerprint": fingerprint} if fingerprint else {}
                        self.token_interceptor.on_response(health_token_id, headers, **guard)
            if release_slot is not None:
                release_slot()
