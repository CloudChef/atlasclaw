# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Reusable section renderers for PromptBuilder."""

from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import sanitize_workflow_only_text


def build_standard_skill_runtime_policy(target_md_skill: dict[str, Any]) -> list[str]:
    """Return execution-only instructions for selected standard markdown skills."""
    tool_names = target_md_skill.get("standard_runtime_tool_names")
    tool_list = ", ".join(str(item) for item in tool_names or [] if str(item).strip())
    lines = [
        "This standard skill has an internal runtime for the current turn. "
        "Use it only to follow this selected skill's instructions."
    ]
    if tool_list:
        lines.append(f"Internal runtime tools available for this skill: {tool_list}.")
    lines.extend(
        [
            "`skill_exec` runs from the current work_dir by default. Use "
            "`$ATLASCLAW_SKILL_DIR/...` only when running files that belong to the selected skill.",
            "Write user-facing outputs under `$ATLASCLAW_WORK_DIR` and expose only final files via "
            "work_dir-relative `download_paths`.",
            "Do not expose scripts, logs, temp files, cache files, or hidden runtime files as downloads.",
            "Use the dependencies already available in the runtime; if a required dependency is missing, "
            "report it instead of installing packages.",
            "`HOME`, `TMPDIR`, `XDG_CONFIG_HOME`, and `XDG_CACHE_HOME` are scoped under the current "
            "user's work_dir for this skill run.",
        ]
    )
    return lines


def build_target_md_skill(target_md_skill: dict[str, Any]) -> str:
    """Build a focused section for stage-two markdown skill execution."""
    qualified_name = target_md_skill.get("qualified_name", "")
    file_path = target_md_skill.get("file_path", "")
    provider = target_md_skill.get("provider", "")
    workflow_context = target_md_skill.get("workflow_context")
    instructions = sanitize_workflow_only_text(
        target_md_skill.get("instructions", ""),
        collapse_whitespace=False,
    )
    lines = ["## Target Markdown Skill", ""]
    if qualified_name:
        lines.append(f"Qualified name: {qualified_name}")
    if provider:
        lines.append(f"Provider: {provider}")
    if file_path:
        lines.append(f"File path: {file_path}")
    if instructions:
        lines.append("Loaded SKILL.md for the selected skill:")
        lines.append("")
        lines.append(instructions)
    else:
        lines.append(
            "This skill was selected for the current turn, but its `SKILL.md` content could not be loaded."
        )
    lines.append("You must use only this markdown skill for the current run.")
    lines.append("Prefer any executable tool already registered for this skill.")
    if bool(target_md_skill.get("standard_runtime_enabled")):
        lines.extend(build_standard_skill_runtime_policy(target_md_skill))
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
    lines.append(
        "When answering or reasoning about a field or fact from the current workflow context, use "
        "only that context. If the current selected item does not include the field, say it is "
        "missing or empty instead of borrowing it from another candidate or prior alternative."
    )
    lines.append(
        "If the user replies with a short value such as a number, interpret it against the "
        "immediately preceding assistant question and the Current Follow-up Context. Repeated "
        "numbers may answer different questions in the same workflow."
    )
    lines.append(
        "When asking the user to choose from options, display explicit numbers for every option. "
        "If a previous option list was shown without numbers and the user replies with a number, "
        "map it by visible order when the ordering is unambiguous."
    )
    lines.append(
        "Do not synthesize or merge missing workflow facts from static examples, prior drafts, or "
        "other candidate items."
    )
    lines.append(
        "Do not imply the existence of additional workflow data or documents unless the current "
        "workflow context explicitly includes them."
    )
    lines.append(
        "Do not switch to a different skill, tool family, or workflow path just because a lookup "
        "failed; stay with the current turn context unless the user explicitly changes intent."
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


def build_current_follow_up_context(context: str) -> str:
    """Build a focused section for interpreting low-information current replies."""
    text = str(context or "").strip()
    if not text:
        return ""
    if len(text) > 4000:
        text = f"{text[:2000].rstrip()}\n...\n{text[-2000:].lstrip()}"
    lines = [
        "## Current Follow-up Context",
        "",
        "The current user message is a low-information reply, such as a number or short confirmation.",
        "Before choosing a tool or replying, interpret the current user message using this section.",
        "Do not evaluate the raw current message as a standalone request until you have first tried to interpret it as the reply to the latest assistant prompt.",
        "Treat a numeric reply as an answer to the latest assistant follow-up prompt below, while preserving the recent selection context.",
        "Repeated numbers can answer different consecutive questions; do not collapse them into one standalone request.",
        "If the latest prompt contains a displayed option list, map the number to that option.",
        "If the options were shown without explicit numbers, map a numeric reply by visible order when that order is unambiguous.",
        "If the mapping is ambiguous, ask a focused clarification question.",
        "Do not say the raw number is unsupported when it can be mapped to an option in the latest assistant prompt.",
        "Do not reinterpret a bare numeric follow-up as an external object ID unless the latest assistant prompt explicitly asked for such an ID.",
        "Do not treat a numeric reply as submission confirmation unless the latest prompt explicitly asked to confirm a JSON preview.",
        "If required fields are still missing after interpreting the reply, ask for the next missing field instead of calling a terminal or mutating tool.",
        "",
        text,
    ]
    return "\n".join(lines)


def build_identity(config, *, memory_available: bool = False) -> str:
    """Build the identity section."""
    capabilities = [
        "- Handling complex multi-turn conversations with context continuity",
        "- Invoking various business skills (cloud resource management, ITSM, ticket processing, etc.)",
    ]
    if memory_available:
        capabilities.append("- Managing long-term memory with semantic retrieval")
    capabilities.append("- Supporting multi-step workflows and task collaboration")
    lines = [
        "## Identity",
        "",
        f"You are {config.agent_name}, {config.agent_description}.",
        "",
        "Your core capabilities include:",
        *capabilities,
    ]
    return "\n".join(lines)


def build_memory_behavior() -> str:
    """Build user-facing guidance for preference memory requests."""
    lines = [
        "## Memory Behavior",
        "",
        "Durable user-experience preferences, such as response language, response style,",
        "and assistant nickname, are handled by AtlasClaw runtime memory after a",
        "successful reply when the current user is eligible.",
        "",
        "When the user asks you to remember a durable user-experience preference, answer",
        "naturally and say you will use that preference going forward. Do not claim that",
        "you lack memory, cannot save memory, or need a visible memory write tool for",
        "those preference requests. Do not overstate storage guarantees; avoid phrases",
        'such as "permanently saved" unless a runtime tool result in this turn explicitly',
        "proves a persistent write.",
    ]
    return "\n".join(lines)


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


def build_tool_policy(
    tool_policy: Optional[dict],
    *,
    memory_available: bool = False,
) -> str:
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
            lines.append("No tools are available in this turn.")
            lines.append(
                "Important terminology: `provider`, `skill`, and `tool` are runtime nouns. "
                "MUST NOT translate, paraphrase, or replace these three words."
            )
            lines.append(
                "Answer normally for ordinary conversation, greetings, identity or capability "
                "questions, general knowledge, explanations, writing, brainstorming, and other "
                "requests that do not depend on unavailable runtime capability."
            )
            lines.append(
                "For those direct conversation requests, answer immediately in one or two "
                "sentences; do not analyze provider capability, wait for tools, or continue a "
                "prior external-system workflow unless the current request explicitly asks for it."
            )
            if memory_available:
                lines.append(
                    "User-experience preference memory requests are direct conversation requests. "
                    "Do not reject them just because no visible memory write tool is available; "
                    "runtime memory handles eligible preferences after the reply."
                )
            lines.append(
                "If the user asks for an action or fact that depends on an external provider, "
                "private system, or unavailable capability, say you cannot perform or verify it "
                "because no provider, skill, or tool is available."
            )
            lines.append(
                "Treat requests to create, submit, file, apply for, approve, reject, start, "
                "stop, delete, update, query, verify, or track enterprise records or service "
                "workflow items as runtime-capability requests when no matching capability is "
                "visible. This includes provider-backed requests, service tickets, approvals, "
                "resource changes, access changes, and private catalog workflows."
            )
            lines.append(
                "For those runtime-capability requests, do not continue by gathering workflow "
                "details, offering categories, or asking which external system to use. State "
                "that no provider, skill, or tool is available to perform or verify the request."
            )
            lines.append(
                "For that unavailable-capability answer, use the user's language and keep it "
                "to one concise sentence. It must explicitly include the runtime words "
                "`provider`, `skill`, and `tool`, and say AtlasClaw cannot perform or verify "
                "the requested operation."
            )
            lines.append(
                "For unavailable runtime capability, be concise: explicitly name the missing "
                "boundary as provider, skill, or tool, and do not list example systems, invent "
                "alternate channels, or ask the user which external system to use."
            )
            lines.append(
                "Do not expose internal evidence terms such as `same-run tool evidence`; the "
                "user-facing reason is simply that no provider, skill, or tool is available."
            )
            lines.append(
                "Do not mention deployment modes, role identifiers, external-system categories, "
                "or out-of-band places where the user might perform the operation."
            )
            lines.append(
                "Never present unavailable external-system state, evidence, or side effects as "
                "real unless tool output from this turn explicitly proves them."
            )
            lines.append(
                "Do not turn missing capability into an external-system fact: without explicit "
                "tool output from this turn, do not say records are absent, results are empty, "
                "an object does not exist, an operation succeeded or failed, or logs, "
                "timestamps, statuses, and verification evidence exist."
            )
            lines.append(
                "Do not emit tool-call markup, XML tags, or pseudo tool invocations like "
                "`<tool_call>` or `<web_search>`."
            )
    return "\n".join(lines)


def build_provider_auth_diagnostics(diagnostics: Optional[dict[str, dict]]) -> str:
    """Build provider-auth guidance from request-scoped runtime diagnostics."""
    if not isinstance(diagnostics, dict) or not diagnostics:
        return ""

    entries: list[str] = []
    for provider_type, instances in diagnostics.items():
        normalized_provider_type = str(provider_type or "").strip()
        if not normalized_provider_type or not isinstance(instances, dict):
            continue
        for instance_name, diagnostic in instances.items():
            if not isinstance(diagnostic, dict):
                continue
            normalized_instance_name = str(
                diagnostic.get("instance_name") or instance_name or ""
            ).strip()
            label = f"provider:{normalized_provider_type}"
            if normalized_instance_name:
                label += f" instance:{normalized_instance_name}"
            if bool(diagnostic.get("missing_user_token")):
                entries.append(
                    f"- {label}: the user's personal provider access credential "
                    "is not configured. If a tool result reports missing authentication for "
                    "this provider, tell the user the requested service is currently "
                    "unavailable and ask them to configure the provider access credential or "
                    "token in personal account settings, then retry. Do not also tell them "
                    "to contact an administrator for this case."
                )
            elif bool(diagnostic.get("user_token_configured")):
                entries.append(
                    f"- {label}: this run is using the user's personal provider access "
                    "credential. If a tool result reports that authentication "
                    "was rejected, invalid, expired, unauthorized, forbidden, or returned "
                    "HTTP 401/403 for this provider, tell the user the requested service is "
                    "currently unavailable and ask them to update the provider access "
                    "credential or token in personal account settings, then retry. Do not "
                    "also tell them to contact an administrator for this user-owned "
                    "credential case."
                )
            elif bool(diagnostic.get("contact_admin")):
                entries.append(
                    f"- {label}: provider runtime access is not configured or authorized for "
                    "this run. If a tool result reports missing authentication for this "
                    "provider, tell the user the requested service is currently unavailable "
                    "and ask them to contact an administrator."
                )

    if not entries:
        return ""

    lines = [
        "## Provider Authentication Diagnostics",
        "",
        "These diagnostics describe runtime authentication handling for visible provider services.",
        "Use them only when a visible provider or skill tool reports missing authentication "
        "or an authentication or authorization rejection.",
        *entries,
        "Do not expose backend setup instructions, configuration file paths, internal "
        "credential mechanics, or raw credentials. Do not ask the user to paste credentials "
        "in chat.",
    ]
    return "\n".join(lines)


def build_safety() -> str:
    """Build the safety section."""
    return """## Safety

Please follow these safety guidelines:
- Avoid power-seeking behaviors or bypassing oversight
- Do not execute operations that may cause irreversible damage
- Sensitive information must be desensitized
- Respect user data privacy"""


def build_response_language() -> str:
    """Build the always-on response language section."""
    return """## Response Language

Use the language explicitly requested by the user. If no response language is requested, use the dominant language of the current user message.

Apply this to direct replies, tool-backed workflows, markdown-skill workflows, previews, confirmations, and follow-up questions.

Do not translate code, JSON keys, API field names, provider names, skill names, tool names, catalog names, or quoted labels unless the user asks."""


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
        "Detailed instructions are loaded after an authorized skill is selected for execution.",
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
        "Detailed skill instructions are loaded after an authorized skill is selected for execution.",
        "Format: `capability_id | name | description | provider/artifact/tools | locator`",
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

You can read files in this directory when a read capability is available.
General-purpose file writes are not supported. User-facing generated files
must be produced by an authorized artifact skill.

When you create or export a user-facing file, mention only the file name in user-facing
text. If you include a markdown download link, use `workspace://<filename>` with no
absolute path, `./` prefix, or directory components. Do not place user-facing generated
files under hidden internal directories such as `.atlasclaw`."""


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
