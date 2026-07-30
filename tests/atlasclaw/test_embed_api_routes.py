# -*- coding: utf-8 -*-
"""Critical API tests for provider-neutral Embed context resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.atlasclaw.api.routes_embed import (
    EmbedContextResolveRequest,
    EmbedSkillResponse,
    _build_context_resolve_response,
    embed_session_matches_scope,
    register_embed_routes,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.embed.context_service import (
    EmbedContextResolution,
    EmbedContextService,
)
from app.atlasclaw.core.embed.models import ContextSnapshot, ResolvedObject
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
        skill_name="default.item",
        skill_description="Manage items.",
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
    assert resolved.skill.name == "default.item"
    assert resolved.skill.description == "Manage items."
    assert [action["action_id"] for action in resolved.object_actions] == ["inspect"]
    assert "tools" not in resolved.model_dump()


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("name", "s" * 257),
        ("description", "d" * 4097),
    ],
)
def test_embed_skill_response_bounds_prompt_visible_metadata(
    field_name: str,
    field_value: str,
) -> None:
    """The browser projection must enforce the same Skill metadata bounds."""
    payload = {
        "ref": "example:item",
        "name": "default.item",
        "description": "Manage items.",
        field_name: field_value,
    }

    with pytest.raises(ValidationError, match="string_too_long"):
        EmbedSkillResponse.model_validate(payload)


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
