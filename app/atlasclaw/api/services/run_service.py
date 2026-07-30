# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status

from ...auth.models import ANONYMOUS_USER, UserInfo
from ...core.security_guard import encode_if_untrusted
from ...session.context import SessionKey, TranscriptEntry
from ..deps_context import APIContext, build_scoped_deps

logger = logging.getLogger(__name__)


def _transition_running_run(
    run_info: dict[str, Any],
    target_status: str,
    *,
    error: str | None = None,
) -> bool:
    """Move an active run to one terminal state without overwriting a prior terminal result."""
    if run_info.get("status") != "running":
        return False
    run_info["status"] = target_status
    run_info.setdefault("completed_at", datetime.now(timezone.utc))
    if error:
        run_info["error"] = error
    else:
        run_info.pop("error", None)
    return True


def build_provider_config(ctx: APIContext) -> dict[str, Any]:
    """Return provider instance config visible to a run from the active registry."""
    if ctx.service_provider_registry:
        return ctx.service_provider_registry.get_all_instance_configs()
    return {}


def init_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    message: str,
    timeout_seconds: int,
) -> None:
    """Create active-run state and its SSE stream before execution starts."""
    ctx.active_runs[run_id] = {
        "status": "running",
        "session_key": session_key,
        "started_at": datetime.now(timezone.utc),
        "message": message,
        "timeout_seconds": timeout_seconds,
        "abort_signal": asyncio.Event(),
        "abort_lifecycle_sent": False,
    }
    ctx.sse_manager.create_stream(run_id)


def normalize_user_message(message: str) -> str:
    """Encode untrusted user input before it enters transcripts or model prompts."""
    normalized, _ = encode_if_untrusted(message)
    return normalized


def get_run_or_404(ctx: APIContext, run_id: str) -> dict[str, Any]:
    """Return active-run metadata or raise the API-level not-found error."""
    run_info = ctx.active_runs.get(run_id)
    if not run_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run not found: {run_id}",
        )
    return run_info


def abort_run(ctx: APIContext, run_id: str) -> str:
    """Abort a running owned agent run and return its actual terminal/current status."""
    run_info = get_run_or_404(ctx, run_id)
    current_status = str(run_info.get("status") or "unknown")
    if current_status != "running":
        return current_status

    abort_signal = run_info.get("abort_signal")
    if isinstance(abort_signal, asyncio.Event):
        abort_signal.set()
    _transition_running_run(run_info, "aborted")
    if not run_info.get("abort_lifecycle_sent"):
        ctx.sse_manager.push_lifecycle(run_id, "aborted")
        run_info["abort_lifecycle_sent"] = True
    ctx.sse_manager.close_stream(run_id)
    return "aborted"


async def _iterate_until_aborted(
    events: AsyncIterator[Any],
    abort_signal: asyncio.Event,
) -> AsyncIterator[Any]:
    """Yield runner events in the caller task and cancel it when abort is signalled."""
    iterator = events.__aiter__()
    execution_task = asyncio.current_task()
    if execution_task is None:
        raise RuntimeError("Agent run execution requires an active asyncio task")

    async def cancel_execution_on_abort() -> None:
        await abort_signal.wait()
        if not execution_task.done():
            execution_task.cancel()

    abort_wait_task = asyncio.create_task(cancel_execution_on_abort())
    try:
        async for event in iterator:
            yield event
    except asyncio.CancelledError:
        if not abort_signal.is_set():
            raise
    finally:
        abort_wait_task.cancel()
        with suppress(asyncio.CancelledError):
            await abort_wait_task
        close_iterator = getattr(iterator, "aclose", None)
        if callable(close_iterator):
            with suppress(asyncio.CancelledError):
                await close_iterator()


async def complete_run_with_static_answer(
    ctx: APIContext,
    *,
    run_id: str,
    session_key: str,
    user_message: str,
    answer: str,
    reason: str,
) -> None:
    """Persist and complete a run whose answer is decided by API-side policy.

    This is used for request-bound validation outcomes that should not call the
    LLM, such as an explicit slash command that is not visible to the current
    user. The function mirrors the observable run contract: transcript entries,
    SSE events, and final run status are all produced.
    """
    session_manager = ctx.session_manager_router.for_session_key(session_key)
    await session_manager.append_transcript(
        session_key,
        TranscriptEntry(
            role="user",
            content=user_message,
            metadata={"static_answer_reason": reason},
        ),
    )
    await session_manager.append_transcript(
        session_key,
        TranscriptEntry(
            role="assistant",
            content=answer,
            metadata={"static_answer_reason": reason},
        ),
    )
    run_info = ctx.active_runs.get(run_id)
    if run_info is None or run_info.get("status") != "running":
        return
    ctx.sse_manager.push_lifecycle(run_id, "start")
    ctx.sse_manager.push_assistant(run_id, answer, is_delta=False)
    ctx.sse_manager.push_runtime(
        run_id,
        "answered",
        "Final answer ready.",
        metadata={"static_answer_reason": reason},
    )
    if not _transition_running_run(run_info, "completed"):
        return
    run_info["tokens_used"] = 0
    ctx.sse_manager.push_lifecycle(run_id, "end")
    ctx.sse_manager.close_stream(run_id)


async def execute_agent_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    message: str,
    timeout_seconds: int,
    user_info: Optional[UserInfo] = None,
    request_cookies: Optional[dict[str, str]] = None,
    provider_config: Optional[dict[str, Any]] = None,
    request_context: Optional[dict[str, Any]] = None,
) -> None:
    """Execute one agent run and bridge runner events into SSE/status state.

    The caller is responsible for creating the run with ``init_run`` and for
    passing already validated request context, including selected capabilities
    and authorization-scoped provider config.
    """
    _user_info = user_info or ANONYMOUS_USER
    encountered_error = False
    final_error_message = ""
    final_answer_committed = False

    try:
        target_agent_id = SessionKey.from_string(session_key).agent_id or "main"
        runner = None
        if ctx.agent_runners:
            runner = ctx.agent_runners.get(target_agent_id) or ctx.agent_runners.get("main")
        if runner is None:
            runner = ctx.agent_runner

        if not runner:
            raise RuntimeError(
                "AgentRunner not configured. Ensure LLM provider is properly configured in atlasclaw.json",
            )

        deps_extra: dict[str, Any] = {
            "agent_id": target_agent_id,
            "run_id": run_id,
            "context": request_context or {},
        }
        deps = build_scoped_deps(
            ctx,
            _user_info,
            session_key,
            abort_signal=get_run_or_404(ctx, run_id).get("abort_signal"),
            request_cookies=request_cookies,
            provider_config=provider_config,
            extra=deps_extra,
        )

        runner_events = runner.run(
            session_key=session_key,
            user_message=message,
            deps=deps,
            timeout_seconds=timeout_seconds,
        )
        async with aclosing(
            _iterate_until_aborted(runner_events, deps.abort_signal)
        ) as abortable_events:
            async for event in abortable_events:
                if event.type == "lifecycle":
                    if event.phase == "aborted":
                        abort_run(ctx, run_id)
                        break
                    if event.phase in {"end", "error", "timeout"}:
                        run_info = ctx.active_runs.get(run_id)
                        if run_info is None:
                            break
                        terminal_status = event.phase
                        terminal_error = final_error_message
                        if event.phase == "end":
                            if encountered_error or not final_answer_committed:
                                terminal_status = "error"
                                terminal_error = (
                                    final_error_message
                                    or "Run ended without a committed final answer"
                                )
                            else:
                                terminal_status = "completed"
                        if not _transition_running_run(
                            run_info,
                            terminal_status,
                            error=(
                                terminal_error
                                if terminal_status in {"error", "timeout"}
                                else None
                            ),
                        ):
                            break
                        ctx.sse_manager.push_lifecycle(
                            run_id,
                            "end" if terminal_status == "completed" else terminal_status,
                        )
                        break
                    if ctx.active_runs.get(run_id, {}).get("status") == "running":
                        ctx.sse_manager.push_lifecycle(run_id, event.phase)
                elif event.type == "assistant":
                    ctx.sse_manager.push_assistant(run_id, event.content)
                elif event.type == "tool":
                    result_str = str(event.content) if event.content else None
                    ctx.sse_manager.push_tool(
                        run_id,
                        event.tool,
                        event.phase,
                        result=result_str,
                    )
                elif event.type == "error":
                    encountered_error = True
                    final_error_message = str(event.error or final_error_message or "")
                    ctx.sse_manager.push_error(run_id, event.error)
                elif event.type == "thinking":
                    ctx.sse_manager.push_thinking(
                        run_id,
                        event.phase,
                        event.content,
                        metadata=event.metadata if event.metadata else None,
                    )
                elif event.type == "runtime":
                    runtime_state = str(
                        (event.metadata or {}).get("state", "") or ""
                    ).strip().lower()
                    if runtime_state == "failed":
                        encountered_error = True
                        final_error_message = str(event.content or final_error_message or "")
                    elif (
                        runtime_state == "answered"
                        and str(event.content or "").strip() == "Final answer ready."
                    ):
                        final_answer_committed = True
                    ctx.sse_manager.push_runtime(
                        run_id,
                        str((event.metadata or {}).get("state", "")),
                        event.content,
                        metadata=event.metadata if event.metadata else None,
                    )

        run_info = ctx.active_runs.get(run_id)
        if run_info is not None and run_info.get("status") == "running":
            if deps.is_aborted():
                abort_run(ctx, run_id)
            elif encountered_error or not final_answer_committed:
                error_message = (
                    final_error_message
                    or "Run ended without a committed final answer"
                )
                if _transition_running_run(run_info, "error", error=error_message):
                    ctx.sse_manager.push_lifecycle(run_id, "error")
            else:
                if _transition_running_run(run_info, "completed"):
                    ctx.sse_manager.push_lifecycle(run_id, "end")

    except asyncio.TimeoutError:
        run_info = ctx.active_runs.get(run_id)
        if run_info is None or run_info.get("status") != "running":
            return
        if _transition_running_run(
            run_info,
            "timeout",
            error="Execution timed out",
        ):
            ctx.sse_manager.push_error(run_id, "Agent execution timed out")
            ctx.sse_manager.push_lifecycle(run_id, "timeout")

    except Exception as e:
        run_info = ctx.active_runs.get(run_id)
        if run_info is None or run_info.get("status") != "running":
            return
        error_msg = str(e)
        if _transition_running_run(run_info, "error", error=error_msg):
            ctx.sse_manager.push_error(run_id, error_msg)
            ctx.sse_manager.push_lifecycle(run_id, "error")

    finally:
        ctx.sse_manager.close_stream(run_id)
