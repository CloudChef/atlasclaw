# -*- coding: utf-8 -*-
"""Critical manifest, resolver, and Snapshot tests for Embed v1."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.config_schema import AtlasClawConfig, EmbedIntegrationConfig
from app.atlasclaw.core.embed.context_service import EmbedContextService
from app.atlasclaw.core.embed.integration_registry import EmbedIntegrationRegistry
from app.atlasclaw.core.embed.models import ContextSnapshot, ResolvedObject, RouteManifest
from app.atlasclaw.core.embed.route_matcher import match_route, normalize_host_path
from app.atlasclaw.core.embed.snapshot_store import (
    EmbedContextSnapshotStore,
    SnapshotExpiredError,
    SnapshotGenerationError,
    SnapshotNotFoundError,
)

_SURFACE_A = "a" * 22
_SURFACE_B = "b" * 22


def _route_payload() -> dict:
    return {
        "schema_version": 1,
        "provider_type": "example",
        "context_resolver": {"entrypoint": "assistant_context/resolve.py"},
        "routes": [
            {
                "id": "dynamic",
                "priority": 100,
                "match": {"path_template": "/main/items/{row_id}"},
                "result": {
                    "page_type": "item-detail",
                    "object_type": "item",
                    "skill_ref": "example:item",
                },
            },
            {
                "id": "static",
                "priority": 100,
                "match": {"path_template": "/main/items/settings"},
                "result": {
                    "page_type": "settings",
                    "object_type": "setting",
                    "skill_ref": "example:item",
                },
            },
        ],
    }


def _snapshot(
    context_id: str,
    *,
    surface_id: str = _SURFACE_A,
    generation: int = 1,
    expires_at: datetime | None = None,
) -> ContextSnapshot:
    now = datetime.now(timezone.utc)
    return ContextSnapshot(
        context_id=context_id,
        owner_user_id="alice",
        integration_id="example-assistant",
        surface_id=surface_id,
        generation=generation,
        provider_type="example",
        provider_instance="default",
        page_type="item-detail",
        skill_ref="example:item",
        object=ResolvedObject(type="item", id=context_id),
        created_at=now,
        expires_at=expires_at or now + timedelta(minutes=5),
    )


def _put(
    store: EmbedContextSnapshotStore,
    snapshot: ContextSnapshot,
    *,
    capacity: int = 8,
) -> None:
    assert store.mark_latest(
        owner_user_id=snapshot.owner_user_id,
        integration_id=snapshot.integration_id,
        surface_id=snapshot.surface_id,
        generation=snapshot.generation,
        context_id=None,
        max_contexts_per_user=capacity,
    )
    assert store.put(snapshot, max_contexts_per_user=capacity)


def test_embed_configuration_is_opt_in_and_rejects_unsafe_origins() -> None:
    assert AtlasClawConfig().embed_integrations == {}
    common = {
        "provider_type": "example",
        "provider_instance": "default",
        "session_scope": "example-assistant",
        "route_manifest": "assistant_context/routes.json",
    }
    for origin in ("https://*.example.test", "https://example.test/path"):
        with pytest.raises(ValidationError):
            EmbedIntegrationConfig(**common, allowed_origins=[origin])


def test_route_manifest_matches_exact_skill_and_rejects_untrusted_contract_fields() -> None:
    manifest = RouteManifest.model_validate(_route_payload())
    matched = match_route(manifest, "/main/items/settings/")
    assert matched is not None and matched.rule.id == "static"
    dynamic = match_route(manifest, "/main/items/abc%20123")
    assert dynamic is not None and dynamic.parameters == {"row_id": "abc 123"}

    for path in (
        "https://example.test/main/items/1",
        "/main/items/1?tab=detail",
        "/main/items/../1",
        "/main/items/a%252f1",
    ):
        with pytest.raises(ValueError):
            normalize_host_path(path)

    for location, value in (
        (("routes", 0, "result", "resolver"), {"entrypoint": "other.py"}),
        (("context_resolver", "arguments"), {"row_id": "$route.row_id"}),
    ):
        payload = _route_payload()
        target = payload
        for key in location[:-1]:
            target = target[key]
        target[location[-1]] = value
        with pytest.raises(ValidationError, match="extra_forbidden"):
            RouteManifest.model_validate(payload)


@pytest.mark.asyncio
async def test_context_service_uses_fixed_server_owned_resolver_contract() -> None:
    manifest = RouteManifest.model_validate(_route_payload())
    integration = SimpleNamespace(
        routes=manifest,
        config=SimpleNamespace(
            provider_type="example",
            provider_instance="default",
            agent_id="main",
            session_scope="example-assistant",
            context_ttl_seconds=300,
            max_contexts_per_user=8,
        ),
        provider_root=Path("/provider"),
    )
    service = EmbedContextService(
        SimpleNamespace(
            skill_registry=SimpleNamespace(
                get_md_skill=lambda _: SimpleNamespace(
                    qualified_name="example:item",
                    provider="example",
                )
            )
        ),
        SimpleNamespace(get=lambda _: integration),
        EmbedContextSnapshotStore(),
    )
    service._snapshots.mark_latest(
        owner_user_id="alice",
        integration_id="example-assistant",
        surface_id=_SURFACE_A,
        generation=7,
        context_id=None,
        max_contexts_per_user=8,
    )
    service._execute_resolver = AsyncMock(
        return_value={"success": True, "object": {"type": "item", "id": "abc 123"}}
    )
    user = UserInfo(user_id="alice")
    authz = AuthorizationContext(user=user, permissions={"providers": {"allow_all": True}})

    resolution = await service.resolve(
        integration_id="example-assistant",
        surface_id=_SURFACE_A,
        generation=7,
        path="/main/items/abc%20123/",
        user_info=user,
        request_cookies={},
        authz=authz,
    )

    assert resolution.snapshot is not None
    service._execute_resolver.assert_awaited_once_with(
        integration=integration,
        user_info=user,
        request_cookies={},
        authz=authz,
        entrypoint="assistant_context/resolve.py",
        arguments={
            "route_id": "dynamic",
            "path": "/main/items/abc%20123",
            "route_parameters": {"row_id": "abc 123"},
            "page_type": "item-detail",
            "object_type": "item",
        },
    )


def test_registry_confines_manifest_and_resolver_to_provider_root(tmp_path: Path) -> None:
    provider_root = tmp_path / "provider"
    resolver_root = provider_root / "assistant_context"
    resolver_root.mkdir(parents=True)
    (resolver_root / "resolve.py").write_text("def handler(ctx, **kwargs): return {}\n")
    profile = EmbedIntegrationConfig(
        provider_type="example",
        provider_instance="default",
        session_scope="example-assistant",
        allowed_origins=["https://example.test"],
        route_manifest="assistant_context/routes.json",
    )
    provider_registry = SimpleNamespace(
        get_template_for_provider_type=lambda _: SimpleNamespace(path=provider_root)
    )

    mismatch = _route_payload()
    mismatch["provider_type"] = "other"
    (resolver_root / "routes.json").write_text(json.dumps(mismatch), encoding="utf-8")
    with pytest.raises(ValueError, match="provider_type"):
        EmbedIntegrationRegistry({"example-assistant": profile}, provider_registry)

    with pytest.raises(ValueError, match="escapes provider root"):
        EmbedIntegrationRegistry(
            {
                "example-assistant": profile.model_copy(
                    update={"route_manifest": "../outside.json"}
                )
            },
            provider_registry,
        )

    unsafe = _route_payload()
    unsafe["context_resolver"]["entrypoint"] = "../resolve.py"
    (resolver_root / "routes.json").write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(ValueError, match="resolver entrypoint"):
        EmbedIntegrationRegistry({"example-assistant": profile}, provider_registry)


def test_snapshot_store_enforces_identity_generation_expiry_and_capacity() -> None:
    store = EmbedContextSnapshotStore()
    first = _snapshot("ctx-one")
    second = _snapshot("ctx-two", surface_id=_SURFACE_B)
    _put(store, first, capacity=1)
    _put(store, second, capacity=1)

    with pytest.raises(SnapshotNotFoundError):
        store.get("ctx-one", owner_user_id="alice", integration_id="example-assistant", generation=1)
    with pytest.raises(SnapshotNotFoundError):
        store.get("ctx-two", owner_user_id="bob", integration_id="example-assistant", generation=1)
    with pytest.raises(SnapshotGenerationError):
        store.get("ctx-two", owner_user_id="alice", integration_id="example-assistant", generation=2)

    expired = _snapshot(
        "ctx-expired",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    _put(store, expired)
    with pytest.raises(SnapshotExpiredError):
        store.get(
            "ctx-expired",
            owner_user_id="alice",
            integration_id="example-assistant",
            generation=1,
        )


def test_snapshot_store_isolates_surfaces_and_retains_stale_generation_tombstone() -> None:
    store = EmbedContextSnapshotStore()
    surface_a = _snapshot("ctx-a2", generation=2)
    surface_b = _snapshot("ctx-b0", surface_id=_SURFACE_B, generation=0)
    late_a = _snapshot("ctx-a1-late", generation=1)
    _put(store, surface_a, capacity=1)
    _put(store, surface_b, capacity=1)

    assert not store.put(late_a, max_contexts_per_user=1)
    assert store.is_latest(
        surface_b.context_id,
        owner_user_id="alice",
        integration_id="example-assistant",
        surface_id=_SURFACE_B,
        generation=0,
    )

    assert store.mark_latest(
        owner_user_id="alice",
        integration_id="example-assistant",
        surface_id=_SURFACE_A,
        generation=3,
        context_id=None,
        max_contexts_per_user=1,
    )
    fresh = _snapshot("ctx-a3", generation=3)
    assert store.put(fresh, max_contexts_per_user=1)


def test_snapshot_store_bounds_empty_surface_state_and_rejects_evicted_completion() -> None:
    store = EmbedContextSnapshotStore()
    surfaces = [f"surface-{index:022d}" for index in range(33)]
    for surface_id in surfaces:
        assert store.mark_latest(
            owner_user_id="alice",
            integration_id="example-assistant",
            surface_id=surface_id,
            generation=1,
            context_id=None,
            max_contexts_per_user=1,
        )

    assert len(store._latest) == 32
    assert not store.put(
        _snapshot("ctx-evicted", surface_id=surfaces[0]),
        max_contexts_per_user=1,
    )
