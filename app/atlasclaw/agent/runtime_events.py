"""Runtime event and hook dispatch helpers for AgentRunner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from pydantic_ai.messages import ToolCallPart

from app.atlasclaw.agent.tool_gate_models import CapabilityMatchResult, ToolEnforcementOutcome, ToolGateDecision
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.hooks.runtime import HookRuntime
from app.atlasclaw.hooks.runtime_models import HookEventType
from app.atlasclaw.session.context import SessionKey

logger = logging.getLogger(__name__)


@dataclass
class ToolDispatchResult:
    """Result returned by tool-call dispatch."""

    events: list[StreamEvent]
    tool_calls_count: int
    should_break: bool = False


class RuntimeEventDispatcher:
    """Dispatch runtime hooks and convert tool activity into stream events."""

    def __init__(
        self,
        hooks: Any = None,
        session_queue: Any = None,
        hook_runtime: HookRuntime | None = None,
    ) -> None:
        self.hooks = hooks
        self.queue = session_queue
        self.hook_runtime = hook_runtime
        self._background_event_tasks: set[asyncio.Task[Any]] = set()

    async def trigger_llm_input(
        self,
        *,
        session_key: str,
        run_id: str = "",
        user_message: str,
        system_prompt: str,
        message_history: list[dict],
    ) -> None:
        payload = {
            "session_key": session_key,
            "user_message": user_message,
            "system_prompt": system_prompt,
            "message_history": message_history,
        }
        if self.hooks:
            await self.hooks.trigger("llm_input", payload)
        self._emit_runtime_event_background(
            HookEventType.LLM_REQUESTED,
            session_key=session_key,
            run_id=run_id,
            payload=payload,
        )

    async def trigger_agent_end(
        self,
        *,
        session_key: str,
        run_id: str = "",
        tool_calls_count: int,
        compaction_applied: bool,
    ) -> None:
        payload = {
            "session_key": session_key,
            "tool_calls_count": tool_calls_count,
            "compaction_applied": compaction_applied,
        }
        if self.hooks:
            await self.hooks.trigger("agent_end", payload)
        await self._emit_runtime_event(
            HookEventType.RUN_COMPLETED,
            session_key=session_key,
            run_id=run_id,
            payload=payload,
        )

    async def trigger_run_started(
        self,
        *,
        session_key: str,
        run_id: str,
        user_message: str,
    ) -> None:
        self._emit_runtime_event_background(
            HookEventType.RUN_STARTED,
            session_key=session_key,
            run_id=run_id,
            payload={"user_message": user_message},
        )

    async def trigger_message_received(
        self,
        *,
        session_key: str,
        run_id: str,
        user_message: str,
    ) -> None:
        self._emit_runtime_event_background(
            HookEventType.MESSAGE_RECEIVED,
            session_key=session_key,
            run_id=run_id,
            payload={"message": user_message},
        )

    async def trigger_run_failed(
        self,
        *,
        session_key: str,
        run_id: str,
        error: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.RUN_FAILED,
            session_key=session_key,
            run_id=run_id,
            payload={"error": error},
        )

    async def trigger_llm_completed(
        self,
        *,
        session_key: str,
        run_id: str,
        assistant_message: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.LLM_COMPLETED,
            session_key=session_key,
            run_id=run_id,
            payload={"assistant_message": assistant_message},
        )

    async def trigger_llm_failed(
        self,
        *,
        session_key: str,
        run_id: str,
        error: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.LLM_FAILED,
            session_key=session_key,
            run_id=run_id,
            payload={"error": error},
        )

    async def trigger_run_context_ready(
        self,
        *,
        session_key: str,
        run_id: str,
        user_message: str,
        system_prompt: str,
        message_history: list[dict],
        assistant_message: str,
        tool_calls: list[dict[str, Any]],
        run_status: str,
        error: str = "",
        session_title: str = "",
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.RUN_CONTEXT_READY,
            session_key=session_key,
            run_id=run_id,
            payload={
                "user_message": user_message,
                "system_prompt": system_prompt,
                "message_history": message_history,
                "assistant_message": assistant_message,
                "tool_calls": tool_calls,
                "run_status": run_status,
                "error": error,
                "session_title": session_title,
            },
        )

    async def trigger_tool_gate_evaluated(
        self,
        *,
        session_key: str,
        run_id: str,
        decision: ToolGateDecision,
    ) -> None:
        payload = {
            "needs_tool": decision.needs_tool,
            "needs_live_data": decision.needs_live_data,
            "needs_private_context": decision.needs_private_context,
            "needs_external_system": decision.needs_external_system,
            "needs_browser_interaction": decision.needs_browser_interaction,
            "needs_grounded_verification": decision.needs_grounded_verification,
            "suggested_tool_classes": list(decision.suggested_tool_classes),
            "confidence": decision.confidence,
            "reason": decision.reason,
            "policy": decision.policy.value,
        }
        self._emit_runtime_event_background(
            HookEventType.TOOL_GATE_EVALUATED,
            session_key=session_key,
            run_id=run_id,
            payload=payload,
        )
        self._emit_runtime_event_background(
            HookEventType.TOOL_GATE_REQUIRED if decision.needs_tool else HookEventType.TOOL_GATE_OPTIONAL,
            session_key=session_key,
            run_id=run_id,
            payload=payload,
        )

    async def trigger_tool_matcher_resolved(
        self,
        *,
        session_key: str,
        run_id: str,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
    ) -> None:
        payload = {
            "policy": decision.policy.value,
            "suggested_tool_classes": list(decision.suggested_tool_classes),
            "resolved_policy": match_result.resolved_policy.value,
            "resolved_tools": [candidate.model_dump() for candidate in match_result.tool_candidates],
            "missing_capabilities": list(match_result.missing_capabilities),
            "reason": match_result.reason,
            "confidence": decision.confidence,
        }
        self._emit_runtime_event_background(
            HookEventType.TOOL_MATCHER_RESOLVED,
            session_key=session_key,
            run_id=run_id,
            payload=payload,
        )
        if match_result.missing_capabilities:
            self._emit_runtime_event_background(
                HookEventType.TOOL_MATCHER_MISSING_CAPABILITY,
                session_key=session_key,
                run_id=run_id,
                payload=payload,
            )

    async def trigger_hint_ranking_started(
        self,
        *,
        session_key: str,
        run_id: str,
        candidate_count: int,
        provider_hint_count: int,
        skill_hint_count: int,
    ) -> None:
        self._emit_runtime_event_background(
            HookEventType.HINT_RANKING_STARTED,
            session_key=session_key,
            run_id=run_id,
            payload={
                "candidate_count": int(candidate_count),
                "provider_hint_count": int(provider_hint_count),
                "skill_hint_count": int(skill_hint_count),
            },
        )

    async def trigger_hint_ranking_completed(
        self,
        *,
        session_key: str,
        run_id: str,
        preferred_provider_types: list[str],
        preferred_capability_classes: list[str],
        preferred_tool_names: list[str],
        confidence: float,
        reason: str,
        elapsed_ms: int,
    ) -> None:
        self._emit_runtime_event_background(
            HookEventType.HINT_RANKING_COMPLETED,
            session_key=session_key,
            run_id=run_id,
            payload={
                "preferred_provider_types": list(preferred_provider_types),
                "preferred_capability_classes": list(preferred_capability_classes),
                "preferred_tool_names": list(preferred_tool_names),
                "confidence": float(confidence),
                "reason": str(reason or ""),
                "elapsed_ms": int(elapsed_ms),
            },
        )

    async def trigger_hint_ranking_fallback(
        self,
        *,
        session_key: str,
        run_id: str,
        reason: str,
        elapsed_ms: int,
    ) -> None:
        self._emit_runtime_event_background(
            HookEventType.HINT_RANKING_FALLBACK,
            session_key=session_key,
            run_id=run_id,
            payload={
                "reason": str(reason or ""),
                "elapsed_ms": int(elapsed_ms),
            },
        )

    async def trigger_tool_enforcement_blocked(
        self,
        *,
        session_key: str,
        run_id: str,
        decision: ToolGateDecision,
        match_result: CapabilityMatchResult,
        outcome: ToolEnforcementOutcome,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.TOOL_ENFORCEMENT_BLOCKED_FINAL_ANSWER,
            session_key=session_key,
            run_id=run_id,
            payload={
                "policy": decision.policy.value,
                "reason": decision.reason,
                "resolved_tools": [candidate.model_dump() for candidate in match_result.tool_candidates],
                "missing_capabilities": list(match_result.missing_capabilities),
                "failure_message": outcome.failure_message or "",
                "blocked_final_answer": outcome.blocked_final_answer,
            },
        )

    async def trigger_tool_enforcement_prefetch_started(
        self,
        *,
        session_key: str,
        run_id: str,
        tool_name: str,
        query: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.TOOL_ENFORCEMENT_PREFETCH_STARTED,
            session_key=session_key,
            run_id=run_id,
            payload={"tool_name": tool_name, "query": query},
        )

    async def trigger_tool_enforcement_prefetch_completed(
        self,
        *,
        session_key: str,
        run_id: str,
        tool_name: str,
        query: str,
        result_count: int,
        provider: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.TOOL_ENFORCEMENT_PREFETCH_COMPLETED,
            session_key=session_key,
            run_id=run_id,
            payload={
                "tool_name": tool_name,
                "query": query,
                "result_count": result_count,
                "provider": provider,
            },
        )

    async def trigger_tool_enforcement_prefetch_failed(
        self,
        *,
        session_key: str,
        run_id: str,
        tool_name: str,
        query: str,
        error: str,
    ) -> None:
        await self._emit_runtime_event(
            HookEventType.TOOL_ENFORCEMENT_PREFETCH_FAILED,
            session_key=session_key,
            run_id=run_id,
            payload={"tool_name": tool_name, "query": query, "error": error},
        )

    def collect_tool_calls(self, node: Any) -> list[Any]:
        """Collect tool-call metadata from an agent node."""
        normalized_calls: list[dict[str, Any]] = []

        if hasattr(node, "tool_call_metadata") and node.tool_call_metadata:
            for tool_call in node.tool_call_metadata:
                normalized = self._normalize_tool_call(tool_call)
                if normalized:
                    normalized_calls.append(normalized)
        if normalized_calls:
            return normalized_calls

        if hasattr(node, "tool_calls") and node.tool_calls:
            for tool_call in node.tool_calls:
                normalized = self._normalize_tool_call(tool_call)
                if normalized:
                    normalized_calls.append(normalized)
        if normalized_calls:
            return normalized_calls

        model_response = getattr(node, "model_response", None)
        response_parts = getattr(model_response, "parts", None) or []
        for part in response_parts:
            normalized = self._normalize_tool_call(part)
            if normalized:
                normalized_calls.append(normalized)
        if normalized_calls:
            return normalized_calls

        if hasattr(node, "tool_name"):
            return [{"name": str(node.tool_name)}]
        return []

    @staticmethod
    def _normalize_tool_call(tool_call: Any) -> dict[str, Any] | None:
        """Normalize runtime tool-call metadata across PydanticAI node shapes."""
        if tool_call is None:
            return None

        if isinstance(tool_call, dict):
            tool_name = str(tool_call.get("name", tool_call.get("tool_name", "")) or "").strip()
            if not tool_name:
                return None
            normalized: dict[str, Any] = {
                "name": tool_name,
                "args": tool_call.get("args", tool_call.get("arguments", {})) or {},
            }
            tool_call_id = str(
                tool_call.get("id", tool_call.get("tool_call_id", tool_call.get("toolCallId", ""))) or ""
            ).strip()
            if tool_call_id:
                normalized["id"] = tool_call_id
            return normalized

        if isinstance(tool_call, ToolCallPart) or getattr(tool_call, "part_kind", "") in {"tool-call", "tool_call"}:
            tool_name = str(getattr(tool_call, "tool_name", getattr(tool_call, "name", "")) or "").strip()
            if not tool_name:
                return None
            normalized = {
                "name": tool_name,
                "args": getattr(tool_call, "args", getattr(tool_call, "arguments", {})) or {},
            }
            tool_call_id = str(
                getattr(
                    tool_call,
                    "tool_call_id",
                    getattr(tool_call, "toolCallId", getattr(tool_call, "id", "")),
                )
                or ""
            ).strip()
            if tool_call_id:
                normalized["id"] = tool_call_id
            return normalized

        tool_name = str(getattr(tool_call, "tool_name", getattr(tool_call, "name", "")) or "").strip()
        if not tool_name:
            return None
        normalized = {
            "name": tool_name,
            "args": getattr(tool_call, "args", getattr(tool_call, "arguments", {})) or {},
        }
        tool_call_id = str(
            getattr(tool_call, "tool_call_id", getattr(tool_call, "toolCallId", getattr(tool_call, "id", "")))
            or ""
        ).strip()
        if tool_call_id:
            normalized["id"] = tool_call_id
        return normalized

    async def dispatch_tool_calls(
        self,
        tool_calls_in_node: list[Any],
        *,
        tool_calls_count: int,
        max_tool_calls: int,
        deps: Any,
        session_key: str,
        run_id: str = "",
    ) -> ToolDispatchResult:
        """Dispatch tool start/end events and related hooks."""
        events: list[StreamEvent] = []

        for tc in tool_calls_in_node:
            tool_calls_count += 1
            if isinstance(tc, dict):
                tool_name = tc.get("name", tc.get("tool_name", "unknown_tool"))
            else:
                tool_name = getattr(tc, "tool_name", getattr(tc, "name", "unknown_tool"))
            tool_name = str(tool_name)

            if deps.is_aborted():
                events.append(StreamEvent.lifecycle_aborted())
                return ToolDispatchResult(events=events, tool_calls_count=tool_calls_count, should_break=True)

            if tool_calls_count > max_tool_calls:
                events.append(StreamEvent.error_event("max_tool_calls_exceeded"))
                return ToolDispatchResult(events=events, tool_calls_count=tool_calls_count, should_break=True)

            if self.hooks:
                await self.hooks.trigger("before_tool_call", {"tool": tool_name})
            self._emit_runtime_event_background(
                HookEventType.TOOL_STARTED,
                session_key=session_key,
                run_id=run_id,
                payload={"tool_name": tool_name},
            )

            events.append(StreamEvent.tool_start(tool_name))
            events.append(StreamEvent.tool_end(tool_name))

            if self.hooks:
                await self.hooks.trigger("after_tool_call", {"tool": tool_name})
            self._emit_runtime_event_background(
                HookEventType.TOOL_COMPLETED,
                session_key=session_key,
                run_id=run_id,
                payload={"tool_name": tool_name},
            )

            if self.queue:
                steer_messages = self.queue.get_steer_messages(session_key)
                if steer_messages:
                    combined = "\n".join(steer_messages)
                    events.append(StreamEvent.assistant_delta(f"\n[用户补充]: {combined}\n"))

        return ToolDispatchResult(events=events, tool_calls_count=tool_calls_count, should_break=False)

    async def _emit_runtime_event(
        self,
        event_type: HookEventType,
        *,
        session_key: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self.hook_runtime is None:
            return
        parsed = SessionKey.from_string(session_key)
        await self.hook_runtime.emit(
            event_type=event_type,
            user_id=parsed.user_id,
            session_key=session_key,
            run_id=run_id,
            channel=parsed.channel,
            agent_id=parsed.agent_id,
            payload=payload,
        )

    def _emit_runtime_event_background(
        self,
        event_type: HookEventType,
        *,
        session_key: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Schedule runtime-event emission without blocking the main request path."""
        if self.hook_runtime is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._emit_runtime_event(
                event_type,
                session_key=session_key,
                run_id=run_id,
                payload=dict(payload),
            )
        )
        self._background_event_tasks.add(task)
        task.add_done_callback(self._on_background_event_done)

    def _on_background_event_done(self, task: asyncio.Task[Any]) -> None:
        self._background_event_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive logging only
            logger.warning("Runtime event background dispatch failed: %s", exc)

