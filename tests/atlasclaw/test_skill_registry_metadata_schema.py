# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.atlasclaw.core.tool_confirmation import ToolConfirmationError, ToolConfirmationStore
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry


async def _generic_script_handler(ctx=None, **kwargs):
    return kwargs


def test_tool_definitions_prefer_metadata_parameters_schema() -> None:
    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="smartcmp_get_request_detail",
        description="Get request detail from SmartCMP.",
        source="provider",
        provider_type="smartcmp",
        parameters_schema={
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Request identifier."},
                "days": {"type": "integer", "description": "Recent day window.", "default": 90},
            },
            "required": ["identifier"],
        },
    )
    registry.register(metadata, _generic_script_handler)

    definitions = registry.to_tool_definitions()

    assert definitions == [
        {
            "name": "smartcmp_get_request_detail",
            "description": "Get request detail from SmartCMP.",
            "parameters": metadata.parameters_schema,
        }
    ]


def test_runtime_handler_signature_uses_metadata_parameters_schema() -> None:
    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="smartcmp_get_request_detail",
        description="Get request detail from SmartCMP.",
        source="provider",
        provider_type="smartcmp",
        parameters_schema={
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Request identifier."},
                "days": {"type": "integer", "description": "Recent day window.", "default": 90},
            },
            "required": ["identifier"],
        },
    )

    wrapped_handler = registry._build_runtime_handler(metadata, _generic_script_handler)
    signature = inspect.signature(wrapped_handler)

    assert list(signature.parameters.keys()) == ["ctx", "identifier", "days"]
    assert signature.parameters["identifier"].default is inspect.Parameter.empty
    assert signature.parameters["days"].default == 90


def test_mutation_runtime_requires_opaque_ticket_and_consumes_frozen_args_once() -> None:
    calls = []

    async def handler(ctx=None, **kwargs):
        calls.append(kwargs)
        return {"success": True}

    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="provider_mutation",
        source="provider",
        provider_type="provider",
        owner_skill_ref="provider:resource",
        effect="mutate",
        requires_approval=True,
        parameters_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
        },
    )
    registry.register(metadata, handler)
    store = ToolConfirmationStore()
    deps = SimpleNamespace(
        user_info=SimpleNamespace(user_id="user-1"),
        session_key="agent:main:user:user-1",
        extra={
            "agent_id": "main",
            "provider_type": "provider",
            "provider_instance_name": "primary",
            "_tool_confirmation_store": store,
            "tools_snapshot": [
                {
                    "name": "provider_mutation",
                    "provider_type": "provider",
                    "owner_skill_ref": "provider:resource",
                }
            ],
        },
    )
    ctx = SimpleNamespace(deps=deps)
    runtime = registry._build_runtime_handler(metadata, handler)

    first = asyncio.run(runtime(ctx, resource_id="resource-1"))
    assert first["confirmation_required"] is True
    assert calls == []
    action = deps.extra["_runtime_tool_confirmation_actions"][0]["object_actions"][0]
    grant = store.claim(
        action["confirmation_token"],
        owner_user_id="user-1",
        session_key="agent:main:user:user-1",
        agent_id="main",
    )
    deps.extra["_tool_confirmation_grant"] = grant
    assert asyncio.run(runtime(ctx, resource_id="model-overwrite")) == {"success": True}
    assert calls == [{"resource_id": "resource-1"}]
    with pytest.raises(ToolConfirmationError, match="already used"):
        asyncio.run(runtime(ctx, resource_id="resource-1"))


def test_mutation_execute_without_deps_and_visible_snapshot_revoke_fail_closed() -> None:
    calls = []

    async def handler(ctx=None, **kwargs):
        calls.append(kwargs)
        return {"success": True}

    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="provider_mutation",
        source="provider",
        provider_type="provider",
        owner_skill_ref="provider:resource",
        effect="mutate",
        requires_approval=True,
    )
    registry.register(metadata, handler)
    without_deps = json.loads(asyncio.run(registry.execute("provider_mutation", "{}")))
    assert "confirmation" in without_deps["error"].lower()

    deps = SimpleNamespace(
        user_info=SimpleNamespace(user_id="user-1"),
        session_key="agent:main:user:user-1",
        extra={
            "agent_id": "main",
            "provider_instance_name": "primary",
            "_tool_confirmation_store": ToolConfirmationStore(),
            "tools_snapshot": [],
        },
    )
    revoked = json.loads(asyncio.run(registry.execute("provider_mutation", "{}", deps)))
    assert "not available" in revoked["error"].lower()
    assert calls == []


def test_programmatic_invalid_effect_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillMetadata(name="unsafe", effect="write")
