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
        lines.append(f"- **{name}**: {description}")
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
    """Build the markdown skills index section with provider grouping and context."""
    if not md_skills:
        return ""

    max_count = config.md_skills_max_count
    desc_max = config.md_skills_desc_max_chars
    budget = config.md_skills_max_index_chars
    home_prefix = str(Path.home())
    provider_contexts = provider_contexts or {}

    instructions = (
        "When a user's task matches a skill description below:\n"
        "1. Check whether an executable tool is already registered for the matched skill\n"
        "2. Prefer approved provider, memory, web, UI, and session tools for execution\n"
        "3. If no executable tool exists, rely on the skill metadata to decide whether to continue or ask for more input\n\n"
        "SKILL SELECTION GUIDANCE:\n"
        "- Check 'use_when' conditions to confirm the skill applies\n"
        "- Check 'avoid_when' conditions to ensure you're using the right skill\n"
        "- Use 'triggers' keywords to match user intent"
    )
    header = f"## MD Skills\n\n{instructions}\n\n"

    provider_skills: dict[str, list[dict]] = {}
    standalone_skills: list[dict] = []
    for skill in md_skills[:max_count]:
        provider = skill.get("provider", "")
        if provider:
            provider_skills.setdefault(provider, []).append(skill)
        else:
            standalone_skills.append(skill)

    def format_skill(skill: dict) -> str:
        name = skill.get("qualified_name") or skill.get("name", "unknown")
        desc = skill.get("description", "")
        file_path = skill.get("file_path", "")
        metadata = skill.get("metadata", {})

        if len(desc) > desc_max:
            desc = desc[: desc_max - 3] + "..."
        if home_prefix and file_path.startswith(home_prefix):
            file_path = "~" + file_path[len(home_prefix) :]

        lines = [
            "    <skill>",
            f"      <name>{name}</name>",
            f"      <description>{desc}</description>",
            f"      <location>{file_path}</location>",
        ]

        triggers = metadata.get("triggers", [])
        if triggers and isinstance(triggers, list):
            lines.append(f"      <triggers>{', '.join(triggers)}</triggers>")

        use_when = metadata.get("use_when", [])
        if use_when and isinstance(use_when, list):
            lines.append("      <use_when>")
            for condition in use_when[:3]:
                lines.append(f"        - {condition}")
            lines.append("      </use_when>")

        avoid_when = metadata.get("avoid_when", [])
        if avoid_when and isinstance(avoid_when, list):
            lines.append("      <avoid_when>")
            for condition in avoid_when[:3]:
                lines.append(f"        - {condition}")
            lines.append("      </avoid_when>")

        examples = metadata.get("examples", [])
        if examples and isinstance(examples, list):
            lines.append("      <examples>")
            for example in examples[:2]:
                lines.append(f"        - {example}")
            lines.append("      </examples>")

        lines.append("    </skill>")
        return "\n".join(lines) + "\n"

    def format_provider_context(provider_type: str, ctx: dict) -> str:
        lines = [f"  <provider type=\"{provider_type}\">"]
        display_name = ctx.get("display_name", provider_type)
        if display_name:
            lines.append(f"    <display_name>{display_name}</display_name>")

        description = ctx.get("description", "")
        if description:
            if len(description) > 200:
                description = description[:197] + "..."
            lines.append(f"    <description>{description}</description>")

        keywords = ctx.get("keywords", [])
        if keywords and isinstance(keywords, list):
            lines.append(f"    <keywords>{', '.join(keywords[:10])}</keywords>")

        capabilities = ctx.get("capabilities", [])
        if capabilities and isinstance(capabilities, list):
            lines.append("    <capabilities>")
            for capability in capabilities[:5]:
                lines.append(f"      - {capability}")
            lines.append("    </capabilities>")

        use_when = ctx.get("use_when", [])
        if use_when and isinstance(use_when, list):
            lines.append("    <use_when>")
            for condition in use_when[:3]:
                lines.append(f"      - {condition}")
            lines.append("    </use_when>")

        avoid_when = ctx.get("avoid_when", [])
        if avoid_when and isinstance(avoid_when, list):
            lines.append("    <avoid_when>")
            for condition in avoid_when[:3]:
                lines.append(f"      - {condition}")
            lines.append("    </avoid_when>")

        lines.append("    <skills>")
        return "\n".join(lines) + "\n"

    accumulated = header + "<available_skills>\n"
    shown = 0
    total_count = len(md_skills)

    for provider_type, skills_list in sorted(provider_skills.items()):
        ctx = provider_contexts.get(provider_type, {})
        provider_header = format_provider_context(provider_type, ctx)
        if len(accumulated) + len(provider_header) > budget:
            break
        accumulated += provider_header
        for skill in skills_list:
            entry = format_skill(skill)
            if len(accumulated) + len(entry) + 50 > budget:
                break
            accumulated += entry
            shown += 1
        accumulated += "    </skills>\n  </provider>\n"

    if standalone_skills:
        accumulated += "  <standalone_skills>\n"
        for skill in standalone_skills:
            entry = format_skill(skill)
            if len(accumulated) + len(entry) + 50 > budget:
                break
            accumulated += entry
            shown += 1
        accumulated += "  </standalone_skills>\n"

    if shown < total_count:
        accumulated += f"  <!-- Showing {shown} of {total_count} skills -->\n"

    accumulated += "</available_skills>"
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

To understand Xuanwu's behavior, commands, configuration, or architecture, please refer to the local documentation first."""


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


def build_heartbeats() -> str:
    """Build heartbeat section (currently optional/no-op)."""
    return ""


def build_runtime_info() -> str:
    """Build runtime environment info section."""
    return f"""## Runtime

Host: {platform.node()}
OS: {platform.system()} {platform.release()}
Python: {platform.python_version()}
Framework: Xuanwu v0.1.0"""
