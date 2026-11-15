# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Thin application policy for PydanticAI's native model-response hook."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from app.atlasclaw.agent.plaintext_tool_calls import looks_like_plaintext_tool_call_attempt
from app.atlasclaw.agent.runner_tool.runner_execution_flow_post import RunnerExecutionFlowPostMixin
from app.atlasclaw.agent.runner_tool_evidence import RunnerToolEvidenceMixin
from app.atlasclaw.core.deps import SkillDeps

logger = logging.getLogger(__name__)
PROGRESS_SINK = "_execution_progress_sink"
STAGE_INSTRUCTIONS = """User-facing intermediate updates:
When continuing a multi-step task after successful read-only tool results, include a short,
useful finding for the user as normal assistant text alongside your next tool calls.
Generate it from the actual results in this conversation.
Follow the shared Response Language policy for intermediate updates, just as for final answers.
Do not narrate thinking, tool names, internal fields, raw payloads or execution logs.
Distinguish confirmed facts from uncertainty. Failed, pending or accepted actions are not completed.
Do not report a future tool's result or ask the user to reply while continuing tools.
Only include new useful information. Never output a progress-only response instead of required tools,
and do not make an extra model or tool call just to provide an update.
Finish with your normal complete answer once the work is done.
"""


def _completed_content(content: Any) -> bool:
    """Reject explicit business failures inside otherwise successful tool returns."""
    for _ in range(3):
        if isinstance(content, str) and content.lstrip().startswith("{"):
            if len(content) > 2 * 1024 * 1024:
                return False
            try:
                content = json.loads(content)
            except ValueError:
                return False
        if not isinstance(content, dict):
            return True  # Native ToolReturnPart.outcome already verified execution.
        if content.get("success") is False or content.get("error") or content.get("is_error") or content.get("returncode", 0) != 0:
            return False
        status = content.get("status")
        if isinstance(status, str) and status.strip().lower() in {"failed", "pending", "accepted", "partial", "unknown"}:
            return False
        output = content.get("output")
        if isinstance(output, str) and output.lstrip().startswith("{"):
            content = output
            continue
        return True
    return False


def build_progress_hooks(deps: SkillDeps) -> Optional[Hooks]:
    """Adapt eligible model commentary to the existing per-run output sink."""
    if not callable(deps.extra.get(PROGRESS_SINK)):
        return None

    async def after_model(ctx: Any, *, request_context: Any, response: ModelResponse) -> ModelResponse:
        def read_only(name: str) -> bool:
            return any(tool.get("name") == name and tool.get("read_only") is True
                       for tool in deps.extra.get("tools_snapshot", []) if isinstance(tool, dict))

        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
        messages = request_context.messages
        if ctx.run_step < 2 or not calls or len(messages) < 2:
            return response
        previous, returned = messages[-2:]
        if not isinstance(previous, ModelResponse) or not isinstance(returned, ModelRequest) or returned.run_id != ctx.run_id:
            return response
        results = returned.parts
        expected = {part.tool_call_id for part in previous.parts if isinstance(part, ToolCallPart)}
        if not results or not all(isinstance(part, ToolReturnPart) and part.outcome == "success"
                                  and read_only(part.tool_name) and _completed_content(part.content) for part in results):
            return response
        if expected != {part.tool_call_id for part in results}:
            return response
        # Use the native run identity/history instead of tracking tool execution ourselves.
        current_calls = [part for message in messages if isinstance(message, ModelResponse) and message.run_id == ctx.run_id
                         for part in message.parts if isinstance(part, ToolCallPart)]
        if not all(read_only(call.tool_name) for call in [*current_calls, *calls]):
            return response
        text = "\n".join(part.content for part in response.parts if isinstance(part, TextPart)).strip()
        if (not text or len(text) > 1000 or looks_like_plaintext_tool_call_attempt(text)
                or RunnerExecutionFlowPostMixin._looks_like_raw_lookup_dump(text)
                or RunnerToolEvidenceMixin._looks_like_raw_tool_payload_dump(text)):
            return response
        sink = deps.extra.get(PROGRESS_SINK)
        if callable(sink):
            try:
                sink("reasoning", text, {"source": "agent_model", "phase": "user_result", "model_step": ctx.run_step})
            except Exception:
                logger.warning("Model update could not be published")
        return response

    return Hooks(after_model_request=after_model)
