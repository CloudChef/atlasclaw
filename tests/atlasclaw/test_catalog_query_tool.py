# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.tools.discovery.catalog_query_tool import atlasclaw_catalog_query_tool


@pytest.mark.asyncio
async def test_catalog_query_tool_returns_provider_scoped_skills_markdown() -> None:
    registry = SimpleNamespace(
        get_all_provider_contexts=lambda: {
            "smartcmp": {
                "display_name": "SmartCMP",
                "description": "SmartCMP enterprise service management",
            }
        }
    )
    deps = SimpleNamespace(
        extra={
            "_service_provider_registry": registry,
            "tools_snapshot": [
                {
                    "name": "atlasclaw_catalog_query",
                    "description": "Query AtlasClaw runtime catalog",
                    "source": "builtin",
                    "provider_type": "",
                    "group_ids": ["group:catalog", "group:atlasclaw"],
                    "capability_class": "atlasclaw_catalog",
                },
                {
                    "name": "smartcmp_list_pending",
                    "description": "List SmartCMP pending approvals",
                    "source": "provider",
                    "provider_type": "smartcmp",
                    "group_ids": ["group:cmp", "group:smartcmp"],
                    "capability_class": "provider:smartcmp",
                    "qualified_skill_name": "smartcmp:approval",
                    "skill_name": "approval",
                },
                {
                    "name": "smartcmp_list_services",
                    "description": "List SmartCMP service catalogs",
                    "source": "provider",
                    "provider_type": "smartcmp",
                    "group_ids": ["group:cmp", "group:smartcmp"],
                    "capability_class": "provider:smartcmp",
                    "qualified_skill_name": "smartcmp:catalog",
                    "skill_name": "catalog",
                },
            ],
            "md_skills_snapshot": [
                {
                    "name": "approval",
                    "provider": "smartcmp",
                    "qualified_name": "smartcmp:approval",
                    "description": "SmartCMP approval workflows",
                    "location": "external",
                    "metadata": {
                        "provider_type": "smartcmp",
                        "triggers": ["cmp", "approval"],
                    },
                },
                {
                    "name": "catalog",
                    "provider": "smartcmp",
                    "qualified_name": "smartcmp:catalog",
                    "description": "SmartCMP service catalog operations",
                    "location": "external",
                    "metadata": {
                        "provider_type": "smartcmp",
                        "triggers": ["cmp", "service catalog"],
                    },
                },
            ],
            "tool_groups_snapshot": {
                "group:cmp": ["smartcmp_list_pending", "smartcmp_list_services"],
                "group:catalog": ["atlasclaw_catalog_query"],
            },
        }
    )
    ctx = SimpleNamespace(deps=deps)

    result = await atlasclaw_catalog_query_tool(
        ctx,
        provider_type="smartcmp",
        kind="skills",
    )

    text = result["content"][0]["text"]
    assert result["is_error"] is False
    assert "## SmartCMP Skills" in text
    assert "- `smartcmp:approval`" in text
    assert "- `smartcmp:catalog`" in text
    assert "`smartcmp_list_pending`" in text
    assert "`smartcmp_list_services`" in text
