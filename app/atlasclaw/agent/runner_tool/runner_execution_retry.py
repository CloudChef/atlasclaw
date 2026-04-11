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
        timeout_seconds: int,
        token_failover_attempt: int,
        emit_lifecycle_bounds: bool,
    ) -> AsyncIterator[StreamEvent]:
        """Rotate away from a hard-failed token and retry the same run once."""
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
        pool_max_attempts = max(len(self.token_policy.token_pool.tokens) - 1, 0)
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

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        provider = extra.get("provider") if isinstance(extra.get("provider"), str) else None
        model = extra.get("model") if isinstance(extra.get("model"), str) else None
        error_text = str(error)
        next_token = None
        if selected_token_id:
            if self.token_interceptor is not None:
                self.token_interceptor.on_hard_failure(selected_token_id, error_text)
            next_token = self.token_policy.mark_session_token_unhealthy(
                session_key,
                reason=error_text,
                provider=provider,
                model=model,
            )
            if next_token is None and provider:
                next_token = self.token_policy.mark_session_token_unhealthy(
                    session_key,
                    reason=error_text,
                    provider=provider,
                    model=None,
                )
            if next_token is None:
                next_token = self.token_policy.mark_session_token_unhealthy(
                    session_key,
                    reason=error_text,
                    provider=None,
                    model=None,
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
        async for event in self.run(
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
    async def _retry_after_missing_tool_execution(
        self,
        *,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        release_slot: Optional[Any],
        selected_token_id: Optional[str],
        start_time: float,
        max_tool_calls: int,
        timeout_seconds: int,
        token_failover_attempt: int,
        emit_lifecycle_bounds: bool,
        failure_message: str,
        preferred_tools: list[str],
        tool_execution_retry_count: int,
        allow_retry: bool,
    ) -> AsyncIterator[StreamEvent]:
        """Retry once when a tool-required turn ended without any real tool execution."""
        if not allow_retry:
            return
        if tool_execution_retry_count >= self.TOOL_POLICY_MAX_RETRIES:
            return

        if release_slot is not None:
            release_slot()

        if not isinstance(deps.extra, dict):
            deps.extra = {}
        deps.extra["_tool_execution_retry_count"] = tool_execution_retry_count + 1
        deps.extra["tool_execution_retry_reason"] = "missing_tool_execution"
        deps.extra["tool_execution_retry_missing_tools"] = list(preferred_tools)

        yield StreamEvent.runtime_update(
            "retrying",
            (
                "The model did not execute a real tool call in a tool-required turn. "
                "Retrying once with stricter tool-execution guidance."
            ),
            metadata={
                "phase": "tool_execution_retry",
                "elapsed": round(time.monotonic() - start_time, 1),
                "attempt": tool_execution_retry_count + 1,
                "failed_token_id": selected_token_id,
                "preferred_tools": list(preferred_tools),
                "failure_message": failure_message,
            },
        )

        async for event in self.run(
            session_key=session_key,
            user_message=user_message,
            deps=deps,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
            _token_failover_attempt=token_failover_attempt,
            _emit_lifecycle_bounds=False,
        ):
            yield event
        if emit_lifecycle_bounds:
            yield StreamEvent.lifecycle_end()
        return
    def _is_hard_token_failure(self, error: Exception) -> bool:
        """Return true when an error indicates the current token should be evicted."""
        lowered = str(error).lower()
        hard_markers = (
            "status_code: 401",
            "status_code: 403",
            "status_code: 429",
            "authenticationerror",
            "accountoverdueerror",
            "forbidden",
            "invalid api key",
            "insufficient_quota",
            "api key format is incorrect",
            "provider returned error', 'code': 429",
            '"code": 429',
            "rate-limited upstream",
            "too many requests",
            "rate limit",
            "invalid response from openai chat completions endpoint",
            "validation errors for chatcompletion",
            "validation error for chatcompletion",
            "input should be a valid string",
            "input should be a valid list",
            "object input should be 'chat.completion'",
            "pydantic validation error",
        )
        return any(marker in lowered for marker in hard_markers)

