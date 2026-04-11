from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.context_pruning import prune_context_messages, should_apply_context_pruning
from app.atlasclaw.agent.context_window_guard import evaluate_context_window_guard
from app.atlasclaw.agent.runner_prompt_context import build_system_prompt, collect_tool_groups_snapshot, collect_tools_snapshot
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    project_minimal_toolset,
    project_planner_toolset,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
)
from app.atlasclaw.core.deps import SkillDeps


logger = logging.getLogger(__name__)


class RunnerExecutionPreparePhaseMixin:
    async def _run_prepare_phase(self, *, state: dict[str, Any], _log_step: Any) -> AsyncIterator[StreamEvent]:
        """Prepare runtime/session/prompt/tool-gate phase before model loop."""
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
        flushed_memory_signatures = state.get("flushed_memory_signatures")
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
        planner_available_tools = state.get("planner_available_tools")
        toolset_filter_trace = state.get("toolset_filter_trace")
        tool_projection_trace = state.get("tool_projection_trace")
        planner_tool_projection_trace = state.get("planner_tool_projection_trace")
        used_toolset_fallback = state.get("used_toolset_fallback")
        provider_hint_docs = state.get("provider_hint_docs")
        skill_hint_docs = state.get("skill_hint_docs")
        builtin_tool_hint_docs = state.get("builtin_tool_hint_docs")
        metadata_candidates = state.get("metadata_candidates")
        ranking_trace = state.get("ranking_trace")
        runtime_message_history = state.get("runtime_message_history")
        session_message_history = state.get("session_message_history")
        runtime_base_history_len = state.get("runtime_base_history_len")
        persist_run_output_start_index = state.get("persist_run_output_start_index")
        try:
            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_start()
            _log_step("lifecycle_start")
            yield StreamEvent.runtime_update(
                "reasoning",
                "Starting response analysis.",
                metadata={"phase": "start", "attempt": 0, "elapsed": 0.0},
            )

            runtime_agent, selected_token_id, release_slot = await self._resolve_runtime_agent(session_key, deps)
            logger.warning(
                "runtime token resolved: session=%s selected_token_id=%s managed_tokens=%s",
                session_key,
                selected_token_id,
                len(self.token_policy.token_pool.tokens) if self.token_policy is not None else 0,
            )
            runtime_context_window_info = self._resolve_runtime_context_window_info(selected_token_id, deps)
            runtime_context_guard = evaluate_context_window_guard(
                tokens=runtime_context_window_info.tokens,
                source=runtime_context_window_info.source,
            )
            runtime_context_window = runtime_context_guard.tokens
            _log_step(
                "context_guard_evaluated",
                tokens=runtime_context_guard.tokens,
                source=runtime_context_guard.source,
                should_warn=runtime_context_guard.should_warn,
                should_block=runtime_context_guard.should_block,
            )
            if runtime_context_guard.should_warn:
                yield StreamEvent.runtime_update(
                    "warning",
                    (
                        "Model context window is below the warning threshold. "
                        f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                    ),
                    metadata={
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "warn",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
            if runtime_context_guard.should_block:
                failure_message = (
                    "Model context window is below the minimum safety threshold. "
                    f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                )
                run_failed = True
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
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "block",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                state["should_stop"] = True
                return
            session_manager = self._resolve_session_manager(session_key, deps)

            # --:session + build prompt --

            session = await session_manager.get_or_create(session_key)
            _log_step("session_get_or_create_done")
            transcript = await session_manager.load_transcript(session_key)
            _log_step("session_load_transcript_done", transcript_entries=len(transcript))
            message_history = self.history.build_message_history(transcript)
            message_history = self.history.prune_summary_messages(message_history)
            if should_apply_context_pruning(settings=self.context_pruning_settings, session=session):
                message_history = prune_context_messages(
                    messages=message_history,
                    settings=self.context_pruning_settings,
                    context_window_tokens=runtime_context_window,
                )
            message_history = self._deduplicate_message_history(message_history)
            context_history_for_hooks = list(message_history)
            session_title = str(getattr(session, "title", "") or "")
            await self.runtime_events.trigger_message_received(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_message_received_dispatched")
            await self.runtime_events.trigger_run_started(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_run_started_dispatched")
            await self._maybe_set_draft_title(
                session_manager=session_manager,
                session_key=session_key,
                session=session,
                transcript=transcript,
                user_message=user_message,
            )
            _log_step("session_draft_title_done")
            all_available_tools = collect_tools_snapshot(agent=runtime_agent, deps=deps)
            _log_step("tools_snapshot_collected", all_tools_count=len(all_available_tools))
            tool_groups_snapshot = collect_tool_groups_snapshot(deps)
            _log_step("tool_groups_snapshot_collected", group_count=len(tool_groups_snapshot))
            available_tools, toolset_filter_trace, used_toolset_fallback = self._build_turn_toolset(
                deps=deps,
                session_key=session_key,
                all_tools=all_available_tools,
                tool_groups=tool_groups_snapshot,
            )
            _log_step(
                "toolset_policy_applied",
                total_tools=len(all_available_tools),
                filtered_tools=len(available_tools),
                used_fallback=used_toolset_fallback,
                policy_layers=len(toolset_filter_trace),
            )
            if isinstance(deps.extra, dict):
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["toolset_policy_trace"] = list(toolset_filter_trace)
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            provider_hint_docs = self._build_provider_hint_docs(
                deps=deps,
                available_tools=available_tools,
            )
            skill_hint_docs = self._build_skill_hint_docs(
                deps=deps,
                available_tools=available_tools,
            )
            builtin_tool_hint_docs = self._build_builtin_tool_hint_docs(
                available_tools=available_tools,
            )
            if isinstance(deps.extra, dict):
                deps.extra["provider_hint_docs"] = provider_hint_docs
                deps.extra["skill_hint_docs"] = skill_hint_docs
                deps.extra["builtin_tool_hint_docs"] = builtin_tool_hint_docs
            _log_step(
                "hint_docs_built",
                provider_hint_count=len(provider_hint_docs),
                skill_hint_count=len(skill_hint_docs),
                builtin_tool_hint_count=len(builtin_tool_hint_docs),
            )
            tool_request_message, used_follow_up_context = self._resolve_contextual_tool_request(
                user_message=user_message,
                recent_history=message_history,
            )
            _log_step(
                "tool_request_resolved",
                used_follow_up_context=used_follow_up_context,
                raw_user_message=user_message,
                resolved_tool_request=tool_request_message,
            )
            metadata_candidates = self._recall_provider_skill_candidates_from_metadata(
                user_message=tool_request_message,
                recent_history=message_history,
                used_follow_up_context=used_follow_up_context,
                available_tools=available_tools,
                provider_hint_docs=provider_hint_docs,
                skill_hint_docs=skill_hint_docs,
                builtin_tool_hint_docs=builtin_tool_hint_docs,
                top_k_provider=self.TOOL_METADATA_PROVIDER_TOP_K,
                top_k_skill=self.TOOL_METADATA_SKILL_TOP_K,
            )
            ranking_trace = {
                "status": "metadata_recall",
                "reason": str(metadata_candidates.get("reason", "") or "metadata_recall"),
                "confidence": float(metadata_candidates.get("confidence", 0.0) or 0.0),
                "preferred_provider_types": list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                "preferred_group_ids": list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                "preferred_capability_classes": list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                "preferred_tool_names": list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            }
            if isinstance(deps.extra, dict):
                deps.extra["tool_metadata_candidates"] = dict(metadata_candidates)
                deps.extra["tool_ranking_trace"] = dict(ranking_trace)
            _log_step(
                "tool_metadata_recalled",
                confidence=float(metadata_candidates.get("confidence", 0.0) or 0.0),
                preferred_provider_types=list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                preferred_group_ids=list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                preferred_capability_classes=list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                preferred_tool_names=list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            )
            planner_available_tools, planner_tool_projection_trace = project_planner_toolset(
                allowed_tools=available_tools,
                metadata_candidates=metadata_candidates,
                used_follow_up_context=used_follow_up_context,
                min_metadata_confidence=self.TOOL_HINT_RANKER_MIN_METADATA_CONFIDENCE,
            )
            planner_provider_hint_docs = self._filter_hint_docs_for_planner_tools(
                hint_docs=provider_hint_docs,
                planner_available_tools=planner_available_tools,
            )
            planner_skill_hint_docs = self._filter_hint_docs_for_planner_tools(
                hint_docs=skill_hint_docs,
                planner_available_tools=planner_available_tools,
            )
            planner_builtin_tool_hint_docs = self._filter_hint_docs_for_planner_tools(
                hint_docs=builtin_tool_hint_docs,
                planner_available_tools=planner_available_tools,
            )
            _log_step(
                "planner_toolset_projected",
                before_count=int(planner_tool_projection_trace.get("before_count", 0) or 0),
                after_count=int(planner_tool_projection_trace.get("after_count", 0) or 0),
                reason=str(planner_tool_projection_trace.get("reason", "") or ""),
            )
            classifier_history = self._build_classifier_history(
                user_message=tool_request_message,
                recent_history=message_history,
                used_follow_up_context=used_follow_up_context,
            )
            _log_step(
                "tool_intent_planner_resolved",
                classifier_enabled=bool(self.tool_gate_model_classifier_enabled),
            )
            tool_gate_cache_key = self._build_tool_gate_cache_key(
                session_key=session_key,
                resolved_tool_request=tool_request_message,
                used_follow_up_context=used_follow_up_context,
                recent_history=classifier_history,
                available_tools=available_tools,
                metadata_candidates=metadata_candidates,
            )
            cached_tool_intent_plan = self._get_cached_tool_intent_plan(tool_gate_cache_key)
            if cached_tool_intent_plan is not None:
                tool_intent_plan = cached_tool_intent_plan
                _log_step(
                    "tool_intent_plan_cache_hit",
                    cache_key=tool_gate_cache_key[:12],
                )
            else:
                _log_step(
                    "tool_intent_plan_cache_miss",
                    cache_key=tool_gate_cache_key[:12],
                )
                tool_intent_plan = self._build_metadata_fallback_tool_intent_plan(
                    metadata_candidates=metadata_candidates,
                    available_tools=available_tools,
                )
                if tool_intent_plan is not None:
                    _log_step("tool_intent_plan_metadata_short_circuit")
                if tool_intent_plan is None:
                    tool_intent_plan = self._build_projected_toolset_short_circuit_intent_plan(
                        planner_available_tools=planner_available_tools,
                    )
                    if tool_intent_plan is not None:
                        _log_step("tool_intent_plan_projected_toolset_short_circuit")
                if tool_intent_plan is None and self.tool_gate_model_classifier_enabled:
                    tool_intent_plan = await self._plan_tool_intent_with_model(
                        agent=runtime_agent,
                        deps=deps,
                        user_message=tool_request_message,
                        recent_history=classifier_history,
                        available_tools=planner_available_tools,
                        provider_hint_docs=planner_provider_hint_docs,
                        skill_hint_docs=planner_skill_hint_docs,
                        builtin_tool_hint_docs=planner_builtin_tool_hint_docs,
                    )
                if tool_intent_plan is None:
                    tool_intent_plan = ToolIntentPlan(
                        action=ToolIntentAction.DIRECT_ANSWER,
                        reason="Intent planner unavailable; runtime fell back to direct-answer mode.",
                    )
            tool_intent_plan = self._align_tool_intent_plan_with_metadata(
                plan=tool_intent_plan,
                metadata_candidates=metadata_candidates,
                available_tools=available_tools,
            )
            if cached_tool_intent_plan is None:
                self._store_tool_intent_plan_cache(
                    cache_key=tool_gate_cache_key,
                    plan=tool_intent_plan,
                )
            if isinstance(deps.extra, dict):
                deps.extra["tool_intent_plan"] = tool_intent_plan.model_dump(mode="python")
            _log_step(
                "tool_intent_planned",
                action=tool_intent_plan.action.value,
                target_provider_types=list(tool_intent_plan.target_provider_types),
                target_skill_names=list(tool_intent_plan.target_skill_names),
                target_group_ids=list(tool_intent_plan.target_group_ids),
                target_capability_classes=list(tool_intent_plan.target_capability_classes),
                target_tool_names=list(tool_intent_plan.target_tool_names),
                missing_inputs=list(tool_intent_plan.missing_inputs),
            )

            tool_gate_decision = self._normalize_tool_gate_decision(
                self._build_tool_gate_decision_from_intent_plan(tool_intent_plan)
            )
            available_tools, tool_projection_trace = project_minimal_toolset(
                allowed_tools=available_tools,
                intent_plan=tool_intent_plan,
            )
            if isinstance(deps.extra, dict):
                deps.extra["tool_projection_trace"] = dict(tool_projection_trace)
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            _log_step(
                "tool_projection_applied",
                before_count=int(tool_projection_trace.get("before_count", 0) or 0),
                after_count=int(tool_projection_trace.get("after_count", 0) or 0),
                reason=str(tool_projection_trace.get("reason", "") or ""),
                coordination_tools=list(tool_projection_trace.get("coordination_tools", []) or []),
            )
            tool_match_result = CapabilityMatcher(available_tools=available_tools).match(
                tool_gate_decision.suggested_tool_classes
            )
            logger.warning(
                "tool_intent decision: session=%s action=%s policy=%s needs_external=%s needs_live_data=%s suggested=%s candidates=%s",
                session_key,
                tool_intent_plan.action.value,
                tool_gate_decision.policy.value,
                bool(tool_gate_decision.needs_external_system),
                bool(tool_gate_decision.needs_live_data),
                list(tool_gate_decision.suggested_tool_classes),
                [
                    str(getattr(candidate, "name", "") or "").strip()
                    for candidate in tool_match_result.tool_candidates
                    if str(getattr(candidate, "name", "") or "").strip()
                ],
            )
            _log_step(
                "tool_gate_decided",
                action=tool_intent_plan.action.value,
                policy=tool_gate_decision.policy.value,
                needs_tool=bool(tool_gate_decision.needs_tool),
                needs_external=bool(tool_gate_decision.needs_external_system),
                needs_live_data=bool(tool_gate_decision.needs_live_data),
                suggested_classes=list(tool_gate_decision.suggested_tool_classes),
                candidate_count=len(tool_match_result.tool_candidates),
                missing_capabilities=list(tool_match_result.missing_capabilities),
            )
            tool_execution_required = turn_action_requires_tool_execution(tool_intent_plan)
            reasoning_retry_limit = self.REASONING_ONLY_MAX_RETRIES
            if tool_execution_required:
                reasoning_retry_limit = 0
            self._inject_tool_policy(
                deps=deps,
                intent_plan=tool_intent_plan,
                available_tools=available_tools,
            )
            _log_step(
                "tool_policy_injected",
                tool_execution_required=tool_execution_required,
                reasoning_retry_limit=reasoning_retry_limit,
            )
            await self.runtime_events.trigger_tool_gate_evaluated(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
            )
            await self.runtime_events.trigger_tool_matcher_resolved(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
                match_result=tool_match_result,
            )

            if tool_execution_required and not available_tools:
                failure_message = (
                    "This turn requires real tool execution, but no executable tools remained "
                    "after policy and metadata filtering."
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={"phase": "gate", "elapsed": round(time.monotonic() - start_time, 1)},
                )
                yield StreamEvent.error_event(failure_message)
                state["run_failed"] = True
                state["should_stop"] = True
                return

            system_prompt = build_system_prompt(
                self.prompt_builder,
                session=session,
                deps=deps,
                agent=runtime_agent or self.agent,
                context_window_tokens=runtime_context_window,
            )
            consume_prompt_warnings = getattr(self.prompt_builder, "consume_warnings", None)
            if callable(consume_prompt_warnings):
                for warning_message in consume_prompt_warnings():
                    if not self._should_surface_prompt_warning(warning_message):
                        logger.debug("Suppressing prompt-context warning: %s", warning_message)
                        continue
                    yield StreamEvent.runtime_update(
                        "warning",
                        warning_message,
                        metadata={
                            "phase": "prompt_context",
                            "elapsed": round(time.monotonic() - start_time, 1),
                        },
                    )

            if self.hooks:
                prompt_ctx = await self.hooks.trigger(
                    "before_prompt_build",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                        "system_prompt": system_prompt,
                    },
                )
                system_prompt = prompt_ctx.get("system_prompt", system_prompt)

            # at iter,.
            if self.compaction.should_memory_flush(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                await self.history.flush_history_to_timestamped_memory(
                    session_key=session_key,
                    messages=message_history,
                    deps=deps,
                    session=session,
                    context_window=runtime_context_window,
                    flushed_signatures=flushed_memory_signatures,
                )

            if message_history and self.compaction.should_compact(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                if self.hooks:
                    await self.hooks.trigger(
                        "before_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )
                yield StreamEvent.compaction_start()
                compressed_history = await self.compaction.compact(message_history, session)
                message_history = self.history.normalize_messages(compressed_history)
                message_history = await self.history.inject_memory_recall(message_history, deps)
                context_history_for_hooks = list(message_history)
                await session_manager.mark_compacted(session_key)
                compaction_applied = True
                yield StreamEvent.compaction_end()
                if self.hooks:
                    await self.hooks.trigger(
                        "after_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )

            # -- hook:before_agent_start --
            if self.hooks:
                start_ctx = await self.hooks.trigger(
                    "before_agent_start",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                    },
                )
                user_message = start_ctx.get("user_message", user_message)
            session_message_history = list(message_history)
            runtime_message_history = self._build_runtime_message_history_for_turn(
                session_message_history=session_message_history,
                used_follow_up_context=used_follow_up_context,
                intent_plan=tool_intent_plan,
            )
            runtime_base_history_len = len(runtime_message_history)
            persist_run_output_start_index = len(session_message_history)
            if runtime_base_history_len != len(session_message_history):
                _log_step(
                    "runtime_message_history_trimmed",
                    session_history_count=len(session_message_history),
                    runtime_history_count=runtime_base_history_len,
                    used_follow_up_context=used_follow_up_context,
                    action=getattr(tool_intent_plan, "action", None).value if tool_intent_plan else "",
                )
        finally:
            resolved_runtime_message_history = (
                list(runtime_message_history)
                if runtime_message_history is not None
                else list(message_history)
            )
            resolved_session_message_history = (
                list(session_message_history)
                if session_message_history is not None
                else list(message_history)
            )
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
                "flushed_memory_signatures": flushed_memory_signatures,
                "extra": extra,
                "run_id": run_id,
                "tool_execution_retry_count": tool_execution_retry_count,
                "run_failed": run_failed,
                "message_history": message_history,
                "runtime_message_history": resolved_runtime_message_history,
                "session_message_history": resolved_session_message_history,
                "runtime_base_history_len": runtime_base_history_len if runtime_base_history_len is not None else len(resolved_runtime_message_history),
                "persist_run_output_start_index": persist_run_output_start_index if persist_run_output_start_index is not None else len(message_history),
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
                "planner_available_tools": planner_available_tools or list(available_tools),
                "toolset_filter_trace": toolset_filter_trace,
                "tool_projection_trace": tool_projection_trace,
                "planner_tool_projection_trace": planner_tool_projection_trace,
                "used_toolset_fallback": used_toolset_fallback,
                "provider_hint_docs": provider_hint_docs,
                "skill_hint_docs": skill_hint_docs,
                "builtin_tool_hint_docs": builtin_tool_hint_docs,
                "metadata_candidates": metadata_candidates,
                "ranking_trace": ranking_trace,
            })

    @staticmethod
    def _filter_hint_docs_for_planner_tools(
        *,
        hint_docs: list[dict[str, Any]],
        planner_available_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        planner_tool_names = {
            str(tool.get("name", "") or "").strip()
            for tool in planner_available_tools
            if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
        }
        if not planner_tool_names:
            return []
        filtered: list[dict[str, Any]] = []
        for doc in hint_docs or []:
            if not isinstance(doc, dict):
                continue
            tool_names = {
                str(item).strip()
                for item in (doc.get("tool_names", []) or [])
                if str(item).strip()
            }
            if tool_names and not tool_names.intersection(planner_tool_names):
                continue
            filtered.append(doc)
        return filtered

    @staticmethod
    def _build_runtime_message_history_for_turn(
        *,
        session_message_history: list[dict[str, Any]],
        used_follow_up_context: bool,
        intent_plan: ToolIntentPlan | None,
    ) -> list[dict[str, Any]]:
        if not session_message_history:
            return []
        if used_follow_up_context:
            return list(session_message_history)
        if intent_plan is None:
            return list(session_message_history)
        if intent_plan.action is not ToolIntentAction.USE_TOOLS:
            return list(session_message_history)
        return []

