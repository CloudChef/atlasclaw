from __future__ import annotations

import time
from typing import Any, AsyncIterator

from app.atlasclaw.agent.runner_tool.runner_execution_flow_error import RunnerExecutionFlowErrorMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_post import RunnerExecutionFlowPostMixin
from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import RunnerExecutionFlowStreamMixin
from app.atlasclaw.agent.stream import StreamEvent


class RunnerExecutionFlowPhaseMixin(
    RunnerExecutionFlowStreamMixin,
    RunnerExecutionFlowPostMixin,
    RunnerExecutionFlowErrorMixin,
):
    async def _run_loop_phase(self, *, state: dict[str, Any], _log_step: Any) -> AsyncIterator[StreamEvent]:
        """Main model/tool streaming loop phase."""
        deps = state.get("deps")
        user_message = state.get("user_message")
        raw_runtime_message_history = state.get("runtime_message_history")
        if raw_runtime_message_history is None:
            raw_runtime_message_history = state.get("message_history") or []
        runtime_message_history = list(raw_runtime_message_history)
        agent_run = None

        deps.user_message = user_message
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
            model_message_history = self.history.to_model_message_history(runtime_message_history)
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
                    "elapsed": round(time.monotonic() - float(state.get("start_time") or 0.0), 1),
                },
            )
            async with self._run_iter_with_optional_override(
                agent=state.get("runtime_agent"),
                user_message=user_message,
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

                async for event in self._process_agent_run_outcome(
                    agent_run=agent_run,
                    state=state,
                    _log_step=_log_step,
                ):
                    yield event

        except Exception as error:
            if agent_run is not None:
                try:
                    runtime_messages = self.history.normalize_messages(agent_run.all_messages())
                    merged_messages = self._merge_runtime_messages_with_session_prefix(
                        session_message_history=state.get("session_message_history") or [],
                        runtime_messages=runtime_messages,
                        runtime_base_history_len=int(state.get("runtime_base_history_len") or 0),
                    )
                    state["latest_runtime_messages"] = runtime_messages
                    state["latest_agent_messages"] = merged_messages
                    state["message_history"] = merged_messages
                except Exception:
                    pass
            async for event in self._handle_loop_phase_exception(error=error, state=state):
                yield event

