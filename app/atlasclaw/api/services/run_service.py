# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from fastapi import HTTPException, status

from ...auth.models import ANONYMOUS_USER, UserInfo
from ...auth.guards import resolve_authorization_context
from ...db import get_db_manager
from ...core.security_guard import encode_if_untrusted
from ...session.context import SessionKey, TranscriptEntry
from ..deps_context import APIContext, build_scoped_deps

logger = logging.getLogger(__name__)


@asynccontextmanager
async def session_execution(ctx: APIContext, session_key: str) -> AsyncIterator[None]:
    """Serialize one complete execution and persistence sequence for a Chat Session."""
    await ctx.session_queue.acquire(session_key)
    try:
        yield
    finally:
        ctx.session_queue.release(session_key)


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


def abort_run(ctx: APIContext, run_id: str) -> None:
    """Mark a run as aborted after ownership checks have resolved the run id."""
    run_info = get_run_or_404(ctx, run_id)
    run_info["status"] = "aborted"


async def complete_run_with_static_answer(
    ctx: APIContext,
    *,
    run_id: str,
    session_key: str,
    user_message: str,
    answer: str,
    reason: str,
) -> None:
    """Complete an API-decided answer under the shared Chat Session execution slot."""
    async with session_execution(ctx, session_key):
        await _complete_run_with_static_answer_locked(
            ctx,
            run_id=run_id,
            session_key=session_key,
            user_message=user_message,
            answer=answer,
            reason=reason,
        )


async def _complete_run_with_static_answer_locked(
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
    ctx.sse_manager.push_lifecycle(run_id, "start")
    ctx.sse_manager.push_assistant(run_id, answer, is_delta=False)
    ctx.sse_manager.push_runtime(
        run_id,
        "answered",
        "Final answer ready.",
        metadata={"static_answer_reason": reason},
    )
    ctx.sse_manager.push_lifecycle(run_id, "end")
    if run_id in ctx.active_runs:
        ctx.active_runs[run_id]["status"] = "completed"
        ctx.active_runs[run_id]["completed_at"] = datetime.now(timezone.utc)
        ctx.active_runs[run_id]["tokens_used"] = 0
        ctx.active_runs[run_id].pop("error", None)
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
    session_slot_acquired = False

    try:
        await ctx.session_queue.acquire(session_key)
        session_slot_acquired = True
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

        request_context = await _refresh_embed_run_context_after_queue(
            ctx,
            user_info=_user_info,
            request_context=request_context,
        )
        if isinstance(request_context, dict) and isinstance(
            request_context.get("embed_scope"), dict
        ):
            provider_config = build_provider_config(ctx)

        deps = build_scoped_deps(
            ctx,
            _user_info,
            session_key,
            request_cookies=request_cookies,
            provider_config=provider_config,
            extra={
                "agent_id": target_agent_id,
                "run_id": run_id,
                "context": request_context or {},
            },
        )

        async for event in runner.run(
            session_key=session_key,
            user_message=message,
            deps=deps,
            timeout_seconds=timeout_seconds,
        ):
            if event.type == "lifecycle":
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
                runtime_state = str((event.metadata or {}).get("state", "") or "").strip().lower()
                if runtime_state == "failed":
                    encountered_error = True
                    final_error_message = str(event.content or final_error_message or "")
                elif runtime_state == "answered" and str(event.content or "").strip() == "Final answer ready.":
                    final_answer_committed = True
                ctx.sse_manager.push_runtime(
                    run_id,
                    str((event.metadata or {}).get("state", "")),
                    event.content,
                    metadata=event.metadata if event.metadata else None,
                )

        if run_id in ctx.active_runs:
            if encountered_error or not final_answer_committed:
                ctx.active_runs[run_id]["status"] = "error"
                ctx.active_runs[run_id]["error"] = (
                    final_error_message or "Run ended without a committed final answer"
                )
            else:
                ctx.active_runs[run_id]["status"] = "completed"
                ctx.active_runs[run_id]["completed_at"] = datetime.now(timezone.utc)

    except asyncio.TimeoutError:
        ctx.sse_manager.push_error(run_id, "Agent execution timed out")
        ctx.sse_manager.push_lifecycle(run_id, "error")
        if run_id in ctx.active_runs:
            ctx.active_runs[run_id]["status"] = "timeout"
            ctx.active_runs[run_id]["error"] = "Execution timed out"

    except Exception as e:
        error_msg = str(e)
        ctx.sse_manager.push_error(run_id, error_msg)
        ctx.sse_manager.push_lifecycle(run_id, "error")
        if run_id in ctx.active_runs:
            ctx.active_runs[run_id]["status"] = "error"
            ctx.active_runs[run_id]["error"] = error_msg

    finally:
        if session_slot_acquired:
            ctx.session_queue.release(session_key)
        ctx.sse_manager.close_stream(run_id)


async def execute_confirmed_tool_run(
    ctx: APIContext,
    run_id: str,
    session_key: str,
    timeout_seconds: int,
    user_info: UserInfo,
    request_cookies: dict[str, str],
    request_context: dict[str, Any],
) -> None:
    """Execute one claimed Tool ticket through the original Registry handler.

    This is a confirmation continuation of the same ``/agent/run`` contract,
    not a second workflow runner. Authorization, Provider visibility, page
    freshness, and ticket expiry are rechecked after acquiring the Session slot.
    """
    acquired = False
    try:
        await ctx.session_queue.acquire(session_key)
        acquired = True
        refreshed_context = await _refresh_confirmed_tool_context_after_queue(
            ctx,
            user_info=user_info,
            request_context=request_context,
        )
        grant = refreshed_context.get("_tool_confirmation_grant")
        if grant is None:
            raise PermissionError("Confirmed Tool state is unavailable")
        provider_config = build_provider_config(ctx) or (ctx.provider_instances or {})
        deps = build_scoped_deps(
            ctx,
            user_info,
            session_key,
            request_cookies=request_cookies,
            provider_config=provider_config,
            extra={
                "agent_id": str(getattr(grant, "agent_id", "") or "main"),
                "run_id": run_id,
                "context": refreshed_context,
            },
        )
        provider_type = str(getattr(grant, "provider_type", "") or "").strip()
        provider_instance = str(getattr(grant, "provider_instance", "") or "").strip()
        if provider_type:
            provider_bucket = deps.extra.get("provider_instances", {}).get(provider_type, {})
            instance_config = (
                provider_bucket.get(provider_instance)
                if isinstance(provider_bucket, dict)
                else None
            )
            if not isinstance(instance_config, dict):
                raise PermissionError("Confirmed Tool Provider is no longer available")
            deps.extra["provider_type"] = provider_type
            deps.extra["provider_instance_name"] = provider_instance
            deps.extra["provider_instance"] = instance_config

        ctx.sse_manager.push_lifecycle(run_id, "start")
        ctx.sse_manager.push_tool(run_id, grant.tool_name, "start")
        result = await asyncio.wait_for(
            ctx.skill_registry.execute(
                grant.tool_name,
                json.dumps(grant.arguments, ensure_ascii=False),
                deps,
            ),
            timeout=max(1, int(timeout_seconds)),
        )
        result_payload: Any
        try:
            result_payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            result_payload = result
        if isinstance(result_payload, dict) and (
            result_payload.get("error") or result_payload.get("success") is False
        ):
            error_text = str(
                result_payload.get("error")
                or result_payload.get("message")
                or "Confirmed Tool execution failed"
            )
            raise RuntimeError(error_text)

        ctx.sse_manager.push_tool(run_id, grant.tool_name, "end")
        answer = "The confirmed operation completed successfully."
        manager = ctx.session_manager_router.for_session_key(session_key)
        await manager.append_transcript(
            session_key,
            TranscriptEntry(
                role="assistant",
                content=answer,
                metadata={"confirmed_tool": grant.tool_name},
            ),
        )
        ctx.sse_manager.push_assistant(run_id, answer, is_delta=False)
        ctx.sse_manager.push_runtime(
            run_id,
            "answered",
            "Final answer ready.",
            metadata={"phase": "confirmed_tool_completed"},
        )
        ctx.sse_manager.push_lifecycle(run_id, "end")
        run = ctx.active_runs.get(run_id)
        if run is not None:
            run["status"] = "completed"
            run["completed_at"] = datetime.now(timezone.utc)
            run["tokens_used"] = 0
            run.pop("error", None)
    except Exception as exc:
        error_text = str(exc) or "Confirmed Tool execution failed"
        ctx.sse_manager.push_error(run_id, error_text)
        ctx.sse_manager.push_lifecycle(run_id, "error")
        run = ctx.active_runs.get(run_id)
        if run is not None:
            run["status"] = "error"
            run["error"] = error_text
    finally:
        ctx.sse_manager.close_stream(run_id)
        if acquired:
            ctx.session_queue.release(session_key)


async def _refresh_confirmed_tool_context_after_queue(
    ctx: APIContext,
    *,
    user_info: UserInfo,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """Refresh DB authorization and optional page scope for a claimed Tool ticket."""
    current = dict(request_context or {})
    grant = current.get("_tool_confirmation_grant")
    if grant is None:
        raise PermissionError("Confirmed Tool state is unavailable")
    if isinstance(current.get("embed_scope"), dict):
        current = await _refresh_embed_run_context_after_queue(
            ctx,
            user_info=user_info,
            request_context=current,
        )

    db_manager = get_db_manager()
    if db_manager is None or not db_manager.is_initialized:
        raise PermissionError("current authorization storage is unavailable")
    async with db_manager.get_session() as session:
        authz = await resolve_authorization_context(session, user_info)

    from app.atlasclaw.auth.guards import has_provider_instance_access
    from app.atlasclaw.core.embed.context_service import build_authorization_extras

    provider_type = str(getattr(grant, "provider_type", "") or "").strip()
    provider_instance = str(getattr(grant, "provider_instance", "") or "").strip()
    if provider_type and not has_provider_instance_access(
        authz,
        provider_type,
        provider_instance,
    ):
        raise PermissionError("Confirmed Tool Provider permission changed")
    current.update(build_authorization_extras(authz))
    current["_tool_confirmation_grant"] = grant
    return current


async def _refresh_embed_run_context_after_queue(
    ctx: APIContext,
    *,
    user_info: UserInfo,
    request_context: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild a page-bound run from current snapshot and authorization state."""
    current = dict(request_context or {})
    scope = current.get("embed_scope")
    if not isinstance(scope, dict):
        return current

    from app.atlasclaw.core.embed.context_service import (
        EmbedContextService,
        build_authorization_extras,
        require_embed_resolver_access,
    )

    context_id = str(scope.get("context_id") or "").strip()
    integration_id = str(scope.get("integration_id") or "").strip()
    generation = int(scope.get("generation"))
    snapshot = ctx.embed_context_store.get(
        context_id,
        owner_user_id=user_info.user_id,
        integration_id=integration_id,
        generation=generation,
    )
    if not ctx.embed_context_store.is_latest(
        snapshot.context_id,
        owner_user_id=user_info.user_id,
        integration_id=snapshot.integration_id,
        surface_id=snapshot.surface_id,
        generation=snapshot.generation,
    ):
        raise PermissionError("Embed context no longer targets the current page")
    integration = ctx.embed_integration_registry.get(snapshot.integration_id)
    if (
        integration is None
        or integration.config.provider_type != snapshot.provider_type
        or integration.config.provider_instance != snapshot.provider_instance
    ):
        raise PermissionError("Embed integration Provider binding changed")
    db_manager = get_db_manager()
    if db_manager is None or not db_manager.is_initialized:
        raise PermissionError("current authorization storage is unavailable")
    async with db_manager.get_session() as session:
        current_authz = await resolve_authorization_context(session, user_info)
    require_embed_resolver_access(
        current_authz,
        provider_type=snapshot.provider_type,
        provider_instance=snapshot.provider_instance,
    )
    EmbedContextService(
        ctx,
        ctx.embed_integration_registry,
        ctx.embed_context_store,
    ).validate_snapshot_skill_binding(
        provider_type=snapshot.provider_type,
        skill_ref=snapshot.skill_ref,
    )

    refreshed = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "turn_context",
            "allowed_page_skill_refs",
            "embed_scope",
            "_user_skill_permissions",
            "_provider_permissions",
        }
    }
    refreshed.update(build_authorization_extras(current_authz))
    refreshed["turn_context"] = {
        "integration_id": snapshot.integration_id,
        "page_type": snapshot.page_type,
        "object": snapshot.object.model_dump(),
        "context_generation": snapshot.generation,
    }
    refreshed["allowed_page_skill_refs"] = [snapshot.skill_ref]
    refreshed["embed_scope"] = {
        "context_id": snapshot.context_id,
        "generation": snapshot.generation,
        "integration_id": snapshot.integration_id,
        "provider_type": snapshot.provider_type,
        "provider_instance": snapshot.provider_instance,
        "object_type": snapshot.object.type,
        "object_id": snapshot.object.id,
    }
    return refreshed
