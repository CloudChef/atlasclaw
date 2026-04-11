# -*- coding: utf-8 -*-
"""Reusable section renderers for PromptBuilder."""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Optional


def build_target_md_skill(target_md_skill: dict[str, str]) -> str:
    """Build a focused section for webhook-directed markdown skill execution."""
    qualified_name = target_md_skill.get("qualified_name", "")
    file_path = target_md_skill.get("file_path", "")
    provider = target_md_skill.get("provider", "")
    lines = ["## Target Markdown Skill", ""]
    if qualified_name:
        lines.append(f"Qualified name: {qualified_name}")
    if provider:
        lines.append(f"Provider: {provider}")
    if file_path:
        lines.append(f"File path: {file_path}")
    lines.append("You must execute only this markdown skill for the current run.")
    lines.append("Prefer any executable tool already registered for this skill.")
    return "\n".join(lines)


def build_user_context(user_info) -> str:
    """Build a user identity section for the current authenticated operator."""
    lines = ["## Current User", ""]
    if user_info.display_name:
        lines.append(f"Name: {user_info.display_name}")
    lines.append(f"User ID: {user_info.user_id}")
    if user_info.tenant_id and user_info.tenant_id != "default":
        lines.append(f"Tenant: {user_info.tenant_id}")
    if user_info.roles:
        lines.append(f"Roles: {', '.join(user_info.roles)}")
    return "\n".join(lines)


def build_identity(config) -> str:
    """Build the identity section."""
    return f"""## Identity

You are {config.agent_name}, {config.agent_description}.

Your core capabilities include:
- Handling complex multi-turn conversations with context continuity
- Invoking various business skills (cloud resource management, ITSM, ticket processing, etc.)
- Managing long-term memory with semantic retrieval
- Supporting multi-step workflows and task collaboration"""


def build_tooling(tools: list[dict]) -> str:
    """Build the tool listing section."""
    lines = ["## Tools", ""]
    lines.append("You can use the following tools to complete tasks:")
    lines.append("")
    for tool in tools:
        name = tool.get("name", "unknown")
        description = tool.get("description", "")
        signature = _format_tool_signature(tool)
        lines.append(f"- **{signature}**: {description}")
    return "\n".join(lines)


def build_tool_policy(tool_policy: Optional[dict]) -> str:
    """Build explicit tool-policy guidance for the current turn."""
    if not isinstance(tool_policy, dict):
        return ""

    mode = str(tool_policy.get("mode", "") or "").strip()
    reason = str(tool_policy.get("reason", "") or "").strip()
    preferred_tools = tool_policy.get("preferred_tools", tool_policy.get("required_tools", []))
    execution_hint = str(tool_policy.get("execution_hint", "") or "").strip().lower()
    retry_count = int(tool_policy.get("retry_count", 0) or 0)
    retry_missing_tools = tool_policy.get("retry_missing_tools", [])
    max_same_tool_calls_per_turn = int(tool_policy.get("max_same_tool_calls_per_turn", 0) or 0)
    target_provider_types = tool_policy.get("target_provider_types", [])
    target_skill_names = tool_policy.get("target_skill_names", [])
    target_group_ids = tool_policy.get("target_group_ids", [])
    target_capability_classes = tool_policy.get("target_capability_classes", [])
    if not mode:
        return ""

    lines = ["## Tool Policy", ""]
    lines.append(f"Turn mode: {mode}")
    if reason:
        lines.append(f"Reason: {reason}")
    if isinstance(preferred_tools, list) and preferred_tools:
        lines.append(f"Preferred tools: {', '.join(str(item) for item in preferred_tools)}")
    if isinstance(target_provider_types, list) and target_provider_types:
        lines.append(f"Target providers: {', '.join(str(item) for item in target_provider_types)}")
    if isinstance(target_skill_names, list) and target_skill_names:
        lines.append(f"Target skills: {', '.join(str(item) for item in target_skill_names)}")
    if isinstance(target_group_ids, list) and target_group_ids:
        lines.append(f"Target groups: {', '.join(str(item) for item in target_group_ids)}")
    if isinstance(target_capability_classes, list) and target_capability_classes:
        lines.append(
            f"Target capabilities: {', '.join(str(item) for item in target_capability_classes)}"
        )
    if retry_count > 0:
        lines.append(f"Retry attempt: {retry_count}")
        if isinstance(retry_missing_tools, list) and retry_missing_tools:
            lines.append(
                "Previously missing tool executions: "
                + ", ".join(str(item) for item in retry_missing_tools)
            )
    if max_same_tool_calls_per_turn > 0:
        lines.append(
            "Maximum repeated calls for the same tool in this turn: "
            f"{max_same_tool_calls_per_turn}"
        )
    lines.extend(
        [
            "",
            "You must not claim any search, verification, lookup, or provider query happened unless tool execution evidence exists in this run.",
        ]
    )
    if mode == "use_tools":
        lines.append("This turn requires real tool execution before a final answer.")
        lines.append(
            "Do exactly one of the following: issue a real tool call from the preferred tool set, or ask a focused clarification question if inputs are missing."
        )
        lines.append("Do not provide narrative analysis or pretend tool results before the first real tool call.")
        if execution_hint == "provider_tool_first":
            lines.append("Prefer provider/skill tools before generic web or fallback tools.")
        lines.append(
            "After tool results arrive, continue the same loop and answer strictly from those results."
        )
        if max_same_tool_calls_per_turn > 0:
            lines.append(
                "Do not call the same tool repeatedly without clear new narrowing input. "
                "If the same tool has already been used enough times in this turn, answer from the "
                "current tool evidence or ask for clarification."
            )
    elif mode == "ask_clarification":
        lines.append("Ask one focused clarification question and wait for the user response.")
        lines.append("Do not call unrelated tools and do not fabricate missing inputs.")
    else:
        lines.append("You may answer directly when the request is stable and does not require tool execution.")
    return "\n".join(lines)


def build_safety() -> str:
    """Build the safety section."""
    return """## Safety

Please follow these safety guidelines:
- Avoid power-seeking behaviors or bypassing oversight
- Do not execute operations that may cause irreversible damage
- Sensitive information must be desensitized
- Respect user data privacy"""


def build_skills_listing(skills: list[dict]) -> str:
    """Build available built-in executable skill listing."""
    if not skills:
        return ""

    lines = ["## Built-in Tools (Use ONLY if no MD Skill matches)", "", "<available_skills>"]
    for skill in skills:
        name = skill.get("name", "unknown")
        description = skill.get("description", "")
        location = skill.get("location", "built-in")
        category = skill.get("category", "utility")
        lines.append(
            f"""  <skill>
    <name>{name}</name>
    <description>{description}</description>
    <category>{category}</category>
    <location>{location}</location>
  </skill>"""
        )
    lines.append("</available_skills>")
    lines.append("\nNOTE: These built-in tools are fallback options. ALWAYS check MD Skills section above first.")
    return "\n".join(lines)


def build_md_skills_index(
    config,
    md_skills: list[dict],
    provider_contexts: Optional[dict[str, dict]] = None,
) -> str:
    """Build a compact markdown-skills index for prompt-time discovery."""
    if not md_skills:
        return ""

    max_count = config.md_skills_max_count
    desc_max = config.md_skills_desc_max_chars
    budget = config.md_skills_max_index_chars
    home_prefix = str(Path.home())
    _ = provider_contexts or {}

    header_lines = [
        "## Skills",
        "",
        "Skills are listed as compact metadata only to save context tokens.",
        "When you need detailed instructions for a skill, call the `read` tool on the skill `file_path` (`SKILL.md`) before executing.",
        "Do not assume the full skill file is already loaded in context.",
        "",
        "Format: `name | description | file_path`",
        "",
    ]
    accumulated = "\n".join(header_lines)
    shown = 0
    total_count = len(md_skills)

    for skill in md_skills[:max_count]:
        name = str(skill.get("qualified_name") or skill.get("name") or "unknown").strip()
        desc = str(skill.get("description", "") or "").strip()
        file_path = str(skill.get("file_path", "") or "").strip()

        if len(desc) > desc_max:
            desc = desc[: desc_max - 3] + "..."
        if home_prefix and file_path.startswith(home_prefix):
            file_path = "~" + file_path[len(home_prefix) :]

        entry = f"- `{name}` | {desc} | `{file_path}`\n"
        if len(accumulated) + len(entry) > budget:
            break
        accumulated += entry
        shown += 1

    if shown < total_count:
        note = f"\n<!-- Showing {shown} of {total_count} skills due to budget/count limits -->"
        if len(accumulated) + len(note) <= budget:
            accumulated += note
        else:
            remaining = budget - len(accumulated)
            if remaining > 4:
                accumulated += note[: remaining - 3] + "..."
            elif remaining > 0:
                accumulated += note[:remaining]
    return accumulated


def build_self_update() -> str:
    """Build self-update section."""
    return """## Self-Update

To apply configuration changes, use the appropriate configuration commands."""


def build_workspace_info(config) -> str:
    """Build workspace section."""
    workspace = Path(config.workspace_path).expanduser()
    return f"""## Workspace

Working directory: `{workspace}`

You can read and write files in this directory."""


def build_documentation() -> str:
    """Build documentation pointers section."""
    return """## Documentation

Local documentation path: `docs/`

To understand AtlasClaw's behavior, commands, configuration, or architecture, please refer to the local documentation first."""


def build_sandbox(config) -> str:
    """Build sandbox section."""
    return f"""## Sandbox

Mode: {config.sandbox.mode}
Sandbox path: {config.sandbox.workspace_root}
Elevated execution: {"Available" if config.sandbox.elevated_exec else "Unavailable"}

In sandbox mode, some operations may be restricted."""


def build_datetime(config) -> str:
    """Build current datetime section."""
    now = datetime.now()
    tz = config.user_timezone or "System timezone"
    if config.time_format == "12":
        time_str = now.strftime("%Y-%m-%d %I:%M:%S %p")
    else:
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    return f"""## Current Time

Timezone: {tz}
Current time: {time_str}"""


def build_reply_tags() -> str:
    """Build reply-tags section (currently optional/no-op)."""
    return ""


def build_heartbeats(
    *,
    heartbeat_markdown: str = "",
    every_seconds: Optional[int] = None,
    active_hours: str = "",
    isolated_session: bool = False,
) -> str:
    """Build heartbeat guidance section when heartbeat context is available."""
    if not heartbeat_markdown.strip():
        return ""

    lines = ["## Heartbeat", ""]
    if every_seconds is not None:
        lines.append(f"Schedule: every {every_seconds} seconds")
    if active_hours:
        lines.append(f"Active hours: {active_hours}")
    lines.append(
        "Execution mode: isolated session"
        if isolated_session
        else "Execution mode: shared session"
    )
    lines.append("")
    lines.append(heartbeat_markdown.strip())
    return "\n".join(lines)


def build_runtime_info() -> str:
    """Build runtime environment info section."""
    return f"""## Runtime

Host: {platform.node()}
OS: {platform.system()} {platform.release()}
Python: {platform.python_version()}
Framework: AtlasClaw v0.1.0"""


def _format_tool_signature(tool: dict) -> str:
    """Render a compact tool signature from metadata JSON schema."""
    name = str(tool.get("name", "unknown") or "unknown").strip() or "unknown"
    parameters_schema = tool.get("parameters_schema", {})
    if not isinstance(parameters_schema, dict):
        return name
    properties = parameters_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return name
    required = {
        str(item).strip()
        for item in (parameters_schema.get("required", []) or [])
        if str(item).strip()
    }
    parts: list[str] = []
    for param_name in properties.keys():
        normalized_name = str(param_name or "").strip()
        if not normalized_name:
            continue
        suffix = "" if normalized_name in required else "?"
        parts.append(f"{normalized_name}{suffix}")
    if not parts:
        return name
    return f"{name}({', '.join(parts)})"
