# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from pydantic_ai import Agent

from app.atlasclaw.agent.prompt_builder import PromptMode
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent import prompt_sections
from app.atlasclaw.agent.runner_prompt_context import (
    build_system_prompt,
    collect_capability_index_snapshot,
    collect_tools_snapshot,
)
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    build_preselected_md_skill_intent_plan,
    build_target_md_skill_workflow_context,
    build_explicit_tool_execution_prompt,
    enrich_target_md_skill_with_workflow_context,
    resolve_selected_md_skill_target,
    select_execution_prompt_mode,
    select_explicit_tool_execution_target,
    should_resolve_target_md_skill,
)
from app.atlasclaw.agent.runner_tool.runner_execution_payload import (
    build_direct_answer_recovery_payload,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import sanitize_workflow_only_text
from app.atlasclaw.agent.tool_gate_models import (
    ToolIntentAction,
    ToolIntentPlan,
)


def test_collect_tools_snapshot_prefers_deps_extra_snapshot() -> None:
    deps = SimpleNamespace(extra={"tools_snapshot": [{"name": "web_search", "description": "search web"}]})
    snapshot = collect_tools_snapshot(agent=object(), deps=deps)
    assert snapshot == [
        {"name": "web_search", "description": "search web", "capability_class": "web_search"}
    ]


def test_collect_tools_snapshot_keeps_authoritative_snapshot_without_remerge() -> None:
    agent = SimpleNamespace(
        tools=[
            {"name": "web_search", "description": "search web"},
            {"name": "web_fetch", "description": "fetch web page"},
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [{"name": "web_search", "description": "search web"}],
            "tools_snapshot_authoritative": True,
        }
    )
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert snapshot == [
        {"name": "web_search", "description": "search web", "capability_class": "web_search"}
    ]


def test_collect_tools_snapshot_keeps_authoritative_empty_snapshot_without_remerge() -> None:
    agent = SimpleNamespace(
        tools=[
            {"name": "web_search", "description": "search web"},
            {"name": "web_fetch", "description": "fetch web page"},
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [],
            "tools_snapshot_authoritative": True,
        }
    )
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert snapshot == []


def test_collect_tools_snapshot_normalizes_silent_backend_tool_result_mode() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "smartcmp_list_components",
                "description": "Internal lookup",
                "result_mode": "tool_only_ok",
                "routing_visibility": "hidden",
            }
        ]
    )
    deps = SimpleNamespace(extra={})

    snapshot = collect_tools_snapshot(agent=agent, deps=deps)

    assert len(snapshot) == 1
    assert snapshot[0]["result_mode"] == "silent_ok"
    assert snapshot[0]["description"] == "Internal lookup"


def test_collect_tools_snapshot_merges_agent_tools_when_snapshot_not_authoritative() -> None:
    agent = SimpleNamespace(
        tools=[
            {"name": "web_search", "description": "search web"},
            {"name": "web_fetch", "description": "fetch web page"},
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [{"name": "web_search", "description": "search web"}],
            "tools_snapshot_authoritative": False,
        }
    )
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    names = [item["name"] for item in snapshot]
    assert names == ["web_search", "web_fetch"]


def test_collect_tools_snapshot_preserves_normalized_metadata_from_deps() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [
                {
                    "name": "cmp_list_pending",
                    "description": "List pending CMP approvals",
                    "source": "provider",
                    "provider_type": "acme",
                    "group_ids": ["group:cmp"],
                    "capability_class": "provider:acme",
                    "priority": 150,
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "identifier": {"type": "string"},
                            "days": {"type": "integer"},
                        },
                        "required": ["identifier"],
                    },
                    "routing_visibility": "always",
                    "aliases": ["cmp", "smartcmp approvals"],
                    "keywords": ["approval", "pending"],
                    "use_when": ["User asks for pending approvals"],
                    "avoid_when": ["User asks for weather"],
                    "result_mode": "tool_only_ok",
                    "success_contract": {
                        "type": "identifier_presence",
                        "fields": ["requestId"],
                        "text_labels": ["Request ID"],
                    },
                }
            ]
        }
    )
    snapshot = collect_tools_snapshot(agent=object(), deps=deps)
    assert snapshot == [
        {
            "name": "cmp_list_pending",
            "description": "List pending CMP approvals",
            "source": "provider",
            "provider_type": "acme",
            "group_ids": ["group:cmp"],
            "capability_class": "provider:acme",
            "priority": 150,
            "parameters_schema": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": ["identifier"],
            },
            "routing_visibility": "always",
            "aliases": ["cmp", "smartcmp approvals"],
            "keywords": ["approval", "pending"],
            "use_when": ["User asks for pending approvals"],
            "avoid_when": ["User asks for weather"],
            "result_mode": "tool_only_ok",
            "success_contract": {
                "type": "identifier_presence",
                "fields": ["requestId"],
                "text_labels": ["Request ID"],
            },
        }
    ]


def test_collect_tools_snapshot_reads_pydantic_ai_toolsets() -> None:
    agent = Agent("test")

    @agent.tool_plain
    def web_search(query: str) -> str:
        """Search the web by query."""
        return query

    deps = SimpleNamespace(extra={"tools_snapshot": []})
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert snapshot
    assert any(tool["name"] == "web_search" for tool in snapshot)


def test_collect_tools_snapshot_infers_provider_capability_from_skills_snapshot() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "jira_search",
                "description": "Search Jira issues",
            }
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [],
            "skills_snapshot": [
                {
                    "name": "jira_search",
                    "description": "Search Jira issues",
                    "category": "provider",
                    "provider_type": "jira",
                }
            ],
            "md_skills_snapshot": [],
        }
    )
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert snapshot == [
        {
            "name": "jira_search",
            "description": "Search Jira issues",
            "provider_type": "jira",
            "category": "provider",
            "capability_class": "provider:jira",
        }
    ]


def test_collect_tools_snapshot_infers_md_skill_capability() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "summarize_skill_run",
                "description": "Run summarize skill",
            }
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [],
            "skills_snapshot": [],
            "md_skills_snapshot": [
                {
                    "name": "summarize",
                    "provider": "",
                    "metadata": {
                        "tool_name": "summarize_skill_run",
                        "category": "skill",
                    },
                }
            ],
        }
    )
    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert snapshot == [
        {
            "name": "summarize_skill_run",
            "description": "Run summarize skill",
            "category": "skill",
            "source": "md_skill",
            "skill_name": "summarize",
            "capability_class": "skill",
        }
    ]


def test_collect_tools_snapshot_prefers_tool_specific_md_success_contract() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "provider_submit_request",
                "description": "Submit request",
                "result_mode": "tool_only_ok",
            }
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [],
            "skills_snapshot": [],
            "md_skills_snapshot": [
                {
                    "name": "request",
                    "qualified_name": "acme:request",
                    "provider": "acme",
                    "metadata": {
                        "success_contract": {
                            "type": "identifier_presence",
                            "fields": ["id"],
                        },
                        "tool_submit_name": "provider_submit_request",
                        "tool_submit_success_contract": {
                            "type": "identifier_presence",
                            "fields": ["requestId"],
                            "text_labels": ["Request ID"],
                        },
                    },
                }
            ],
        }
    )

    snapshot = collect_tools_snapshot(agent=agent, deps=deps)

    assert len(snapshot) == 1
    assert snapshot[0]["name"] == "provider_submit_request"
    assert snapshot[0]["provider_type"] == "acme"
    assert snapshot[0]["result_mode"] == "tool_only_ok"
    assert snapshot[0]["capability_class"] == "provider:acme"
    assert snapshot[0]["success_contract"] == {
        "type": "identifier_presence",
        "fields": ["requestId"],
        "text_labels": ["Request ID"],
    }


def test_collect_tools_snapshot_falls_back_to_skills_snapshot_when_agent_has_no_tools() -> None:
    agent = SimpleNamespace(tools=[])
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [],
            "skills_snapshot": [
                {
                    "name": "web_search",
                    "description": "Web search",
                    "category": "builtin:web",
                },
                {
                    "name": "openmeteo_weather",
                    "description": "Weather lookup",
                    "category": "builtin:web",
                },
            ],
            "md_skills_snapshot": [],
        }
    )

    snapshot = collect_tools_snapshot(agent=agent, deps=deps)
    assert any(tool["name"] == "web_search" for tool in snapshot)
    assert any(tool["name"] == "openmeteo_weather" for tool in snapshot)
    assert any(
        tool["name"] == "web_search" and tool.get("capability_class") == "web_search"
        for tool in snapshot
    )


def test_collect_tools_snapshot_preserves_runtime_tool_metadata_fields() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get current and forecast weather via Open-Meteo APIs",
                "source": "builtin",
                "group_ids": ["group:web"],
                "capability_class": "weather",
                "routing_visibility": "contextual",
                "aliases": ["weather", "forecast"],
                "keywords": ["澶╂皵", "棰勬姤", "temperature"],
                "use_when": ["User asks for a forecast by place and date"],
                "avoid_when": ["User asks for enterprise approvals"],
                "result_mode": "tool_only_ok",
            }
        ]
    )
    deps = SimpleNamespace(extra={"tools_snapshot": []})

    snapshot = collect_tools_snapshot(agent=agent, deps=deps)

    assert snapshot == [
        {
            "name": "openmeteo_weather",
            "description": "Get current and forecast weather via Open-Meteo APIs",
            "source": "builtin",
            "group_ids": ["group:web"],
            "capability_class": "weather",
            "routing_visibility": "contextual",
            "aliases": ["weather", "forecast"],
            "keywords": ["澶╂皵", "棰勬姤", "temperature"],
            "use_when": ["User asks for a forecast by place and date"],
            "avoid_when": ["User asks for enterprise approvals"],
            "result_mode": "tool_only_ok",
        }
    ]


def test_collect_tools_snapshot_does_not_stringify_none_provider_metadata() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot": [
                {
                    "name": "web_search",
                    "description": "Web search",
                    "provider_type": "",
                    "category": "builtin:web",
                    "source": "builtin",
                    "group_ids": ["group:web"],
                    "capability_class": "web_search",
                    "routing_visibility": "general",
                }
            ],
            "skills_snapshot": [
                {
                    "name": "web_search",
                    "description": "Web search",
                    "provider_type": None,
                    "category": "builtin:web",
                    "source": "builtin",
                    "group_ids": ["group:web"],
                    "capability_class": "web_search",
                    "routing_visibility": "general",
                }
            ],
            "md_skills_snapshot": [],
        }
    )

    snapshot = collect_tools_snapshot(agent=object(), deps=deps)

    assert snapshot == [
        {
            "name": "web_search",
            "description": "Web search",
            "category": "builtin:web",
            "source": "builtin",
            "group_ids": ["group:web"],
            "capability_class": "web_search",
            "routing_visibility": "general",
        }
    ]


def test_collect_capability_index_snapshot_orders_sources_and_omits_bodies() -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                },
            }
        ]
    )
    deps = SimpleNamespace(
        extra={
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [
                {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                    },
                }
            ],
            "md_skills_snapshot": [
                {
                    "name": "jira",
                    "qualified_name": "jira:search",
                    "description": "Search Jira",
                    "file_path": "/skills/jira/SKILL.md",
                    "provider": "jira",
                    "metadata": {
                        "tool_name": "jira_search",
                        "triggers": ["jira", "issue"],
                        "use_when": ["User asks for Jira issues"],
                    },
                    "body": "FULL BODY SHOULD NOT APPEAR",
                }
            ],
            "skills_snapshot": [
                {
                    "name": "archive_issue",
                    "description": "Archive issue",
                    "location": "built-in",
                    "keywords": ["archive"],
                }
            ],
        }
    )

    snapshot = collect_capability_index_snapshot(agent=agent, deps=deps)

    assert [item["kind"] for item in snapshot] == ["md_skill", "tool", "skill"]
    assert snapshot[0]["capability_id"] == "skill:jira:search"
    assert snapshot[1]["capability_id"] == "tool:web_search"
    assert snapshot[2]["capability_id"] == "skill:archive_issue"
    assert snapshot[0]["locator"] == "/skills/jira/SKILL.md"
    assert snapshot[1]["locator"] == "web_search(query?)"
    assert snapshot[2]["locator"] == "built-in"
    assert snapshot[0]["provider_type"] == "jira"
    assert snapshot[0]["declared_tool_names"] == ["jira_search", "jira"]
    assert "input_hints" not in snapshot[0]
    assert "use_instructions" not in snapshot[0]
    assert snapshot[1]["declared_tool_names"] == ["web_search"]
    assert all("body" not in item for item in snapshot)


def test_collect_capability_index_snapshot_enriches_md_skill_routing_hints() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [],
            "md_skills_snapshot": [
                {
                    "name": "acme:request",
                    "qualified_name": "acme:request",
                    "description": "Submit provider requests.",
                    "file_path": "/skills/request/SKILL.md",
                    "provider": "acme",
                    "metadata": {
                        "provider_type": "acme",
                        "use_when": [
                            "User already knows the service they want and is ready to provide request parameters",
                        ],
                        "avoid_when": [
                            "User asks for multiple resource requests with per-item differences such as quantity, first/second/third configurations, or different settings per instance",
                        ],
                    },
                },
                {
                    "name": "acme:request-decomposition-agent",
                    "qualified_name": "acme:request-decomposition-agent",
                    "description": "Draft provider request plans.",
                    "file_path": "/skills/request-decomposition-agent/SKILL.md",
                    "provider": "acme",
                    "metadata": {
                        "provider_type": "acme",
                        "use_when": [
                            "User asks for multiple items with distinct per-item configuration",
                            "User enumerates differences like first item / second item / third item",
                        ],
                        "avoid_when": [
                            "User has specific parameters ready for a single request",
                        ],
                    },
                },
            ],
            "skills_snapshot": [],
        }
    )

    snapshot = collect_capability_index_snapshot(agent=SimpleNamespace(tools=[]), deps=deps)

    request_entry = next(
        item for item in snapshot if item["capability_id"] == "skill:acme:request"
    )
    decomposition_entry = next(
        item
        for item in snapshot
        if item["capability_id"] == "skill:acme:request-decomposition-agent"
    )

    assert "Routing hints:" in request_entry["description"]
    assert "multiple items" in request_entry["description"]
    assert "single request" in decomposition_entry["description"]
    assert "ordinal item differences" in decomposition_entry["description"]


def test_collect_capability_index_snapshot_uses_explicit_md_tool_artifact_capability() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [],
            "md_skills_snapshot": [
                {
                    "name": "exporter",
                    "qualified_name": "files:exporter",
                    "description": "Export the current result set.",
                    "file_path": "/skills/exporter/SKILL.md",
                    "provider": "",
                    "metadata": {
                        "tool_create_name": "pdf_create_document",
                        "tool_create_capability_class": "artifact:pdf",
                    },
                }
            ],
            "skills_snapshot": [],
        }
    )

    snapshot = collect_capability_index_snapshot(agent=SimpleNamespace(tools=[]), deps=deps)

    assert snapshot[0]["capability_id"] == "skill:files:exporter"
    assert snapshot[0]["artifact_types"] == ["pdf"]


def test_collect_capability_index_snapshot_does_not_infer_artifacts_from_plain_text_tokens() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [
                {
                    "name": "presentation_helper",
                    "description": "Create PPT decks for reviews.",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                        },
                    },
                }
            ],
            "md_skills_snapshot": [],
            "skills_snapshot": [],
        }
    )

    snapshot = collect_capability_index_snapshot(agent=SimpleNamespace(tools=[]), deps=deps)

    assert snapshot[0]["capability_id"] == "tool:presentation_helper"
    assert snapshot[0]["artifact_types"] == []


def test_collect_capability_index_snapshot_omits_internal_tools() -> None:
    deps = SimpleNamespace(
        extra={
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [
                {
                    "name": "atlasclaw_catalog_query",
                    "description": "Query runtime catalogs",
                    "routing_visibility": "internal",
                },
                {
                    "name": "public_lookup",
                    "description": "Visible lookup tool",
                    "routing_visibility": "general",
                },
            ],
            "md_skills_snapshot": [],
            "skills_snapshot": [],
        }
    )

    snapshot = collect_capability_index_snapshot(agent=SimpleNamespace(tools=[]), deps=deps)

    assert [item["capability_id"] for item in snapshot] == ["tool:public_lookup"]


def test_build_system_prompt_uses_unified_capability_index_surface(tmp_path) -> None:
    agent = SimpleNamespace(
        tools=[
            {
                "name": "web_search",
                "description": "Search the web",
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                },
            }
        ]
    )
    deps = SimpleNamespace(
        user_info=SimpleNamespace(
            user_id="anonymous",
            display_name="",
            tenant_id="default",
            roles=[],
        ),
        extra={
            "md_skills_snapshot": [
                {
                    "name": "jira",
                    "qualified_name": "jira:search",
                    "description": "Search Jira",
                    "file_path": "/skills/jira/SKILL.md",
                    "body": "FULL BODY SHOULD NOT APPEAR",
                }
            ],
            "skills_snapshot": [
                {
                    "name": "archive_issue",
                    "description": "Archive issue",
                    "location": "built-in",
                }
            ],
        },
    )
    builder = PromptBuilder(
        PromptBuilderConfig(
            workspace_path=str(tmp_path),
            capability_index_max_chars=2000,
        )
    )

    prompt = build_system_prompt(builder, session=None, deps=deps, agent=agent)

    assert "## Capabilities" in prompt
    assert "skill:jira:search" in prompt
    assert "provider:jira" in prompt
    assert "## Built-in Tools (Use ONLY if no MD Skill matches)" not in prompt
    assert "<available_skills>" not in prompt
    assert "FULL BODY SHOULD NOT APPEAR" not in prompt


def test_build_system_prompt_capability_index_keeps_md_routing_hints(tmp_path) -> None:
    deps = SimpleNamespace(
        user_info=SimpleNamespace(
            user_id="anonymous",
            display_name="",
            tenant_id="default",
            roles=[],
        ),
        extra={
            "md_skills_snapshot": [
                {
                    "name": "acme:request-decomposition-agent",
                    "qualified_name": "acme:request-decomposition-agent",
                    "description": "Draft provider request plans.",
                    "file_path": "/skills/request-decomposition-agent/SKILL.md",
                    "provider": "acme",
                    "metadata": {
                        "provider_type": "acme",
                        "use_when": [
                            "User asks for multiple items with distinct per-item configuration",
                            "User enumerates differences like first item / second item / third item",
                        ],
                        "avoid_when": [
                            "User has specific parameters ready for a single request",
                        ],
                    },
                }
            ],
            "skills_snapshot": [],
        },
    )
    builder = PromptBuilder(
        PromptBuilderConfig(
            workspace_path=str(tmp_path),
            capability_index_max_chars=3000,
        )
    )

    prompt = build_system_prompt(
        builder,
        session=None,
        deps=deps,
        agent=SimpleNamespace(tools=[]),
    )

    assert "Routing hints:" in prompt
    assert "multiple items" in prompt
    assert "single request" in prompt


def test_build_system_prompt_includes_provider_auth_diagnostics(tmp_path) -> None:
    deps = SimpleNamespace(
        user_info=SimpleNamespace(
            user_id="user-1",
            display_name="User One",
            tenant_id="default",
            roles=["user"],
        ),
        extra={
            "provider_auth_diagnostics": {
                "example-provider": {
                    "default": {
                        "provider_type": "example-provider",
                        "instance_name": "default",
                        "auth_chain": ["user_token"],
                        "missing_user_token": True,
                        "contact_admin": False,
                    }
                }
            },
            "tools_snapshot_authoritative": True,
            "tools_snapshot": [],
            "md_skills_snapshot": [],
            "skills_snapshot": [],
        },
    )
    builder = PromptBuilder(PromptBuilderConfig(workspace_path=str(tmp_path)))

    prompt = build_system_prompt(builder, session=None, deps=deps, agent=SimpleNamespace(tools=[]))

    assert "## Provider Authentication Diagnostics" in prompt
    assert "provider:example-provider instance:default" in prompt
    assert "personal provider access credential is not configured" in prompt
    assert "`user_token`" not in prompt
    assert "personal account settings" in prompt
    assert "Do not also tell them to contact an administrator for this case." in prompt
    assert "atlasclaw.json" not in prompt
    assert "paste credentials" in prompt


def test_build_capability_index_truncates_stably_with_budget() -> None:
    config = PromptBuilderConfig(
        workspace_path="",
        capability_index_max_chars=420,
        capability_index_max_count=10,
        capability_index_desc_max_chars=40,
    )
    capability_index = [
        {
            "kind": "md_skill",
            "capability_id": f"skill:skill{i}",
            "name": f"skill{i}",
            "description": "D" * 120,
            "locator": f"/skills/skill{i}/SKILL.md",
        }
        for i in range(8)
    ]

    first = prompt_sections.build_capability_index(config, capability_index)
    second = prompt_sections.build_capability_index(config, capability_index)

    assert first == second
    assert len(first) <= 420
    assert "Showing" in first
    assert "## Capabilities" in first
    assert "D" * 41 not in first


def test_select_execution_prompt_mode_uses_minimal_for_small_explicit_toolset() -> None:
    mode = select_execution_prompt_mode(
        intent_action="use_tools",
        is_follow_up=False,
        projected_tool_count=1,
    )

    assert mode is PromptMode.MINIMAL


def test_select_execution_prompt_mode_uses_minimal_for_direct_answer_without_visible_tools() -> None:
    mode = select_execution_prompt_mode(
        intent_action="direct_answer",
        is_follow_up=False,
        projected_tool_count=0,
    )

    assert mode is PromptMode.MINIMAL


def test_select_execution_prompt_mode_uses_minimal_when_target_md_skill_selected() -> None:
    mode = select_execution_prompt_mode(
        intent_action="create_artifact",
        is_follow_up=True,
        projected_tool_count=1,
        has_target_md_skill=True,
    )

    assert mode is PromptMode.MINIMAL


def test_select_execution_prompt_mode_keeps_full_for_follow_up_tool_turn() -> None:
    mode = select_execution_prompt_mode(
        intent_action="use_tools",
        is_follow_up=True,
        projected_tool_count=1,
    )

    assert mode is PromptMode.FULL


def test_select_explicit_tool_execution_target_returns_single_tool_only_candidate() -> None:
    target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_tool_names=["openmeteo_weather"]),
        is_follow_up=False,
        projected_tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "result_mode": "tool_only_ok",
                "capability_class": "weather",
            },
            {
                "name": "select_provider_instance",
                "description": "Select provider instance",
                "capability_class": "session",
                "coordination_only": True,
            },
        ],
    )

    assert target is not None
    assert target["name"] == "openmeteo_weather"


def test_select_explicit_tool_execution_target_allows_explicit_create_artifact_tool() -> None:
    target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.CREATE_ARTIFACT,
            target_tool_names=["pptx_create_deck"],
            target_capability_classes=["artifact:pptx"],
        ),
        is_follow_up=False,
        projected_tools=[
            {
                "name": "pptx_create_deck",
                "description": "Create a PPTX deck",
                "result_mode": "tool_only_ok",
                "capability_class": "artifact:pptx",
            }
        ],
    )

    assert target is not None
    assert target["name"] == "pptx_create_deck"


def test_select_explicit_tool_execution_target_skips_when_target_md_skill_is_loaded() -> None:
    target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.CREATE_ARTIFACT,
            target_tool_names=["pptx_create_deck"],
            target_skill_names=["pptx"],
        ),
        is_follow_up=False,
        projected_tools=[
            {
                "name": "pptx_create_deck",
                "description": "Create a PPTX deck",
                "result_mode": "tool_only_ok",
                "capability_class": "artifact:pptx",
            }
        ],
        has_target_md_skill=True,
    )

    assert target is None


def test_resolve_selected_md_skill_target_loads_selected_skill_instructions(tmp_path) -> None:
    skill_dir = tmp_path / "pptx"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# PPTX Skill\n\nCreate PPTX decks.", encoding="utf-8")
    deps = SimpleNamespace(
        extra={
            "md_skills_snapshot": [
                {
                    "name": "pptx",
                    "qualified_name": "pptx",
                    "description": "Create PPTX decks",
                    "file_path": str(skill_path),
                    "provider": "",
                    "metadata": {
                        "tool_name": "pptx_create_deck",
                        "capability_class": "artifact:pptx",
                        "triggers": ["pptx"],
                        "use_when": ["User asks for PPTX output"],
                    },
                }
            ]
        }
    )

    target = resolve_selected_md_skill_target(
        agent=SimpleNamespace(tools=[]),
        deps=deps,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.CREATE_ARTIFACT,
            target_skill_names=["pptx"],
            target_capability_classes=["artifact:pptx"],
            target_tool_names=["pptx_create_deck"],
        ),
        max_file_bytes=1024,
    )

    assert target is not None
    assert target["qualified_name"] == "pptx"
    assert target["file_path"] == str(skill_path)
    assert "PPTX Skill" in target["instructions"]
    assert "Create PPTX decks." in target["instructions"]
    assert "content" not in target


def test_resolve_selected_md_skill_target_loads_full_provider_skill_instructions(tmp_path) -> None:
    skill_dir = tmp_path / "acme-request"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "# Request Skill\n\n"
        "Short intro.\n\n"
        + "\n".join(f"filler line {index}" for index in range(80))
        + "\n\nCRITICAL TAIL RULE: request body must follow the selected skill instructions.",
        encoding="utf-8",
    )
    deps = SimpleNamespace(
        extra={
            "md_skills_snapshot": [
                {
                    "name": "acme:request",
                    "qualified_name": "acme:request",
                    "description": "Submit provider requests",
                    "file_path": str(skill_path),
                    "provider": "acme",
                    "metadata": {
                        "provider_type": "acme",
                        "tool_submit_name": "acme_submit_request",
                        "tool_submit_entrypoint": "scripts/submit.py",
                    },
                }
            ]
        }
    )

    target = resolve_selected_md_skill_target(
        agent=SimpleNamespace(tools=[]),
        deps=deps,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_skill_names=["acme:request"],
            target_provider_types=["acme"],
            target_tool_names=["acme_submit_request"],
        ),
        max_file_bytes=10_000,
    )

    assert target is not None
    assert target["qualified_name"] == "acme:request"
    assert "instructions_mode" not in target
    assert "CRITICAL TAIL RULE" in target["instructions"]


def test_resolve_selected_md_skill_target_loads_full_docs_only_skill(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "# XLSX Skill\n\n"
        "Create Excel workbooks.\n\n"
        + "\n".join(f"filler line {index}" for index in range(200))
        + "\n\nTAIL SHOULD NOT BE INCLUDED",
        encoding="utf-8",
    )
    deps = SimpleNamespace(
        extra={
            "md_skills_snapshot": [
                {
                    "name": "xlsx",
                    "qualified_name": "xlsx",
                    "description": "Create Excel workbooks",
                    "file_path": str(skill_path),
                    "provider": "",
                    "metadata": {},
                }
            ]
        }
    )

    target = resolve_selected_md_skill_target(
        agent=SimpleNamespace(tools=[]),
        deps=deps,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.CREATE_ARTIFACT,
            target_skill_names=["xlsx"],
            target_capability_classes=["artifact:xlsx"],
        ),
        max_file_bytes=10_000,
    )

    assert target is not None
    assert target["qualified_name"] == "xlsx"
    assert "instructions_mode" not in target
    assert "XLSX Skill" in target["instructions"]
    assert "TAIL SHOULD NOT BE INCLUDED" in target["instructions"]


def test_resolve_selected_md_skill_target_prefers_docs_only_skill_for_multi_skill_turn(tmp_path) -> None:
    provider_dir = tmp_path / "acme"
    provider_dir.mkdir()
    provider_path = provider_dir / "SKILL.md"
    provider_path.write_text("# Approval Skill\n\nQuery pending approvals.", encoding="utf-8")
    xlsx_dir = tmp_path / "xlsx"
    xlsx_dir.mkdir()
    xlsx_path = xlsx_dir / "SKILL.md"
    xlsx_path.write_text("# XLSX Skill\n\nCreate Excel workbooks.", encoding="utf-8")
    deps = SimpleNamespace(
        extra={
            "md_skills_snapshot": [
                {
                    "name": "acme:approval",
                    "qualified_name": "acme:approval",
                    "description": "Query pending approvals",
                    "file_path": str(provider_path),
                    "provider": "acme",
                    "metadata": {
                        "provider_type": "acme",
                        "tool_query_name": "acme_list_pending",
                        "tool_query_entrypoint": "scripts/approval.py:list_pending",
                    },
                },
                {
                    "name": "xlsx",
                    "qualified_name": "xlsx",
                    "description": "Create Excel workbooks",
                    "file_path": str(xlsx_path),
                    "provider": "",
                    "metadata": {},
                },
            ]
        }
    )

    target = resolve_selected_md_skill_target(
        agent=SimpleNamespace(tools=[]),
        deps=deps,
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_skill_names=["acme:approval", "xlsx"],
            target_provider_types=["acme"],
        ),
        max_file_bytes=1024,
    )

    assert target is not None
    assert target["qualified_name"] == "xlsx"
    assert "XLSX Skill" in target["instructions"]
    assert "Create Excel workbooks." in target["instructions"]
    assert "content" not in target


def test_preselected_md_skill_plan_overrides_routing_skill_for_webhook() -> None:
    deps = SimpleNamespace(
        extra={
            "webhook_skill": "acme:preapproval-agent",
            "target_md_skill": {
                "provider": "acme",
                "qualified_name": "acme:preapproval-agent",
                "file_path": "/tmp/preapproval-agent/SKILL.md",
            },
        }
    )

    plan = build_preselected_md_skill_intent_plan(deps)

    assert plan is not None
    assert plan.action == ToolIntentAction.USE_TOOLS
    assert plan.target_provider_types == ["acme"]
    assert plan.target_skill_names == ["acme:preapproval-agent"]
    assert plan.target_group_ids == ["group:acme"]
    assert plan.reason == "preselected_target_md_skill"


def test_enrich_target_md_skill_with_workflow_context_attaches_structured_context() -> None:
    enriched = enrich_target_md_skill_with_workflow_context(
        target_md_skill={
            "provider": "acme",
            "qualified_name": "acme:request",
            "file_path": "/skills/request/SKILL.md",
            "content": "# request",
        },
        workflow_trace={
            "recent_tool_metadata": [
                {
                    "tool_name": "acme_list_services",
                    "metadata": [
                        {
                            "index": 1,
                            "id": "BUILD-IN-CATALOG-LINUX-VM",
                            "name": "Linux VM",
                        }
                    ],
                }
            ]
        },
    )

    assert isinstance(enriched, dict)
    assert enriched["provider"] == "acme"
    assert enriched["qualified_name"] == "acme:request"
    assert enriched["workflow_context"] == {
        "recent_tool_metadata": [
            {
                "tool_name": "acme_list_services",
                "metadata": [
                    {
                        "index": 1,
                        "id": "BUILD-IN-CATALOG-LINUX-VM",
                        "name": "Linux VM",
                    }
                ],
            }
        ]
    }


def test_build_target_md_skill_workflow_context_collects_recent_tool_internal_metadata() -> None:
    context = build_target_md_skill_workflow_context(
        recent_history=[
            {"role": "user", "content": "request cloud resource"},
            {
                "role": "tool",
                "tool_name": "smartcmp_list_services",
                "content": {
                    "output": "Found 1 published catalog(s)",
                    "_internal": '[{"index":1,"id":"BUILD-IN-CATALOG-LINUX-VM","name":"Linux VM"}]',
                },
            },
        ]
    )

    assert context == {
        "recent_tool_metadata": [
            {
                "tool_name": "smartcmp_list_services",
                "metadata": [
                    {
                        "index": 1,
                        "id": "BUILD-IN-CATALOG-LINUX-VM",
                        "name": "Linux VM",
                    }
                ],
            }
        ]
    }


def test_build_target_md_skill_renders_current_workflow_context_block() -> None:
    rendered = prompt_sections.build_target_md_skill(
        {
            "provider": "acme",
            "qualified_name": "acme:request",
            "file_path": "/skills/request/SKILL.md",
            "content": "# request",
            "workflow_context": {
                "recent_tool_metadata": [
                    {
                        "tool_name": "smartcmp_list_services",
                        "metadata": [{"index": 1, "name": "Linux VM"}],
                    }
                ]
            },
        }
    )

    assert "### Current Workflow Context" in rendered
    assert '"tool_name": "smartcmp_list_services"' in rendered
    assert '"name": "Linux VM"' in rendered


def test_build_target_md_skill_sanitizes_workflow_only_lookup_text() -> None:
    rendered = prompt_sections.build_target_md_skill(
        {
            "provider": "acme",
            "qualified_name": "acme:request",
            "file_path": "/skills/request/SKILL.md",
            "content": (
                "Silent backend lookup for request workflow.\n"
                "After the internal lookup result is available, continue with the next natural-language follow-up."
            ),
        }
    )

    assert "backend" not in rendered.lower()
    assert "Silent backend lookup for request workflow." not in rendered
    assert "next user-facing question or confirmation" in rendered
    assert "Never repeat lookup scaffolding" in rendered
    assert "raw user-facing reply" in rendered
    assert sanitize_workflow_only_text(
        "Silent backend lookup for request workflow. Continue with the next natural-language follow-up."
    ) == "Continue with the next natural-language follow-up."
    assert sanitize_workflow_only_text(
        "Do not announce intermediate tool calls or expose their internal metadata as a raw user-facing reply."
    ) == (
        "Do not announce intermediate tool calls or expose their internal metadata as a raw user-facing reply."
    )


def test_should_resolve_target_md_skill_for_llm_first_skill_hint_plan() -> None:
    assert should_resolve_target_md_skill(
        ToolIntentPlan(
            action=ToolIntentAction.DIRECT_ANSWER,
            target_skill_names=["acme:request"],
            target_provider_types=["acme"],
        )
    )


def test_should_resolve_target_md_skill_for_llm_first_tool_hint_plan() -> None:
    assert should_resolve_target_md_skill(
        ToolIntentPlan(
            action=ToolIntentAction.DIRECT_ANSWER,
            target_provider_types=["acme"],
            target_tool_names=["acme_submit_request"],
        )
    )


def test_select_explicit_tool_execution_target_skips_follow_up_and_non_terminal_tools() -> None:
    follow_up_target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_tool_names=["openmeteo_weather"]),
        is_follow_up=True,
        projected_tools=[
            {
                "name": "openmeteo_weather",
                "description": "Get weather forecast",
                "result_mode": "tool_only_ok",
            }
        ],
    )
    llm_target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(action=ToolIntentAction.USE_TOOLS, target_tool_names=["smartcmp_submit"]),
        is_follow_up=False,
        projected_tools=[
            {
                "name": "smartcmp_submit",
                "description": "Submit SmartCMP request",
                "result_mode": "llm",
            }
        ],
    )

    assert follow_up_target is None
    assert llm_target is None


def test_select_explicit_tool_execution_target_allows_silent_backend_tools_on_forced_follow_up_turns() -> None:
    target = select_explicit_tool_execution_target(
        intent_plan=ToolIntentPlan(
            action=ToolIntentAction.USE_TOOLS,
            target_tool_names=["smartcmp_list_components"],
            target_skill_names=["acme:request"],
        ),
        is_follow_up=True,
        projected_tools=[
            {
                "name": "smartcmp_list_components",
                "description": "Internal lookup",
                "result_mode": "tool_only_ok",
                "routing_visibility": "hidden",
            }
        ],
        has_target_md_skill=True,
    )

    assert target is not None
    assert target["name"] == "smartcmp_list_components"


def test_build_explicit_tool_execution_prompt_is_compact_and_includes_tool_schema() -> None:
    prompt = build_explicit_tool_execution_prompt(
        tool={
            "name": "openmeteo_weather",
            "description": "Get weather forecast for a city.",
            "capability_class": "weather",
            "result_mode": "tool_only_ok",
            "parameters_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or place name"},
                    "target_date": {"type": "string", "description": "Target date in YYYY-MM-DD"},
                },
                "required": ["location"],
            },
        },
        now_local=datetime(2026, 4, 11, 9, 30, 0),
    )

    assert "Allowed tool:" in prompt
    assert "- name: openmeteo_weather" in prompt
    assert "- location (string, required): City or place name" in prompt
    assert "- target_date (string, optional): Target date in YYYY-MM-DD" in prompt
    assert "Do not answer from memory." in prompt


def test_build_explicit_tool_execution_prompt_hides_intermediate_tool_calls() -> None:
    prompt = build_explicit_tool_execution_prompt(
        tool={
            "name": "smartcmp_list_components",
            "description": "Get component type info for a service.",
            "capability_class": "provider:smartcmp",
            "result_mode": "silent_ok",
            "parameters_schema": {
                "type": "object",
                "properties": {
                    "source_key": {"type": "string", "description": "Service source key"},
                },
                "required": ["source_key"],
            },
        },
        now_local=datetime(2026, 4, 11, 9, 30, 0),
    )

    assert "Do not mention the tool call to the user" in prompt
    assert "next user-facing question or confirmation" in prompt
    assert "Phrase that next user-facing step naturally" in prompt
    assert "Never quote scaffolding phrases such as 'Found N ...'" in prompt
    assert "unlabeled UUID/ID dumps" in prompt
    assert "Do not call the same tool again with the same arguments" in prompt
    assert "backend" not in prompt.lower()


def test_build_explicit_tool_execution_prompt_ignores_resolved_workflow_arguments() -> None:
    prompt = build_explicit_tool_execution_prompt(
        tool={
            "name": "smartcmp_list_components",
            "description": "Get component type info for a service.",
            "capability_class": "provider:smartcmp",
            "result_mode": "silent_ok",
            "parameters_schema": {
                "type": "object",
                "properties": {
                    "source_key": {"type": "string", "description": "Service source key"},
                },
                "required": ["source_key"],
            },
            "resolved_arguments": {"source_key": "resource.iaas.machine.instance.abstract"},
        },
        now_local=datetime(2026, 4, 11, 9, 30, 0),
    )

    assert "Resolved workflow arguments:" not in prompt
    assert '"source_key": "resource.iaas.machine.instance.abstract"' not in prompt
    assert "using those exact values" not in prompt


def test_no_tools_prompt_policy_forbids_external_system_success_claims() -> None:
    prompt = prompt_sections.build_tool_policy(
        {"mode": "llm_first", "preferred_tools": []}
    )

    assert "No tools are available in this turn." in prompt
    assert "no provider, skill, or tool is available" in prompt
    assert "Never present unavailable external-system state" in prompt
    assert "Do not turn missing capability into an external-system fact" in prompt
    assert "Treat requests to create, submit, file, apply for" in prompt
    assert "do not continue by gathering workflow details" in prompt
    assert "records are absent" in prompt
    assert "results are empty" in prompt
    assert "logs, timestamps, statuses" in prompt


def test_direct_answer_recovery_forbids_external_system_success_claims() -> None:
    payload = build_direct_answer_recovery_payload(
        user_message="申请 Linux VM",
        invalid_output="<tool_call name='create_vm' />",
    )

    assert "No tools are available in this turn." in payload["system_prompt"]
    assert "no provider, skill, or tool is available" in payload["system_prompt"]
    assert "Never present unavailable external-system state" in payload["system_prompt"]
    assert "Do not turn missing capability into an external-system fact" in payload["system_prompt"]
    assert "records are absent" in payload["system_prompt"]
    assert "results are empty" in payload["system_prompt"]
