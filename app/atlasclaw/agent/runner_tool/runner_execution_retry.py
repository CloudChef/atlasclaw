# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter
from app.atlasclaw.core.deps import SkillDeps

logger = logging.getLogger(__name__)


class RunnerExecutionRetryMixin:
    """Handle bounded retry behavior for hard model-token failures."""

    async def _retry_after_hard_token_failure(
        self,
        *,
        error: Exception,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        selected_token_id: Optional[str],
        release_slot: Optional[Any],
        thinking_emitter: ThinkingStreamEmitter,
        start_time: float,
        max_tool_calls: int,
        timeout_seconds: int | None,
        token_failover_attempt: int,
        emit_lifecycle_bounds: bool,
    ) -> AsyncIterator[StreamEvent]:
        """Rotate away from a hard-failed token and retry the same run once."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        if extra.get("_skill_stage_active"):
            if extra.get("_skill_stage_side_effect_started"):
                return  # This stage may have performed a write; never replay it.
        elif extra.get("_chat_tool_started"):
            return  # Preserve the legacy no-replay guard outside routed stages.
        if (
            self.token_policy is None
            or not self._is_hard_token_failure(error)
        ):
            logger.warning(
                "token failover skipped: token_policy=%s hard_failure=%s error=%s",
                self.token_policy is not None,
                self._is_hard_token_failure(error),
                str(error),
            )
            return
        binding_selection = extra.get("_current_binding_selection")
        binding_target = extra.get("_current_binding_target")
        bound_tokens = tuple(getattr(binding_selection, "eligible_tokens", ()) or ())
        is_bound_retry = binding_target is not None and bool(bound_tokens)
        pool_max_attempts = max(
            (len(bound_tokens) if is_bound_retry else len(self.token_policy.token_pool.tokens)) - 1,
            0,
        )
        configured_max_attempts = max(
            int(getattr(self, "TOKEN_FAILOVER_MAX_ATTEMPTS", pool_max_attempts) or 0),
            0,
        )
        max_failover_attempts = min(pool_max_attempts, configured_max_attempts)
        if token_failover_attempt >= max_failover_attempts:
            logger.warning(
                "token failover exhausted: attempt=%s max_attempts=%s",
                token_failover_attempt,
                max_failover_attempts,
            )
            return

        provider = extra.get("provider") if isinstance(extra.get("provider"), str) else None
        model = extra.get("model") if isinstance(extra.get("model"), str) else None
        error_text = str(error)
        next_token = None
        if is_bound_retry:
            next_token = await self._select_bound_retry_token(
                session_key=session_key,
                deps=deps,
                failed_token_id=selected_token_id,
                error_text=error_text,
            )
            if next_token is not None:
                extra["_binding_retry_token"] = next_token
        else:
            image_filter = ({"eligible_token_ids": extra["_chat_vision_tokens"]}
                            if "_chat_vision_tokens" in extra else {})
            credential_guard = ({"expected_fingerprint": extra["_selected_token_fingerprint"]}
                                if extra.get("_selected_token_fingerprint") else {})
            if selected_token_id:
                if self.token_interceptor is not None:
                    self.token_interceptor.on_hard_failure(selected_token_id, error_text, **credential_guard)
                next_token = self.token_policy.mark_session_token_unhealthy(
                    session_key,
                    reason=error_text,
                    provider=provider,
                    model=model,
                    **image_filter,
                    **credential_guard,
                )
                if next_token is None and provider:
                    next_token = self.token_policy.mark_session_token_unhealthy(
                        session_key,
                        reason=error_text,
                        provider=provider,
                        model=None,
                        **image_filter,
                        **credential_guard,
                    )
                if next_token is None:
                    next_token = self.token_policy.mark_session_token_unhealthy(
                        session_key,
                        reason=error_text,
                        provider=None,
                        model=None,
                        **image_filter,
                        **credential_guard,
                    )
            else:
                next_token = self.token_policy.get_or_select_session_token(
                    session_key,
                    provider=provider,
                    model=model,
                )
                if next_token is None and provider:
                    next_token = self.token_policy.get_or_select_session_token(
                        session_key,
                        provider=provider,
                        model=None,
                    )
                if next_token is None:
                    next_token = self.token_policy.get_or_select_session_token(
                        session_key,
                        provider=None,
                        model=None,
                    )

        if next_token is None or (selected_token_id and next_token.token_id == selected_token_id):
            logger.warning(
                "token failover unavailable: selected_token_id=%s next_token=%s",
                selected_token_id,
                None if next_token is None else next_token.token_id,
            )
            return

        async for event in thinking_emitter.close_if_active():
            yield event
        if release_slot is not None:
            release_slot()

        yield StreamEvent.runtime_update(
            "retrying",
            (
                (
                    "Current model token failed with a provider/model-side error or stream stall. "
                    f"Switching to fallback model token `{next_token.token_id}`."
                )
                if selected_token_id
                else (
                    "Current run failed before a managed token was pinned. "
                    f"Switching to managed fallback token `{next_token.token_id}`."
                )
            ),
            metadata={
                "phase": "token_failover",
                "elapsed": round(time.monotonic() - start_time, 1),
                "attempt": token_failover_attempt + 1,
                "failed_token_id": selected_token_id,
                "fallback_token_id": next_token.token_id,
            },
        )
        async for event in self._run_single_stage(
            session_key=session_key,
            user_message=user_message,
            deps=deps,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
            _token_failover_attempt=token_failover_attempt + 1,
            _emit_lifecycle_bounds=False,
        ):
            yield event
        if emit_lifecycle_bounds:
            yield StreamEvent.lifecycle_end()
        return

    async def _select_bound_retry_token(
        self,
        *,
        session_key: str,
        deps: SkillDeps,
        failed_token_id: Optional[str],
        error_text: str,
    ) -> Any:
        """Choose a fresh, still-authorized candidate from the failed target's policy."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        target = extra.get("_current_binding_target")
        selection = extra.get("_current_binding_selection")
        if target is None or selection is None or not failed_token_id:
            return None
        failed_fingerprint = str(extra.get("_selected_token_fingerprint") or "")
        failed_credentials = extra.setdefault("_binding_failed_credentials", set())
        if not isinstance(failed_credentials, set):
            failed_credentials = set()
            extra["_binding_failed_credentials"] = failed_credentials
        failed_credentials.add((str(failed_token_id), failed_fingerprint))

        pool = self.token_policy.token_pool
        pool_ids = getattr(selection, "pool_token_ids", {}) or {}
        raw_pool_id = str(pool_ids.get(failed_token_id, failed_token_id) or "")
        pool_entry = pool.tokens.get(raw_pool_id)
        if (
            pool_entry is not None
            and failed_fingerprint
            and pool_entry.credential_fingerprint == failed_fingerprint
        ):
            if self.token_interceptor is not None:
                self.token_interceptor.on_hard_failure(
                    raw_pool_id,
                    error_text,
                    expected_fingerprint=failed_fingerprint,
                )
            else:
                pool.mark_token_unhealthy(
                    raw_pool_id,
                    reason=error_text,
                    expected_fingerprint=failed_fingerprint,
                )

        try:
            _agent, _token_id, _release = await self._resolve_runtime_agent(
                session_key,
                deps,
                binding_target=target,
                acquire_slot=False,
            )
            fresh_selection = extra.get("_current_binding_selection")
            fresh_tokens = tuple(getattr(fresh_selection, "eligible_tokens", ()) or ())
            fresh_pool_ids = getattr(fresh_selection, "pool_token_ids", {}) or {}
            fresh_id = str(getattr(fresh_selection, "token", None).token_id or "")
            by_id = {str(candidate.token_id): candidate for candidate in fresh_tokens}
            selected = by_id.get(fresh_id)
            if selected is not None and (
                selected.token_id,
                selected.credential_fingerprint,
            ) not in failed_credentials:
                return selected

            candidates = [
                candidate
                for candidate in fresh_tokens
                if (candidate.token_id, candidate.credential_fingerprint) not in failed_credentials
            ]
            if not candidates:
                return None

            def rank(candidate: Any) -> tuple[bool, float, int, int]:
                candidate_pool_id = str(fresh_pool_ids.get(candidate.token_id, candidate.token_id))
                pool_candidate = pool.tokens.get(candidate_pool_id)
                health = pool.get_token_health(candidate_pool_id)
                if (
                    pool_candidate is None
                    or pool_candidate.credential_fingerprint != candidate.credential_fingerprint
                ):
                    health = None
                return (
                    health.is_healthy if health is not None else True,
                    float(health.health_score) if health is not None else 100.0,
                    int(getattr(candidate, "priority", 0) or 0),
                    int(getattr(candidate, "weight", 0) or 0),
                )

            return max(candidates, key=rank)
        except Exception as exc:
            logger.warning("policy-bound token failover unavailable for target %s: %s", target, exc)
            return None
    def _is_hard_token_failure(self, error: Exception) -> bool:
        """Return true when an error indicates the current token should be evicted."""
        if self._is_token_access_failure(error):
            return True
        lowered = str(error).lower()
        hard_markers = (
            "invalid response from openai chat completions endpoint",
            "validation errors for chatcompletion",
            "validation error for chatcompletion",
            "input should be a valid string",
            "input should be a valid list",
            "object input should be 'chat.completion'",
            "pydantic validation error",
        )
        return any(marker in lowered for marker in hard_markers)

    @staticmethod
    def _is_token_access_failure(error: Exception) -> bool:
        """Return true for provider failures that another model token may resolve."""

        status_code = getattr(error, "status_code", None)
        if status_code is None:
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
        if status_code in {401, 402, 403, 429}:
            return True

        lowered = str(error).lower()
        access_markers = (
            "status_code: 401",
            "status_code: 402",
            "status_code: 403",
            "status_code: 429",
            "http 401",
            "http 402",
            "http 403",
            "http 429",
            "authenticationerror",
            "accountoverdueerror",
            "forbidden",
            "invalid api key",
            "insufficient balance",
            "insufficient_quota",
            "api key format is incorrect",
            "provider returned error', 'code': 429",
            '"code": 429',
            "rate-limited upstream",
            "too many requests",
            "rate limit",
        )
        return any(marker in lowered for marker in access_markers)
