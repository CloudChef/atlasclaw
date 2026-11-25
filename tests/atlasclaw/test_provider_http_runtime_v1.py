# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""runtime-api.json v1: limits, binary responses and manifest validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.responses import Response

from app.atlasclaw.api.provider_http_routes import _build_provider_http_response
from app.atlasclaw.core.provider_http_runtime import (
    ProviderHttpRuntimeError,
    load_provider_http_manifest,
)

HANDLER_SOURCE = '''
from fastapi.responses import Response


async def list_documents(request, context):
    return {"items": []}


async def download_document(request, context):
    return Response(content=b"%PDF-1.4", media_type="application/pdf")


async def download_report(request, context):
    return Response(content=b"<html></html>", media_type="text/html")
'''


def _provider(tmp_path: Path, manifest: dict) -> Path:
    root = tmp_path / "audit-report"
    root.mkdir(parents=True)
    (root / "api").mkdir()
    (root / "api" / "handlers.py").write_text(HANDLER_SOURCE, encoding="utf-8")
    (root / "runtime-api.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _manifest(**overrides) -> dict:
    manifest = {
        "version": 1,
        "timeout_seconds": 45,
        "max_body_bytes": 1048576,
        "routes": [
            {
                "id": "documents.list",
                "method": "GET",
                "path": "documents",
                "module": "api/handlers.py",
                "handler": "list_documents",
                "capability": "documents",
                "access": "user",
            },
            {
                "id": "documents.file",
                "method": "GET",
                "path": "documents/{document_id}/file",
                "module": "api/handlers.py",
                "handler": "download_document",
                "capability": "documents",
                "access": "user",
                "response": "binary",
                "timeout_seconds": 120,
                "max_body_bytes": 33554432,
            },
        ],
    }
    manifest.update(overrides)
    return manifest


def test_manifest_defaults_apply_to_routes(tmp_path: Path) -> None:
    loaded = load_provider_http_manifest(
        _provider(tmp_path, _manifest()),
        provider_type="audit-report",
        runtime_capabilities=("documents",),
    )

    assert loaded is not None
    assert loaded.timeout_seconds == 45
    assert loaded.max_body_bytes == 1048576
    by_id = {route.route_id: route for route in loaded.routes}
    assert by_id["documents.list"].timeout_seconds == 45
    assert by_id["documents.list"].max_body_bytes == 1048576
    assert by_id["documents.list"].response == "json"
    assert by_id["documents.file"].response == "binary"
    assert by_id["documents.file"].timeout_seconds == 120
    assert by_id["documents.file"].max_body_bytes == 33554432


def test_schema_version_key_is_still_accepted(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest.pop("version")
    manifest["schema_version"] = 1

    loaded = load_provider_http_manifest(
        _provider(tmp_path, manifest),
        provider_type="audit-report",
        runtime_capabilities=("documents",),
    )

    assert loaded is not None


@pytest.mark.parametrize("bad_manifest", [
    {"version": 2},
    {"timeout_seconds": 0},
    {"max_body_bytes": -1},
    {"routes": [{"response": "stream"}]},
])
def test_invalid_manifest_fields_are_rejected(tmp_path: Path, bad_manifest: dict) -> None:
    manifest = _manifest()
    if "routes" in bad_manifest:
        manifest["routes"][0].update(bad_manifest["routes"][0])
    else:
        manifest.update(bad_manifest)

    with pytest.raises(ValueError):
        load_provider_http_manifest(
            _provider(tmp_path, manifest),
            provider_type="audit-report",
            runtime_capabilities=("documents",),
        )


def test_binary_response_passthrough_adds_nosniff() -> None:
    response = _build_provider_http_response(
        Response(content=b"%PDF", media_type="application/pdf"),
        status_code=200,
        response_mode="binary",
    )

    assert response.media_type == "application/pdf"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_binary_html_gets_sandbox_csp() -> None:
    response = _build_provider_http_response(
        Response(content=b"<html></html>", media_type="text/html"),
        status_code=200,
        response_mode="binary",
    )

    assert "sandbox" in response.headers["Content-Security-Policy"]


def test_json_route_cannot_return_response() -> None:
    with pytest.raises(ProviderHttpRuntimeError):
        _build_provider_http_response(
            Response(content=b"{}", media_type="application/json"),
            status_code=200,
            response_mode="json",
        )


def test_binary_route_requires_response() -> None:
    with pytest.raises(ProviderHttpRuntimeError):
        _build_provider_http_response(
            {"ok": True},
            status_code=200,
            response_mode="binary",
        )
