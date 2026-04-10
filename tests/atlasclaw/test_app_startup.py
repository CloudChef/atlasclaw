# -*- coding: utf-8 -*-
"""Regression tests for application startup imports."""

from __future__ import annotations

import importlib
import sys


def test_main_module_imports_successfully() -> None:
    """The FastAPI entry module should remain importable for service startup."""
    sys.modules.pop("app.atlasclaw.main", None)

    module = importlib.import_module("app.atlasclaw.main")

    assert module.app is not None
