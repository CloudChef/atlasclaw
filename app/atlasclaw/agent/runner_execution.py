from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from app.atlasclaw.agent.context_pruning import prune_context_messages
from app.atlasclaw.agent.context_window_guard import (
    ContextWindowInfo,
    evaluate_context_window_guard,
    resolve_context_window_info,
)
from app.atlasclaw.agent.runner_prompt_context import build_system_prompt, collect_tools_snapshot
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolGateDecision, ToolPolicyMode
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.session.context import SessionKey

if TYPE_CHECKING:
    from app.atlasclaw.agent.agent_pool import AgentInstancePool
    from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
    from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
    from app.atlasclaw.hooks.system import HookSystem
    from app.atlasclaw.session.manager import SessionManager
    from app.atlasclaw.session.queue import SessionQueue
    from app.atlasclaw.session.router import SessionManagerRouter


logger = logging.getLogger(__name__)


@dataclass
class _ModelNodeTimeout(RuntimeError):
    """Raised when the model stream stalls waiting for next node."""

    first_node: bool
    timeout_seconds: float


class RunnerExecutionMixin:
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
        run_failed = False
        message_history: list[dict] = []
        system_prompt = ""
        final_assistant = ""
        context_history_for_hooks: list[dict] = []
        tool_call_summaries: list[dict[str, Any]] = []
        session_title = ""
        buffered_assistant_events: list[StreamEvent] = []
        tool_request_message = user_message
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
        post_tool_wrap_mode = False
        run_output_start_index = 0


        try:
            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_start()
            yield StreamEvent.runtime_update(
                "reasoning",
                "Starting response analysis.",
                metadata={"phase": "start", "attempt": 0, "elapsed": 0.0},
            )

            runtime_agent, selected_token_id, release_slot = await self._resolve_runtime_agent(session_key, deps)
            runtime_context_window_info = self._resolve_runtime_context_window_info(selected_token_id, deps)
            runtime_context_guard = evaluate_context_window_guard(
                tokens=runtime_context_window_info.tokens,
                source=runtime_context_window_info.source,
            )
            runtime_context_window = runtime_context_guard.tokens
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
                return
            session_manager = self._resolve_session_manager(session_key, deps)

            # --:session + build prompt --

            session = await session_manager.get_or_create(session_key)
            transcript = await session_manager.load_transcript(session_key)
            message_history = self.history.build_message_history(transcript)
            message_history = self.history.prune_summary_messages(message_history)
            message_history = prune_context_messages(
                messages=message_history,
                settings=self.context_pruning_settings,
                context_window_tokens=runtime_context_window,
            )
            context_history_for_hooks = list(message_history)
            session_title = str(getattr(session, "title", "") or "")
            await self.runtime_events.trigger_message_received(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            await self.runtime_events.trigger_run_started(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            await self._maybe_set_draft_title(
                session_manager=session_manager,
                session_key=session_key,
                session=session,
                transcript=transcript,
                user_message=user_message,
            )
            available_tools = collect_tools_snapshot(agent=runtime_agent, deps=deps)
            tool_request_message, used_follow_up_context = self._resolve_contextual_tool_request(
                user_message=user_message,
                recent_history=message_history,
            )
            tool_gate_classifier = self._resolve_tool_gate_classifier(
                agent=runtime_agent,
                deps=deps,
                available_tools=available_tools,
            )
            tool_gate_decision = await self.tool_gate.classify_async(
                tool_request_message,
                message_history,
                classifier=tool_gate_classifier,
            )
            tool_gate_decision = self._normalize_tool_gate_decision(tool_gate_decision)
            tool_gate_decision = self._apply_no_classifier_follow_up_fallback(
                decision=tool_gate_decision,
                used_follow_up_context=used_follow_up_context,
                available_tools=available_tools,
            )
            tool_match_result = CapabilityMatcher(available_tools=available_tools).match(
                tool_gate_decision.suggested_tool_classes
            )
            tool_gate_decision, tool_match_result = self._align_external_system_intent(
                decision=tool_gate_decision,
                match_result=tool_match_result,
                available_tools=available_tools,
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

            if (
                tool_gate_decision.policy is ToolPolicyMode.MUST_USE_TOOL
                and tool_match_result.missing_capabilities
            ):
                warning_message = self._build_missing_capability_message(tool_match_result)
                tool_gate_decision = tool_gate_decision.model_copy(
                    update={
                        "policy": ToolPolicyMode.PREFER_TOOL,
                        "reason": (
                            f"{tool_gate_decision.reason} "
                            "Downgraded to prefer_tool because required capabilities are not fully available."
                        ).strip(),
                    }
                )
                yield StreamEvent.runtime_update(
                    "warning",
                    warning_message,
                    metadata={"phase": "gate", "elapsed": round(time.monotonic() - start_time, 1)},
                )

            self._inject_tool_policy(
                deps=deps,
                decision=tool_gate_decision,
                match_result=tool_match_result,
            )

            system_prompt = build_system_prompt(
                self.prompt_builder,
                session=session,
                deps=deps,
                agent=runtime_agent or self.agent,
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
            await self.runtime_events.trigger_llm_input(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
                system_prompt=system_prompt,
                message_history=message_history,
            )

            # -- inject user_message to deps, for Skills --
            deps.user_message = user_message
            run_output_start_index = len(message_history)

            # ========================================
            # :PydanticAI iter()
            # ========================================
            try:
                model_message_history = self.history.to_model_message_history(message_history)
                async with self._run_iter_with_optional_override(
                    agent=runtime_agent,
                    user_message=user_message,
                    deps=deps,
                    message_history=model_message_history,
                    system_prompt=system_prompt,
                ) as agent_run:

                    node_count = 0
                    try:
                        async for node in self._iter_agent_nodes_with_timeout(agent_run):
                            node_count += 1
                            # -- checkpoint 1:abort_signal --
                            if deps.is_aborted():
                                yield StreamEvent.lifecycle_aborted()
                                break

                            # -- checkpoint 2:--
                            if time.monotonic() - start_time > timeout_seconds:
                                yield StreamEvent.error_event("timeout")
                                break

                            # -- checkpoint 3:context -> trigger --
                            current_messages = self.history.normalize_messages(agent_run.all_messages())
                            current_messages = self.history.prune_summary_messages(current_messages)
                            current_messages = prune_context_messages(
                                messages=current_messages,
                                settings=self.context_pruning_settings,
                                context_window_tokens=runtime_context_window,
                            )
                            context_history_for_hooks = list(current_messages)
                            if self.compaction.should_memory_flush(
                                current_messages,
                                session,
                                context_window_override=runtime_context_window,
                            ):
                                await self.history.flush_history_to_timestamped_memory(
                                    session_key=session_key,
                                    messages=current_messages,
                                    deps=deps,
                                    session=session,
                                    context_window=runtime_context_window,
                                    flushed_signatures=flushed_memory_signatures,
                                )

                            if self.compaction.should_compact(
                                current_messages,
                                session,
                                context_window_override=runtime_context_window,
                            ):
                                if self.hooks:
                                    await self.hooks.trigger(
                                        "before_compaction",
                                        {
                                            "session_key": session_key,
                                            "message_count": len(current_messages),
                                        },
                                    )
                                yield StreamEvent.compaction_start()
                                compressed = await self.compaction.compact(current_messages, session)
                                persist_override_messages = self.history.normalize_messages(compressed)
                                persist_override_messages = await self.history.inject_memory_recall(
                                    persist_override_messages,
                                    deps,
                                )
                                context_history_for_hooks = list(persist_override_messages)
                                persist_override_base_len = len(current_messages)
                                await session_manager.mark_compacted(session_key)
                                compaction_applied = True
                                yield StreamEvent.compaction_end()
                                if self.hooks:
                                    await self.hooks.trigger(
                                        "after_compaction",
                                        {
                                            "session_key": session_key,
                                            "message_count": len(persist_override_messages),
                                        },
                                    )
    
                            # -- hook:llm_input() --
                            if self._is_model_request_node(node):
                                current_model_attempt += 1
                                current_attempt_started_at = time.monotonic()
                                current_attempt_has_text = False
                                current_attempt_has_tool = False
                                thinking_emitter.reset_cycle_flags()
                                await self.runtime_events.trigger_llm_input(
                                    session_key=session_key,
                                    run_id=run_id,
                                    user_message=user_message,
                                    system_prompt=system_prompt,
                                    message_history=current_messages,
                                )
                                yield StreamEvent.runtime_update(
                                    "reasoning",
                                    (
                                        "Analyzing request."
                                        if current_model_attempt == 1
                                        else "Continuing reasoning after retry."
                                    ),
                                    metadata={
                                        "phase": "model_request",
                                        "attempt": current_model_attempt,
                                        "elapsed": round(time.monotonic() - start_time, 1),
                                    },
                                )
    
                            # Emit model output chunks as assistant deltas.
                            if hasattr(node, "model_response") and node.model_response:
                                async for event in thinking_emitter.emit_from_model_response(
                                    model_response=node.model_response,
                                    hooks=self.hooks,
                                    session_key=session_key,
                                ):
                                    if (
                                        event.type == "assistant"
                                        and (
                                            (
                                                tool_gate_decision.policy in {
                                                    ToolPolicyMode.MUST_USE_TOOL,
                                                    ToolPolicyMode.PREFER_TOOL,
                                                }
                                                and not tool_call_summaries
                                            )
                                            or post_tool_wrap_mode
                                        )
                                    ):
                                        buffered_assistant_events.append(event)
                                    else:
                                        if event.type == "assistant":
                                            current_attempt_has_text = True
                                        yield event
                            elif hasattr(node, "content") and node.content:
                                content = str(node.content)
                                async for event in thinking_emitter.emit_plain_content(
                                    content=content,
                                    hooks=self.hooks,
                                    session_key=session_key,
                                ):
                                    if (
                                        event.type == "assistant"
                                        and (
                                            (
                                                tool_gate_decision.policy in {
                                                    ToolPolicyMode.MUST_USE_TOOL,
                                                    ToolPolicyMode.PREFER_TOOL,
                                                }
                                                and not tool_call_summaries
                                            )
                                            or post_tool_wrap_mode
                                        )
                                    ):
                                        buffered_assistant_events.append(event)
                                    else:
                                        if event.type == "assistant":
                                            current_attempt_has_text = True
                                        yield event
    
                            # Surface tool activity in the event stream.
                            tool_calls_in_node = self.runtime_events.collect_tool_calls(node)
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
                            if tool_calls_in_node:
                                post_tool_wrap_mode = True
                                current_attempt_has_tool = True
                                yield StreamEvent.runtime_update(
                                    "waiting_for_tool",
                                    "Preparing tool execution.",
                                    metadata={
                                        "phase": "planned",
                                        "attempt": current_model_attempt,
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
                                tool_calls_count=tool_calls_count,
                                max_tool_calls=max_tool_calls,
                                deps=deps,
                                session_key=session_key,
                                run_id=run_id,
                            )
                            tool_calls_count = tool_dispatch.tool_calls_count
                            for event in tool_dispatch.events:
                                yield event
                            if tool_call_summaries and buffered_assistant_events and not post_tool_wrap_mode:
                                while buffered_assistant_events:
                                    yield buffered_assistant_events.pop(0)
                            if (
                                self._is_call_tools_node(node)
                                and not current_attempt_has_text
                                and not current_attempt_has_tool
                                and thinking_emitter.current_cycle_had_thinking
                            ):
                                elapsed_total = round(time.monotonic() - start_time, 1)
                                attempt_elapsed = round(
                                    time.monotonic() - current_attempt_started_at,
                                    1,
                                ) if current_attempt_started_at is not None else elapsed_total
                                should_escalate = (
                                    elapsed_total >= self.REASONING_ONLY_ESCALATION_SECONDS
                                    or reasoning_retry_count >= self.REASONING_ONLY_MAX_RETRIES
                                )
                                if should_escalate:
                                    if tool_gate_decision.policy in {
                                        ToolPolicyMode.MUST_USE_TOOL,
                                        ToolPolicyMode.PREFER_TOOL,
                                    }:
                                        yield StreamEvent.runtime_update(
                                            "warning",
                                            "Verification did not produce a usable tool-backed answer in this cycle.",
                                            metadata={
                                                "phase": "verification",
                                                "attempt": current_model_attempt,
                                                "elapsed": elapsed_total,
                                                "attempt_elapsed": attempt_elapsed,
                                            },
                                        )
                                        break
                                    raise RuntimeError(
                                        "The model did not produce a usable answer after bounded reasoning retries."
                                    )
                                reasoning_retry_count += 1
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
                    except _ModelNodeTimeout as timeout_exc:
                        raise RuntimeError(
                            "The model stream timed out before producing a usable response."
                        ) from timeout_exc

                    # Ensure thinking phase is properly closed if still active.
                    async for event in thinking_emitter.close_if_active():
                        yield event

                    # Persist the final normalized transcript.
                    final_messages = self.history.normalize_messages(agent_run.all_messages())
                    if persist_override_messages is not None:
                        if len(final_messages) > persist_override_base_len > 0:
                            # Preserve override messages and append new run output.
                            final_messages = persist_override_messages + final_messages[persist_override_base_len:]
                        else:
                            final_messages = persist_override_messages
                        run_output_start_index = len(persist_override_messages)

                    final_assistant = self._extract_latest_assistant_from_messages(
                        messages=final_messages,
                        start_index=run_output_start_index,
                    )
                    if post_tool_wrap_mode and tool_call_summaries:
                        wrapped_message = await self._build_post_tool_wrapped_message(
                            runtime_agent=runtime_agent,
                            deps=deps,
                            user_message=user_message,
                            tool_calls=tool_call_summaries,
                        )
                        if wrapped_message:
                            final_assistant = wrapped_message
                            buffered_assistant_events.clear()
                            final_messages = self._replace_last_assistant_message(
                                messages=final_messages,
                                content=wrapped_message,
                            )
                    if buffered_assistant_events and final_assistant:
                        buffered_reasoning_text = self._collect_buffered_assistant_text(
                            buffered_assistant_events
                        )
                        if buffered_reasoning_text:
                            yield StreamEvent.thinking_delta(buffered_reasoning_text)
                            yield StreamEvent.thinking_end(elapsed=0.0)
                        buffered_assistant_events.clear()
                    if buffered_assistant_events and not final_assistant:
                        while buffered_assistant_events:
                            event = buffered_assistant_events.pop(0)
                            if event.type == "assistant":
                                final_assistant += event.content
                            yield event
                        thinking_emitter.assistant_emitted = bool(final_assistant)

                    if not thinking_emitter.assistant_emitted:
                        # Try to get response from agent_run.result first (pydantic-ai structure)
                        if not final_assistant and hasattr(agent_run, "result") and agent_run.result:
                            result = agent_run.result
                            # Try response property first
                            if hasattr(result, "response") and result.response:
                                response = result.response
                                # Extract text content from response parts, excluding thinking parts
                                if hasattr(response, "parts"):
                                    for part in response.parts:
                                        part_kind = getattr(part, "part_kind", "")
                                        # Skip thinking parts, only extract text parts
                                        if part_kind != "thinking" and hasattr(part, "content") and part.content:
                                            content = str(part.content)
                                            if content:
                                                final_assistant = content
                                                break
                                elif hasattr(response, "content") and response.content:
                                    final_assistant = str(response.content)
                            # Try data property as fallback
                            if not final_assistant and hasattr(result, "data") and result.data:
                                final_assistant = str(result.data)
                        
                        # Fallback: search in final_messages
                        if not final_assistant:
                            final_assistant = self._extract_latest_assistant_from_messages(
                                messages=final_messages,
                                start_index=run_output_start_index,
                            )
                        
                        if final_assistant:
                            thinking_emitter.assistant_emitted = True
                            yield StreamEvent.assistant_delta(final_assistant)
                    missing_required_tool_names = self._missing_required_tool_names(
                        decision=tool_gate_decision,
                        match_result=tool_match_result,
                        tool_call_summaries=tool_call_summaries,
                    )
                    if (
                        tool_gate_decision.policy is ToolPolicyMode.PREFER_TOOL
                        and missing_required_tool_names
                    ):
                        warning_message = self._build_tool_evidence_required_message(
                            match_result=tool_match_result,
                            missing_required_tools=missing_required_tool_names,
                        )
                        yield StreamEvent.runtime_update(
                            "warning",
                            warning_message,
                            metadata={
                                "phase": "final",
                                "attempt": current_model_attempt,
                                "elapsed": round(time.monotonic() - start_time, 1),
                            },
                        )
                    if (
                        tool_gate_decision.policy is ToolPolicyMode.MUST_USE_TOOL
                        and missing_required_tool_names
                    ):
                        failure_message = self._build_tool_evidence_required_message(
                            match_result=tool_match_result,
                            missing_required_tools=missing_required_tool_names,
                        )
                        safe_messages = self._remove_last_assistant_from_run(
                            messages=final_messages,
                            start_index=run_output_start_index,
                        )
                        await session_manager.persist_transcript(session_key, safe_messages)
                        await self.runtime_events.trigger_run_context_ready(
                            session_key=session_key,
                            run_id=run_id,
                            user_message=user_message,
                            system_prompt=system_prompt,
                            message_history=context_history_for_hooks,
                            assistant_message="",
                            tool_calls=tool_call_summaries,
                            run_status="completed" if final_assistant.strip() else "failed",
                            error="" if final_assistant.strip() else failure_message,
                            session_title=session_title,
                        )
                        if final_assistant.strip():
                            yield StreamEvent.runtime_update(
                                "warning",
                                failure_message,
                                metadata={
                                    "phase": "final",
                                    "attempt": current_model_attempt,
                                    "elapsed": round(time.monotonic() - start_time, 1),
                                },
                            )
                            buffered_assistant_events.clear()
                            final_assistant = ""
                        else:
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
                            if tool_gate_decision.policy in {
                                ToolPolicyMode.PREFER_TOOL,
                            }:
                                warning_message = (
                                    "Tool-backed verification did not produce a usable grounded final answer in this run."
                                )
                                safe_messages = self._remove_last_assistant_from_run(
                                    messages=final_messages,
                                    start_index=run_output_start_index,
                                )
                                await session_manager.persist_transcript(session_key, safe_messages)
                                await self.runtime_events.trigger_run_context_ready(
                                    session_key=session_key,
                                    run_id=run_id,
                                    user_message=user_message,
                                    system_prompt=system_prompt,
                                    message_history=context_history_for_hooks,
                                    assistant_message="",
                                    tool_calls=tool_call_summaries,
                                    run_status="completed",
                                    session_title=session_title,
                                )
                                yield StreamEvent.runtime_update(
                                    "warning",
                                    warning_message,
                                    metadata={
                                        "phase": "final",
                                        "attempt": current_model_attempt,
                                        "elapsed": round(time.monotonic() - start_time, 1),
                                    },
                                )
                                buffered_assistant_events.clear()
                                final_assistant = ""
                            else:
                                run_failed = True
                                failure_message = "The run ended without a usable final answer."
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
                                safe_messages = self._remove_last_assistant_from_run(
                                    messages=final_messages,
                                    start_index=run_output_start_index,
                                )
                                await session_manager.persist_transcript(session_key, safe_messages)
                                await self.runtime_events.trigger_run_context_ready(
                                    session_key=session_key,
                                    run_id=run_id,
                                    user_message=user_message,
                                    system_prompt=system_prompt,
                                    message_history=context_history_for_hooks,
                                    assistant_message="",
                                    tool_calls=tool_call_summaries,
                                    run_status="failed",
                                    error=failure_message,
                                    session_title=session_title,
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
                            await self.runtime_events.trigger_llm_completed(
                                session_key=session_key,
                                run_id=run_id,
                                assistant_message=final_assistant,
                            )
                            await session_manager.persist_transcript(session_key, final_messages)
                            await self._maybe_finalize_title(
                                session_manager=session_manager,
                                session_key=session_key,
                                session=session,
                                final_messages=final_messages,
                                user_message=user_message,
                            )
                            session_title = str(getattr(session, "title", "") or "")
                            await self.runtime_events.trigger_run_context_ready(
                                session_key=session_key,
                                run_id=run_id,
                                user_message=user_message,
                                system_prompt=system_prompt,
                                message_history=context_history_for_hooks,
                                assistant_message=final_assistant,
                                tool_calls=tool_call_summaries,
                                run_status="completed",
                                session_title=session_title,
                            )
                            yield StreamEvent.runtime_update(
                                "answered",
                                "Final answer ready.",
                                metadata={
                                    "phase": "final",
                                    "attempt": current_model_attempt,
                                    "elapsed": round(time.monotonic() - start_time, 1),
                                },
                            )

            except Exception as e:
                hard_failure_retried = False
                async for retry_event in self._retry_after_hard_token_failure(
                    error=e,
                    session_key=session_key,
                    user_message=user_message,
                    deps=deps,
                    selected_token_id=selected_token_id,
                    release_slot=release_slot,
                    thinking_emitter=thinking_emitter,
                    start_time=start_time,
                    max_tool_calls=max_tool_calls,
                    timeout_seconds=timeout_seconds,
                    token_failover_attempt=_token_failover_attempt,
                    emit_lifecycle_bounds=_emit_lifecycle_bounds,
                ):
                    hard_failure_retried = True
                    yield retry_event
                if hard_failure_retried:
                    release_slot = None
                    selected_token_id = None
                    return
                run_failed = True
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=str(e),
                )
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
                # Close thinking phase on exception to maintain contract
                async for event in thinking_emitter.close_if_active():
                    yield event
                        
                # Surface agent runtime errors as stream events.
                yield StreamEvent.runtime_update(
                    "failed",
                    f"Agent runtime error: {str(e)}",
                    metadata={"phase": "exception", "elapsed": round(time.monotonic() - start_time, 1)},
                )
                yield StreamEvent.error_event(f"agent_error: {str(e)}")

            # -- hook:agent_end --
            if not run_failed:
                await self.runtime_events.trigger_agent_end(
                    session_key=session_key,
                    run_id=run_id,
                    tool_calls_count=tool_calls_count,
                    compaction_applied=compaction_applied,
                )

            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_end()

        except Exception as e:
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
            # Close thinking phase on exception to maintain contract
            async for event in thinking_emitter.close_if_active():
                yield event
                
            yield StreamEvent.runtime_update(
                "failed",
                str(e),
                metadata={"phase": "exception", "elapsed": round(time.monotonic() - start_time, 1)},
            )
            yield StreamEvent.error_event(str(e))
        finally:
            if selected_token_id and self.token_interceptor is not None:
                headers = self._extract_rate_limit_headers(deps)
                if headers:
                    self.token_interceptor.on_response(selected_token_id, headers)
            if release_slot is not None:
                release_slot()

    async def _retry_after_hard_token_failure(
        self,
        *,
        error: Exception,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        selected_token_id: Optional[str],
        release_slot: Optional[Any],
        thinking_emitter: ThinkingStreamEmitter,
        start_time: float,
        max_tool_calls: int,
        timeout_seconds: int,
        token_failover_attempt: int,
        emit_lifecycle_bounds: bool,
    ) -> AsyncIterator[StreamEvent]:
        """Rotate away from a hard-failed token and retry the same run once."""
        if (
            selected_token_id is None
            or self.token_policy is None
            or self.token_interceptor is None
            or not self._is_hard_token_failure(error)
        ):
            return
        max_failover_attempts = max(len(self.token_policy.token_pool.tokens) - 1, 0)
        if token_failover_attempt >= max_failover_attempts:
            return

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        provider = extra.get("provider") if isinstance(extra.get("provider"), str) else None
        model = extra.get("model") if isinstance(extra.get("model"), str) else None
        error_text = str(error)
        self.token_interceptor.on_hard_failure(selected_token_id, error_text)
        next_token = self.token_policy.mark_session_token_unhealthy(
            session_key,
            reason=error_text,
            provider=provider,
            model=model,
        )
        if next_token is None or next_token.token_id == selected_token_id:
            return

        async for event in thinking_emitter.close_if_active():
            yield event
        if release_slot is not None:
            release_slot()

        yield StreamEvent.runtime_update(
            "retrying",
            f"Current model token failed with a provider-side error. Switching to fallback model token `{next_token.token_id}`.",
            metadata={
                "phase": "token_failover",
                "elapsed": round(time.monotonic() - start_time, 1),
                "attempt": token_failover_attempt + 1,
                "failed_token_id": selected_token_id,
                "fallback_token_id": next_token.token_id,
            },
        )
        async for event in self.run(
            session_key=session_key,
            user_message=user_message,
            deps=deps,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
            _token_failover_attempt=token_failover_attempt + 1,
            _emit_lifecycle_bounds=False,
        ):
            yield event
        if emit_lifecycle_bounds:
            yield StreamEvent.lifecycle_end()
        return

    def _is_hard_token_failure(self, error: Exception) -> bool:
        """Return true when an error indicates the current token should be evicted."""
        lowered = str(error).lower()
        hard_markers = (
            "status_code: 401",
            "status_code: 403",
            "status_code: 429",
            "authenticationerror",
            "accountoverdueerror",
            "forbidden",
            "invalid api key",
            "insufficient_quota",
            "api key format is incorrect",
            "provider returned error', 'code': 429",
            '"code": 429',
            "rate-limited upstream",
            "too many requests",
            "rate limit",
        )
        return any(marker in lowered for marker in hard_markers)

    async def _resolve_runtime_agent(
        self,
        session_key: str,
        deps: SkillDeps,
    ) -> tuple[Any, Optional[str], Optional[Any]]:
        """Resolve runtime agent instance and optional semaphore release callback."""
        if self.token_policy is None or self.agent_pool is None or self.agent_factory is None:
            return self.agent, None, None

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        provider = extra.get("provider") if isinstance(extra.get("provider"), str) else None
        model = extra.get("model") if isinstance(extra.get("model"), str) else None

        token = self.token_policy.get_or_select_session_token(
            session_key,
            provider=provider,
            model=model,
        )
        if token is None:
            return self.agent, None, None

        instance = await self.agent_pool.get_or_create(
            self.agent_id,
            token,
            self.agent_factory,
        )
        await instance.concurrency_sem.acquire()
        return instance.agent, token.token_id, instance.concurrency_sem.release

    def _extract_rate_limit_headers(self, deps: SkillDeps) -> dict[str, str]:
        """Best-effort extraction of ratelimit headers from deps.extra."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        candidates = [
            extra.get("rate_limit_headers"),
            extra.get("response_headers"),
            extra.get("llm_response_headers"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                return {str(k): str(v) for k, v in candidate.items()}
        return {}

    def _resolve_runtime_context_window_info(
        self,
        selected_token_id: Optional[str],
        deps: SkillDeps,
    ) -> ContextWindowInfo:
        """Resolve context window info with source tags for runtime guard checks."""
        selected_token_window: Optional[int] = None
        if selected_token_id and self.token_policy is not None:
            token = self.token_policy.token_pool.tokens.get(selected_token_id)
            context_window = getattr(token, "context_window", None) if token else None
            if isinstance(context_window, int) and context_window > 0:
                selected_token_window = context_window

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        runtime_override = extra.get("context_window") or extra.get("model_context_window")
        models_config_window = (
            extra.get("models_config_context_window")
            or extra.get("configured_context_window")
            or extra.get("provider_config_context_window")
        )
        default_window = self.compaction.config.context_window

        return resolve_context_window_info(
            selected_token_window=selected_token_window,
            models_config_window=models_config_window if isinstance(models_config_window, int) else None,
            runtime_override_window=runtime_override if isinstance(runtime_override, int) else None,
            default_window=default_window,
        )

    def _resolve_runtime_context_window(
        self,
        selected_token_id: Optional[str],
        deps: SkillDeps,
    ) -> Optional[int]:
        """Backward-compatible helper returning only resolved token count."""
        return self._resolve_runtime_context_window_info(selected_token_id, deps).tokens

    def _resolve_session_manager(self, session_key: str, deps: SkillDeps) -> Any:
        """Resolve the correct per-user session manager for the active session."""
        parsed = SessionKey.from_string(session_key)
        scoped_manager = getattr(deps, "session_manager", None)
        scoped_user_id = getattr(scoped_manager, "user_id", None)
        if scoped_manager is not None and scoped_user_id == parsed.user_id:
            return scoped_manager
        if self.session_manager_router is not None:
            return self.session_manager_router.for_session_key(session_key)
        return self.sessions

    async def _maybe_set_draft_title(
        self,
        *,
        session_manager: Any,
        session_key: str,
        session: Any,
        transcript: list[Any],
        user_message: str,
    ) -> None:
        """Create a draft title for brand-new chat threads."""
        if getattr(session, "title_status", "empty") not in {"", "empty"}:
            return
        if transcript:
            return
        draft_title = self.title_generator.build_draft_title(user_message)
        await session_manager.update_title(
            session_key,
            title=draft_title,
            title_status="draft",
        )
        session.title = draft_title
        session.title_status = "draft"

    async def _maybe_finalize_title(
        self,
        *,
        session_manager: Any,
        session_key: str,
        session: Any,
        final_messages: list[dict],
        user_message: str,
    ) -> None:
        """Promote a draft title to a stable final title after the first assistant reply."""
        if getattr(session, "title_status", "empty") == "final":
            return
        assistant_message = next(
            (
                msg.get("content", "")
                for msg in final_messages
                if msg.get("role") == "assistant" and msg.get("content")
            ),
            "",
        )
        final_title = self.title_generator.build_final_title(
            first_user_message=user_message,
            first_assistant_message=assistant_message,
            existing_title=getattr(session, "title", ""),
        )
        await session_manager.update_title(
            session_key,
            title=final_title,
            title_status="final",
        )
        session.title = final_title
        session.title_status = "final"

    @asynccontextmanager

    async def _run_iter_with_optional_override(
        self,
        *,
        agent: Any,
        user_message: str,
        deps: SkillDeps,
        message_history: list[dict],
        system_prompt: str,
    ):

        """Run `agent.iter()` with optional system-prompt overrides."""
        override_factory = getattr(agent, "override", None)

        if callable(override_factory) and system_prompt:
            try:
                override_cm = override_factory(system_prompt=system_prompt)
            except TypeError:
                override_cm = nullcontext()
        else:
            override_cm = nullcontext()

        if hasattr(override_cm, "__aenter__"):
            async with override_cm:
                async with agent.iter(
                    user_message,
                    deps=deps,
                    message_history=message_history,
                ) as agent_run:
                    yield agent_run
            return

        with override_cm:
            async with agent.iter(
                user_message,
                deps=deps,
                message_history=message_history,
            ) as agent_run:

                yield agent_run

    async def _iter_agent_nodes_with_timeout(self, agent_run: Any) -> AsyncIterator[Any]:
        iterator = agent_run.__aiter__()
        waiting_for_first_node = True
        while True:
            timeout_seconds = (
                self.MODEL_FIRST_NODE_TIMEOUT_SECONDS
                if waiting_for_first_node
                else self.MODEL_NEXT_NODE_TIMEOUT_SECONDS
            )
            try:
                node = await asyncio.wait_for(iterator.__anext__(), timeout=timeout_seconds)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise _ModelNodeTimeout(
                    first_node=waiting_for_first_node,
                    timeout_seconds=float(timeout_seconds),
                ) from exc
            waiting_for_first_node = False
            yield node

    def _is_model_request_node(self, node: Any) -> bool:
        """Return whether a node represents a model request boundary."""
        node_type = type(node).__name__.lower()
        return "modelrequest" in node_type or node_type.endswith("requestnode")

    def _is_call_tools_node(self, node: Any) -> bool:
        """Return whether a node represents the tool-dispatch boundary."""
        node_type = type(node).__name__.lower()
        return "calltools" in node_type

    async def run_single(
        self,
        user_message: str,
        deps: SkillDeps,
        *,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Run a single non-streaming agent call."""
        # Simplified helper that bypasses the streaming session pipeline.
        try:
            result = await self.agent.run(
                user_message,
                deps=deps,
            )
            return result.output if hasattr(result, "output") else str(result)
        except Exception as e:
            return f"[Error: {str(e)}]"

