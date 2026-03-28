# -*- coding: utf-8 -*-
"""Prompt-context helpers for AgentRunner."""

from __future__ import annotations

from typing import Any, Optional


def build_system_prompt(prompt_builder, session: Any, deps, *, agent: Optional[Any] = None) -> str:
    """Build the runtime system prompt for the current session."""
    return prompt_builder.build(
        session=session,
        skills=collect_skills_snapshot(deps),
        tools=collect_tools_snapshot(agent=agent),
        md_skills=collect_md_skills_snapshot(deps),
        target_md_skill=collect_target_md_skill(deps),
        user_info=deps.user_info,
        provider_contexts=collect_provider_contexts(deps),
    )


def collect_skills_snapshot(deps) -> list[dict]:
    """Read a structured skills snapshot from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    for key in ("skills_snapshot", "skills"):
        value = extra.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def collect_md_skills_snapshot(deps) -> list[dict]:
    """Read a Markdown-skill snapshot from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    for key in ("md_skills_snapshot", "md_skills"):
        value = extra.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def collect_target_md_skill(deps) -> Optional[dict]:
    """Read a targeted markdown-skill descriptor from `deps.extra` if present."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    value = extra.get("target_md_skill")
    return value if isinstance(value, dict) else None


def collect_provider_contexts(deps) -> dict[str, dict]:
    """Collect provider LLM contexts from ServiceProviderRegistry."""
    extra = deps.extra if isinstance(deps.extra, dict) else {}
    registry = extra.get("_service_provider_registry")
    if registry is None:
        return {}

    get_contexts = getattr(registry, "get_all_provider_contexts", None)
    if get_contexts is None:
        return {}

    try:
        contexts = get_contexts()
        result = {}
        for provider_type, ctx in contexts.items():
            if hasattr(ctx, "__dict__"):
                result[provider_type] = {
                    "display_name": getattr(ctx, "display_name", ""),
                    "description": getattr(ctx, "description", ""),
                    "keywords": getattr(ctx, "keywords", []),
                    "capabilities": getattr(ctx, "capabilities", []),
                    "use_when": getattr(ctx, "use_when", []),
                    "avoid_when": getattr(ctx, "avoid_when", []),
                }
            elif isinstance(ctx, dict):
                result[provider_type] = ctx
        return result
    except Exception:
        return {}


def collect_tools_snapshot(*, agent: Any) -> list[dict]:
    """Collect tool name and description pairs for prompt building."""
    raw_tools = getattr(agent, "tools", None)
    if not raw_tools:
        return []

    tools: list[dict] = []
    for tool in raw_tools:
        if isinstance(tool, dict):
            name = tool.get("name")
            description = tool.get("description", "")
        else:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            description = getattr(tool, "description", "") or getattr(tool, "__doc__", "") or ""
        if name:
            tools.append({"name": str(name), "description": str(description).strip()})
    return tools
