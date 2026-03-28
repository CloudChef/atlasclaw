# -*- coding: utf-8 -*-
"""Authentication providers for Xuanwu."""

from __future__ import annotations

from .provider import AuthProvider
from .registry import AuthRegistry

__all__ = [
    "AuthProvider",
    "AuthRegistry",
]
