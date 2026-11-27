# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status

from ...agent.runner_tool.runner_progress_hooks import PROGRESS_SINK
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


async def _filter_provider_instances_for_new_run(
    ctx: APIContext,
    provider_config: Optional[dict[str, Any]],
    *,
    admission: Any = None,
) -> dict[str, Any]:
    """Apply the current activation overlay while honoring a server admission."""
    provider_config = provider_config or {}
    original_provider_config = provider_config
    resolver = getattr(ctx, "model_binding_resolver", None)
    effective_instances = getattr(resolver, "effective_provider_instances", None)
    if callable(effective_instances):
        provider_config = await effective_instances(provider_config)
        # A Provider host has already admitted this exact server-owned run. Keep
        # its input config visible to that run if activation changed after start.
        # All other new runs use only the fresh committed activation view.
        admitted_type = str(getattr(admission, "provider_type", "") or "").strip().lower()
        admitted_instance = str(getattr(admission, "instance_name", "") or "").strip()
        if admitted_type and admitted_instance:
            for provider_type, instances in original_provider_config.items():
                if str(provider_type).strip().lower() != admitted_type or not isinstance(instances, dict):
                    continue
                admitted_config = instances.get(admitted_instance)
                if isinstance(admitted_config, dict):
                    bucket = provider_config.setdefault(str(provider_type), {})
                    bucket.setdefault(admitted_instance, dict(admitted_config))
                break
    provider_is_enabled = getattr(resolver, "provider_is_enabled", None)
    if not callable(provider_is_enabled):
        return provider_config
    admitted = (
        str(getattr(admission, "provider_type", "") or "").strip().lower(),
        str(getattr(admission, "instance_name", "") or "").strip(),
    )
    filtered: dict[str, Any] = {}
    for provider_type, instances in provider_config.items():
        if not isinstance(instances, dict):
            continue
        active_instances: dict[str, Any] = {}
        for instance_name, instance_config in instances.items():
            exact_admission = (
                str(provider_type).strip().lower() == admitted[0]
                and str(instance_name).strip() == admitted[1]
                and bool(admitted[0] and admitted[1])
            )
            if exact_admission or await provider_is_enabled(str(provider_type), str(instance_name)):
                active_instances[str(instance_name)] = instance_config
        if active_instances:
            filtered[str(provider_type)] = active_instances
    return filtered


def init_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    message: str,
    timeout_seconds: int | None,
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
    attachments: Optional[list[dict]] = None,
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
            metadata={"static_answer_reason": reason, "attachments": attachments or [], "model_input_eligible": False},
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
    timeout_seconds: int | None,
    user_info: Optional[UserInfo] = None,
    request_cookies: Optional[dict[str, str]] = None,
    provider_config: Optional[dict[str, Any]] = None,
    request_context: Optional[dict[str, Any]] = None,
    run_limits: Optional[dict[str, int]] = None,
    authorization_context: Optional[Any] = None,
    provider_admission: Optional[Any] = None,
) -> None:
    """Execute one agent run and bridge runner events into SSE/status state.

    The caller is responsible for creating the run with ``init_run`` and for
    passing already validated request context, including selected capabilities
    and authorization-scoped provider config. ``run_limits`` (internal callers
    only) may raise the per-run tool/model-request ceilings.
    """
    _user_info = user_info or ANONYMOUS_USER
    encountered_error = False
    final_error_message = ""
    final_answer_committed = False
    progress_started = time.monotonic()
    progress_closed = False
    deps = None

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
        if provider_admission is not None:
            from ...core.model_bindings import ProviderAdmission

            if (
                not isinstance(provider_admission, ProviderAdmission)
                or str(provider_admission.run_id) != str(run_id)
                or str(provider_admission.user_id) != str(_user_info.user_id)
            ):
                raise RuntimeError("Provider admission context does not match the active run")
        provider_config = await _filter_provider_instances_for_new_run(
            ctx,
            provider_config,
            admission=provider_admission,
        )
        limits = run_limits or {}
        if limits:
            model_limit = int(limits.get("max_model_requests", 0))
            deps_extra["_model_request_limit"] = model_limit if model_limit > 0 else -1
            max_tool_calls = int(limits.get("max_tool_calls", 0))
        else:
            max_tool_calls = 50
        from ...core.chat_attachments import ChatAttachments, AttachmentInputError, image_token_allowance, MAX_FILES, MAX_HISTORY_BYTES, MAX_HISTORY_IMAGES
        from ...agent.model_capabilities import image_capabilities, session_model

        session_manager = ctx.session_manager_router.for_session_key(session_key)
        attachments = get_run_or_404(ctx, run_id).get("attachments", [])
        image_attachments = [ref for ref in attachments if ref.get("kind", "image") == "image"]
        transcript = await session_manager.load_transcript(session_key)
        retained = list(image_attachments)
        retained_groups = [image_attachments] if image_attachments else []
        seen_ids = {ref["id"] for ref in retained}
        document_refs = [ref for ref in attachments if ref.get("kind") == "document"]
        document_ids = {ref["id"] for ref in document_refs}
        dropped = 0
        for entry in reversed(transcript):
            if entry.role != "user" or entry.metadata.get("model_input_eligible") is False:
                continue
            entry_refs = entry.metadata.get("attachments", []) or []
            for ref in entry_refs:
                if (
                    ref.get("kind") == "document"
                    and ref.get("id") not in document_ids
                    and len(document_refs) < MAX_FILES
                ):
                    document_refs.append(ref)
                    document_ids.add(ref["id"])
            group = [ref for ref in entry_refs if ref.get("kind", "image") == "image" and ref["id"] not in seen_ids]
            if len(retained) + len(group) > MAX_HISTORY_IMAGES or sum(r.get("size", 0) for r in retained + group) > MAX_HISTORY_BYTES:
                dropped += len(group)
                continue
            retained.extend(group)
            if group:
                retained_groups.append(group)
            seen_ids.update(ref["id"] for ref in group)

        deps_extra["_chat_attachments"] = attachments
        store = ChatAttachments(session_manager.workspace_path, _user_info.user_id)
        if document_refs:
            try:
                materialized = await store.materialize(session_key, document_refs)
            except AttachmentInputError:
                materialized = []
            if materialized:
                deps_extra["_chat_documents"] = materialized

        if retained:
            snapshot = await image_capabilities(runner)
            token = session_model(runner, session_key)
            token_pool = getattr(getattr(runner, "token_policy", None), "token_pool", None)
            token_entries = list(getattr(token_pool, "tokens", {}).values())
            vision_context_windows = [
                int(entry.context_window)
                for entry in token_entries
                if entry.token_id in snapshot
                and snapshot.get(entry.token_id)
                and isinstance(getattr(entry, "context_window", None), int)
                and entry.context_window > 0
            ]
            image_context_window = max(
                vision_context_windows,
                default=int(getattr(token, "context_window", 0) or 128000),
            )
            image_budget = image_context_window // 2
            if sum(image_token_allowance(ref) for ref in image_attachments) > image_budget:
                raise AttachmentInputError("image_context_limit", "Reduce the image count or resolution for this model.")
            while sum(image_token_allowance(ref) for ref in retained) > image_budget:
                group = retained_groups.pop()
                remove_ids = {ref["id"] for ref in group}
                retained = [ref for ref in retained if ref["id"] not in remove_ids]
                dropped += len(group)
            inputs = await store.model_images(session_key, retained, optional_ids={
                ref["id"] for ref in retained if ref["id"] not in {item["id"] for item in image_attachments}
            })
            dropped += len(retained) - len(inputs)
            deps_extra.update({
                "_chat_image_inputs": inputs,
                "_chat_vision_tokens": {key for key, enabled in snapshot.items() if enabled},
            })
            get_run_or_404(ctx, run_id)["model"] = token.model
            get_run_or_404(ctx, run_id)["image_count"] = len(retained)
            if dropped:
                ctx.sse_manager.push_runtime(run_id, "warning", (
                    f"{dropped} older image(s) are unavailable or outside the image context limit. Reattach them to analyze them again."
                ), metadata={"code": "image_context_limit", "dropped_images": dropped})
        deps = build_scoped_deps(
            ctx,
            _user_info,
            session_key,
            abort_signal=get_run_or_404(ctx, run_id).get("abort_signal"),
            request_cookies=request_cookies,
            provider_config=provider_config,
            extra=deps_extra,
            authorization_context=authorization_context,
            provider_admission=provider_admission,
            selected_capability=(
                request_context.get("_selected_capability")
                if isinstance(request_context, dict)
                and isinstance(request_context.get("_selected_capability"), dict)
                else None
            ),
        )

        def publish_progress(state: str, message: str, metadata: dict[str, Any]) -> None:
            run_info = ctx.active_runs.get(run_id, {})
            if (progress_closed or final_answer_committed or deps.is_aborted()
                    or run_info.get("status") != "running"
                    or run_info.get("session_key") != session_key):
                return
            ctx.sse_manager.push_runtime(run_id, state, message, metadata={
                **metadata, "elapsed": time.monotonic() - progress_started,
            })

        # Install after request context resolution; clients cannot supply this callback.
        deps.extra[PROGRESS_SINK] = publish_progress

        runner_events = runner.run(
            session_key=session_key,
            user_message=message,
            deps=deps,
            max_tool_calls=max_tool_calls,
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
                    if event.phase == "start" and deps.extra.get("_chat_image_inputs"):
                        deps.extra["_chat_tool_started"] = True
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
                    code = str(event.error).split(":", 1)[0]
                    ctx.sse_manager.push_error(run_id, event.error, code=code if code.startswith(("image_", "model_image_", "attachment_")) else None)
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
            ctx.sse_manager.push_error(run_id, error_msg, code=getattr(e, "code", None))
            ctx.sse_manager.push_lifecycle(run_id, "error")

    finally:
        progress_closed = True
        if deps is not None:
            deps.extra.pop(PROGRESS_SINK, None)
        ctx.sse_manager.close_stream(run_id)
