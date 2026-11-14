# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, Tool
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


def _runtime_tool(properties, *, handler=_generic_script_handler, required=()):
    metadata = SkillMetadata(
        name="example_query",
        description="Query with tool arguments.",
        parameters_schema={"type": "object", "properties": properties, "required": list(required)},
    )
    wrapped = SkillRegistry()._build_runtime_handler(metadata, handler)
    return Tool(wrapped, takes_ctx=True)


@pytest.mark.asyncio
async def test_omitted_argument_uses_callable_default() -> None:
    async def handler(ctx, query: str = "fallback"):
        return query

    tool = _runtime_tool({"query": {"type": "string"}}, handler=handler)
    validate = tool.function_schema.validator.validate_python

    assert await tool.function(None, **validate({})) == "fallback"
    schema = tool.function_schema.json_schema
    assert "default" not in schema["properties"]["query"]
    assert "query" not in schema.get("required", [])
    with pytest.raises(ValidationError):
        validate({"query": None})


@pytest.mark.asyncio
async def test_schema_defaults_preserve_explicit_falsey_values() -> None:
    tool = _runtime_tool({
        "query": {"type": "string", "default": "fallback"},
        "enabled": {"type": "boolean", "default": True},
        "count": {"type": "integer", "default": 5},
    })
    validate = tool.function_schema.validator.validate_python

    assert await tool.function(None, **validate({})) == {
        "query": "fallback", "enabled": True, "count": 5,
    }
    explicit = {"query": "", "enabled": False, "count": 0}
    assert await tool.function(None, **validate(explicit)) == explicit


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [
    {"type": ["string", "null"]},
    {"anyOf": [{"type": "string"}, {"type": "null"}]},
])
async def test_nullable_argument_distinguishes_null_from_omission(schema: dict) -> None:
    tool = _runtime_tool({"query": schema})
    validate = tool.function_schema.validator.validate_python

    assert await tool.function(None, **validate({})) == {}
    assert await tool.function(None, **validate({"query": None})) == {"query": None}
    with pytest.raises(ValidationError):
        validate({"query": {"unexpected": "object"}})


def test_required_field_with_schema_default_still_requires_input() -> None:
    tool = _runtime_tool({"identifier": {"type": "string", "default": "fallback"}}, required=("identifier",))
    with pytest.raises(ValidationError):
        tool.function_schema.validator.validate_python({})
