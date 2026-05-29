# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

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
from .webhook_dispatch import (
    WebhookRobotProfileError,
    WebhookRobotProfileSelection,
    WebhookSkillSelection,
    WebhookSystemIdentity,
    build_webhook_user_message,
    redact_webhook_payload,
    resolve_webhook_robot_profile_selection,
)

logger = logging.getLogger(__name__)


async def execute_webhook_dispatch(
    ctx: APIContext,
    dispatch_id: str,
    system: WebhookSystemIdentity,
    skill_selection: WebhookSkillSelection,
    session_key: str,
    agent_id: str,
    args: dict[str, Any],
    timeout_seconds: int,
    robot_profile_selection: WebhookRobotProfileSelection | None = None,
) -> None:
    """Run an authenticated webhook request against its selected provider skill.

    The dispatch path pins both the markdown skill and provider instance in
    ``SkillDeps`` so natural-language routing cannot switch to a different
    provider capability while handling the webhook payload.
    """
    if not ctx.agent_runner:
        logger.error("Webhook dispatch %s failed: AgentRunner not configured", dispatch_id)
        return

    user_info = UserInfo(
        user_id=f"webhook-{system.system_id}",
        display_name=system.system_id,
        roles=["webhook"],
        extra={"system_id": system.system_id},
    )
    skill_entry = skill_selection.skill_entry
    user_message = build_webhook_user_message(
        skill_entry,
        args,
        system.system_id,
        provider_skill_name=skill_selection.provider_skill_name,
    )

    provider_config: dict[str, Any] = {}
    if robot_profile_selection is not None:
        # Robot dispatch gets a narrowed, runtime-only provider config. It
        # must not expose unrelated provider instances or normal user creds.
        provider_config = robot_profile_selection.provider_config
    else:
        provider_config = {
            skill_selection.provider_type: {
                skill_selection.provider_instance: dict(
                    skill_selection.provider_instance_config
                )
            }
        }

    target_fields = skill_selection.target_fields()
    extra: dict[str, Any] = {
        "webhook_system_id": system.system_id,
        "webhook_skill": skill_selection.provider_skill_name,
        "webhook_qualified_skill": skill_entry.qualified_name,
        "webhook_args": redact_webhook_payload(args, provider_type=skill_entry.provider),
        # Pin the markdown skill selected by the authenticated webhook so the
        # runner does not re-infer a different skill from the payload text.
        "target_md_skill": {
            "name": skill_entry.name,
            "provider": skill_entry.provider,
            "qualified_name": skill_entry.qualified_name,
            "file_path": skill_entry.file_path,
            **target_fields,
        },
        "provider_type": skill_selection.provider_type,
        "provider_instance_name": skill_selection.provider_instance,
        "provider_instance": dict(skill_selection.provider_instance_config),
    }
    if robot_profile_selection is not None:
        extra.update(
            {
                "provider_type": robot_profile_selection.provider_type,
                "provider_instance_name": robot_profile_selection.provider_instance,
                "provider_instance": dict(robot_profile_selection.provider_instance_config),
                "robot_profile": robot_profile_selection.robot_profile,
            }
        )

    deps = build_scoped_deps(
        ctx,
        user_info,
        session_key,
        request_cookies={},
        provider_config=provider_config,
        extra=extra,
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
    """Register webhook dispatch routes and their authentication checks."""

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
            skill_selection = manager.resolve_allowed_skill(system, request.skill)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

        if skill_selection is None:
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

        args = dict(request.args or {})
        try:
            robot_profile_selection = resolve_webhook_robot_profile_selection(
                skill_selection=skill_selection,
                args=args,
                service_provider_registry=ctx.service_provider_registry,
            )
        except WebhookRobotProfileError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
            ) from exc

        agent_id = request.agent_id or system.default_agent_id
        dispatch_id = str(uuid.uuid4())
        session_key = SessionKey(
            agent_id=agent_id,
            user_id=f"webhook-{system.system_id}",
            channel="webhook",
            chat_type=SessionChatType.DM,
            peer_id=system.system_id,
            thread_id=dispatch_id,
        ).to_string(scope=SessionScope.PER_CHANNEL_PEER)

        background_tasks.add_task(
            execute_webhook_dispatch,
            ctx,
            dispatch_id,
            system,
            skill_selection,
            session_key,
            agent_id,
            robot_profile_selection.args if robot_profile_selection is not None else args,
            request.timeout_seconds,
            robot_profile_selection,
        )
        return WebhookDispatchResponse(status="accepted")
