# -*- coding: utf-8 -*-
"""Critical API tests for provider-neutral Embed context resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, Request

from app.atlasclaw.api.routes_embed import (
    EmbedContextResolveRequest,
    _build_context_resolve_response,
    embed_session_matches_scope,
    register_embed_routes,
)
from app.atlasclaw.api.services.run_service import _require_current_embed_provider_tool
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.embed.context_service import (
    EmbedContextResolution,
    EmbedContextService,
)
from app.atlasclaw.core.embed.models import ContextSkillTool, ContextSnapshot, ResolvedObject
from app.atlasclaw.session.context import ChatType, SessionKey


def test_embed_session_requires_exact_authenticated_web_dm_scope() -> None:
    user = UserInfo(user_id="alice")
    valid = SessionKey(
        agent_id="main",
        user_id="alice",
        channel="web",
        account_id="smartcmp-assistant",
        chat_type=ChatType.DM,
        peer_id="alice",
        thread_id="thread-1",
    )
    assert embed_session_matches_scope(
        valid,
        user=user,
        agent_id="main",
        session_scope="smartcmp-assistant",
    )

    for field, value in (
        ("channel", "api"),
        ("chat_type", ChatType.GROUP),
        ("peer_id", "other"),
        ("thread_id", None),
    ):
        invalid = SessionKey(**{**valid.__dict__, field: value})
        assert not embed_session_matches_scope(
            invalid,
            user=user,
            agent_id="main",
            session_scope="smartcmp-assistant",
        )


def test_context_response_distinguishes_all_resolution_states() -> None:
    """Unsupported, unavailable, and resolved pages must remain distinguishable."""
    now = datetime.now(timezone.utc)
    snapshot = ContextSnapshot(
        context_id="ctx-resolved",
        owner_user_id="alice",
        surface_id="a" * 22,
        generation=5,
        provider_type="example",
        provider_instance="default",
        page_type="item-detail",
        skill_ref="example:item",
        object=ResolvedObject(type="item", id="ITEM-5", name="Item 5"),
        object_actions=[
            {
                "action_id": "inspect",
                "kind": "agent_prompt",
                "display_label": {"default": "Inspect"},
                "agent_prompt": {"default": "Inspect ITEM-5"},
                "tone": "success",
            }
        ],
        tools=[
            ContextSkillTool(
                name="example_inspect",
                label="Inspect item",
                description="Inspect the current item.",
            )
        ],
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    unsupported = _build_context_resolve_response(3, EmbedContextResolution(matched=False))
    unavailable = _build_context_resolve_response(4, EmbedContextResolution(matched=True))
    resolved = _build_context_resolve_response(
        5,
        EmbedContextResolution(matched=True, snapshot=snapshot),
    )

    assert unsupported.status == "unsupported"
    assert unavailable.status == "unavailable"
    assert resolved.status == "resolved"
    assert resolved.context_id == "ctx-resolved"
    assert resolved.object is not None and resolved.object.id == "ITEM-5"
    assert resolved.skill is not None and resolved.skill.ref == "example:item"
    assert [action["action_id"] for action in resolved.object_actions] == ["inspect"]
    assert "tools" not in resolved.model_dump()


@pytest.mark.asyncio
async def test_rejected_generation_skips_provider_resolution(monkeypatch) -> None:
    """An older PAGE_CHANGED request must perform no Provider I/O."""
    router = APIRouter()
    register_embed_routes(router)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/embed/context/resolve"
    )
    resolver = AsyncMock()
    monkeypatch.setattr(EmbedContextService, "resolve", resolver)
    integration = SimpleNamespace(max_contexts_per_user=8, context_ttl_seconds=300)
    ctx = SimpleNamespace(
        embed_context_store=SimpleNamespace(mark_latest=Mock(return_value=False)),
        embed_integration_registry=SimpleNamespace(get=lambda: integration),
    )
    request_obj = Request(
        {"type": "http", "method": "POST", "path": "/embed/context/resolve", "headers": []}
    )
    request_obj.state.user_info = UserInfo(user_id="alice")

    response = await endpoint(
        request_obj,
        EmbedContextResolveRequest(
            surface_id="a" * 22,
            generation=1,
            path="/main/items/one",
        ),
        ctx,
    )

    assert response.status == "unavailable"
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_io_guard_revalidates_scope_without_deferred_name_error(
    monkeypatch,
) -> None:
    """The deferred Tool guard must resolve its context-service dependencies at call time."""
    from app.atlasclaw.api.services import run_service
    from app.atlasclaw.core.embed import context_service

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    service = Mock()
    service_type = Mock(return_value=service)
    require_access = Mock()
    authz = SimpleNamespace(permissions={})
    monkeypatch.setattr(context_service, "EmbedContextService", service_type)
    monkeypatch.setattr(context_service, "require_embed_resolver_access", require_access)
    monkeypatch.setattr(
        run_service,
        "get_db_manager",
        lambda: SimpleNamespace(
            is_initialized=True,
            get_session=lambda: SessionContext(),
        ),
    )
    monkeypatch.setattr(
        run_service,
        "resolve_authorization_context",
        AsyncMock(return_value=authz),
    )

    snapshot = SimpleNamespace(
        context_id="ctx-1",
        owner_user_id="alice",
        surface_id="surface-1",
        generation=3,
        provider_type="example",
        provider_instance="default",
        skill_ref="example:item",
    )
    integration = SimpleNamespace(
        config=SimpleNamespace(provider_type="example", provider_instance="default")
    )
    ctx = SimpleNamespace(
        embed_context_store=SimpleNamespace(is_latest=Mock(return_value=True)),
        embed_integration_registry=SimpleNamespace(get=Mock(return_value=integration)),
    )

    await _require_current_embed_provider_tool(
        ctx,
        user_info=UserInfo(user_id="alice"),
        snapshot=snapshot,
        tool_name="example_inspect",
    )

    require_access.assert_called_once_with(
        authz,
        provider_type="example",
        provider_instance="default",
    )
    service.validate_snapshot_skill_binding.assert_called_once_with(
        provider_type="example",
        skill_ref="example:item",
    )
    service.require_visible_snapshot_tool.assert_called_once_with(
        snapshot=snapshot,
        integration=integration,
        authz=authz,
        tool_name="example_inspect",
    )
