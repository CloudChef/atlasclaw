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
    register_embed_routes,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.embed.context_service import (
    EmbedContextResolution,
    EmbedContextService,
)
from app.atlasclaw.core.embed.models import ContextSnapshot, ResolvedObject


def test_context_response_distinguishes_all_resolution_states() -> None:
    """Unsupported, unavailable, and resolved pages must remain distinguishable."""
    now = datetime.now(timezone.utc)
    snapshot = ContextSnapshot(
        context_id="ctx-resolved",
        owner_user_id="alice",
        integration_id="example-assistant",
        surface_id="a" * 22,
        generation=5,
        provider_type="example",
        provider_instance="default",
        page_type="item-detail",
        skill_ref="example:item",
        object=ResolvedObject(type="item", id="ITEM-5", name="Item 5"),
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
    integration = SimpleNamespace(
        config=SimpleNamespace(max_contexts_per_user=8, context_ttl_seconds=300)
    )
    ctx = SimpleNamespace(
        embed_context_store=SimpleNamespace(mark_latest=Mock(return_value=False)),
        embed_integration_registry=SimpleNamespace(get=lambda _: integration),
    )
    request_obj = Request(
        {"type": "http", "method": "POST", "path": "/embed/context/resolve", "headers": []}
    )
    request_obj.state.user_info = UserInfo(user_id="alice")

    response = await endpoint(
        request_obj,
        EmbedContextResolveRequest(
            integration_id="example-assistant",
            surface_id="a" * 22,
            generation=1,
            path="/main/items/one",
        ),
        ctx,
    )

    assert response.status == "unavailable"
    resolver.assert_not_awaited()
