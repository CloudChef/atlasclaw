# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""


Provider instance and tool

for toolfor LLM and Provider instance:
- list_provider_instances:Provider instance()
- select_provider_instance:instance inject configuration into deps.extra
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


PROVIDER_INSTANCE_SELECTIONS_KEY = "provider_instance_selections"
_SESSION_PROVIDER_INSTANCE_SELECTIONS: dict[str, dict[str, str]] = {}


@dataclass(frozen=True)
class ProviderInstanceSelectionResolution:
    """Outcome from applying request-scoped provider instance selection policy."""

    resolved: bool
    provider_type: str = ""
    instance_name: str = ""
    error_text: str = ""


def _deps_run_key(deps: "SkillDeps") -> str:
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict):
        return ""
    return str(extra.get("run_id", "") or "").strip()


def record_provider_instance_selection(
    deps: "SkillDeps",
    provider_type: str,
    instance_name: str,
) -> None:
    """Record a just-selected provider instance for other tool calls in this run."""
    run_key = _deps_run_key(deps)
    normalized_provider_type = str(provider_type or "").strip()
    normalized_instance_name = str(instance_name or "").strip()
    if not run_key or not normalized_provider_type or not normalized_instance_name:
        return
    selections = _SESSION_PROVIDER_INSTANCE_SELECTIONS.setdefault(run_key, {})
    selections[normalized_provider_type] = normalized_instance_name


def get_recorded_provider_instance_selection(
    deps: "SkillDeps",
    provider_type: str,
) -> str:
    """Return a provider instance selection recorded for the current run."""
    run_key = _deps_run_key(deps)
    normalized_provider_type = str(provider_type or "").strip()
    if not run_key or not normalized_provider_type:
        return ""
    selections = _SESSION_PROVIDER_INSTANCE_SELECTIONS.get(run_key, {})
    return str(selections.get(normalized_provider_type, "") or "").strip()


def clear_recorded_provider_instance_selections(deps: "SkillDeps") -> None:
    """Clear transient provider selections for the current run."""
    run_key = _deps_run_key(deps)
    if run_key:
        _SESSION_PROVIDER_INSTANCE_SELECTIONS.pop(run_key, None)


def provider_instance_usage_hint(instance_config: Any) -> str:
    """Return the public LLM-facing usage hint for one provider instance."""
    if not isinstance(instance_config, dict):
        return ""
    return str(instance_config.get("usage_hint", "") or "").strip()


def format_provider_instance_choices(
    provider_type: str,
    instances: dict[str, dict[str, Any]],
) -> str:
    """Build a safe candidate list for provider instance clarification."""
    lines = [f"Provider '{provider_type}' has {len(instances)} instances:"]
    for instance_name in sorted(instances.keys()):
        instance_config = instances.get(instance_name)
        hint = provider_instance_usage_hint(instance_config)
        suffix = f" — {hint}" if hint else ""
        lines.append(f"  - {instance_name}{suffix}")
    return "\n".join(lines)


def _normalize_instance_configs(instances: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(instances, dict):
        return {}
    return {
        str(instance_name): dict(instance_config)
        for instance_name, instance_config in instances.items()
        if str(instance_name or "").strip() and isinstance(instance_config, dict)
    }


def _apply_provider_instance_selection(
    *,
    extra: dict[str, Any],
    provider_type: str,
    instance_name: str,
    instance_config: dict[str, Any],
) -> ProviderInstanceSelectionResolution:
    extra["provider_type"] = provider_type
    extra["provider_instance_name"] = instance_name
    extra["provider_instance"] = dict(instance_config)
    return ProviderInstanceSelectionResolution(
        resolved=True,
        provider_type=provider_type,
        instance_name=instance_name,
    )


def resolve_provider_instance_selection(
    *,
    provider_type: str,
    instances: Any,
    extra: dict[str, Any],
    deps: "SkillDeps" | None = None,
) -> ProviderInstanceSelectionResolution:
    """Resolve and apply the provider instance selection for one provider type.

    The policy is shared by provider skill wrappers and Markdown provider tools:
    use an explicit selected instance first, then a session-sticky selection, then
    a same-run recorded selection, then auto-select only when one visible instance
    exists. Multiple visible instances without a selection fail closed with safe
    candidate names and usage hints.
    """
    target_provider = str(provider_type or "").strip()
    visible_instances = _normalize_instance_configs(instances)
    if not target_provider or not visible_instances:
        return ProviderInstanceSelectionResolution(
            resolved=True,
            provider_type=target_provider,
        )

    selected_type = str(extra.get("provider_type", "") or "").strip()
    selected_name = str(extra.get("provider_instance_name", "") or "").strip()
    if selected_type == target_provider and selected_name in visible_instances:
        return _apply_provider_instance_selection(
            extra=extra,
            provider_type=target_provider,
            instance_name=selected_name,
            instance_config=visible_instances[selected_name],
        )

    sticky_selections = extra.get(PROVIDER_INSTANCE_SELECTIONS_KEY)
    sticky_name = ""
    if isinstance(sticky_selections, dict):
        sticky_name = str(sticky_selections.get(target_provider, "") or "").strip()
    if sticky_name in visible_instances:
        return _apply_provider_instance_selection(
            extra=extra,
            provider_type=target_provider,
            instance_name=sticky_name,
            instance_config=visible_instances[sticky_name],
        )

    recorded_name = (
        get_recorded_provider_instance_selection(deps, target_provider)
        if deps is not None
        else ""
    )
    if recorded_name in visible_instances:
        selection = _apply_provider_instance_selection(
            extra=extra,
            provider_type=target_provider,
            instance_name=recorded_name,
            instance_config=visible_instances[recorded_name],
        )
        selections = extra.setdefault(PROVIDER_INSTANCE_SELECTIONS_KEY, {})
        if isinstance(selections, dict):
            selections[target_provider] = recorded_name
        return selection

    if len(visible_instances) == 1:
        instance_name, instance_config = next(iter(visible_instances.items()))
        return _apply_provider_instance_selection(
            extra=extra,
            provider_type=target_provider,
            instance_name=instance_name,
            instance_config=instance_config,
        )

    return ProviderInstanceSelectionResolution(
        resolved=False,
        provider_type=target_provider,
        error_text=(
            format_provider_instance_choices(target_provider, visible_instances)
            + "\nCall select_provider_instance before invoking this provider tool. "
            "If the usage hints do not clearly identify the user's target, "
            "ask the user which instance to use."
        ),
    )


async def persist_provider_instance_selection(
    deps: "SkillDeps",
    provider_type: str,
    instance_name: str,
) -> None:
    """Persist the selected provider instance into session metadata when possible."""
    session_manager = getattr(deps, "session_manager", None)
    session_key = str(getattr(deps, "session_key", "") or "").strip()
    if session_manager is None or not session_key:
        return

    update_extra = getattr(session_manager, "update_extra", None)
    if not callable(update_extra):
        return

    current = {}
    extra = getattr(deps, "extra", None)
    if isinstance(extra, dict) and isinstance(
        extra.get(PROVIDER_INSTANCE_SELECTIONS_KEY),
        dict,
    ):
        current = dict(extra[PROVIDER_INSTANCE_SELECTIONS_KEY])
    current[str(provider_type)] = str(instance_name)
    await update_extra(
        session_key,
        {PROVIDER_INSTANCE_SELECTIONS_KEY: current},
    )


async def list_provider_instances_tool(ctx: "RunContext[SkillDeps]", provider_type: str) -> dict:
    """


Provider type availableinstance

 return instance name and(without token/password etc.).

 Args:
 ctx:RunContext[SkillDeps]
 provider_type:Provider type(such as "jira")

 Returns:
 `ToolResult`-formatted dictionary
 
"""
    extra = ctx.deps.extra if hasattr(ctx, "deps") and hasattr(ctx.deps, "extra") else {}
    available = extra.get("available_providers", {})

    instance_names = available.get(provider_type, [])
    if not instance_names:
        return ToolResult.error(
            f"Provider '{provider_type}' instance not found. "
            f"Available Providers: {', '.join(available.keys()) or 'None'}"
        ).to_dict()

    # get instance
    sp_registry = extra.get("_service_provider_registry")
    instances_info = []
    for name in instance_names:
        info: dict[str, Any] = {"name": name}
        if sp_registry is not None:
            redacted = sp_registry.get_instance_config_redacted(provider_type, name)
            if redacted:
                info["params"] = redacted
                hint = provider_instance_usage_hint(redacted)
                if hint:
                    info["usage_hint"] = hint
        instances_info.append(info)

    text_lines = [f"Provider '{provider_type}' has {len(instances_info)} instance(s):"]
    for inst in instances_info:
        params_str = ""
        if "params" in inst:
            safe_params = {k: v for k, v in inst["params"].items() if v != "***"}
            if safe_params:
                params_str = " — " + ", ".join(f"{k}={v}" for k, v in safe_params.items())
        hint = str(inst.get("usage_hint", "") or "").strip()
        hint_str = (
            f" — usage_hint={hint}"
            if hint and "usage_hint=" not in params_str
            else ""
        )
        text_lines.append(f"  - {inst['name']}{params_str}{hint_str}")

    return ToolResult.text("\n".join(text_lines), details={"instances": instances_info}).to_dict()


async def select_provider_instance_tool(
    ctx: "RunContext[SkillDeps]",
    provider_type: str,
    instance_name: str,
) -> dict:
    """


Provider instance

 convert in instance configurationparameter(${ENV} parse) ctx.deps.extra,
 for Provider Skill.

 Args:
 ctx:RunContext[SkillDeps]
 provider_type:Provider type(such as "jira")
 instance_name:instancename(such as "prod")

 Returns:
 `ToolResult`-formatted dictionary
 
"""
    extra = ctx.deps.extra if hasattr(ctx, "deps") and hasattr(ctx.deps, "extra") else {}
    sp_registry = extra.get("_service_provider_registry")

    if sp_registry is None:
        return ToolResult.error("ServiceProviderRegistry not initialized").to_dict()

    config = sp_registry.get_instance_config(provider_type, instance_name)
    if config is None:
        available = sp_registry.list_instances(provider_type)
        return ToolResult.error(
            f"Provider '{provider_type}' instance '{instance_name}' not found. "
            f"Available instances: {', '.join(available) or 'None'}"
        ).to_dict()

    # inject parameterto deps.extra
    extra["provider_type"] = provider_type
    extra["provider_instance_name"] = instance_name
    extra["provider_instance"] = config
    selections = extra.setdefault(PROVIDER_INSTANCE_SELECTIONS_KEY, {})
    if isinstance(selections, dict):
        selections[provider_type] = instance_name
    record_provider_instance_selection(ctx.deps, provider_type, instance_name)
    await persist_provider_instance_selection(ctx.deps, provider_type, instance_name)

    # return
    redacted = sp_registry.get_instance_config_redacted(provider_type, instance_name)
    return ToolResult.text(
        f"Selected {provider_type} instance '{instance_name}'",
        details={
            "provider_type": provider_type,
            "instance_name": instance_name,
            "params_redacted": redacted,
        },
    ).to_dict()
