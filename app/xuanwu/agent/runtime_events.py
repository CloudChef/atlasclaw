"""Runtime event and hook dispatch helpers for AgentRunner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.xuanwu.agent.stream import StreamEvent


@dataclass
class ToolDispatchResult:
    """Result returned by tool-call dispatch."""

    events: list[StreamEvent]
    tool_calls_count: int
    should_break: bool = False


class RuntimeEventDispatcher:
    """Dispatch runtime hooks and convert tool activity into stream events."""

    def __init__(self, hooks: Any = None, session_queue: Any = None) -> None:
        self.hooks = hooks
        self.queue = session_queue

    async def trigger_llm_input(
        self,
        *,
        session_key: str,
        user_message: str,
        system_prompt: str,
        message_history: list[dict],
    ) -> None:
        if not self.hooks:
            return
        await self.hooks.trigger(
            "llm_input",
            {
                "session_key": session_key,
                "user_message": user_message,
                "system_prompt": system_prompt,
                "message_history": message_history,
            },
        )

    async def trigger_agent_end(
        self,
        *,
        session_key: str,
        tool_calls_count: int,
        compaction_applied: bool,
    ) -> None:
        if not self.hooks:
            return
        await self.hooks.trigger(
            "agent_end",
            {
                "session_key": session_key,
                "tool_calls_count": tool_calls_count,
                "compaction_applied": compaction_applied,
            },
        )

    def collect_tool_calls(self, node: Any) -> list[Any]:
        """Collect tool-call metadata from an agent node."""
        if hasattr(node, "tool_call_metadata") and node.tool_call_metadata:
            return list(node.tool_call_metadata)
        if hasattr(node, "tool_calls") and node.tool_calls:
            return list(node.tool_calls)
        if hasattr(node, "tool_name"):
            return [{"name": str(node.tool_name)}]
        return []

    async def dispatch_tool_calls(
        self,
        tool_calls_in_node: list[Any],
        *,
        tool_calls_count: int,
        max_tool_calls: int,
        deps: Any,
        session_key: str,
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

            events.append(StreamEvent.tool_start(tool_name))
            events.append(StreamEvent.tool_end(tool_name))

            if self.hooks:
                await self.hooks.trigger("after_tool_call", {"tool": tool_name})

            if self.queue:
                steer_messages = self.queue.get_steer_messages(session_key)
                if steer_messages:
                    combined = "\n".join(steer_messages)
                    events.append(StreamEvent.assistant_delta(f"\n[用户补充]: {combined}\n"))

        return ToolDispatchResult(events=events, tool_calls_count=tool_calls_count, should_break=False)

