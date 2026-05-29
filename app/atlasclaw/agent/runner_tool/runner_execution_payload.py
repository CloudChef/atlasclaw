# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from typing import Any, Optional

from app.atlasclaw.agent.runner_tool.runner_agent_override import resolve_override_tools
from app.atlasclaw.core.deps import SkillDeps


def _provider_auth_diagnostic_message(diagnostic: dict[str, Any] | None) -> str:
    if not isinstance(diagnostic, dict):
        return ""
    if bool(diagnostic.get("missing_user_token")):
        return (
            "Provider authentication diagnostic: the requested provider service is currently "
            "unavailable because the user's personal provider access credential "
            "is not configured. Tell the user to configure it in personal account settings, "
            "then retry."
        )
    if bool(diagnostic.get("user_token_configured")):
        return (
            "Provider authentication diagnostic: the requested provider service is currently "
            "unavailable because the user's personal provider access credential "
            "was rejected or may be invalid or expired. Tell the user to update it in personal "
            "account settings, then retry."
        )
    if bool(diagnostic.get("contact_admin")):
        return (
            "Provider authentication diagnostic: the requested provider service is currently "
            "unavailable because the provider instance is not configured or authorized for "
            "runtime access. Tell the user to contact an administrator, then retry."
        )
    return ""


def provider_auth_diagnostic_user_message(diagnostic: dict[str, Any] | None) -> str:
    """Return a concise user-facing provider authentication message."""
    if not isinstance(diagnostic, dict):
        return ""
    if bool(diagnostic.get("missing_user_token")):
        return (
            "The requested provider service is unavailable because your personal provider "
            "access credential is not configured. Configure it in personal "
            "account settings, then retry."
        )
    if bool(diagnostic.get("user_token_configured")):
        return (
            "The requested provider service is unavailable because your personal provider "
            "access credential was rejected, invalid, or expired. Update it "
            "in personal account settings, then retry."
        )
    if bool(diagnostic.get("contact_admin")):
        return (
            "The requested provider service is unavailable because the provider instance is "
            "not configured or authorized for runtime access. Contact an administrator, then retry."
        )
    return ""


def _provider_auth_system_instruction(diagnostic: dict[str, Any] | None) -> str:
    if not isinstance(diagnostic, dict):
        return (
            "If the tool failure indicates missing, rejected, invalid, or expired provider "
            "authentication or access credentials, first say the service the user is trying "
            "to use is currently unavailable. Do not expose backend setup details, internal "
            "field names, low-level credential mechanics, or configuration file paths. If a "
            "user-owned personal provider access credential is not configured or may be "
            "invalid/expired, ask the user to configure or update it in personal account "
            "settings. For provider instance or server-side configuration problems, "
            "or when the credential owner is unclear, tell the user to contact an "
            "administrator. Do not ask diagnostic questions about backend setup, and do not "
            "ask the user to paste access credentials into chat."
        )
    if bool(diagnostic.get("missing_user_token")):
        return (
            "The provider authentication diagnostic for this turn is authoritative: the "
            "user's personal provider access credential is not configured. "
            "Tell the user the requested service is currently unavailable and ask them to "
            "configure the provider access credential or token in personal account settings, "
            "then retry. Do not mention contacting an administrator."
        )
    if bool(diagnostic.get("user_token_configured")):
        return (
            "The provider authentication diagnostic for this turn is authoritative: the "
            "user's personal provider access credential was rejected or may "
            "be invalid or expired. Tell the user the requested service is currently "
            "unavailable and ask them to update the provider access credential or token in "
            "personal account settings, then retry. Do not mention contacting an administrator."
        )
    if bool(diagnostic.get("contact_admin")):
        return (
            "The provider authentication diagnostic for this turn is authoritative: the "
            "provider instance is not configured or authorized for runtime access. Tell the "
            "user the requested service is currently unavailable and ask them to contact an "
            "administrator. Do not mention personal credential setup."
        )
    return ""


def _looks_like_provider_auth_failure(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    has_provider_auth_context = any(
        marker in text
        for marker in (
            "auth",
            "credential",
            "token",
            "configuration",
            "config",
            "unauthorized",
            "forbidden",
            "permission",
            "access denied",
        )
    )
    has_failure_context = any(
        marker in text
        for marker in (
            "not configured",
            "not available",
            "missing",
            "required",
            "no usable",
            "unavailable",
            "rejected",
            "invalid",
            "expired",
            "denied",
            "failed",
        )
    )
    has_http_auth_status = any(
        marker in text
        for marker in (
            "http 401",
            "http 403",
            "status 401",
            "status 403",
            "401 unauthorized",
            "403 forbidden",
        )
    )
    has_backend_detail = any(
        marker in text
        for marker in (
            "atlasclaw.json",
            "service_providers",
            "provider_config",
            "environment variable",
            "http request",
        )
    )
    return has_http_auth_status or (has_provider_auth_context and has_failure_context) or has_backend_detail


def _sanitize_provider_auth_text(value: Any, diagnostic: dict[str, Any] | None) -> str:
    text = str(value or "").strip()
    diagnostic_message = _provider_auth_diagnostic_message(diagnostic)
    if diagnostic_message and _looks_like_provider_auth_failure(text):
        return diagnostic_message
    return text


def select_provider_auth_diagnostic(
    *,
    extra: Any,
    attempted_tools: list[dict[str, Any]] | list[str] | None = None,
    failure_reasons: list[str] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick a request-scoped provider-auth diagnostic for failed tool output."""
    if not isinstance(extra, dict):
        return None
    diagnostics = extra.get("provider_auth_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        return None

    evidence_texts = [str(reason or "") for reason in (failure_reasons or [])]
    for result in tool_results or []:
        if isinstance(result, dict):
            evidence_texts.append(str(result.get("content", "") or ""))
    if evidence_texts and not any(_looks_like_provider_auth_failure(text) for text in evidence_texts):
        return None

    attempted_tool_names: set[str] = set()
    for item in attempted_tools or []:
        if isinstance(item, dict):
            tool_name = str(item.get("name", "") or item.get("tool_name", "")).strip()
        else:
            tool_name = str(item or "").strip()
        if tool_name:
            attempted_tool_names.add(tool_name)

    provider_types: set[str] = set()
    tools_snapshot = extra.get("tools_snapshot")
    if isinstance(tools_snapshot, list) and attempted_tool_names:
        for tool in tools_snapshot:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name", "") or "").strip()
            if tool_name not in attempted_tool_names:
                continue
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            if provider_type:
                provider_types.add(provider_type)

    candidates: list[dict[str, Any]] = []
    for provider_type, instances in diagnostics.items():
        normalized_provider_type = str(provider_type or "").strip().lower()
        if provider_types and normalized_provider_type not in provider_types:
            continue
        if not isinstance(instances, dict):
            continue
        for diagnostic in instances.values():
            if isinstance(diagnostic, dict):
                candidates.append(diagnostic)

    if not candidates:
        # Do not borrow diagnostics from unrelated providers; generic failure text
        # is safer than telling the user to fix the wrong credential.
        return None
    for diagnostic in candidates:
        if bool(diagnostic.get("missing_user_token")):
            return diagnostic
    for diagnostic in candidates:
        if bool(diagnostic.get("user_token_configured")):
            return diagnostic
    return candidates[0]


def _provider_auth_diagnostic_candidates(extra: Any) -> list[dict[str, Any]]:
    if not isinstance(extra, dict):
        return []
    diagnostics = extra.get("provider_auth_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        return []

    candidates: list[dict[str, Any]] = []
    for provider_type, instances in diagnostics.items():
        normalized_provider_type = str(provider_type or "").strip().lower()
        if not normalized_provider_type or not isinstance(instances, dict):
            continue
        for instance_name, diagnostic in instances.items():
            if not isinstance(diagnostic, dict):
                continue
            normalized_instance_name = str(
                diagnostic.get("instance_name") or instance_name or ""
            ).strip()
            if not normalized_instance_name:
                continue
            candidates.append(
                {
                    **diagnostic,
                    "provider_type": str(
                        diagnostic.get("provider_type") or normalized_provider_type
                    ).strip().lower(),
                    "instance_name": normalized_instance_name,
                }
            )
    return candidates


def _provider_auth_diagnostic_matches_plan(
    diagnostic: dict[str, Any],
    intent_plan: Any,
) -> bool:
    provider_type = str(diagnostic.get("provider_type", "") or "").strip().lower()
    instance_name = str(diagnostic.get("instance_name", "") or "").strip()
    if not provider_type or not instance_name:
        return False

    target_instances = {
        str(item or "").strip().lower()
        for item in getattr(intent_plan, "target_provider_instances", []) or []
        if str(item or "").strip()
    }
    target_types = {
        str(item or "").strip().lower()
        for item in getattr(intent_plan, "target_provider_types", []) or []
        if str(item or "").strip()
    }
    qualified_instance = f"{provider_type}.{instance_name}".lower()
    if target_instances:
        return qualified_instance in target_instances or instance_name.lower() in target_instances
    if target_types:
        return provider_type in target_types
    return False


def select_no_runtime_provider_auth_diagnostic(
    *,
    extra: Any,
    intent_plan: Any = None,
) -> dict[str, Any] | None:
    """Pick a provider-auth diagnostic when no runtime tool survived projection."""
    candidates = _provider_auth_diagnostic_candidates(extra)
    if not candidates:
        return None

    if intent_plan is not None:
        scoped = [
            diagnostic
            for diagnostic in candidates
            if _provider_auth_diagnostic_matches_plan(diagnostic, intent_plan)
        ]
        if scoped:
            candidates = scoped
        elif (
            getattr(intent_plan, "target_provider_instances", None)
            or getattr(intent_plan, "target_provider_types", None)
        ):
            return None
    elif len(candidates) != 1:
        return None

    if len(candidates) != 1:
        missing_user_token = [
            diagnostic for diagnostic in candidates if bool(diagnostic.get("missing_user_token"))
        ]
        if len(missing_user_token) == 1:
            return missing_user_token[0]
        configured_user_token = [
            diagnostic for diagnostic in candidates if bool(diagnostic.get("user_token_configured"))
        ]
        if len(configured_user_token) == 1:
            return configured_user_token[0]
        return None
    return candidates[0]


def build_finalize_payload(
    *,
    user_message: str,
    tool_results: list[dict[str, Any]],
    provider_auth_diagnostic: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build a minimal final-answer payload for a tool-backed turn."""
    evidence_lines: list[str] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip() or "tool"
        content = _sanitize_provider_auth_text(item.get("content", ""), provider_auth_diagnostic)
        if not content:
            continue
        evidence_lines.append(f"- {tool_name}: {content}")

    if not evidence_lines:
        evidence_lines.append("- tool: no tool output available")

    provider_auth_instruction = _provider_auth_system_instruction(provider_auth_diagnostic)
    return {
        "system_prompt": (
            "You are the assistant. Produce a concise markdown answer using only the supplied tool evidence. "
            "Do not fabricate facts or mention hidden reasoning. "
            "If a Provider authentication diagnostic appears in the supplied evidence, follow that diagnostic exactly: "
            "when it says a personal provider access credential is not configured, rejected, invalid, or expired, "
            "do not also tell the user to contact an administrator; "
            "when it says to contact an administrator, do not also mention personal credential setup. "
            f"{provider_auth_instruction} "
            "Do not add wrapper headings like 'Answer' or 'Result' unless the user explicitly asked for them."
        ),
        "user_prompt": (
            f"User request:\n{str(user_message or '').strip()}\n\n"
            f"Tool evidence:\n{chr(10).join(evidence_lines)}\n\n"
            "Return concise markdown. Use bullets or short paragraphs when helpful. "
            "If there are source links in the evidence, keep them as markdown links.\n"
        ),
    }


def build_direct_answer_recovery_payload(
    *,
    user_message: str,
    invalid_output: str,
) -> dict[str, str]:
    """Build a recovery payload for direct-answer turns that emitted fake tool markup."""
    invalid_preview = str(invalid_output or "").strip() or "(empty draft)"
    return {
        "system_prompt": (
            "You are the assistant. No tools are available in this turn.\n"
            "Answer the user directly from model knowledge.\n"
            "Do not emit tool-call markup, XML tags, or pseudo tool invocations such as "
            "<tool_call>, <web_search>, or similar placeholders.\n"
            "Important terminology: `provider`, `skill`, and `tool` are runtime nouns. "
            "MUST NOT translate, paraphrase, or replace these three words.\n"
            "Do not mention hidden reasoning. Do not say you searched the web unless real tool "
            "evidence exists in this run.\n"
            "If the user asks for an action or fact that depends on an external provider, "
            "private system, or unavailable capability, say you cannot perform or verify it "
            "because no provider, skill, or tool is available.\n"
            "For that unavailable-capability answer, use the user's language and keep it "
            "to one concise sentence. It must explicitly include the runtime words "
            "`provider`, `skill`, and `tool`, and say AtlasClaw cannot perform or verify "
            "the requested operation.\n"
            "Do not expose internal evidence terms such as `same-run tool evidence`; the "
            "user-facing reason is simply that no provider, skill, or tool is available.\n"
            "Do not mention deployment modes, role identifiers, external-system categories, "
            "or out-of-band places where the user might perform the operation.\n"
            "Answer only the current user request; do not continue, summarize, or confirm a "
            "prior external-system workflow unless the current request explicitly asks for that.\n"
            "Never present unavailable external-system state, evidence, or side effects as "
            "real unless tool output from this turn explicitly proves them.\n"
            "Do not turn missing capability into an external-system fact: without explicit tool output "
            "from this turn, do not say records are absent, results are empty, an object does not exist, "
            "an operation succeeded or failed, or logs, timestamps, statuses, and verification evidence exist.\n"
            "Return a concise markdown answer."
        ),
        "user_prompt": (
            f"User request:\n{str(user_message or '').strip()}\n\n"
            f"Discard this invalid draft:\n{invalid_preview}\n\n"
            "Rewrite it as a normal user-facing answer with no tool markup."
        ),
    }


def build_no_runtime_capability_answer(
    provider_auth_diagnostic: dict[str, Any] | None = None,
) -> str:
    """Return the canonical fail-closed answer for unavailable runtime capability."""
    provider_auth_message = provider_auth_diagnostic_user_message(provider_auth_diagnostic)
    if provider_auth_message:
        return provider_auth_message
    return "当前没有可用的 provider、skill 或工具，AtlasClaw 不能执行或验证该操作。"


def build_lookup_dump_recovery_payload(
    *,
    user_message: str,
    invalid_output: str,
    tool_results: list[dict[str, Any]],
    workflow_notes: list[str] | None = None,
) -> dict[str, str]:
    """Build a recovery payload for hidden workflow-lookup dumps echoed by the model."""
    evidence_lines: list[str] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip() or "tool"
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        compact_content = content[:5000].rstrip()
        if len(content) > 5000:
            compact_content = f"{compact_content}\n...[truncated]"
        indented_content = compact_content.replace("\n", "\n  ")
        evidence_lines.append(f"- {tool_name}:\n  {indented_content}")
    if not evidence_lines:
        evidence_lines.append("- tool: no usable lookup evidence was captured")

    note_lines = [
        f"- {str(note).strip()}"
        for note in (workflow_notes or [])
        if str(note).strip()
    ]
    if not note_lines:
        note_lines.append("- none")

    invalid_preview = str(invalid_output or "").strip()[:1200] or "(empty draft)"
    return {
        "system_prompt": (
            "You are the assistant. The previous draft incorrectly echoed raw internal lookup metadata "
            "or knowledge-base evidence blocks.\n"
            "Use only the supplied tool evidence to answer or continue the workflow in natural language.\n"
            "Preserve decisions already made in the workflow notes instead of restarting from an earlier lookup step.\n"
            "Ask the next concise user-facing question or confirmation only when appropriate.\n"
            "Do not quote JSON, UUIDs, IDs, raw metadata dumps, or scaffolding phrases like 'Found N ...'.\n"
            "Do not start with a source heading, do not use the literal label 'Source:', and do not paste "
            "chunks shaped like '### ...' followed by '- Source: ...'.\n"
            "Answer the user's question directly first. Cite concise source paths only when they help the user.\n"
            "Do not call tools. Do not mention hidden reasoning."
        ),
        "user_prompt": (
            f"User request:\n{str(user_message or '').strip()}\n\n"
            f"Discard this invalid draft:\n{invalid_preview}\n\n"
            f"Workflow notes:\n{chr(10).join(note_lines)}\n\n"
            f"Tool evidence:\n{chr(10).join(evidence_lines)}\n\n"
            "Rewrite it as the next natural-language workflow response."
        ),
    }


class RunnerExecutionPayloadMixin:
    """Build prompt payloads and diagnostics for runner execution phases."""

    @staticmethod
    def _should_surface_prompt_warning(warning_message: Any) -> bool:
        normalized = str(warning_message or "").strip().lower()
        if not normalized:
            return False
        if normalized.startswith("missing bootstrap file:"):
            return False
        return True
    @classmethod
    def _build_llm_payload_profile(
        cls,
        *,
        system_prompt: str,
        user_message: str,
        message_history: list[dict],
    ) -> dict[str, Any]:
        system_text = str(system_prompt or "")
        user_text = str(user_message or "")
        history_rows = [cls._normalize_payload_message(row) for row in (message_history or [])]

        system_chars = len(system_text)
        user_chars = len(user_text)
        history_chars = sum(len(row) for row in history_rows)

        system_bytes = len(system_text.encode("utf-8", errors="ignore"))
        user_bytes = len(user_text.encode("utf-8", errors="ignore"))
        history_bytes = sum(len(row.encode("utf-8", errors="ignore")) for row in history_rows)

        total_chars = system_chars + user_chars + history_chars
        total_bytes = system_bytes + user_bytes + history_bytes
        estimated_tokens = cls._estimate_tokens_by_chars(total_chars)

        duplicate_message_count, duplicate_group_count = cls._count_duplicate_history_messages(
            history_rows
        )
        history_count = len(history_rows)
        duplicate_ratio = (
            round(float(duplicate_message_count) / float(history_count), 4)
            if history_count > 0
            else 0.0
        )
        max_history_message_chars = max((len(row) for row in history_rows), default=0)
        user_repeated_in_history = cls._has_user_message_duplicate_in_history(
            user_text,
            history_rows,
        )

        return {
            "system_prompt_chars": system_chars,
            "system_prompt_bytes": system_bytes,
            "history_message_count": history_count,
            "history_chars": history_chars,
            "history_bytes": history_bytes,
            "history_max_message_chars": max_history_message_chars,
            "history_duplicate_messages": duplicate_message_count,
            "history_duplicate_groups": duplicate_group_count,
            "history_duplicate_ratio": duplicate_ratio,
            "user_message_chars": user_chars,
            "user_message_bytes": user_bytes,
            "user_message_repeated_in_history": user_repeated_in_history,
            "total_chars": total_chars,
            "total_bytes": total_bytes,
            "estimated_tokens": estimated_tokens,
        }
    @staticmethod
    def _normalize_payload_message(message: Any) -> str:
        if not isinstance(message, dict):
            return str(message or "")
        role = str(message.get("role", "") or "").strip()
        content = message.get("content", "")
        if isinstance(content, (dict, list)):
            content_text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        else:
            content_text = str(content or "")
        name = str(message.get("name", "") or "").strip()
        if name:
            return f"{role}:{name}:{content_text}"
        return f"{role}:{content_text}"
    @staticmethod
    def _estimate_tokens_by_chars(char_count: int) -> int:
        if char_count <= 0:
            return 0
        # Rough multilingual estimate for runtime observability.
        return max(1, int((char_count + 3) / 4))
    @staticmethod
    def _count_duplicate_history_messages(history_rows: list[str]) -> tuple[int, int]:
        if not history_rows:
            return 0, 0
        counts: dict[str, int] = {}
        for row in history_rows:
            normalized = " ".join(str(row or "").split()).strip()
            if not normalized:
                continue
            digest = hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()
            counts[digest] = counts.get(digest, 0) + 1
        duplicate_messages = sum(max(0, count - 1) for count in counts.values() if count > 1)
        duplicate_groups = sum(1 for count in counts.values() if count > 1)
        return duplicate_messages, duplicate_groups
    @staticmethod
    def _has_user_message_duplicate_in_history(user_message: str, history_rows: list[str]) -> bool:
        normalized_user = " ".join(str(user_message or "").split()).strip()
        if not normalized_user:
            return False
        user_entry = f"user:{normalized_user}"
        for row in history_rows:
            normalized_row = " ".join(str(row or "").split()).strip()
            if normalized_row == user_entry:
                return True
        return False
    @staticmethod
    def _deduplicate_message_history(messages: list[dict]) -> list[dict]:
        if len(messages) <= 1:
            return messages

        head_system: Optional[dict] = None
        core_messages = messages
        first = messages[0]
        if isinstance(first, dict) and str(first.get("role", "")).strip().lower() == "system":
            head_system = first
            core_messages = messages[1:]

        seen_signatures: set[str] = set()
        dedup_reversed: list[dict] = []
        for msg in reversed(core_messages):
            if not isinstance(msg, dict):
                dedup_reversed.append(msg)
                continue
            role = str(msg.get("role", "")).strip().lower()
            if role != "user":
                dedup_reversed.append(msg)
                continue
            if msg.get("tool_calls") or msg.get("tool_name") or msg.get("tool_call_id"):
                dedup_reversed.append(msg)
                continue
            normalized_content = " ".join(str(msg.get("content", "") or "").split()).strip()
            if not normalized_content:
                dedup_reversed.append(msg)
                continue
            user_identity = str(
                msg.get("user_id")
                or msg.get("name")
                or msg.get("sender_id")
                or "current_user"
            ).strip().lower()
            signature = f"{role}:{user_identity}:{normalized_content}"
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            dedup_reversed.append(msg)

        deduped = list(reversed(dedup_reversed))
        if head_system is not None:
            return [head_system, *deduped]
        return deduped

    @staticmethod
    def _merge_runtime_messages_with_session_prefix(
        *,
        session_message_history: list[dict],
        runtime_messages: list[dict],
        runtime_base_history_len: int,
    ) -> list[dict]:
        """Merge trimmed runtime history back onto the persisted session prefix.

        Runtime model loops may intentionally see a smaller history slice than the
        persisted session transcript. For persistence, hooks, and final answer
        extraction we reconstruct the full turn-visible transcript by keeping the
        session prefix and appending only the new suffix produced in the runtime
        loop.

        NOTE: pydantic-ai's internal ``_clean_message_history`` may merge
        consecutive ModelRequest/ModelResponse objects, reducing the model-message
        count below the original dict count.  After normalization back to dicts the
        "history portion" of ``runtime_messages`` may therefore contain *fewer*
        items than ``runtime_base_history_len``.  To avoid accidentally discarding
        the current turn's user message we locate the actual boundary by scanning
        backwards for the first user-role message that does NOT appear in the
        session prefix tail.
        """
        session_prefix = list(session_message_history or [])
        normalized_runtime = list(runtime_messages or [])

        if not normalized_runtime:
            return session_prefix

        # --- Determine safe cut index ------------------------------------------------
        # The naive cut at ``runtime_base_history_len`` works when the roundtrip
        # dict→model→dict is count-stable.  When it isn't (pydantic-ai merges),
        # the user message from this turn may end up *before* that index.  We
        # detect this by checking if normalized_runtime[runtime_base_history_len-1:]
        # contains a user message that should be part of the new suffix.
        nominal_cut = max(0, min(int(runtime_base_history_len or 0), len(normalized_runtime)))

        if nominal_cut <= 0:
            return session_prefix + normalized_runtime

        # Heuristic: if the nominal cut already places a user message as the first
        # item in the suffix, that's the expected normal case — use it directly.
        if nominal_cut < len(normalized_runtime):
            first_suffix = normalized_runtime[nominal_cut]
            if isinstance(first_suffix, dict) and first_suffix.get("role") == "user":
                return session_prefix + normalized_runtime[nominal_cut:]

        # Otherwise, scan backwards from nominal_cut to find where the new content
        # actually starts.  The new content starts at the first user-role message
        # (scanning from the end of the history zone) whose content does not match
        # the last user message in session_prefix.
        last_session_user_content: str | None = None
        for msg in reversed(session_prefix):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_session_user_content = str(msg.get("content", "")).strip()
                break

        # Scan normalized_runtime from nominal_cut backwards looking for the
        # turn's new user message that got shifted into the history zone.
        adjusted_cut = nominal_cut
        search_start = max(0, nominal_cut - 3)  # don't search too far back
        for idx in range(nominal_cut - 1, search_start - 1, -1):
            candidate = normalized_runtime[idx]
            if not isinstance(candidate, dict):
                continue
            if candidate.get("role") != "user":
                continue
            candidate_content = str(candidate.get("content", "")).strip()
            # Skip if it matches the last user message already in session prefix
            if candidate_content and candidate_content == last_session_user_content:
                continue
            # Found a user message that is NOT in session prefix — this is
            # the start of the new turn content.
            adjusted_cut = idx
            break

        return session_prefix + normalized_runtime[adjusted_cut:]
    async def run_single(
        self,
        user_message: str,
        deps: SkillDeps,
        *,
        system_prompt: Optional[str] = None,
        agent: Optional[Any] = None,
        allowed_tool_names: Optional[list[str]] = None,
    ) -> str:
        """Run a single non-streaming agent call."""
        runtime_agent = agent or getattr(self, "agent", None)
        if runtime_agent is None:
            return "[Error: no runtime agent available]"
        override_factory = getattr(runtime_agent, "override", None)
        override_cm = nullcontext()
        override_tools = resolve_override_tools(
            agent=runtime_agent,
            allowed_tool_names=allowed_tool_names,
        )
        if callable(override_factory) and system_prompt:
            override_candidates = []
            if override_tools is not None:
                override_candidates.append({"instructions": system_prompt, "tools": override_tools})
                override_candidates.append({"system_prompt": system_prompt, "tools": override_tools})
            else:
                override_candidates.append({"instructions": system_prompt})
                override_candidates.append({"system_prompt": system_prompt})
            for override_kwargs in override_candidates:
                try:
                    override_cm = override_factory(**override_kwargs)
                    break
                except TypeError:
                    continue
        elif callable(override_factory) and override_tools is not None:
            try:
                override_cm = override_factory(tools=override_tools)
            except TypeError:
                override_cm = nullcontext()
        try:
            if hasattr(override_cm, "__aenter__"):
                async with override_cm:
                    result = await runtime_agent.run(user_message, deps=deps)
            else:
                with override_cm:
                    result = await runtime_agent.run(user_message, deps=deps)
            return result.output if hasattr(result, "output") else str(result)
        except Exception as e:
            return f"[Error: {str(e)}]"
