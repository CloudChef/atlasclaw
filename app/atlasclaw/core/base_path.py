# -*- coding: utf-8 -*-
"""Helpers for browser-visible URLs under a reverse-proxy base path."""

from __future__ import annotations


def normalize_base_path(base_path: str | None) -> str:
    """Normalize a configured base path to '' or '/segment[/child]'."""
    raw = str(base_path or "").strip()
    if not raw or raw == "/":
        return ""
    if not raw.startswith("/"):
        raw = f"/{raw}"
    raw = raw.rstrip("/")
    return "" if raw == "/" else raw


def build_base_path_url(base_path: str | None, path: str) -> str:
    """Prefix a root-relative path with the normalized base path."""
    normalized_base = normalize_base_path(base_path)
    if not path:
        return f"{normalized_base}/" if normalized_base else "/"
    if "://" in path or path.startswith("//"):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    if path == "/":
        return f"{normalized_base}/" if normalized_base else "/"
    return f"{normalized_base}{path}" if normalized_base else path


def strip_base_path(base_path: str | None, path: str) -> str:
    """Remove the configured base path from a browser pathname."""
    normalized_base = normalize_base_path(base_path)
    raw_path = str(path or "").strip() or "/"
    if not normalized_base:
        return raw_path if raw_path.startswith("/") else f"/{raw_path}"
    if raw_path == normalized_base:
        return "/"
    if raw_path.startswith(f"{normalized_base}/"):
        stripped = raw_path[len(normalized_base):]
        return stripped if stripped.startswith("/") else f"/{stripped}"
    return raw_path if raw_path.startswith("/") else f"/{raw_path}"


def cookie_path_for_base_path(base_path: str | None) -> str:
    """Return the cookie path that matches the browser-visible app scope."""
    normalized_base = normalize_base_path(base_path)
    return normalized_base or "/"
