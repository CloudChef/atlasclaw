# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import inspect
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry


async def _generic_script_handler(ctx=None, **kwargs):
    return kwargs


async def _zero_argument_generated_handler(ctx: "RunContext[Any]") -> "dict[str, Any]":
    return {"ctx": ctx}


_zero_argument_generated_handler.__annotations__ = {
    "args": Any,
    "kwargs": Any,
    "return": Any,
}


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


def test_runtime_handler_wraps_explicit_zero_argument_object_schema() -> None:
    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="smartcmp_read_current_form_schema",
        description="Read the current form schema draft.",
        source="provider",
        provider_type="smartcmp",
        parameters_schema={
            "type": "object",
            "properties": {},
        },
    )

    wrapped_handler = registry._build_runtime_handler(
        metadata,
        _zero_argument_generated_handler,
    )
    signature = inspect.signature(wrapped_handler)

    assert wrapped_handler is not _zero_argument_generated_handler
    assert list(signature.parameters.keys()) == ["ctx"]
    assert "ctx" in wrapped_handler.__annotations__

    registry.register_entry_to_agent(
        Agent(TestModel()),
        metadata,
        _zero_argument_generated_handler,
    )
