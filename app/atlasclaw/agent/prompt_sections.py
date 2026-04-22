# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Reusable section renderers for PromptBuilder."""

from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import sanitize_workflow_only_text


def build_target_md_skill(target_md_skill: dict[str, Any]) -> str:
    """Build a focused section for stage-two markdown skill execution."""
    qualified_name = target_md_skill.get("qualified_name", "")
    file_path = target_md_skill.get("file_path", "")
    provider = target_md_skill.get("provider", "")
    workflow_context = target_md_skill.get("workflow_context")
    loaded_body = sanitize_workflow_only_text(
        target_md_skill.get("content", ""),
        collapse_whitespace=False,
    )
    body_truncated = bool(target_md_skill.get("content_truncated"))
    lines = ["## Target Markdown Skill", ""]
    if qualified_name:
        lines.append(f"Qualified name: {qualified_name}")
    if provider:
        lines.append(f"Provider: {provider}")
    if file_path:
        lines.append(f"File path: {file_path}")
    if loaded_body:
        lines.append("This skill body was loaded specifically for the current turn.")
    else:
        lines.append(
            "This skill was selected for the current turn. If you need its detailed instructions, "
            "read the referenced `SKILL.md` before executing."
        )
    lines.append("You must use only this markdown skill for the current run.")
    lines.append("Prefer any executable tool already registered for this skill.")
    lines.append(
        "If the workflow needs intermediate metadata lookups, treat them as internal-only steps "
        "and continue directly to the next user-facing question or confirmation after the lookup result is available."
    )
    lines.append(
        "Use only the resolved facts from those lookup results. Never repeat lookup scaffolding "
        "such as 'Found N ...', numbered raw dumps, JSON blobs, or unlabeled UUID/ID dumps as the reply."
    )
    lines.append(
        "Do not announce intermediate tool calls or expose their internal metadata as a raw "
        "user-facing reply."
    )
    if workflow_context:
        try:
            serialized_context = json.dumps(workflow_context, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            serialized_context = str(workflow_context)
        # Determine if this context is scoped to a specific request flow instance
        trace_id = workflow_context.get("internal_request_trace_id") if isinstance(workflow_context, dict) else None
        scope_note = (
            "All metadata below belongs to a single request flow instance "
            f"(trace: {trace_id}). Do not mix with data from other flow instances."
            if trace_id
            else "Use the structured metadata below only for the currently selected skill in this turn."
        )
        lines.extend(
            [
                "",
                "### Current Workflow Context",
                "",
                scope_note,
                "Interpret earlier numbered user selections against this context.",
                "Do not quote or dump this raw metadata to the user as a reply.",
                "",
                "```json",
                serialized_context,
                "```",
            ]
        )
    if loaded_body:
        lines.extend(
            [
                "",
                "### Loaded SKILL.md",
                "",
                loaded_body,
            ]
        )
        if body_truncated:
            lines.append("")
            lines.append("Note: the loaded SKILL.md content was truncated to stay within prompt limits.")
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


def build_skill_continuation_hint(hint_skill: str) -> str:
    """Build a non-binding hint about the likely active skill from transcript.

    This section is purely advisory.  The LLM decides whether to continue
    the hinted workflow based on the actual user intent.
    """
    lines = [
        "## Skill Continuation Hint",
        "",
        f"Recent transcript analysis suggests this turn may be continuing the **{hint_skill}** workflow.",
        "If the current user input is part of that workflow, continue within that skill's instructions.",
        "This is a non-binding hint — evaluate the user's actual intent before deciding.",
    ]
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
    artifact_goal = tool_policy.get("artifact_goal")
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
    if isinstance(artifact_goal, dict):
        artifact_label = str(
            artifact_goal.get("label", "") or artifact_goal.get("kind", "")
        ).strip()
        if artifact_label:
            lines.append(f"Requested artifact: {artifact_label}")
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
    elif mode == "create_artifact":
        lines.append("This turn is an artifact-generation request.")
        lines.append(
            "If this turn already exposes a matching artifact tool, you must use that tool before giving the final answer."
        )
        lines.append(
            "You may use tools when they help gather or save data, but do not stop after intermediate lookup results."
        )
        lines.append(
            "After any internal lookup result, continue with the next user-facing question or "
            "confirmation in the workflow, phrased naturally."
        )
        lines.append(
            "Never answer with raw lookup scaffolding such as 'Found N ...', numbered dumps, "
            "JSON, or unlabeled UUID/ID dumps; convert the resolved facts into the workflow response."
        )
        lines.append(
            "Either produce the requested artifact, ask one focused clarification question, or explain what blocked artifact creation."
        )
    else:
        lines.append(
            "Use the visible capabilities and conversation context to decide this turn inside the main model request."
        )
        if preferred_tools:
            lines.append(
                "You may answer directly, ask one focused clarification question, call a visible tool, or continue toward the requested artifact."
            )
            lines.append(
                "Metadata and preferred tools are hints only unless the policy above explicitly says real tool execution is required."
            )
            lines.append(
                "Only call a tool when the current request clearly matches a visible capability or when earlier tool evidence in this same run makes the next tool step obvious."
            )
            lines.append(
                "For general recommendations, brainstorming, summaries, or public knowledge questions that you can answer from existing knowledge and conversation context, answer directly instead of calling generic tools."
            )
            lines.append(
                "Do not call generic web tools just because they are visible. Use them only when the user explicitly asks to search/verify/browse, or when a visible web capability is the clearest fit for the request."
            )
        else:
            lines.append("You may answer directly when the request is stable and does not require tool execution.")
            lines.append("No tools are available in this turn.")
            lines.append(
                "Do not emit tool-call markup, XML tags, or pseudo tool invocations like "
                "`<tool_call>` or `<web_search>`."
            )
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


def build_capability_index(config, capability_index: list[dict]) -> str:
    """Build a unified compact capability index for skills, tools, and MD skills."""
    if not capability_index:
        return ""

    max_count = max(1, int(getattr(config, "capability_index_max_count", 20) or 20))
    desc_max = max(1, int(getattr(config, "capability_index_desc_max_chars", 200) or 200))
    budget = max(1, int(getattr(config, "capability_index_max_chars", 3000) or 3000))
    home_prefix = str(Path.home())

    normalized_entries: list[dict[str, Any]] = []
    for raw_entry in capability_index:
        if not isinstance(raw_entry, dict):
            continue
        kind = str(raw_entry.get("kind", "") or "").strip().lower()
        if kind not in {"md_skill", "tool", "skill"}:
            kind = "capability"
        name = str(raw_entry.get("name") or raw_entry.get("qualified_name") or "unknown").strip()
        capability_id = str(raw_entry.get("capability_id", "") or "").strip()
        description = str(raw_entry.get("description", "") or "").strip()
        locator = str(raw_entry.get("locator", "") or "").strip()
        provider_type = str(raw_entry.get("provider_type", "") or "").strip()
        artifact_types = [
            str(item).strip()
            for item in (raw_entry.get("artifact_types", []) or [])
            if str(item).strip()
        ]
        declared_tool_names = [
            str(item).strip()
            for item in (raw_entry.get("declared_tool_names", []) or [])
            if str(item).strip()
        ]
        input_hints = [
            str(item).strip()
            for item in (raw_entry.get("input_hints", []) or [])
            if str(item).strip()
        ]
        if locator.startswith(home_prefix):
            locator = "~" + locator[len(home_prefix) :]
        normalized_entries.append(
            {
                "kind": kind,
                "capability_id": capability_id or name or "unknown",
                "name": name or "unknown",
                "description": description,
                "locator": locator,
                "provider_type": provider_type,
                "artifact_types": artifact_types,
                "declared_tool_names": declared_tool_names,
                "input_hints": input_hints,
            }
        )

    if not normalized_entries:
        return ""

    total_count = len(normalized_entries)
    selected_entries = normalized_entries[:max_count]
    sections: list[tuple[str, list[dict[str, str]]]] = [
        ("Markdown Skills", [entry for entry in selected_entries if entry["kind"] == "md_skill"]),
        ("Tools", [entry for entry in selected_entries if entry["kind"] == "tool"]),
        ("Built-in Skills", [entry for entry in selected_entries if entry["kind"] == "skill"]),
    ]
    uncategorized = [entry for entry in selected_entries if entry["kind"] == "capability"]
    if uncategorized:
        sections.append(("Capabilities", uncategorized))

    rendered_lines = [
        "## Capabilities",
        "",
        "Capabilities are listed as compact metadata only to save context tokens.",
        (
            "When you need detailed instructions for a capability, use the referenced "
            "locator rather than expecting a full body in context."
        ),
        "Format: `capability_id | name | description | provider/artifact/tools/hints | locator`",
        "",
    ]

    def _fits(lines: list[str], extra: list[str]) -> bool:
        candidate = list(lines)
        candidate.extend(extra)
        return len("\n".join(candidate)) <= budget

    truncated = False
    shown_count = 0

    for heading, entries in sections:
        if not entries:
            continue
        section_lines = [f"### {heading}", ""]
        if not _fits(rendered_lines, section_lines):
            truncated = True
            break
        rendered_lines.extend(section_lines)
        for entry in entries:
            description = entry["description"]
            if len(description) > desc_max:
                description = description[: desc_max - 3] + "..."
            detail_parts: list[str] = []
            provider_type = str(entry.get("provider_type", "") or "").strip()
            if provider_type:
                detail_parts.append(f"provider:{provider_type}")
            artifact_types = [str(item).strip() for item in entry.get("artifact_types", []) or [] if str(item).strip()]
            if artifact_types:
                detail_parts.append("artifacts:" + ",".join(artifact_types[:2]))
            declared_tool_names = [
                str(item).strip()
                for item in entry.get("declared_tool_names", []) or []
                if str(item).strip()
            ]
            if declared_tool_names:
                detail_parts.append("tools:" + ",".join(declared_tool_names[:2]))
            input_hints = [
                str(item).strip()
                for item in entry.get("input_hints", []) or []
                if str(item).strip()
            ]
            if input_hints:
                detail_parts.append("hints:" + ",".join(input_hints[:2]))
            detail_text = " ; ".join(detail_parts) if detail_parts else "-"
            line = (
                f"- `{entry['capability_id']}` | {entry['name']} | {description} | "
                f"{detail_text} | `{entry['locator']}`"
            )
            if not _fits(rendered_lines, [line]):
                truncated = True
                break
            rendered_lines.append(line)
            shown_count += 1
        if truncated:
            break

    if shown_count < total_count:
        note = f"Showing {shown_count} of {total_count} capabilities due to budget/count limits"
        note_lines = ["", f"<!-- {note} -->"]
        if _fits(rendered_lines, note_lines):
            rendered_lines.extend(note_lines)
        else:
            note_text = f"\n<!-- {note} -->"
            while note_text and not _fits(rendered_lines, [note_text]):
                note_text = note_text[:-1]
            if note_text:
                rendered_lines.append(note_text)

    return "\n".join(rendered_lines)


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
