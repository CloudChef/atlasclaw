# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect

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
