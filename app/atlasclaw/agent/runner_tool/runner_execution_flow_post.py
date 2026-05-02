# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, unquote, urlparse

from app.atlasclaw.agent.plaintext_tool_calls import looks_like_plaintext_tool_call_attempt
from app.atlasclaw.agent.runner_tool.runner_execution_payload import (
    build_direct_answer_recovery_payload,
    build_lookup_dump_recovery_payload,
    build_tool_failure_fallback_payload,
    provider_auth_diagnostic_user_message,
    select_provider_auth_diagnostic,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import messages_satisfy_artifact_goal
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import has_hidden_lookup_result_content
from app.atlasclaw.agent.runner_tool.runner_tool_messages import overlay_synthetic_tool_messages
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    tool_required_turn_has_real_execution,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.tool_gate_models import ToolPolicyMode

logger = logging.getLogger(__name__)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class RunnerExecutionFlowPostMixin:
    @staticmethod
    def _strip_workspace_download_link_paths(answer: str, download_keys: list[str] | set[str]) -> str:
        """Replace generated-file markdown links with filename-only text."""
        if not answer or not download_keys:
            return answer

        normalized_keys = {
            str(key or "").strip().replace("\\", "/")
            for key in download_keys
            if str(key or "").strip()
        }
        if not normalized_keys:
            return answer
        download_names = {Path(key).name for key in normalized_keys if Path(key).name}

        def _target_matches_download(raw_target: str) -> str:
            target = str(raw_target or "").strip().strip("<>").replace("\\", "/")
            if not target:
                return ""
            parsed = urlparse(target)
            if parsed.query:
                params = parse_qs(parsed.query)
                for key in ("path", "file", "filename"):
                    for item in params.get(key, []):
                        item_name = Path(unquote(item).replace("\\", "/")).name
                        if item_name in download_names:
                            return item_name
            if parsed.scheme:
                if parsed.scheme.lower() in {"http", "https", "mailto"}:
                    return ""
                if target.lower().startswith("workspace://"):
                    target = target.split("://", 1)[1]
                else:
                    target = target.split(":", 1)[1]
            if target.startswith("./"):
                target = target[2:]
            target = unquote(target).strip()
            if not target:
                return ""
            target_name = Path(target).name
            if target in normalized_keys or target_name in download_names:
                return target_name
            return ""

        def _replace(match: re.Match[str]) -> str:
            filename = _target_matches_download(match.group(2))
            return filename or match.group(0)

        return _MARKDOWN_LINK_RE.sub(_replace, answer)

    def _schedule_background_post_success_task(self, task: asyncio.Task[Any]) -> None:
        background_tasks = getattr(self, "_background_post_success_tasks", None)
        if background_tasks is None:
            background_tasks = set()
            setattr(self, "_background_post_success_tasks", background_tasks)
        background_tasks.add(task)
        task.add_done_callback(self._on_background_post_success_done)

    def _on_background_post_success_done(self, task: asyncio.Task[Any]) -> None:
        background_tasks = getattr(self, "_background_post_success_tasks", None)
        if isinstance(background_tasks, set):
            background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Background post-success side effect failed: %s", exc)

    async def _await_background_post_success_tasks(self) -> None:
        background_tasks = list(getattr(self, "_background_post_success_tasks", set()) or [])
        if not background_tasks:
            return
        await asyncio.gather(*background_tasks, return_exceptions=True)

    def _schedule_post_success_side_effects(
        self,
        *,
        state: dict[str, Any],
        _log_step: Any,
        session_key: str,
        run_id: str,
        session_manager: Any,
        persist_messages: list[dict[str, Any]],
        final_assistant: str,
        final_messages: list[dict[str, Any]],
        session: Any,
        user_message: str,
        system_prompt: str,
        tool_call_summaries: list[dict[str, Any]],
    ) -> None:
        async def _run_post_success_side_effects() -> None:
            _log_step("post_success_background_side_effects_start")
            llm_completed_task = asyncio.create_task(
                self.runtime_events.trigger_llm_completed(
                    session_key=session_key,
                    run_id=run_id,
                    assistant_message=final_assistant,
                )
            )
            persist_transcript_task = asyncio.create_task(
                session_manager.persist_transcript(session_key, persist_messages)
            )
            llm_completed_result, persist_result = await asyncio.gather(
                llm_completed_task,
                persist_transcript_task,
                return_exceptions=True,
            )
            if isinstance(llm_completed_result, Exception):
                logger.exception("post_success_llm_completed failed")
                _log_step("post_success_llm_completed_error", error=str(llm_completed_result))
            else:
                _log_step("post_success_llm_completed_done")
            if isinstance(persist_result, Exception):
                logger.exception("post_success_persist_transcript failed")
                _log_step("post_success_persist_transcript_error", error=str(persist_result))
            else:
                _log_step("post_success_persist_transcript_done")

            _log_step("post_success_finalize_title_start")
            try:
                await self._maybe_finalize_title(
                    session_manager=session_manager,
                    session_key=session_key,
                    session=session,
                    final_messages=final_messages,
                    user_message=user_message,
                )
                _log_step("post_success_finalize_title_done")
                state["session_title"] = str(getattr(session, "title", "") or "")
            except Exception as exc:
                logger.exception("post_success_finalize_title failed")
                _log_step("post_success_finalize_title_error", error=str(exc))

            _log_step("post_success_run_context_ready_start")
            try:
                await self.runtime_events.trigger_run_context_ready(
                    session_key=session_key,
                    run_id=run_id,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=state.get("context_history_for_hooks") or [],
                    assistant_message=final_assistant,
                    tool_calls=tool_call_summaries,
                    run_status="completed",
                    session_title=state.get("session_title"),
                )
                _log_step("post_success_run_context_ready_done")
            except Exception as exc:
                logger.exception("post_success_run_context_ready failed")
                _log_step("post_success_run_context_ready_error", error=str(exc))

        self._schedule_background_post_success_task(
            asyncio.create_task(_run_post_success_side_effects())
        )

    def _build_missing_tool_evidence_fallback_reasons(
        self,
        *,
        state: dict[str, Any],
        missing_required_tools: list[str],
        final_messages: list[dict[str, Any]],
        start_index: int,
        planned_tool_names: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if missing_required_tools:
            reasons.append(
                "Tools without usable results: " + ", ".join(
                    str(name).strip() for name in missing_required_tools if str(name).strip()
                )
            )

        repeated_tool_failure = state.get("repeated_tool_failure")
        if isinstance(repeated_tool_failure, dict):
            tool_name = str(repeated_tool_failure.get("tool_name", "") or "").strip()
            error_text = str(repeated_tool_failure.get("error", "") or "").strip()
            if tool_name and error_text:
                reasons.append(f"Repeated tool failure for {tool_name}: {error_text}")

        repeated_tool_no_progress = state.get("repeated_tool_no_progress")
        if isinstance(repeated_tool_no_progress, dict):
            tool_name = str(repeated_tool_no_progress.get("tool_name", "") or "").strip()
            if tool_name:
                reasons.append(
                    f"Repeated tool execution for {tool_name} did not add new evidence."
                )

        repeated_tool_loop = state.get("repeated_tool_loop")
        if isinstance(repeated_tool_loop, dict):
            tool_name = str(repeated_tool_loop.get("tool_name", "") or "").strip()
            if tool_name:
                reasons.append(
                    f"Runtime stopped a repeated tool loop for {tool_name} before evidence converged."
                )

        safe_start = max(0, min(int(start_index), len(final_messages)))
        seen_error_signatures: set[str] = set()
        extract_tool_error = getattr(self, "_extract_tool_error_signature", None)
        for message in final_messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            payload_items: list[tuple[str, Any]] = []
            if role in {"tool", "toolresult", "tool_result"}:
                payload_items.append(
                    (
                        str(message.get("tool_name", "") or message.get("name", "")).strip() or "tool",
                        message.get("content"),
                    )
                )
            for result in message.get("tool_results", []) or []:
                if isinstance(result, dict):
                    payload_items.append(
                        (
                            str(result.get("tool_name", "") or result.get("name", "")).strip() or "tool",
                            result.get("content", result),
                        )
                    )
            for tool_name, payload in payload_items:
                signature = extract_tool_error(payload) if callable(extract_tool_error) else ""
                if not signature or signature in seen_error_signatures:
                    continue
                seen_error_signatures.add(signature)
                reasons.append(f"{tool_name} error: {signature}")

        terminal_no_evidence = getattr(self, "_tool_results_are_terminal_no_evidence", None)
        if (
            not reasons
            and planned_tool_names
            and callable(terminal_no_evidence)
            and terminal_no_evidence(
                messages=final_messages,
                start_index=start_index,
                target_tool_names=planned_tool_names,
            )
        ):
            reasons.append("The latest tool results ended in no-results responses.")

        return reasons

    def _should_attempt_missing_tool_evidence_fallback(
        self,
        *,
        state: dict[str, Any],
        tool_required_has_real_execution: bool,
        missing_required_tools: list[str],
    ) -> bool:
        decision = state.get("tool_gate_decision")
        if getattr(decision, "policy", ToolPolicyMode.ANSWER_DIRECT) is ToolPolicyMode.MUST_USE_TOOL:
            return False
        if tool_required_has_real_execution and missing_required_tools:
            return True
        return any(
            isinstance(state.get(key), dict)
            for key in ("repeated_tool_failure", "repeated_tool_no_progress", "repeated_tool_loop")
        )

    def _collect_recent_tool_result_records(
        self,
        *,
        final_messages: list[dict[str, Any]],
        start_index: int,
        max_items: int = 3,
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        extract_records = getattr(self, "_extract_tool_result_records_from_messages", None)
        if not callable(extract_records):
            return tool_results
        for record in extract_records(
            messages=final_messages,
            start_index=start_index,
            max_items=max_items,
        ) or []:
            if not isinstance(record, dict):
                continue
            content = str(record.get("text", "") or "").strip()
            if not content:
                continue
            tool_results.append(
                {
                    "tool_name": str(record.get("tool_name", "") or "tool").strip() or "tool",
                    "content": content,
                }
            )
        return tool_results

    async def _generate_missing_tool_evidence_fallback_answer(
        self,
        *,
        user_message: str,
        deps: Any,
        final_messages: list[dict[str, Any]],
        start_index: int,
        tool_call_summaries: list[dict[str, Any]],
        failure_reasons: list[str],
        agent: Any,
    ) -> str:
        tool_results = self._collect_recent_tool_result_records(
            final_messages=final_messages,
            start_index=start_index,
            max_items=3,
        )
        payload = build_tool_failure_fallback_payload(
            user_message=user_message,
            tool_results=tool_results,
            attempted_tools=tool_call_summaries,
            failure_reasons=failure_reasons,
            provider_auth_diagnostic=select_provider_auth_diagnostic(
                extra=getattr(deps, "extra", {}),
                attempted_tools=tool_call_summaries,
                failure_reasons=failure_reasons,
                tool_results=tool_results,
            ),
        )
        run_single = getattr(self, "run_single", None)
        if not callable(run_single):
            return ""
        raw_output = await run_single(
            payload["user_prompt"],
            deps,
            system_prompt=payload["system_prompt"],
            agent=agent,
            allowed_tool_names=[],
        )
        normalized = str(raw_output or "").strip()
        if not normalized or normalized.startswith("[Error:"):
            return ""
        return normalized

    @staticmethod
    def _looks_like_plaintext_tool_call_attempt(text: str) -> bool:
        return looks_like_plaintext_tool_call_attempt(text)

    async def _generate_direct_answer_recovery_answer(
        self,
        *,
        user_message: str,
        invalid_output: str,
        deps: Any,
        agent: Any,
    ) -> str:
        payload = build_direct_answer_recovery_payload(
            user_message=user_message,
            invalid_output=invalid_output,
        )
        run_single = getattr(self, "run_single", None)
        if not callable(run_single):
            return ""
        raw_output = await run_single(
            payload["user_prompt"],
            deps,
            system_prompt=payload["system_prompt"],
            agent=agent,
            allowed_tool_names=[],
        )
        normalized = str(raw_output or "").strip()
        if not normalized or normalized.startswith("[Error:"):
            return ""
        if self._looks_like_plaintext_tool_call_attempt(normalized):
            return ""
        return normalized

    @staticmethod
    def _looks_like_raw_lookup_dump(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        lowered = normalized.lower()
        if '"_internal"' in normalized or '"returncode"' in normalized or '"success"' in normalized:
            return True
        if lowered.startswith("found ") and ("catalog" in lowered or "business group" in lowered):
            return True
        try:
            parsed = json.loads(normalized)
        except Exception:
            blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
            if not blocks:
                return False
            try:
                return all(
                    RunnerExecutionFlowPostMixin._parsed_lookup_payload_seems_internal(
                        json.loads(block)
                    )
                    for block in blocks
                )
            except Exception:
                return False
        return RunnerExecutionFlowPostMixin._parsed_lookup_payload_seems_internal(parsed)

    @staticmethod
    def _parsed_lookup_payload_seems_internal(payload: Any) -> bool:
        if isinstance(payload, dict):
            keys = {str(key).strip() for key in payload.keys()}
            if {"_internal", "success"} <= keys:
                return True
            if "_internal" in keys or "returncode" in keys:
                return True
            if "catalogs" in keys and ("success" in keys or "returncode" in keys):
                return True
            return False

        if isinstance(payload, list) and payload:
            if not all(isinstance(item, dict) for item in payload):
                return False
            allowed_keys = {"index", "id", "name", "code"}
            return all(
                set(str(key).strip() for key in item.keys()).issubset(allowed_keys)
                and "name" in item
                for item in payload
            )

        return False

    @staticmethod
    def _has_silent_lookup_results(
        *,
        messages: list[dict[str, Any]],
        start_index: int,
    ) -> bool:
        safe_start = max(0, min(int(start_index), len(messages)))
        for message in messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role in {"tool", "toolresult", "tool_result"}:
                if has_hidden_lookup_result_content(message.get("content")):
                    return True
            for result in message.get("tool_results", []) or []:
                if not isinstance(result, dict):
                    continue
                if has_hidden_lookup_result_content(result.get("content", result)):
                    return True
        return False

    def _extract_lookup_workflow_notes(
        self,
        *,
        final_messages: list[dict[str, Any]],
        start_index: int,
        invalid_output: str,
        max_items: int = 3,
    ) -> list[str]:
        safe_start = max(0, min(int(start_index), len(final_messages)))
        invalid_normalized = str(invalid_output or "").strip()
        notes: list[str] = []
        seen: set[str] = set()
        for message in final_messages[safe_start:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip().lower()
            if role != "assistant":
                continue
            content = str(message.get("content", "") or "").strip()
            if not content or content == invalid_normalized:
                continue
            if self._looks_like_raw_lookup_dump(content):
                continue
            if self._looks_like_plaintext_tool_call_attempt(content):
                continue
            signature = content[:240]
            if signature in seen:
                continue
            seen.add(signature)
            notes.append(content)
            if len(notes) >= max(1, int(max_items)):
                break
        return notes

    async def _generate_lookup_dump_recovery_answer(
        self,
        *,
        user_message: str,
        invalid_output: str,
        final_messages: list[dict[str, Any]],
        start_index: int,
        deps: Any,
        agent: Any,
    ) -> str:
        tool_results: list[dict[str, Any]] = []
        extract_records = getattr(self, "_extract_tool_result_records_from_messages", None)
        if callable(extract_records):
            for record in extract_records(
                messages=final_messages,
                start_index=start_index,
                max_items=3,
            ) or []:
                if not isinstance(record, dict):
                    continue
                content = str(record.get("text", "") or "").strip()
                if not content:
                    continue
                tool_results.append(
                    {
                        "tool_name": str(record.get("tool_name", "") or "tool").strip() or "tool",
                        "content": content,
                    }
                )
        workflow_notes = self._extract_lookup_workflow_notes(
            final_messages=final_messages,
            start_index=start_index,
            invalid_output=invalid_output,
        )
        payload = build_lookup_dump_recovery_payload(
            user_message=user_message,
            invalid_output=invalid_output,
            tool_results=tool_results,
            workflow_notes=workflow_notes,
        )
        run_single = getattr(self, "run_single", None)
        if not callable(run_single):
            return ""
        raw_output = await run_single(
            payload["user_prompt"],
            deps,
            system_prompt=payload["system_prompt"],
            agent=agent,
            allowed_tool_names=[],
        )
        normalized = str(raw_output or "").strip()
        if not normalized or normalized.startswith("[Error:"):
            return ""
        if self._looks_like_raw_lookup_dump(normalized):
            return ""
        return normalized

    async def _process_agent_run_outcome(
        self,
        *,
        agent_run: Any,
        state: dict[str, Any],
        _log_step: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Resolve final assistant output, persist transcript, and emit terminal events."""
        start_time = float(state.get("start_time") or 0.0)
        session_key = state.get("session_key")
        session_manager = state.get("session_manager")
        session = state.get("session")
        run_id = state.get("run_id")
        user_message = state.get("user_message")
        system_prompt = state.get("system_prompt")
        deps = state.get("deps")
        max_tool_calls = int(state.get("max_tool_calls") or 0)
        timeout_seconds = float(state.get("timeout_seconds") or 0.0)
        token_failover_attempt = int(state.get("_token_failover_attempt") or 0)
        emit_lifecycle_bounds = bool(state.get("_emit_lifecycle_bounds"))
        selected_token_id = state.get("selected_token_id")
        release_slot = state.get("release_slot")
        tool_execution_retry_count = int(state.get("tool_execution_retry_count") or 0)

        try:
            raw_final_messages = agent_run.all_messages()
        except Exception:
            raw_final_messages = list(state.get("message_history") or []) + [
                {"role": "user", "content": user_message}
            ]
        runtime_final_messages = self.history.normalize_messages(raw_final_messages)
        final_messages = self._merge_runtime_messages_with_session_prefix(
            session_message_history=state.get("session_message_history") or [],
            runtime_messages=runtime_final_messages,
            runtime_base_history_len=int(state.get("runtime_base_history_len") or 0),
        )
        final_messages = overlay_synthetic_tool_messages(
            messages=final_messages,
            synthetic_tool_messages=list(state.get("synthetic_tool_messages") or []),
            start_index=int(state.get("persist_run_output_start_index") or state.get("run_output_start_index") or 0),
        )
        state["latest_agent_messages"] = list(final_messages)

        persist_override_messages = state.get("persist_override_messages")
        persist_override_base_len = int(state.get("persist_override_base_len") or 0)
        if persist_override_messages is not None:
            if len(final_messages) > persist_override_base_len > 0:
                final_messages = persist_override_messages + final_messages[persist_override_base_len:]
            else:
                final_messages = persist_override_messages
            state["run_output_start_index"] = len(persist_override_messages)
            final_messages = overlay_synthetic_tool_messages(
                messages=final_messages,
                synthetic_tool_messages=list(state.get("synthetic_tool_messages") or []),
                start_index=int(state.get("run_output_start_index") or 0),
            )

        run_output_start_index = int(state.get("run_output_start_index") or 0)
        persist_run_output_start_index = int(
            state.get("persist_run_output_start_index") or run_output_start_index
        )
        final_assistant = self._extract_latest_assistant_from_messages(
            messages=final_messages,
            start_index=persist_run_output_start_index,
        )

        buffered_assistant_events = state.get("buffered_assistant_events") or []
        tool_intent_plan = state.get("tool_intent_plan")
        tool_execution_required = bool(state.get("tool_execution_required")) or turn_action_requires_tool_execution(
            tool_intent_plan
        )
        tool_call_summaries = state.get("tool_call_summaries") or []
        if buffered_assistant_events and tool_execution_required:
            buffered_assistant_events.clear()
        elif buffered_assistant_events and not final_assistant:
            if not tool_call_summaries and not tool_execution_required:
                while buffered_assistant_events:
                    event = buffered_assistant_events.pop(0)
                    if event.type == "assistant":
                        final_assistant += event.content
                        state["assistant_output_streamed"] = True
                    yield event
                state.get("thinking_emitter").assistant_emitted = bool(final_assistant)

        if final_messages:
            inferred_tool_calls = self._collect_tool_call_summaries_from_messages(
                messages=final_messages,
                start_index=persist_run_output_start_index,
            )
            if inferred_tool_calls:
                existing_signatures: set[tuple[str, str]] = set()
                for item in tool_call_summaries:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "") or "").strip()
                    if not name:
                        continue
                    args = item.get("args")
                    args_signature = str(sorted(args.items())) if isinstance(args, dict) else ""
                    existing_signatures.add((name, args_signature))
                for item in inferred_tool_calls:
                    name = str(item.get("name", "") or "").strip()
                    if not name:
                        continue
                    args = item.get("args")
                    args_signature = str(sorted(args.items())) if isinstance(args, dict) else ""
                    signature = (name, args_signature)
                    if signature in existing_signatures:
                        continue
                    existing_signatures.add(signature)
                    tool_call_summaries.append(item)

        model_stream_timed_out = bool(state.get("model_stream_timed_out"))
        model_timeout_error_message = str(state.get("model_timeout_error_message") or "")
        current_model_attempt = int(state.get("current_model_attempt") or 0)

        tool_required_has_real_execution = tool_required_turn_has_real_execution(
            intent_plan=tool_intent_plan,
            tool_call_summaries=tool_call_summaries,
            final_messages=final_messages,
            start_index=persist_run_output_start_index,
            executed_tool_names=state.get("executed_tool_names"),
        )
        artifact_goal = state.get("artifact_goal")
        workspace_path = str(getattr(session_manager, "workspace_path", "") or "").strip()
        user_id = str(getattr(getattr(deps, "user_info", None), "user_id", "") or "").strip()
        artifact_completion_missing = bool(artifact_goal) and not messages_satisfy_artifact_goal(
            messages=final_messages,
            start_index=persist_run_output_start_index,
            target_tool_names=[
                str(item.get("name", "") or "").strip()
                for item in tool_call_summaries
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            ],
            artifact_goal=artifact_goal,
            workspace_path=workspace_path,
            user_id=user_id,
        )
        has_workspace_download_evidence = bool(state.get("workspace_download_reference_keys"))
        artifact_download_ready = (
            bool(artifact_goal)
            and has_workspace_download_evidence
            and not artifact_completion_missing
        )
        preferred_tool_only_answer = ""
        if tool_required_has_real_execution and not artifact_download_ready:
            candidate_tool_only_answer = self._build_tool_only_markdown_answer_from_messages(
                messages=final_messages,
                start_index=persist_run_output_start_index,
            )
            if candidate_tool_only_answer and not artifact_completion_missing:
                preferred_tool_only_answer = candidate_tool_only_answer
                if bool(state.get("force_tool_only_finalize")):
                    final_assistant = preferred_tool_only_answer
        missing_required_tools = self._missing_required_tool_names(
            decision=state.get("tool_gate_decision"),
            match_result=state.get("tool_match_result"),
            intent_plan=tool_intent_plan,
            tool_call_summaries=tool_call_summaries,
            available_tools=state.get("available_tools"),
            final_messages=final_messages,
            run_output_start_index=persist_run_output_start_index,
        )
        planned_tool_names = [
            str(item.get("name", "") or "").strip()
            for item in tool_call_summaries
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        ]
        provider_auth_failure_needs_fallback = False
        if any(
            isinstance(state.get(key), dict)
            for key in ("repeated_tool_failure", "repeated_tool_no_progress", "repeated_tool_loop")
        ):
            fallback_reasons_for_auth = self._build_missing_tool_evidence_fallback_reasons(
                state=state,
                missing_required_tools=missing_required_tools,
                final_messages=final_messages,
                start_index=persist_run_output_start_index,
                planned_tool_names=planned_tool_names,
            )
            provider_auth_failure_needs_fallback = select_provider_auth_diagnostic(
                extra=getattr(deps, "extra", {}),
                attempted_tools=tool_call_summaries,
                failure_reasons=fallback_reasons_for_auth,
                tool_results=self._collect_recent_tool_result_records(
                    final_messages=final_messages,
                    start_index=persist_run_output_start_index,
                    max_items=3,
                ),
            ) is not None

        should_fail_for_missing_evidence = artifact_completion_missing or (
            tool_execution_required
            and (
                not tool_required_has_real_execution
                or bool(missing_required_tools)
                or (
                    not has_workspace_download_evidence
                    and (
                        isinstance(state.get("repeated_tool_failure"), dict)
                        or isinstance(state.get("repeated_tool_no_progress"), dict)
                        or isinstance(state.get("repeated_tool_loop"), dict)
                    )
                )
            )
        ) or provider_auth_failure_needs_fallback
        should_block_assistant_emit = should_fail_for_missing_evidence

        if model_stream_timed_out and not final_assistant.strip():
            failure_message = (
                model_timeout_error_message
                or "The model stream timed out before producing a usable response."
            )
            safe_messages = self._sanitize_turn_messages_for_persistence(
                messages=final_messages,
                start_index=persist_run_output_start_index,
                final_assistant="",
                clear_tool_planning_text=tool_execution_required,
            )
            await session_manager.persist_transcript(session_key, safe_messages)
            await self.runtime_events.trigger_run_context_ready(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
                system_prompt=system_prompt,
                message_history=state.get("context_history_for_hooks") or [],
                assistant_message="",
                tool_calls=tool_call_summaries,
                run_status="failed",
                error=failure_message,
                session_title=state.get("session_title"),
            )
            state["run_failed"] = True
            await self.runtime_events.trigger_llm_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            await self.runtime_events.trigger_run_failed(
                session_key=session_key,
                run_id=run_id,
                error=failure_message,
            )
            yield StreamEvent.runtime_update(
                "failed",
                failure_message,
                metadata={
                    "phase": "final",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            yield StreamEvent.error_event(failure_message)
            buffered_assistant_events.clear()
            state["should_stop"] = True
            return

        assistant_output_streamed = bool(state.get("assistant_output_streamed"))
        allow_agent_result_fallback = not tool_execution_required
        if not assistant_output_streamed and not should_block_assistant_emit:
            if (
                allow_agent_result_fallback
                and not final_assistant
                and hasattr(agent_run, "result")
                and agent_run.result
            ):
                result = agent_run.result
                if hasattr(result, "response") and result.response:
                    response = result.response
                    if hasattr(response, "parts"):
                        for part in response.parts:
                            part_kind = getattr(part, "part_kind", "")
                            if part_kind != "thinking" and hasattr(part, "content") and part.content:
                                content = str(part.content)
                                if content:
                                    final_assistant = content
                                    break
                    elif hasattr(response, "content") and response.content:
                        final_assistant = str(response.content)
                if not final_assistant and hasattr(result, "data") and result.data:
                    final_assistant = str(result.data)

            if not final_assistant:
                final_assistant = self._extract_latest_assistant_from_messages(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                )

        if (
            not tool_execution_required
            and not state.get("available_tools")
            and final_assistant
            and self._looks_like_plaintext_tool_call_attempt(final_assistant)
        ):
            yield StreamEvent.runtime_update(
                "warning",
                "Discarding invalid tool-style draft and regenerating a direct answer.",
                metadata={
                    "phase": "direct_answer_recovery",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            yield StreamEvent.runtime_update(
                "reasoning",
                "Recovering a normal answer without tools.",
                metadata={
                    "phase": "direct_answer_recovery",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            _log_step("direct_answer_recovery_start")
            try:
                recovered_answer = await self._generate_direct_answer_recovery_answer(
                    user_message=user_message,
                    invalid_output=final_assistant,
                    deps=deps,
                    agent=state.get("runtime_agent") or getattr(self, "agent", None),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("direct_answer_recovery failed: %s", exc)
                _log_step("direct_answer_recovery_error", error=str(exc))
            else:
                if recovered_answer.strip():
                    final_assistant = recovered_answer.strip()
                _log_step(
                    "direct_answer_recovery_done",
                    answered=bool(final_assistant.strip()),
                )

        if (
            final_assistant
            and self._looks_like_raw_lookup_dump(final_assistant)
            and self._has_silent_lookup_results(
                messages=final_messages,
                start_index=persist_run_output_start_index,
            )
        ):
            yield StreamEvent.runtime_update(
                "warning",
                "Discarding a raw lookup dump and regenerating a user-facing workflow response.",
                metadata={
                    "phase": "lookup_dump_recovery",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            yield StreamEvent.runtime_update(
                "reasoning",
                "Rewriting the lookup result into the next natural-language workflow step.",
                metadata={
                    "phase": "lookup_dump_recovery",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                },
            )
            _log_step("lookup_dump_recovery_start")
            try:
                recovered_lookup_answer = await self._generate_lookup_dump_recovery_answer(
                    user_message=user_message,
                    invalid_output=final_assistant,
                    final_messages=final_messages,
                    start_index=persist_run_output_start_index,
                    deps=deps,
                    agent=state.get("runtime_agent") or getattr(self, "agent", None),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("lookup_dump_recovery failed: %s", exc)
                _log_step("lookup_dump_recovery_error", error=str(exc))
            else:
                if recovered_lookup_answer.strip():
                    final_assistant = recovered_lookup_answer.strip()
                _log_step(
                    "lookup_dump_recovery_done",
                    answered=bool(final_assistant.strip()),
                )

        plaintext_tool_markup_attempt = bool(
            state.get("available_tools")
            and final_assistant
            and self._looks_like_plaintext_tool_call_attempt(final_assistant)
            and not any(
                isinstance(message, dict)
                and (
                    str(message.get("role", "") or "").strip().lower() in {"tool", "toolresult", "tool_result"}
                    or (
                        isinstance(message.get("tool_results"), list)
                        and bool(message.get("tool_results"))
                    )
                )
                for message in final_messages[persist_run_output_start_index:]
            )
        )
        if plaintext_tool_markup_attempt:
            preferred_tools = [
                str(item.get("name", "") or "").strip()
                for item in tool_call_summaries
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            ]
            yield StreamEvent.runtime_update(
                "warning",
                "Model returned plaintext tool-call markup instead of a real tool call.",
                metadata={
                    "phase": "plaintext_tool_call_retry",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                    "preferred_tools": preferred_tools,
                },
            )
            yield StreamEvent.runtime_update(
                "reasoning",
                "Retrying once with stricter structured tool-execution guidance.",
                metadata={
                    "phase": "plaintext_tool_call_retry",
                    "attempt": current_model_attempt,
                    "elapsed": round(time.monotonic() - start_time, 1),
                    "preferred_tools": preferred_tools,
                },
            )
            plaintext_tool_retry_started = False
            async for retry_event in self._retry_after_missing_tool_execution(
                session_key=session_key,
                user_message=user_message,
                deps=deps,
                release_slot=release_slot,
                selected_token_id=selected_token_id,
                start_time=start_time,
                max_tool_calls=max_tool_calls,
                timeout_seconds=timeout_seconds,
                token_failover_attempt=token_failover_attempt,
                emit_lifecycle_bounds=emit_lifecycle_bounds,
                failure_message="The model emitted plaintext tool-call markup instead of executing a real tool call.",
                preferred_tools=preferred_tools,
                tool_execution_retry_count=tool_execution_retry_count,
                allow_retry=True,
            ):
                plaintext_tool_retry_started = True
                yield retry_event
            if plaintext_tool_retry_started:
                state["release_slot"] = None
                state["selected_token_id"] = None
                state["should_stop"] = True
                buffered_assistant_events.clear()
                return
            final_assistant = ""
            should_fail_for_missing_evidence = True
            should_block_assistant_emit = True

        provider_auth_message = provider_auth_diagnostic_user_message(
            select_provider_auth_diagnostic(
                extra=getattr(deps, "extra", {}),
                attempted_tools=tool_call_summaries or list(state.get("executed_tool_names") or []),
                failure_reasons=[final_assistant] if final_assistant else [],
                tool_results=self._collect_recent_tool_result_records(
                    final_messages=final_messages,
                    start_index=persist_run_output_start_index,
                    max_items=3,
                ),
            )
        )
        if provider_auth_message and final_assistant:
            final_assistant = provider_auth_message

        if final_assistant:
            final_assistant = self._strip_workspace_download_link_paths(
                final_assistant,
                state.get("workspace_download_reference_keys") or [],
            )
        persist_messages = self._sanitize_turn_messages_for_persistence(
            messages=final_messages,
            start_index=persist_run_output_start_index,
            final_assistant=final_assistant,
            clear_tool_planning_text=tool_execution_required,
        )

        if not assistant_output_streamed and final_assistant and not should_block_assistant_emit:
            state.get("thinking_emitter").assistant_emitted = True
            assistant_output_streamed = True
            yield StreamEvent.assistant_delta(final_assistant)

        if should_fail_for_missing_evidence:
            repeated_tool_failure = state.get("repeated_tool_failure")
            failure_message = (
                "This turn required real tool execution, but the model did not execute any real tool calls."
            )
            if missing_required_tools:
                build_required_message = getattr(self, "_build_tool_evidence_required_message", None)
                if callable(build_required_message):
                    failure_message = build_required_message(
                        match_result=state.get("tool_match_result"),
                        missing_required_tools=missing_required_tools,
                    )
                else:
                    failure_message = (
                        "This request needs tool execution, but required tools did not "
                        f"succeed: {', '.join(missing_required_tools)}."
                    )
            if isinstance(repeated_tool_failure, dict):
                repeated_tool_name = str(repeated_tool_failure.get("tool_name", "") or "").strip()
                repeated_error = str(repeated_tool_failure.get("error", "") or "").strip()
                if repeated_tool_name and repeated_error:
                    failure_message = (
                        f"Tool execution repeated the same failure for {repeated_tool_name}: "
                        f"{repeated_error}"
                    )
            if artifact_completion_missing:
                artifact_label = str(
                    (artifact_goal or {}).get("label", "") or (artifact_goal or {}).get("kind", "")
                ).strip() or "requested artifact"
                failure_message = (
                    f"The runtime gathered intermediate data, but it did not actually produce the requested {artifact_label}."
                )
            fallback_reasons = self._build_missing_tool_evidence_fallback_reasons(
                state=state,
                missing_required_tools=missing_required_tools,
                final_messages=final_messages,
                start_index=persist_run_output_start_index,
                planned_tool_names=planned_tool_names,
            )
            if artifact_completion_missing:
                artifact_label = str(
                    (artifact_goal or {}).get("label", "") or (artifact_goal or {}).get("kind", "")
                ).strip() or "requested artifact"
                fallback_reasons.append(
                    f"The runtime never produced the requested {artifact_label}; only intermediate lookup results were available."
                )
            recovery_answer = ""
            answer_phase = "tool_failure_fallback"
            max_tool_policy_retries = int(self.TOOL_POLICY_MAX_RETRIES or 0)
            unsupported_tool_request_exhausted = (
                tool_execution_required
                and not tool_required_has_real_execution
                and tool_execution_retry_count >= max_tool_policy_retries
            )
            if unsupported_tool_request_exhausted:
                yield StreamEvent.runtime_update(
                    "reasoning",
                    "Generating response for unsupported tool request.",
                    metadata={
                        "phase": "unsupported_tool_request",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                _log_step(
                    "unsupported_tool_request_answer_start",
                    missing_required_tools=list(missing_required_tools),
                    available_tool_count=len(state.get("available_tools") or []),
                )
                try:
                    required_tool_names = [
                        str(name or "").strip()
                        for name in missing_required_tools
                        if str(name or "").strip()
                    ]
                    required_tool_name_set = set(required_tool_names)
                    relevant_tools = []
                    for tool in list(state.get("available_tools") or []):
                        if not isinstance(tool, dict):
                            continue
                        tool_name = str(tool.get("name", "") or "").strip()
                        if required_tool_name_set and tool_name not in required_tool_name_set:
                            continue
                        schema = {}
                        for schema_key in ("parameters_schema", "parameters", "input_schema", "schema"):
                            candidate = tool.get(schema_key)
                            if isinstance(candidate, dict):
                                schema = candidate
                                break
                        relevant_tools.append(
                            {
                                "name": tool_name,
                                "parameters_schema": schema,
                            }
                        )
                    facts = {
                        "user_request": str(user_message or "").strip(),
                        "required_tools_without_successful_execution": required_tool_names,
                        "available_tool_count": len(
                            [tool for tool in state.get("available_tools") or [] if isinstance(tool, dict)]
                        ),
                        "relevant_tools": relevant_tools,
                    }
                    run_single = getattr(self, "run_single", None)
                    if callable(run_single):
                        raw_output = await run_single(
                            (
                                "Structured facts for the unsupported tool request:\n"
                                f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
                                "Return only the final user-facing answer."
                            ),
                            deps,
                            system_prompt=(
                                "You are AtlasClaw. The runtime could not execute the "
                                "tool-backed operation requested in this turn. Write a concise "
                                "final answer in the user's language. Use only the structured "
                                "facts. State clearly that no action was executed. Explain that "
                                "the requested operation is not supported by the available "
                                "runtime tool, or that no matching runtime tool is available "
                                "when available_tool_count is zero. If enum values in a tool "
                                "schema show supported options, mention them. Do not claim a "
                                "tool ran or that any external object changed state."
                            ),
                            agent=state.get("runtime_agent") or getattr(self, "agent", None),
                            allowed_tool_names=[],
                        )
                        recovery_answer = str(raw_output or "").strip()
                        looks_like_tool_call = self._looks_like_plaintext_tool_call_attempt(
                            recovery_answer
                        )
                        if recovery_answer.startswith("[Error:") or looks_like_tool_call:
                            recovery_answer = ""
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.warning("unsupported_tool_request_answer failed: %s", exc)
                    _log_step("unsupported_tool_request_answer_error", error=str(exc))
                    recovery_answer = ""
                else:
                    _log_step(
                        "unsupported_tool_request_answer_done",
                        answered=bool(recovery_answer.strip()),
                    )
                answer_phase = "unsupported_tool_request"
            elif self._should_attempt_missing_tool_evidence_fallback(
                state=state,
                tool_required_has_real_execution=tool_required_has_real_execution,
                missing_required_tools=missing_required_tools,
            ) or artifact_completion_missing:
                yield StreamEvent.runtime_update(
                    "warning",
                    "Tool execution did not yield usable evidence. Falling back to a direct model answer.",
                    metadata={
                        "phase": "tool_failure_fallback",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.runtime_update(
                    "reasoning",
                    "Generating fallback answer from tool failure context.",
                    metadata={
                        "phase": "tool_failure_fallback",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                _log_step(
                    "tool_failure_fallback_start",
                    failure_reasons=list(fallback_reasons),
                )
                try:
                    recovery_answer = await self._generate_missing_tool_evidence_fallback_answer(
                        user_message=user_message,
                        deps=deps,
                        final_messages=final_messages,
                        start_index=persist_run_output_start_index,
                        tool_call_summaries=tool_call_summaries,
                        failure_reasons=fallback_reasons,
                        agent=state.get("runtime_agent") or getattr(self, "agent", None),
                    )
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.warning("tool_failure_fallback_answer failed: %s", exc)
                    _log_step("tool_failure_fallback_error", error=str(exc))
                    recovery_answer = ""
                else:
                    _log_step(
                        "tool_failure_fallback_done",
                        answered=bool(recovery_answer.strip()),
                    )
            if recovery_answer.strip():
                final_assistant = recovery_answer.strip()
                final_assistant = self._strip_workspace_download_link_paths(
                    final_assistant,
                    state.get("workspace_download_reference_keys") or [],
                )
                persist_messages = self._sanitize_turn_messages_for_persistence(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                    final_assistant=final_assistant,
                    clear_tool_planning_text=tool_execution_required,
                )
                if not assistant_output_streamed:
                    thinking_emitter = state.get("thinking_emitter")
                    if thinking_emitter is not None:
                        thinking_emitter.assistant_emitted = True
                    assistant_output_streamed = True
                    yield StreamEvent.assistant_delta(final_assistant)
                yield StreamEvent.runtime_update(
                    "answered",
                    "Final answer ready.",
                    metadata={
                        "phase": answer_phase,
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                state["answer_committed"] = True
                self._schedule_post_success_side_effects(
                    state=state,
                    _log_step=_log_step,
                    session_key=session_key,
                    run_id=run_id,
                    session_manager=session_manager,
                    persist_messages=persist_messages,
                    final_assistant=final_assistant,
                    final_messages=final_messages,
                    session=session,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    tool_call_summaries=tool_call_summaries,
                )
            else:
                preferred_tools = []
                if isinstance(getattr(deps, "extra", None), dict):
                    preferred_tools = list(deps.extra.get("tool_policy", {}).get("preferred_tools", []) or [])
                tool_execution_retried = False
                async for retry_event in self._retry_after_missing_tool_execution(
                    session_key=session_key,
                    user_message=user_message,
                    deps=deps,
                    release_slot=release_slot,
                    selected_token_id=selected_token_id,
                    start_time=start_time,
                    max_tool_calls=max_tool_calls,
                    timeout_seconds=timeout_seconds,
                    token_failover_attempt=token_failover_attempt,
                    emit_lifecycle_bounds=emit_lifecycle_bounds,
                    failure_message=failure_message,
                    preferred_tools=preferred_tools,
                    tool_execution_retry_count=tool_execution_retry_count,
                    allow_retry=True,
                ):
                    tool_execution_retried = True
                    yield retry_event
                if tool_execution_retried:
                    state["release_slot"] = None
                    state["selected_token_id"] = None
                    state["should_stop"] = True
                    return

                safe_messages = self._sanitize_turn_messages_for_persistence(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                    final_assistant="",
                    clear_tool_planning_text=tool_execution_required,
                )
                await session_manager.persist_transcript(session_key, safe_messages)
                await self.runtime_events.trigger_run_context_ready(
                    session_key=session_key,
                    run_id=run_id,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=state.get("context_history_for_hooks") or [],
                    assistant_message="",
                    tool_calls=tool_call_summaries,
                    run_status="failed",
                    error=failure_message,
                    session_title=state.get("session_title"),
                )
                state["run_failed"] = True
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                await self.runtime_events.trigger_run_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={
                        "phase": "final",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                buffered_assistant_events.clear()
                final_assistant = ""

        else:
            if not final_assistant.strip():
                if artifact_download_ready:
                    artifact_label = str(
                        (artifact_goal or {}).get("label", "")
                        or (artifact_goal or {}).get("kind", "")
                    ).strip() or "requested artifact"
                    download_names = [
                        Path(str(key)).name
                        for key in (state.get("workspace_download_reference_keys") or [])
                        if str(key).strip()
                    ]
                    if download_names:
                        final_assistant = (
                            f"The {artifact_label} is ready for download: "
                            f"{', '.join(download_names[:3])}."
                        )
                    else:
                        final_assistant = f"The {artifact_label} is ready for download."
                    final_assistant = self._strip_workspace_download_link_paths(
                        final_assistant,
                        state.get("workspace_download_reference_keys") or [],
                    )
                    persist_messages = self._sanitize_turn_messages_for_persistence(
                        messages=final_messages,
                        start_index=persist_run_output_start_index,
                        final_assistant=final_assistant,
                        clear_tool_planning_text=tool_execution_required,
                    )
                    if not assistant_output_streamed:
                        state.get("thinking_emitter").assistant_emitted = True
                        assistant_output_streamed = True
                        yield StreamEvent.assistant_delta(final_assistant)
                elif tool_required_has_real_execution:
                    tool_only_answer = preferred_tool_only_answer or self._build_tool_only_markdown_answer_from_messages(
                        messages=final_messages,
                        start_index=persist_run_output_start_index,
                    )
                    if tool_only_answer and not artifact_completion_missing:
                        final_assistant = tool_only_answer
                        final_assistant = self._strip_workspace_download_link_paths(
                            final_assistant,
                            state.get("workspace_download_reference_keys") or [],
                        )
                        persist_messages = self._sanitize_turn_messages_for_persistence(
                            messages=final_messages,
                            start_index=persist_run_output_start_index,
                            final_assistant=final_assistant,
                            clear_tool_planning_text=tool_execution_required,
                        )
                        if not assistant_output_streamed:
                            state.get("thinking_emitter").assistant_emitted = True
                            assistant_output_streamed = True
                            yield StreamEvent.assistant_delta(final_assistant)
            if not final_assistant.strip():
                state["run_failed"] = True
                failure_message = (
                    model_timeout_error_message
                    if model_stream_timed_out and model_timeout_error_message
                    else "The run ended without a usable final answer."
                )
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                await self.runtime_events.trigger_run_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                safe_messages = self._sanitize_turn_messages_for_persistence(
                    messages=final_messages,
                    start_index=persist_run_output_start_index,
                    final_assistant="",
                    clear_tool_planning_text=tool_execution_required,
                )
                await session_manager.persist_transcript(session_key, safe_messages)
                await self.runtime_events.trigger_run_context_ready(
                    session_key=session_key,
                    run_id=run_id,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=state.get("context_history_for_hooks") or [],
                    assistant_message="",
                    tool_calls=tool_call_summaries,
                    run_status="failed",
                    error=failure_message,
                    session_title=state.get("session_title"),
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={
                        "phase": "final",
                        "attempt": current_model_attempt,
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                buffered_assistant_events.clear()
                final_assistant = ""
            else:
                answered_elapsed = round(time.monotonic() - start_time, 1)
                yield StreamEvent.runtime_update(
                    "answered",
                    "Final answer ready.",
                    metadata={
                        "phase": "final",
                        "attempt": current_model_attempt,
                        "elapsed": answered_elapsed,
                    },
                )
                state["answer_committed"] = True
                self._schedule_post_success_side_effects(
                    state=state,
                    _log_step=_log_step,
                    session_key=session_key,
                    run_id=run_id,
                    session_manager=session_manager,
                    persist_messages=persist_messages,
                    final_assistant=final_assistant,
                    final_messages=final_messages,
                    session=session,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    tool_call_summaries=tool_call_summaries,
                )

        state["assistant_output_streamed"] = assistant_output_streamed
        state["final_assistant"] = final_assistant
        state["tool_call_summaries"] = tool_call_summaries
        state["buffered_assistant_events"] = buffered_assistant_events
        state["message_history"] = persist_messages
