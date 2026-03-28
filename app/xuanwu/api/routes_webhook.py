# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from ..auth.models import UserInfo
from ..session.context import ChatType as SessionChatType
from ..session.context import SessionKey, SessionScope
from .deps_context import APIContext, build_scoped_deps, get_api_context
from .schemas import WebhookDispatchRequest, WebhookDispatchResponse
from .webhook_dispatch import WebhookSystemIdentity, build_webhook_user_message

logger = logging.getLogger(__name__)


async def execute_webhook_dispatch(
    ctx: APIContext,
    dispatch_id: str,
    system: WebhookSystemIdentity,
    skill_entry: Any,
    session_key: str,
    agent_id: str,
    args: dict[str, Any],
    timeout_seconds: int,
) -> None:
    if not ctx.agent_runner:
        logger.error("Webhook dispatch %s failed: AgentRunner not configured", dispatch_id)
        return

    user_info = UserInfo(
        user_id=f"webhook-{system.system_id}",
        display_name=system.system_id,
        roles=["webhook"],
        extra={"system_id": system.system_id},
    )
    user_message = build_webhook_user_message(skill_entry, args, system.system_id)

    provider_config: dict[str, Any] = {}
    if ctx.service_provider_registry:
        provider_config = ctx.service_provider_registry.get_all_instance_configs()

    deps = build_scoped_deps(
        ctx,
        user_info,
        session_key,
        request_cookies={},
        provider_config=provider_config,
        extra={
            "webhook_skill": skill_entry.qualified_name,
            "webhook_args": dict(args),
            "target_md_skill": {
                "name": skill_entry.name,
                "provider": skill_entry.provider,
                "qualified_name": skill_entry.qualified_name,
                "file_path": skill_entry.file_path,
            },
        },
    )

    logger.info(
        "Accepted webhook dispatch: dispatch_id=%s system_id=%s agent_id=%s skill=%s",
        dispatch_id,
        system.system_id,
        agent_id,
        skill_entry.qualified_name,
    )
    try:
        async for _event in ctx.agent_runner.run(
            session_key=session_key,
            user_message=user_message,
            deps=deps,
            timeout_seconds=timeout_seconds,
        ):
            pass
        logger.info(
            "Webhook dispatch completed: dispatch_id=%s system_id=%s skill=%s",
            dispatch_id,
            system.system_id,
            skill_entry.qualified_name,
        )
    except Exception:
        logger.exception(
            "Webhook dispatch failed: dispatch_id=%s system_id=%s skill=%s",
            dispatch_id,
            system.system_id,
            skill_entry.qualified_name,
        )


def register_webhook_routes(router: APIRouter) -> None:
    @router.post(
        "/webhook/dispatch",
        response_model=WebhookDispatchResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def dispatch_webhook_skill(
        request_obj: Request,
        request: WebhookDispatchRequest,
        background_tasks: BackgroundTasks,
        ctx: APIContext = Depends(get_api_context),
    ) -> WebhookDispatchResponse:
        manager = ctx.webhook_manager
        if manager is None or not manager.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook dispatch not enabled",
            )

        secret = request_obj.headers.get(manager.header_name, "").strip()
        system = manager.authenticate(secret)
        if system is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook secret",
            )

        try:
            skill_entry = manager.resolve_allowed_skill(system, request.skill)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

        if skill_entry is None:
            if request.skill in ctx.skill_registry.list_skills():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Webhook skill {request.skill!r} resolves to an executable tool, not a markdown skill",
                )
            if request.skill not in system.allowed_skills:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Webhook skill {request.skill!r} is not allowed for system {system.system_id!r}",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook markdown skill not found: {request.skill}",
            )

        agent_id = request.agent_id or system.default_agent_id
        session_key = SessionKey(
            agent_id=agent_id,
            user_id=f"webhook-{system.system_id}",
            channel="webhook",
            chat_type=SessionChatType.DM,
            peer_id=system.system_id,
        ).to_string(scope=SessionScope.PER_CHANNEL_PEER)

        background_tasks.add_task(
            execute_webhook_dispatch,
            ctx,
            str(uuid.uuid4()),
            system,
            skill_entry,
            session_key,
            agent_id,
            request.args,
            request.timeout_seconds,
        )
        return WebhookDispatchResponse(status="accepted")
