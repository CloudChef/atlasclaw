# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Critical registration and path-safety tests for provider-declared HTTP APIs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.atlasclaw.api import deps_context
from app.atlasclaw.api.provider_http_routes import (
    _body_limited_request,
    _build_provider_http_response,
)
from app.atlasclaw.api.provider_info_routes import _get_registered_runtime_capabilities
from app.atlasclaw.api import provider_http_routes
from app.atlasclaw.core.provider_http_runtime import ProviderHttpRuntimeError
from app.atlasclaw.core.provider_http_runtime import load_provider_http_manifest
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry


def _write_provider_package(provider_root: Path, runtime_manifest: dict) -> None:
    """Create the smallest provider package accepted by Core discovery."""
    provider_root.mkdir()
    (provider_root / "PROVIDER.md").write_text(
        "---\nprovider_type: sample-provider\ndisplay_name: Sample Provider\n---\n\nSample.\n",
        encoding="utf-8",
    )
    (provider_root / "provider.schema.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_type": "sample-provider",
                "runtime_capabilities": ["records_read"],
                "catalog": {"display_name": "Sample Provider"},
                "config_schema": {"fields": []},
            }
        ),
        encoding="utf-8",
    )
    (provider_root / "runtime-api.json").write_text(
        json.dumps(runtime_manifest),
        encoding="utf-8",
    )
    (provider_root / "http_runtime.py").write_text(
        "async def get_record(request, context):\n    return {'id': context.path_params['record_id']}\n",
        encoding="utf-8",
    )


def test_registry_automatically_matches_provider_declared_route(tmp_path: Path) -> None:
    """Verify adding a valid provider manifest makes its API visible to Core dispatch."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "records/{record_id}",
                    "module": "http_runtime.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "provider_user",
                }
            ],
        },
    )

    registry = ServiceProviderRegistry()
    assert registry.load_from_directory(tmp_path) == 1
    route, path_params = registry.match_provider_http_route(
        "sample-provider",
        "GET",
        "records/record-17",
    )

    assert route is not None
    assert route.handler == "get_record"
    assert path_params == {"record_id": "record-17"}
    assert registry.get_provider_http_capabilities("sample-provider") == frozenset(
        {"records_read"}
    )


def test_runtime_manifest_rejects_module_escape(tmp_path: Path) -> None:
    """Verify executable module paths cannot escape a provider package."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "records/{record_id}",
                    "module": "../outside.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "provider_user",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="safe relative .py path"):
        load_provider_http_manifest(
            provider_root,
            provider_type="sample-provider",
            runtime_capabilities=("records_read",),
        )


def test_runtime_manifest_rejects_overlapping_route_templates(tmp_path: Path) -> None:
    """Verify one request cannot resolve to routes with different access policies."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "public/{record_id}",
                    "module": "http_runtime.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "provider_user",
                },
                {
                    "method": "GET",
                    "path": "{resource}/secret",
                    "module": "http_runtime.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "admin",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="ambiguous overlapping routes"):
        load_provider_http_manifest(
            provider_root,
            provider_type="sample-provider",
            runtime_capabilities=("records_read",),
        )


def test_runtime_manifest_rejects_synchronous_handler(tmp_path: Path) -> None:
    """Verify provider HTTP entrypoints cannot use Core's removed sync fallback."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "records/{record_id}",
                    "module": "http_runtime.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "provider_user",
                }
            ],
        },
    )
    (provider_root / "http_runtime.py").write_text(
        "def get_record(request, context):\n    return {'id': context.path_params['record_id']}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="handler does not exist or is not async"):
        load_provider_http_manifest(
            provider_root,
            provider_type="sample-provider",
            runtime_capabilities=("records_read",),
        )


@pytest.mark.parametrize(
    "handler_source",
    [
        "async def get_record():\n    return {}\n",
        "async def get_record(request):\n    return {}\n",
        "async def get_record(request, context, required):\n    return {}\n",
    ],
)
def test_runtime_manifest_rejects_incompatible_async_handler_signature(
    tmp_path: Path,
    handler_source: str,
) -> None:
    """Verify registration rejects async handlers that cannot honor Core's call contract."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "records/{record_id}",
                    "module": "http_runtime.py",
                    "handler": "get_record",
                    "capability": "records_read",
                    "access": "provider_user",
                }
            ],
        },
    )
    (provider_root / "http_runtime.py").write_text(handler_source, encoding="utf-8")

    with pytest.raises(ValueError, match=r"must accept \(request, context\)"):
        load_provider_http_manifest(
            provider_root,
            provider_type="sample-provider",
            runtime_capabilities=("records_read",),
        )


def test_failed_http_registration_does_not_advertise_callable_capability(
    tmp_path: Path,
) -> None:
    """Verify schema metadata cannot claim an HTTP capability whose handler failed."""
    provider_root = tmp_path / "sample-provider"
    _write_provider_package(
        provider_root,
        {
            "schema_version": 1,
            "routes": [
                {
                    "method": "GET",
                    "path": "records/{record_id}",
                    "module": "http_runtime.py",
                    "handler": "missing_handler",
                    "capability": "records_read",
                    "access": "provider_user",
                }
            ],
        },
    )

    registry = ServiceProviderRegistry()
    assert registry.load_from_directory(tmp_path) == 1
    assert registry.get_provider_schema_definition("sample-provider") is not None
    assert registry.get_provider_http_capabilities("sample-provider") == frozenset()


def _streaming_request(
    chunks: list[bytes],
    *,
    content_length: int | None = None,
    disconnect: bool = False,
) -> Request:
    """Build a request whose ASGI body arrives in multiple receive messages."""
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    message_list = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
    if disconnect:
        message_list.append({"type": "http.disconnect"})
    messages = iter(message_list)

    async def receive() -> dict:
        return next(messages)

    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, 1])
async def test_core_body_limit_counts_actual_chunked_bytes(
    content_length: int | None,
) -> None:
    """Verify the complete body is rejected before any provider work can begin."""
    handler_called = False

    with pytest.raises(HTTPException) as error:
        async with _body_limited_request(
            _streaming_request([b"123", b"456"], content_length=content_length),
            max_body_bytes=5,
        ):
            handler_called = True

    assert error.value.status_code == 413
    assert handler_called is False


@pytest.mark.asyncio
async def test_core_body_spool_replays_complete_body_to_partial_consumers() -> None:
    """Verify complete replay and later disconnect visibility after body validation."""
    async with _body_limited_request(
        _streaming_request([b"123", b"456"], disconnect=True),
        max_body_bytes=6,
    ) as request:
        assert await request.is_disconnected() is False
        assert await request.body() == b"123456"
        assert await request.is_disconnected() is True


@pytest.mark.asyncio
async def test_core_body_spool_rolls_to_disk_and_closes_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the bounded spool is closed when provider execution is cancelled."""
    created_spools = []
    original_factory = provider_http_routes.tempfile.SpooledTemporaryFile

    def capture_spool(*args, **kwargs):
        spool = original_factory(*args, **kwargs)
        created_spools.append(spool)
        return spool

    monkeypatch.setattr(
        provider_http_routes.tempfile,
        "SpooledTemporaryFile",
        capture_spool,
    )
    monkeypatch.setattr(provider_http_routes, "_BODY_MEMORY_SPOOL_BYTES", 2)

    with pytest.raises(asyncio.CancelledError):
        async with _body_limited_request(
            _streaming_request([b"123", b"456"]),
            max_body_bytes=6,
        ):
            assert created_spools[0]._rolled is True
            raise asyncio.CancelledError

    assert created_spools[0].closed is True


def test_provider_http_result_rejects_delayed_responses() -> None:
    """Verify handlers cannot retain a request-backed stream beyond spool lifetime."""
    response = StreamingResponse(iter([b"delayed"]))

    with pytest.raises(ProviderHttpRuntimeError, match="JSON-serializable"):
        _build_provider_http_response(response, status_code=200)

    with pytest.raises(ProviderHttpRuntimeError, match="JSON-serializable"):
        _build_provider_http_response((item for item in ["lazy"]), status_code=200)

    with pytest.raises(ProviderHttpRuntimeError, match="non-JSON"):
        _build_provider_http_response(object(), status_code=200)


def test_capability_discovery_fails_closed_without_callable_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an unavailable dispatcher registry cannot advertise schema-only APIs."""
    monkeypatch.setattr(
        deps_context,
        "get_api_context",
        lambda: SimpleNamespace(service_provider_registry=None),
    )
    assert _get_registered_runtime_capabilities("sample-provider") == set()

    class BrokenRegistry:
        """Represent a registry whose callable capability lookup failed."""

        def get_provider_http_capabilities(self, provider_type: str) -> frozenset[str]:
            """Raise the same way a corrupt runtime registry would."""
            raise RuntimeError(f"Cannot inspect {provider_type}")

    monkeypatch.setattr(
        deps_context,
        "get_api_context",
        lambda: SimpleNamespace(service_provider_registry=BrokenRegistry()),
    )
    assert _get_registered_runtime_capabilities("sample-provider") == set()
