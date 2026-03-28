"""


Provider instance and tool

for toolfor LLM and Provider instance:
- list_provider_instances:Provider instance()
- select_provider_instance:instance inject configuration into deps.extra
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


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
        instances_info.append(info)

    text_lines = [f"Provider '{provider_type}' has {len(instances_info)} instance(s):"]
    for inst in instances_info:
        params_str = ""
        if "params" in inst:
            safe_params = {k: v for k, v in inst["params"].items() if v != "***"}
            if safe_params:
                params_str = " — " + ", ".join(f"{k}={v}" for k, v in safe_params.items())
        text_lines.append(f"  - {inst['name']}{params_str}")

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

    # return
    redacted = sp_registry.get_instance_config_redacted(provider_type, instance_name)
    return ToolResult.text(
        f"Selected {provider_type} instance '{instance_name}'",
        details={"provider_type": provider_type, "instance_name": instance_name, "params_redacted": redacted},
    ).to_dict()
