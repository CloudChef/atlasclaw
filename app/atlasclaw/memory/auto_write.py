# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Automatic post-reply memory distillation and persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.atlasclaw.core.config import get_config
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.memory.access import (
    memory_available_for_deps,
    memory_chat_type_allowed,
    memory_manager_from_deps,
)
from app.atlasclaw.memory.formatting import compact_text
from app.atlasclaw.memory.manager import LONG_TERM_PREFERENCES_SECTION


RunSingleCallable = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class AutoMemoryWriteResult:
    """Outcome of one background memory distillation attempt."""

    status: str
    long_term_count: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _DistillationResult:
    """Internal model-distillation payload plus non-sensitive diagnostics."""

    payload: dict[str, list[str]]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _MaintenanceResult:
    """Internal maintained preference list plus non-sensitive diagnostics."""

    preferences: list[str]
    diagnostics: dict[str, Any]


class AutomaticMemoryWriteService:
    """Distill completed replies into user-scoped long-term preference memory.

    This service is an internal post-success side effect, not a chat-visible
    write tool. It writes only when memory is globally enabled, the request has
    memory permission, the chat type is allowed, and the model returns a
    structured distillation payload for ``MEMORY.md``.
    """

    async def write_after_success(
        self,
        *,
        deps: SkillDeps,
        session_key: str,
        run_id: str,
        user_message: str,
        assistant_message: str,
        final_messages: list[dict[str, Any]],
        run_single: Optional[RunSingleCallable] = None,
        agent: Any = None,
    ) -> AutoMemoryWriteResult:
        """Persist distilled memories after a completed user-facing reply.

        The distiller is intentionally fail-open. ``run_single`` is called with
        an empty tool allow-list when available. If the model distiller is
        unavailable or returns no structured memory payload, nothing is written.
        """
        config = self._resolve_config()
        diagnostics = self._base_diagnostics()
        if not bool(getattr(config, "enabled", True)):
            diagnostics["skip_reason"] = "config_disabled"
            return self._build_result("disabled", diagnostics=diagnostics)
        if not memory_available_for_deps(deps):
            diagnostics["skip_reason"] = "memory_unavailable_or_denied"
            return self._build_result("unavailable", diagnostics=diagnostics)
        if not memory_chat_type_allowed(session_key, getattr(config, "allowed_chat_types", None)):
            diagnostics["skip_reason"] = "chat_type_skipped"
            return self._build_result("chat_type_skipped", diagnostics=diagnostics)
        if not str(assistant_message or "").strip():
            diagnostics["skip_reason"] = "empty_answer"
            return self._build_result("empty_answer", diagnostics=diagnostics)

        memory_manager = memory_manager_from_deps(deps)
        if memory_manager is None:
            diagnostics["skip_reason"] = "memory_manager_unavailable"
            return self._build_result("unavailable", diagnostics=diagnostics)
        diagnostics["memory_path"] = self._display_memory_path(memory_manager)

        payload: dict[str, list[str]] = {"long_term": []}
        if callable(run_single):
            # Keep distillation separate from task execution: no tools are
            # allowed, and invalid/empty model output means "do not write".
            distillation = await self._distill_with_model(
                run_single=run_single,
                deps=deps,
                agent=agent,
                user_message=user_message,
                assistant_message=assistant_message,
                final_messages=final_messages,
                timeout_ms=int(getattr(config, "timeout_ms", 15000)),
            )
            payload = distillation.payload
            diagnostics.update(distillation.diagnostics)
        else:
            diagnostics["skip_reason"] = "distiller_not_available"

        long_term_items = self._sanitize_items(
            payload.get("long_term", []),
            max_items=int(getattr(config, "max_long_term_items", 3)),
            max_chars=int(getattr(config, "max_item_chars", 360)),
        )
        diagnostics["sanitized_long_term_count"] = len(long_term_items)
        if not long_term_items:
            if diagnostics.get("parsed_long_term_count", 0) > 0:
                diagnostics["skip_reason"] = "sanitize_empty"
            elif diagnostics.get("skip_reason") in {"", "none"}:
                diagnostics["skip_reason"] = "distiller_no_long_term"
            return self._build_result("no_memory", diagnostics=diagnostics)

        source = f"auto:{run_id or session_key}"
        maintained_items = long_term_items
        if callable(run_single) and self._has_existing_memory(memory_manager):
            maintenance = await self._maintain_memory_with_model(
                run_single=run_single,
                deps=deps,
                agent=agent,
                memory_manager=memory_manager,
                new_items=long_term_items,
                timeout_ms=int(getattr(config, "timeout_ms", 15000)),
            )
            diagnostics.update(maintenance.diagnostics)
            maintained_items = self._sanitize_items(
                maintenance.preferences,
                max_items=int(getattr(config, "max_maintained_preferences", 50)),
                max_chars=int(getattr(config, "max_item_chars", 360)),
            )
            diagnostics["maintained_long_term_count"] = len(maintained_items)
            if not maintained_items:
                if diagnostics.get("memory_maintainer_skip_reason") in {"", "none", None}:
                    diagnostics["skip_reason"] = "memory_maintainer_empty"
                else:
                    diagnostics["skip_reason"] = str(diagnostics["memory_maintainer_skip_reason"])
                return self._build_result("no_memory", diagnostics=diagnostics)
            entries = await memory_manager.replace_long_term_section(
                maintained_items,
                source=source,
                tags=["auto", "distilled"],
                section=LONG_TERM_PREFERENCES_SECTION,
            )
            written_count = len(entries)
        else:
            written_count = 0
            for item in long_term_items:
                await memory_manager.write_long_term(
                    item,
                    source=source,
                    tags=["auto", "distilled"],
                    section=LONG_TERM_PREFERENCES_SECTION,
                )
                written_count += 1

        diagnostics["written_long_term_count"] = written_count
        diagnostics["skip_reason"] = "none"
        return self._build_result("ok", long_term_count=written_count, diagnostics=diagnostics)

    @staticmethod
    def _resolve_config() -> Any:
        """Return automatic-memory-write config with schema defaults applied."""
        return getattr(get_config().memory, "auto_write", None)

    async def _distill_with_model(
        self,
        *,
        run_single: RunSingleCallable,
        deps: SkillDeps,
        agent: Any,
        user_message: str,
        assistant_message: str,
        final_messages: list[dict[str, Any]],
        timeout_ms: int,
    ) -> _DistillationResult:
        """Ask the model for a strict JSON memory distillation payload."""
        prompt = self._build_distiller_prompt(
            user_message=user_message,
            assistant_message=assistant_message,
            final_messages=final_messages,
        )
        system_prompt = (
            "You distill AtlasClaw conversation memory. Extract only durable "
            "user-experience preferences that should affect future conversations. "
            "Durable user-experience preferences include response language, response "
            "style, and the assistant nickname chosen by the user. "
            "Return JSON only with keys long_term and skip_reason. When memory exists, "
            'return {"long_term":["User prefers English replies."],"skip_reason":"none"}. '
            "When no durable user-experience preference exists, return "
            '{"long_term":[],"skip_reason":"no_durable_memory"}. '
            "Write long_term items in clear English. "
            "If the user asks for future assistant behavior and the assistant confirms "
            "that future behavior, include it in long_term unless it is sensitive data, "
            "a one-off request, a task-specific parameter, a task execution result, or "
            "tool/skill/provider routing. Use skip_reason=\"none\" only when long_term "
            "is non-empty. If long_term is empty, skip_reason must explain the semantic "
            "reason. Do not include raw conversation text, provider records, tool outputs, "
            "task execution summaries, one-off task parameters, or tool/skill/provider "
            "routing."
        )
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                run_single(
                    prompt,
                    deps,
                    system_prompt=system_prompt,
                    agent=agent,
                    allowed_tool_names=[],
                ),
                timeout=max(0.001, timeout_ms / 1000),
            )
        except asyncio.TimeoutError:
            return _DistillationResult(
                payload={"long_term": []},
                diagnostics=self._distiller_error_diagnostics(
                    started,
                    parse_status="timeout",
                    skip_reason="distiller_timeout",
                    error_type="TimeoutError",
                ),
            )
        except Exception as exc:
            return _DistillationResult(
                payload={"long_term": []},
                diagnostics=self._distiller_error_diagnostics(
                    started,
                    parse_status="exception",
                    skip_reason="distiller_exception",
                    error_type=type(exc).__name__,
                ),
            )
        return self._parse_distiller_json(raw, elapsed_ms=self._elapsed_ms(started))

    async def _maintain_memory_with_model(
        self,
        *,
        run_single: RunSingleCallable,
        deps: SkillDeps,
        agent: Any,
        memory_manager: Any,
        new_items: list[str],
        timeout_ms: int,
    ) -> _MaintenanceResult:
        """Ask the model to maintain the final long-term preference section."""
        existing_content = await self._read_existing_long_term_content(memory_manager)
        prompt = self._build_memory_maintenance_prompt(
            existing_content=existing_content,
            new_items=new_items,
        )
        system_prompt = (
            "You maintain AtlasClaw long-term user preferences. Return JSON only with "
            'shape {"preferences":["..."],"skip_reason":"none"}. Write every preference '
            "in clear English. Keep only durable user-experience preferences. Existing "
            "preferences are ordered oldest to newest, and new preferences are from the "
            "latest completed turn. Merge duplicates and paraphrases. If a new preference "
            "conflicts with an existing preference about the same user-experience setting, "
            "keep only the latest preference. Keep unrelated existing preferences. Do not "
            "include sensitive data, one-off requests, task-specific parameters, task "
            "execution results, raw conversation summaries, or tool/skill/provider routing."
        )
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                run_single(
                    prompt,
                    deps,
                    system_prompt=system_prompt,
                    agent=agent,
                    allowed_tool_names=[],
                ),
                timeout=max(0.001, timeout_ms / 1000),
            )
        except asyncio.TimeoutError:
            return _MaintenanceResult(
                preferences=[],
                diagnostics=self._memory_maintainer_error_diagnostics(
                    started,
                    parse_status="timeout",
                    skip_reason="memory_maintainer_timeout",
                    error_type="TimeoutError",
                ),
            )
        except Exception as exc:
            return _MaintenanceResult(
                preferences=[],
                diagnostics=self._memory_maintainer_error_diagnostics(
                    started,
                    parse_status="exception",
                    skip_reason="memory_maintainer_exception",
                    error_type=type(exc).__name__,
                ),
            )
        return self._parse_memory_maintenance_json(raw, elapsed_ms=self._elapsed_ms(started))

    @staticmethod
    def _build_distiller_prompt(
        *,
        user_message: str,
        assistant_message: str,
        final_messages: list[dict[str, Any]],
    ) -> str:
        """Build the compact JSON distillation prompt for one completed turn."""
        recent_tool_names: list[str] = []
        for message in final_messages[-8:]:
            if not isinstance(message, dict):
                continue
            tool_name = str(message.get("tool_name", "") or message.get("name", "") or "").strip()
            if tool_name and tool_name not in recent_tool_names:
                recent_tool_names.append(tool_name)
        tool_context = ", ".join(recent_tool_names[:6]) if recent_tool_names else "none"
        return (
            "Distill only user-experience preference memory from this completed turn.\n"
            f"User message:\n{user_message.strip()}\n\n"
            f"Assistant reply:\n{assistant_message.strip()}\n\n"
            f"Tools used: {tool_context}\n\n"
            "Return compact JSON only. Do not summarize completed operations or tool results."
        )

    @staticmethod
    def _build_memory_maintenance_prompt(
        *,
        existing_content: str,
        new_items: list[str],
    ) -> str:
        """Build a compact prompt for maintaining the Preferences section."""
        new_memory = "\n".join(f"- {item}" for item in new_items if str(item or "").strip())
        return (
            "Maintain the final Preferences section for MEMORY.md.\n"
            "Existing MEMORY.md content, with preferences ordered oldest to newest:\n"
            f"{existing_content.strip() or '(empty)'}\n\n"
            "New preferences from the latest completed turn:\n"
            f"{new_memory or '- (none)'}\n\n"
            "Return only the final preference lines as JSON. Do not include Markdown headers, "
            "bullets, citations, timestamps, or explanations."
        )

    def _parse_distiller_json(self, raw: str, *, elapsed_ms: int) -> _DistillationResult:
        """Parse the strict JSON payload returned by the distiller."""
        text = str(raw or "").strip()
        diagnostics = self._raw_output_diagnostics(text, elapsed_ms=elapsed_ms)
        if not text or text.startswith("[Error:"):
            diagnostics["json_parse_status"] = "empty" if not text else "error_prefix"
            diagnostics["skip_reason"] = "distiller_empty" if not text else "distiller_model_error"
            return _DistillationResult(payload={"long_term": []}, diagnostics=diagnostics)
        parsed, parse_status = self._parse_json_object(text)
        if parsed is None:
            diagnostics["json_parse_status"] = parse_status
            diagnostics["skip_reason"] = f"distiller_{parse_status}"
            return _DistillationResult(payload={"long_term": []}, diagnostics=diagnostics)
        long_term_items = self._normalize_json_list(parsed.get("long_term"))
        skip_reason = str(parsed.get("skip_reason", "") or "").strip()
        effective_skip_reason = skip_reason or ("none" if long_term_items else "distiller_inconsistent_empty")
        if not long_term_items and effective_skip_reason.lower() == "none":
            effective_skip_reason = "distiller_inconsistent_empty"
        diagnostics.update(
            {
                "json_parse_status": "ok",
                "parsed_long_term_count": len(long_term_items),
                "model_skip_reason": skip_reason,
                "skip_reason": effective_skip_reason,
            }
        )
        return _DistillationResult(payload={"long_term": long_term_items}, diagnostics=diagnostics)

    def _parse_memory_maintenance_json(self, raw: str, *, elapsed_ms: int) -> _MaintenanceResult:
        """Parse the strict JSON payload returned by the memory maintainer."""
        text = str(raw or "").strip()
        diagnostics = self._memory_maintainer_raw_output_diagnostics(text, elapsed_ms=elapsed_ms)
        if not text or text.startswith("[Error:"):
            diagnostics["memory_maintainer_json_parse_status"] = "empty" if not text else "error_prefix"
            diagnostics["memory_maintainer_skip_reason"] = (
                "memory_maintainer_empty" if not text else "memory_maintainer_model_error"
            )
            return _MaintenanceResult(preferences=[], diagnostics=diagnostics)
        parsed, parse_status = self._parse_json_object(text)
        if parsed is None:
            diagnostics["memory_maintainer_json_parse_status"] = parse_status
            diagnostics["memory_maintainer_skip_reason"] = f"memory_maintainer_{parse_status}"
            return _MaintenanceResult(preferences=[], diagnostics=diagnostics)
        preferences = self._normalize_json_list(parsed.get("preferences"))
        skip_reason = str(parsed.get("skip_reason", "") or "").strip()
        diagnostics.update(
            {
                "memory_maintainer_json_parse_status": "ok",
                "memory_maintainer_preference_count": len(preferences),
                "memory_maintainer_model_skip_reason": skip_reason,
                "memory_maintainer_skip_reason": skip_reason or "none",
            }
        )
        return _MaintenanceResult(preferences=preferences, diagnostics=diagnostics)

    @classmethod
    def _parse_json_object(cls, raw_text: str) -> tuple[dict[str, Any] | None, str]:
        """Parse a JSON object from raw model text and return a stable status."""
        text = cls._extract_json_object_text(raw_text)
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "invalid_json"
        if not isinstance(parsed, dict):
            return None, "invalid_shape"
        return parsed, "ok"

    @staticmethod
    def _extract_json_object_text(raw_text: str) -> str:
        """Extract a JSON object from plain or fenced model output."""
        text = str(raw_text or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fenced:
            return fenced.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start : end + 1]
        return text

    @staticmethod
    def _normalize_json_list(value: Any) -> list[str]:
        """Return string items from a distiller JSON array-like value."""
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                text = item.get("content") or item.get("memory") or item.get("text")
                if isinstance(text, str):
                    items.append(text)
        return items

    def _sanitize_items(
        self,
        raw_items: list[str],
        *,
        max_items: int,
        max_chars: int,
    ) -> list[str]:
        """Filter empty or duplicate items and enforce mechanical write limits."""
        if max_items <= 0:
            return []
        sanitized: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            item = self._compact_text(raw, max_chars)
            if not item:
                continue
            normalized = item.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            sanitized.append(item)
            if len(sanitized) >= max_items:
                break
        return sanitized

    @staticmethod
    def _compact_text(value: str, max_chars: int) -> str:
        """Collapse whitespace and cap one memory item to the configured budget."""
        return compact_text(value, max_chars)

    @staticmethod
    def _base_diagnostics() -> dict[str, Any]:
        """Return the common non-sensitive diagnostic fields for one attempt."""
        return {
            "status": "",
            "distiller_attempted": False,
            "distiller_elapsed_ms": 0,
            "raw_output_chars": 0,
            "raw_output_sha256": "",
            "json_parse_status": "not_attempted",
            "parsed_long_term_count": 0,
            "model_skip_reason": "",
            "sanitized_long_term_count": 0,
            "written_long_term_count": 0,
            "maintained_long_term_count": 0,
            "memory_maintainer_attempted": False,
            "memory_maintainer_elapsed_ms": 0,
            "memory_maintainer_raw_output_chars": 0,
            "memory_maintainer_raw_output_sha256": "",
            "memory_maintainer_json_parse_status": "not_attempted",
            "memory_maintainer_preference_count": 0,
            "memory_maintainer_model_skip_reason": "",
            "memory_maintainer_skip_reason": "",
            "skip_reason": "",
            "memory_path": "",
            "section": LONG_TERM_PREFERENCES_SECTION,
        }

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        """Return elapsed milliseconds from a monotonic start timestamp."""
        return max(0, int((time.monotonic() - started) * 1000))

    def _distiller_error_diagnostics(
        self,
        started: float,
        *,
        parse_status: str,
        skip_reason: str,
        error_type: str,
    ) -> dict[str, Any]:
        """Build diagnostics for distiller timeout or exception paths."""
        return {
            "distiller_attempted": True,
            "distiller_elapsed_ms": self._elapsed_ms(started),
            "json_parse_status": parse_status,
            "skip_reason": skip_reason,
            "model_skip_reason": "",
            "error_type": error_type,
        }

    def _raw_output_diagnostics(self, raw: str, *, elapsed_ms: int) -> dict[str, Any]:
        """Describe distiller output without persisting the raw text."""
        return {
            "distiller_attempted": True,
            "distiller_elapsed_ms": int(elapsed_ms),
            "raw_output_chars": len(raw),
            "raw_output_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else "",
        }

    @staticmethod
    def _has_existing_memory(memory_manager: Any) -> bool:
        """Return whether the user's long-term memory file already exists."""
        path = getattr(memory_manager, "long_term_path", None)
        return bool(getattr(path, "exists", lambda: False)())

    @staticmethod
    async def _read_existing_long_term_content(memory_manager: Any) -> str:
        """Read existing long-term memory content for model maintenance."""
        path = getattr(memory_manager, "long_term_path", None)
        if path is None or not getattr(path, "exists", lambda: False)():
            return ""
        try:
            return await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError:
            return ""

    def _memory_maintainer_error_diagnostics(
        self,
        started: float,
        *,
        parse_status: str,
        skip_reason: str,
        error_type: str,
    ) -> dict[str, Any]:
        """Build diagnostics for memory-maintainer timeout or exception paths."""
        return {
            "memory_maintainer_attempted": True,
            "memory_maintainer_elapsed_ms": self._elapsed_ms(started),
            "memory_maintainer_json_parse_status": parse_status,
            "memory_maintainer_skip_reason": skip_reason,
            "memory_maintainer_model_skip_reason": "",
            "memory_maintainer_error_type": error_type,
        }

    @staticmethod
    def _memory_maintainer_raw_output_diagnostics(raw: str, *, elapsed_ms: int) -> dict[str, Any]:
        """Describe memory-maintainer output without persisting the raw text."""
        return {
            "memory_maintainer_attempted": True,
            "memory_maintainer_elapsed_ms": int(elapsed_ms),
            "memory_maintainer_raw_output_chars": len(raw),
            "memory_maintainer_raw_output_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if raw
            else "",
        }

    @staticmethod
    def _display_memory_path(memory_manager: Any) -> str:
        """Return a workspace-relative memory path when supported."""
        path = getattr(memory_manager, "long_term_path", None)
        if path is None:
            return ""
        display_path = getattr(memory_manager, "display_path", None)
        if callable(display_path):
            try:
                return str(display_path(path))
            except Exception:
                return str(path)
        return str(path)

    @staticmethod
    def _build_result(
        status: str,
        *,
        long_term_count: int = 0,
        diagnostics: dict[str, Any],
    ) -> AutoMemoryWriteResult:
        """Attach status to the diagnostic payload returned to the runner."""
        payload = dict(diagnostics)
        payload["status"] = status
        return AutoMemoryWriteResult(
            status=status,
            long_term_count=long_term_count,
            diagnostics=payload,
        )


automatic_memory_write_service = AutomaticMemoryWriteService()
