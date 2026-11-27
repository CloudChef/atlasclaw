# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Admission filtering when Provider activation changes during a run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.api.services.run_service import _filter_provider_instances_for_new_run
from app.atlasclaw.core.model_bindings import ProviderAdmission


@pytest.mark.asyncio
async def test_new_run_activation_filter_preserves_only_exact_admitted_instance() -> None:
    class DisabledResolver:
        async def effective_provider_instances(self, _instances):
            # A committed disable removes all instances from subsequent discovery.
            return {}

        async def provider_is_enabled(self, _provider_type, _instance_name):
            return False

    config = {
        "crm": {
            "production": {"credential": "admitted-server-config"},
            "staging": {"credential": "unrelated-server-config"},
        },
        "erp": {"default": {"credential": "other-provider-config"}},
    }
    ctx = SimpleNamespace(model_binding_resolver=DisabledResolver())
    admission = ProviderAdmission(
        provider_type="crm",
        instance_name="production",
        user_id="alice",
        run_id="parent-run-7",
    )

    admitted = await _filter_provider_instances_for_new_run(
        ctx,
        config,
        admission=admission,
    )
    new_run = await _filter_provider_instances_for_new_run(ctx, config)

    assert admitted == {"crm": {"production": {"credential": "admitted-server-config"}}}
    assert new_run == {}
