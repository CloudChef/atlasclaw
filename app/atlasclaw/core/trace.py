# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Trace context and LLM HTTP logging helpers."""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Optional
from urllib.parse import parse_qsl

import httpx

from app.atlasclaw.core.provider_registry import _is_sensitive as _is_sensitive_provider_config_key
from app.atlasclaw.session.context import SessionKey

if TYPE_CHECKING:
    from app.atlasclaw.core.deps import SkillDeps


logger = logging.getLogger(__name__)

_CURRENT_TRACE_CONTEXT: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "atlasclaw_current_trace_context",
    default=None,
)

_SENSITIVE_KEY_TOKENS = (
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "cookie",
    "set-cookie",
)


@dataclass(frozen=True)
class TraceContext:
    """Stable log correlation fields for one runtime turn."""

    trace_id: str
    session_key: str
    thread_id: str = ""
    run_id: str = ""
    channel: str = ""
    user_id: str = ""

    def as_log_fields(self) -> dict[str, str]:
        """Return normalized trace fields for structured logging."""
        return {
            "trace_id": self.trace_id,
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "channel": self.channel,
            "user_id": self.user_id,
        }


def resolve_trace_context(
    session_key: str,
    *,
    run_id: str = "",
    deps: Optional["SkillDeps"] = None,
) -> TraceContext:
    """Resolve `thread_id`-first trace metadata for the current run."""
    parsed = SessionKey.from_string(str(session_key or ""))
    deps_extra = getattr(deps, "extra", None)
    if not isinstance(deps_extra, dict):
        deps_extra = {}

    explicit_thread_id = str(deps_extra.get("thread_id", "") or "").strip()
    explicit_trace_id = str(deps_extra.get("trace_id", "") or "").strip()
    resolved_run_id = str(run_id or deps_extra.get("run_id", "") or "").strip()

    thread_id = explicit_thread_id or str(parsed.thread_id or "").strip()
    trace_id = explicit_trace_id or thread_id or str(session_key or "").strip()

    channel = str(getattr(deps, "channel", "") or parsed.channel or "").strip()
    user_info = getattr(deps, "user_info", None)
    user_id = str(getattr(user_info, "user_id", "") or parsed.user_id or "").strip()

    return TraceContext(
        trace_id=trace_id,
        session_key=str(session_key or "").strip(),
        thread_id=thread_id,
        run_id=resolved_run_id,
        channel=channel,
        user_id=user_id,
    )


def enrich_trace_metadata(
    session_key: str,
    *,
    extra: Optional[dict[str, Any]] = None,
    run_id: str = "",
    deps: Optional["SkillDeps"] = None,
) -> dict[str, Any]:
    """Copy `extra` and populate stable trace metadata fields."""
    enriched = dict(extra or {})
    trace_context = resolve_trace_context(
        session_key,
        run_id=run_id or str(enriched.get("run_id", "") or ""),
        deps=deps,
    )
    if trace_context.run_id:
        enriched["run_id"] = trace_context.run_id
    enriched["thread_id"] = trace_context.thread_id
    enriched["trace_id"] = trace_context.trace_id
    return enriched


def get_current_trace_context() -> Optional[TraceContext]:
    """Return the currently bound trace context, if any."""
    return _CURRENT_TRACE_CONTEXT.get()


@contextmanager
def bind_trace_context(trace_context: TraceContext) -> Iterator[TraceContext]:
    """Bind one trace context to the current async task for downstream hooks."""
    token = _CURRENT_TRACE_CONTEXT.set(trace_context)
    try:
        yield trace_context
    finally:
        _CURRENT_TRACE_CONTEXT.reset(token)


def sanitize_log_value(
    value: Any,
    *,
    max_string_chars: int = 512,
    max_list_items: int = 20,
    max_depth: int = 6,
    redacted_text: str = "[REDACTED]",
    provider_type: str = "",
    field_defaults: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Recursively redact sensitive values and truncate long payloads."""
    if max_depth <= 0:
        return "[max depth reached]"

    if isinstance(value, Mapping):
        effective_defaults = field_defaults if isinstance(field_defaults, Mapping) else value
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_log_key(
                key_str,
                provider_type=provider_type,
                field_defaults=effective_defaults,
            ):
                sanitized[key_str] = redacted_text
            else:
                sanitized[key_str] = sanitize_log_value(
                    item,
                    max_string_chars=max_string_chars,
                    max_list_items=max_list_items,
                    max_depth=max_depth - 1,
                    redacted_text=redacted_text,
                    provider_type=provider_type,
                    field_defaults=effective_defaults,
                )
        return sanitized

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        sanitized_items = [
            sanitize_log_value(
                item,
                max_string_chars=max_string_chars,
                max_list_items=max_list_items,
                max_depth=max_depth - 1,
                redacted_text=redacted_text,
                provider_type=provider_type,
                field_defaults=field_defaults,
            )
            for item in items[:max_list_items]
        ]
        if len(items) > max_list_items:
            sanitized_items.append(f"...[{len(items) - max_list_items} more items truncated]")
        return sanitized_items

    if isinstance(value, bytes):
        return _truncate_text(value.decode("utf-8", errors="replace"), max_string_chars=max_string_chars)

    if isinstance(value, str):
        return _truncate_text(value, max_string_chars=max_string_chars)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return _truncate_text(repr(value), max_string_chars=max_string_chars)


def build_http_request_log_payload(
    request: httpx.Request,
    trace_context: Optional[TraceContext],
    *,
    provider_name: str = "",
) -> dict[str, Any]:
    """Build a redacted request log payload without mutating the request."""
    payload = {
        "provider": str(provider_name or request.url.host or "").strip(),
        "method": request.method,
        "url": str(request.url),
        "path": request.url.path,
        "query": request.url.query.decode("utf-8", errors="replace") if request.url.query else "",
        "query_params": dict(parse_qsl(request.url.query.decode("utf-8", errors="replace")))
        if request.url.query
        else {},
        "headers": sanitize_log_value(_normalize_headers(request.headers)),
        "body_snapshot": sanitize_log_value(_extract_request_body(request)),
    }
    if trace_context is not None:
        payload.update(trace_context.as_log_fields())
    return payload


def build_http_response_log_payload(
    response: httpx.Response,
    trace_context: Optional[TraceContext],
    *,
    provider_name: str = "",
) -> dict[str, Any]:
    """Build a safe response log payload without consuming streaming bodies."""
    request = response.request
    request_body = _extract_request_body(request)
    streaming = bool(request_body.get("stream")) if isinstance(request_body, dict) else False
    body_snapshot: Any
    if streaming or not response.is_closed:
        body_snapshot = "[streaming response body not captured]"
    else:
        body_snapshot = sanitize_log_value(_extract_response_body(response))

    payload = {
        "provider": str(provider_name or request.url.host or "").strip(),
        "method": request.method,
        "url": str(request.url),
        "path": request.url.path,
        "status_code": int(response.status_code),
        "headers": sanitize_log_value(_normalize_headers(response.headers)),
        "streaming": streaming,
        "body_snapshot": body_snapshot,
    }
    if trace_context is not None:
        payload.update(trace_context.as_log_fields())
    return payload


def create_traced_http_client(provider_name: str) -> httpx.AsyncClient:
    """Create an async HTTP client with request/response trace logging hooks."""

    async def _on_request(request: httpx.Request) -> None:
        logger.info(
            "llm_http_request %s",
            build_http_request_log_payload(
                request,
                get_current_trace_context(),
                provider_name=provider_name,
            ),
        )

    async def _on_response(response: httpx.Response) -> None:
        logger.info(
            "llm_http_response %s",
            build_http_response_log_payload(
                response,
                get_current_trace_context(),
                provider_name=provider_name,
            ),
        )

    return httpx.AsyncClient(
        trust_env=True,
        event_hooks={"request": [_on_request], "response": [_on_response]},
    )


def _extract_request_body(request: httpx.Request) -> Any:
    try:
        content = request.content
    except Exception:
        return "[request body unavailable]"

    if not content:
        return ""

    return _decode_possible_json(content)


def _extract_response_body(response: httpx.Response) -> Any:
    try:
        content = response.content
    except Exception:
        return "[response body unavailable]"

    if not content:
        return ""

    return _decode_possible_json(content)


def _decode_possible_json(content: bytes | str) -> Any:
    if isinstance(content, str):
        text = content
    else:
        text = content.decode("utf-8", errors="replace")

    try:
        return json.loads(text)
    except Exception:
        return text


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _is_sensitive_log_key(
    key: str,
    *,
    provider_type: str = "",
    field_defaults: Optional[Mapping[str, Any]] = None,
) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if _is_sensitive_key(normalized) or _is_sensitive_provider_config_key(normalized):
        return True

    try:
        from app.atlasclaw.api.service_provider_schemas import is_provider_config_field_sensitive
    except Exception:
        return False
    return is_provider_config_field_sensitive(
        provider_type,
        normalized,
        field_defaults=field_defaults,
    )


def _truncate_text(value: str, *, max_string_chars: int) -> str:
    if len(value) <= max_string_chars:
        return value
    return f"{value[:max_string_chars]}...[truncated]"


def _normalize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        key_str = str(key or "").strip()
        display_key = "-".join(part.capitalize() for part in key_str.split("-")) if key_str else key_str
        normalized[display_key] = str(value)
    return normalized
