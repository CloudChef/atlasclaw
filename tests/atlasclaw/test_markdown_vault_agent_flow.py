# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.selected_capability import SELECTED_CAPABILITY_KEY
from app.atlasclaw.agent.runner_prompt_context import collect_capability_index_snapshot
from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.agent.tool_gate_models import ToolIntentAction
from app.atlasclaw.api.agent_capabilities import (
    build_agent_capabilities,
    resolve_auto_selected_capability,
)
from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


SOURCE_PROVIDER_ROOT = (
    Path(__file__).resolve().parents[3]
    / "atlasclaw-providers"
    / "providers"
    / "markdown-vault"
)


class _SelectorRunner(RunnerToolGateModelMixin):
    pass


class _SelectorAgent:
    async def run(self, user_message, *, deps):
        _ = user_message, deps
        return SimpleNamespace(
            output=json.dumps(
                {
                    "action": "use_tools",
                    "targets": ["skill:markdown-vault:markdown-vault-query"],
                    "reason": "The user asked to answer from the configured knowledge base.",
                }
            )
        )


def _copy_provider(tmp_path: Path) -> Path:
    if not SOURCE_PROVIDER_ROOT.is_dir():
        pytest.skip(f"markdown-vault provider not found: {SOURCE_PROVIDER_ROOT}")

    providers_root = tmp_path / "providers"
    provider_root = providers_root / "markdown-vault"
    shutil.copytree(
        SOURCE_PROVIDER_ROOT,
        provider_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return provider_root


def _refresh_index(*, provider_root: Path, config_path: Path) -> dict:
    script = provider_root / "skills" / "markdown-vault-query" / "scripts" / "manage_index.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "refresh",
            "--config",
            str(config_path),
            "--instance",
            "default",
        ],
        cwd=script.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _call_tool(registry: SkillRegistry, deps, tool_name: str, **kwargs) -> dict:
    entry = registry.get(tool_name)
    assert entry is not None, f"tool not registered: {tool_name}"
    _metadata, handler = entry
    result = asyncio.run(handler(SimpleNamespace(deps=deps), **kwargs))
    assert result["success"] is True, result
    return json.loads(result["output"])


def test_markdown_vault_provider_is_available_to_agent_flow(tmp_path: Path) -> None:
    provider_root = _copy_provider(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    index_path = tmp_path / "markdown-vault.sqlite3"
    (vault / "smartcmp-runbook.md").write_text(
        "\n".join(
            [
                "# SmartCMP Request Runbook",
                "",
                "Use the blue business group for SmartCMP virtual machine requests.",
                "The approval SLA is four hours for normal requests.",
                "Related note: [[request-graph]].",
            ]
        ),
        encoding="utf-8",
    )
    (vault / "request-graph.md").write_text(
        "\n".join(
            [
                "# Request Graph",
                "",
                "SmartCMP request knowledge links back to [[smartcmp-runbook]].",
            ]
        ),
        encoding="utf-8",
    )

    raw_instance_config = {
        "vault_path": str(vault),
        "index_backend": "sqlite",
        "index_path": str(index_path),
        "max_chunk_chars": 400,
    }
    config_path = tmp_path / "atlasclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "service_providers": {
                    "markdown-vault": {"default": raw_instance_config},
                }
            }
        ),
        encoding="utf-8",
    )

    refresh_payload = _refresh_index(provider_root=provider_root, config_path=config_path)
    assert refresh_payload["success"] is True
    assert refresh_payload["indexed_documents"] == 2
    assert refresh_payload["status"]["stale"] is False

    provider_registry = ServiceProviderRegistry()
    provider_registry.load_from_directory(provider_root.parent)
    provider_registry.load_instances_from_config(
        {"markdown-vault": {"default": raw_instance_config}}
    )

    skill_registry = SkillRegistry()
    loaded = skill_registry.load_from_directory(
        str(provider_root / "skills"),
        location="provider",
        provider="markdown-vault",
    )
    assert loaded == 1
    assert {
        "markdown_vault_search",
        "markdown_vault_get",
    }.issubset(set(skill_registry.list_skills()))

    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path / ".atlasclaw")),
        session_queue=SessionQueue(),
        skill_registry=skill_registry,
        service_provider_registry=provider_registry,
        provider_instances=provider_registry.get_all_instance_configs(),
    )
    capabilities = build_agent_capabilities(
        ctx=ctx,
        provider_instances=provider_registry.get_all_instance_configs(),
    )
    commands = {item["command"]: item for item in capabilities["capabilities"]}
    selected = commands["/default.markdown-vault-query"]
    assert selected["provider_type"] == "markdown-vault"
    assert selected["instance_name"] == "default"
    assert set(selected["target_tool_names"]) == {
        "markdown_vault_get",
        "markdown_vault_search",
    }
    auto_selected = resolve_auto_selected_capability(
        ctx=ctx,
        message="Based on the knowledge base, what are the virtual machine request steps?",
        provider_instances=provider_registry.get_all_instance_configs(),
    )
    assert auto_selected is None
    ordinary_chat = resolve_auto_selected_capability(
        ctx=ctx,
        message="Hello",
        provider_instances=provider_registry.get_all_instance_configs(),
    )
    assert ordinary_chat is None

    session_key = SessionKey(
        agent_id="main",
        user_id="alice",
        channel="web",
        chat_type=ChatType.DM,
        peer_id="alice",
        thread_id="kb",
    ).to_string(SessionScope.PER_CHANNEL_PEER)
    deps = build_scoped_deps(
        ctx,
        UserInfo(user_id="alice", display_name="Alice"),
        session_key,
        provider_config=provider_registry.get_all_instance_configs(),
        extra={"context": {SELECTED_CAPABILITY_KEY: selected}},
    )

    assert deps.extra["provider_type"] == "markdown-vault"
    assert deps.extra["provider_instance_name"] == "default"
    assert deps.extra["provider_instance"]["auth_type"] == "app_credentials"
    assert deps.extra["provider_instance"]["vault_path"] == str(vault)

    capability_index = collect_capability_index_snapshot(agent=SimpleNamespace(tools=[]), deps=deps)
    markdown_capability = [
        item
        for item in capability_index
        if item.get("capability_id") == "skill:markdown-vault:markdown-vault-query"
    ]
    assert markdown_capability
    manifest = json.loads(provider_root.joinpath("provider.schema.json").read_text(encoding="utf-8"))
    assert "agent_skill_overlays" not in manifest

    selector_plan = asyncio.run(
        _SelectorRunner()._select_capability_intent_plan_with_model(
            agent=_SelectorAgent(),
            deps=deps,
            user_message="\u57fa\u4e8e\u77e5\u8bc6\u5e93\uff0c"
            "\u7533\u8bf7\u865a\u62df\u673a\u9700\u8981\u54ea\u4e9b\u6b65\u9aa4\uff1f",
            recent_history=[],
            capability_index=capability_index,
        )
    )
    assert selector_plan is not None
    assert selector_plan.action is ToolIntentAction.USE_TOOLS
    assert selector_plan.target_skill_names == ["markdown-vault:markdown-vault-query"]

    search_payload = _call_tool(
        skill_registry,
        deps,
        "markdown_vault_search",
        query="SmartCMP virtual machine approval SLA",
        limit=3,
    )
    assert search_payload["success"] is True
    assert search_payload["stale"] is False
    assert search_payload["results"][0]["path"] == "smartcmp-runbook.md"
    assert "four hours" in search_payload["results"][0]["snippet"]

    get_payload = _call_tool(
        skill_registry,
        deps,
        "markdown_vault_get",
        path="smartcmp-runbook.md",
        start_line=1,
        end_line=5,
    )
    assert get_payload["success"] is True
    assert "blue business group" in get_payload["text"]
    assert get_payload["path"] == "smartcmp-runbook.md"

    answer = (
        "SmartCMP VM requests should use the blue business group, and normal "
        "request approval SLA is four hours "
        f"({get_payload['path']}:{get_payload['start_line']}-{get_payload['end_line']})."
    )
    assert "blue business group" in answer
    assert "four hours" in answer
    assert "smartcmp-runbook.md" in answer
