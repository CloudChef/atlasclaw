# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Native provider defaults must work without an administrator-supplied API URL."""
from types import SimpleNamespace

import pytest

from app.atlasclaw.bootstrap.startup_helpers import _filter_usable_token_entries, create_pydantic_model
from app.atlasclaw.core.token_pool import TokenEntry


def test_google_uses_native_sdk_endpoint_without_explicit_base_url():
    from pydantic_ai.models.google import GoogleModel

    token = TokenEntry("google", "google", "gemini-2.5-flash", "", "test-key", api_type="google")
    assert _filter_usable_token_entries([token], source="test") == [token]
    model = create_pydantic_model(token)
    assert isinstance(model, GoogleModel)
    assert model.model_name == token.model


@pytest.mark.asyncio
async def test_google_model_discovery_uses_native_endpoint_with_key_only(monkeypatch):
    from app.atlasclaw.api import provider_info_routes as routes

    requested = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            requested.append((url, kwargs["headers"]))
            return SimpleNamespace(status_code=200, json=lambda: {"models": [{"name": "models/gemini-2.5-flash"}]})
    monkeypatch.setattr(routes.httpx, "AsyncClient", Client)
    result = await routes.fetch_provider_models(routes.FetchModelsRequest(provider="google", api_key="test-key"))
    assert result == {"models": ["gemini-2.5-flash"], "source": "api"}
    assert len(requested) == 1
    assert "test-key" not in requested[0][0]
    assert requested[0][1]["x-goog-api-key"] == "test-key"
