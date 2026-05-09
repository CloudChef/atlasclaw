# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Helpers for user-scoped runtime storage paths."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def normalize_runtime_user_id(user_id: str) -> str:
    """Return a stable single path segment for user-scoped runtime storage."""
    value = str(user_id or "").strip()
    if not value or value == "anonymous" or "\x00" in value:
        return "default"
    return quote(value, safe="-._~") or "default"


def user_runtime_dir(workspace_path: str | Path, user_id: str) -> Path:
    """Return `<workspace>/users/<safe_user_id>` for runtime storage."""
    return Path(workspace_path).resolve() / "users" / normalize_runtime_user_id(user_id)
