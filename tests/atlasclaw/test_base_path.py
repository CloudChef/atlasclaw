# -*- coding: utf-8 -*-
"""Unit tests for reverse-proxy base-path helpers."""

from __future__ import annotations

from app.atlasclaw.core.base_path import (
    build_base_path_url,
    cookie_path_for_base_path,
    normalize_base_path,
    strip_base_path,
)


class TestBasePathHelpers:
    """Validate browser-visible base-path transformations."""

    def test_normalize_base_path(self) -> None:
        assert normalize_base_path("") == ""
        assert normalize_base_path("/") == ""
        assert normalize_base_path("atlasclaw") == "/atlasclaw"
        assert normalize_base_path("/atlasclaw/") == "/atlasclaw"

    def test_build_base_path_url(self) -> None:
        assert build_base_path_url("", "/api/health") == "/api/health"
        assert build_base_path_url("/atlasclaw", "/api/health") == "/atlasclaw/api/health"
        assert build_base_path_url("/atlasclaw/", "/") == "/atlasclaw/"

    def test_strip_base_path(self) -> None:
        assert strip_base_path("/atlasclaw", "/atlasclaw") == "/"
        assert strip_base_path("/atlasclaw", "/atlasclaw/models") == "/models"
        assert strip_base_path("", "/models") == "/models"

    def test_cookie_path_for_base_path(self) -> None:
        assert cookie_path_for_base_path("") == "/"
        assert cookie_path_for_base_path("/atlasclaw") == "/atlasclaw"
