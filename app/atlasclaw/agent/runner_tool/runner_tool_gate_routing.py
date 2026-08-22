# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Optional

from app.atlasclaw.agent.tool_gate_models import ToolGateDecision


@dataclass(frozen=True)
class _ExactChoiceMatch:
    """One exact selection resolved from the latest visible option list."""

    ordinal: int
    label: str
    visible_line: str
    match_mode: str
    prompt_message_index: int = -1


class RunnerToolGateRoutingMixin:
    """Resolve structured choices and build planner-authorized continuation context."""

    @staticmethod
    def _normalize_visible_choice_text(value: Any) -> str:
        """Normalize text as rendered by the Markdown option prompt."""
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        normalized = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", normalized)
        normalized = re.sub(r"(?:\*\*|__|~~|`)", "", normalized)
        return " ".join(normalized.split()).strip()

    @classmethod
    def _normalize_choice_match_key(cls, value: Any) -> str:
        """Build an exact visible-text key while ignoring all whitespace."""
        return re.sub(r"\s+", "", cls._normalize_visible_choice_text(value))

    @classmethod
    def _visible_choice_rows(cls, assistant_prompt: str) -> list[_ExactChoiceMatch]:
        """Parse one unambiguous numbered option list from visible assistant text."""
        rows: list[_ExactChoiceMatch] = []
        seen_ordinals: set[int] = set()
        row_pattern = re.compile(
            r"^\s*(?:\[(\d+)\]|(\d+)[\.\)\u3001])\s+(.+?)\s*$"
        )
        for raw_line in str(assistant_prompt or "").splitlines():
            match = row_pattern.match(raw_line)
            if match is None:
                continue
            ordinal = int(match.group(1) or match.group(2))
            label = cls._normalize_visible_choice_text(match.group(3))
            visible_line = cls._normalize_visible_choice_text(raw_line)
            if ordinal <= 0 or not label or ordinal in seen_ordinals:
                return []
            seen_ordinals.add(ordinal)
            rows.append(
                _ExactChoiceMatch(
                    ordinal=ordinal,
                    label=label,
                    visible_line=visible_line,
                    match_mode="",
                )
            )
        return rows

    @classmethod
    def _resolve_single_visible_choice_prompt(
        cls,
        assistant_prompt: str,
    ) -> _ExactChoiceMatch | None:
        """Parse a sole numbered row for a metadata-authorized continuation.

        The execution flow applies this result only to a causal read-only tool
        that explicitly declares ``auto_select_single_option``. Prompt wording
        is untrusted display text and does not authorize automatic selection.
        """
        rows = cls._visible_choice_rows(assistant_prompt)
        if len(rows) != 1:
            return None
        return replace(rows[0], match_mode="single_auto")

    @classmethod
    def _resolve_exact_choice_reply(
        cls,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> _ExactChoiceMatch | None:
        """Resolve a bare number or exact visible option row from the latest prompt.

        This matcher intentionally accepts only the two deterministic forms.  Any
        extra prose, ambiguous label, malformed list, or out-of-range number is
        left to the normal conversation planner.
        """
        reply = cls._normalize_visible_choice_text(user_message)
        if not reply:
            return None
        reply_key = cls._normalize_choice_match_key(reply)

        assistant_prompt = ""
        assistant_prompt_index = -1
        for message_index in range(len(recent_history) - 1, -1, -1):
            message = recent_history[message_index]
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role == "assistant":
                assistant_prompt = str(message.get("content", "") or "").strip()
                assistant_prompt_index = message_index
                break
            if role == "user":
                return None
        if not assistant_prompt:
            return None

        rows = cls._visible_choice_rows(assistant_prompt)
        if not rows:
            return None

        if re.fullmatch(r"[1-9]\d*", reply_key):
            ordinal = int(reply_key)
            matches = [row for row in rows if row.ordinal == ordinal]
            if len(matches) != 1:
                return None
            return replace(
                matches[0],
                match_mode="number",
                prompt_message_index=assistant_prompt_index,
            )

        matches = [
            row
            for row in rows
            if reply_key
            in {
                cls._normalize_choice_match_key(row.visible_line),
                cls._normalize_choice_match_key(row.label),
            }
        ]
        if len(matches) != 1:
            return None
        return replace(
            matches[0],
            match_mode="full_line",
            prompt_message_index=assistant_prompt_index,
        )

    @staticmethod
    def _build_contextual_follow_up_request(
        *,
        previous_user_message: str,
        recent_context: str = "",
        assistant_prompt: str,
        current_user_message: str,
    ) -> str:
        previous = " ".join((previous_user_message or "").split()).strip()
        context = str(recent_context or "").strip()
        prompt = str(assistant_prompt or "").strip()
        current = " ".join((current_user_message or "").split()).strip()
        if not prompt:
            return current
        if len(prompt) > 2400:
            prompt = f"{prompt[:1200].rstrip()}\n...\n{prompt[-1200:].lstrip()}"

        parts = []
        parts.append(
            "Continue the active multi-turn workflow. Interpret the latest user reply below "
            "as an answer to the latest assistant follow-up prompt before treating it as a "
            "standalone request."
        )
        parts.append(
            "If the latest assistant prompt displays options, map the reply to that option "
            "and continue the workflow. Do not say the raw reply is unsupported when such a "
            "mapping is possible."
        )
        parts.append(
            "If the latest options were shown without explicit numbers, map a numeric reply by "
            "the visible option order when that order is unambiguous."
        )
        if previous:
            parts.append(f"Original user request:\n{previous}")
        if context:
            parts.append(f"Recent follow-up context:\n{context}")
        parts.append(f"Latest assistant follow-up prompt:\n{prompt}")
        parts.append(f"User reply to that prompt:\n{current}")
        return "\n\n".join(parts).strip()

    @staticmethod
    def _format_recent_follow_up_context(
        *,
        recent_history: list[dict[str, Any]],
        start_index: int,
        end_index: int,
    ) -> str:
        rows: list[str] = []
        for item in recent_history[start_index:end_index]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            if len(content) > 800:
                content = f"{content[:400].rstrip()}\n...\n{content[-400:].lstrip()}"
            label = "Assistant" if role == "assistant" else "User"
            rows.append(f"{label}: {content}")
        return "\n\n".join(rows).strip()

    @staticmethod
    def _tool_gate_has_strict_need(decision: ToolGateDecision) -> bool:
        return any(
            [
                bool(decision.needs_external_system),
                bool(decision.needs_browser_interaction),
                bool(decision.needs_private_context),
                bool(decision.needs_grounded_verification and not decision.needs_live_data),
            ]
        )
    def _build_active_capability_continuation_request(
        self,
        *,
        user_message: str,
        recent_history: list[dict[str, Any]],
    ) -> tuple[str, bool]:
        normalized_user_message = " ".join((user_message or "").split()).strip()
        if not normalized_user_message:
            return user_message, False

        last_assistant_index: Optional[int] = None
        last_assistant_raw_message = ""
        for index in range(len(recent_history) - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "assistant":
                continue
            content_raw = str(item.get("content", "") or "")
            if not content_raw.strip():
                continue
            last_assistant_index = index
            last_assistant_raw_message = content_raw
            break

        if last_assistant_index is None:
            return normalized_user_message, False

        previous_user_message = ""
        previous_user_index: Optional[int] = None
        for index in range(last_assistant_index - 1, -1, -1):
            item = recent_history[index]
            if str(item.get("role", "")).strip() != "user":
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if not content:
                continue
            previous_user_message = content
            previous_user_index = index
            break

        recent_context = self._format_recent_follow_up_context(
            recent_history=recent_history,
            start_index=(previous_user_index + 1) if previous_user_index is not None else 0,
            end_index=last_assistant_index,
        )
        combined = self._build_contextual_follow_up_request(
            previous_user_message=previous_user_message,
            recent_context=recent_context,
            assistant_prompt=last_assistant_raw_message,
            current_user_message=normalized_user_message,
        )
        return combined, combined != normalized_user_message
