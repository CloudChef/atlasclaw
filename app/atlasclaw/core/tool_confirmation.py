# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Server-owned confirmation tickets for mutation-capable Tools.

The store is deliberately provider-neutral and process-local. Tickets authorize
one exact Tool call in one authenticated Chat Session; they do not grant Tool or
Provider access, which is re-evaluated by the normal request-scoped permission
pipeline before execution.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping


class ToolConfirmationError(PermissionError):
    """Raised when a confirmation token is invalid, stale, or out of scope."""


class ToolConfirmationRequired(ToolConfirmationError):
    """Raised internally after the server has staged a confirmation action."""


def canonical_tool_arguments(arguments: Mapping[str, Any]) -> str:
    """Serialize Tool arguments deterministically for exact-call binding."""
    return json.dumps(
        dict(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def tool_arguments_fingerprint(arguments: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint for exact Tool arguments."""
    return hashlib.sha256(canonical_tool_arguments(arguments).encode("utf-8")).hexdigest()


def _canonical_embed_scope(scope: Any) -> str:
    if not isinstance(scope, Mapping):
        return ""
    allowed = {
        key: scope.get(key)
        for key in (
            "context_id",
            "generation",
            "integration_id",
            "provider_type",
            "provider_instance",
            "object_type",
            "object_id",
        )
    }
    return json.dumps(allowed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class ToolConfirmationGrant:
    """Claimed authorization for one exact Tool execution.

    The mutable consumed flag is shared by all wrappers in one Run so a model
    retry cannot execute the confirmed mutation twice.
    """

    token: str
    owner_user_id: str
    session_key: str
    agent_id: str
    tool_name: str
    owner_skill_ref: str
    contract_fingerprint: str
    arguments: dict[str, Any]
    arguments_fingerprint: str
    provider_type: str
    provider_instance: str
    embed_scope: str
    expires_at: float
    consumed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def consume_for(
        self,
        *,
        tool_name: str,
        owner_skill_ref: str,
        provider_type: str,
        provider_instance: str,
        contract_fingerprint: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Consume the grant once and return its frozen arguments."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            if current_time >= self.expires_at:
                raise ToolConfirmationError("Tool confirmation expired")
            if self.consumed:
                raise ToolConfirmationError("Tool confirmation was already used")
            if str(tool_name or "").strip() != self.tool_name:
                raise ToolConfirmationError("Tool confirmation does not match this Tool")
            if str(owner_skill_ref or "").strip().lower() != self.owner_skill_ref.lower():
                raise ToolConfirmationError("Tool confirmation owner changed")
            if str(provider_type or "").strip().lower() != self.provider_type.lower():
                raise ToolConfirmationError("Tool confirmation Provider changed")
            if str(provider_instance or "").strip() != self.provider_instance:
                raise ToolConfirmationError("Tool confirmation Provider instance changed")
            if str(contract_fingerprint or "").strip() != self.contract_fingerprint:
                raise ToolConfirmationError("Tool confirmation contract changed")
            self.consumed = True
            return dict(self.arguments)


@dataclass
class _PendingToolConfirmation:
    token: str
    owner_user_id: str
    session_key: str
    agent_id: str
    tool_name: str
    owner_skill_ref: str
    contract_fingerprint: str
    arguments: dict[str, Any]
    arguments_fingerprint: str
    provider_type: str
    provider_instance: str
    embed_scope: str
    expires_at: float
    scope_key: tuple[str, ...]


class ToolConfirmationStore:
    """Bounded in-memory store for single-use Tool confirmation tickets."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_tickets: int = 1024) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_tickets <= 0:
            raise ValueError("max_tickets must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._max_tickets = int(max_tickets)
        self._pending: OrderedDict[str, _PendingToolConfirmation] = OrderedDict()
        self._latest_by_scope: dict[tuple[str, ...], str] = {}
        self._claimed_tokens: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.RLock()

    def issue(
        self,
        *,
        owner_user_id: str,
        session_key: str,
        agent_id: str,
        tool_name: str,
        owner_skill_ref: str,
        contract_fingerprint: str,
        arguments: Mapping[str, Any],
        provider_type: str = "",
        provider_instance: str = "",
        embed_scope: Any = None,
        now: float | None = None,
    ) -> _PendingToolConfirmation:
        """Issue a ticket, replacing an older ticket for the exact same call scope."""
        current_time = time.monotonic() if now is None else now
        normalized_user = str(owner_user_id or "").strip()
        normalized_session = str(session_key or "").strip()
        normalized_agent = str(agent_id or "").strip()
        normalized_tool = str(tool_name or "").strip()
        normalized_owner = str(owner_skill_ref or "").strip().lower()
        normalized_contract = str(contract_fingerprint or "").strip()
        normalized_provider = str(provider_type or "").strip().lower()
        normalized_instance = str(provider_instance or "").strip()
        if not all(
            (
                normalized_user,
                normalized_session,
                normalized_agent,
                normalized_tool,
                normalized_contract,
            )
        ):
            raise ToolConfirmationError("Authenticated Tool confirmation scope is unavailable")
        if normalized_provider and not normalized_instance:
            raise ToolConfirmationError("Provider instance must be selected before confirmation")
        try:
            arguments_json = canonical_tool_arguments(arguments)
            if len(arguments_json.encode("utf-8")) > 32 * 1024:
                raise ValueError("Tool arguments exceed the confirmation limit")
            frozen_arguments = json.loads(arguments_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolConfirmationError("Tool arguments cannot be frozen safely") from exc
        arguments_fingerprint = hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()
        frozen_embed_scope = _canonical_embed_scope(embed_scope)
        scope_key = (
            normalized_user,
            normalized_session,
            normalized_agent,
            normalized_tool,
            normalized_owner,
            normalized_contract,
            arguments_fingerprint,
            normalized_provider,
            normalized_instance,
            frozen_embed_scope,
        )
        with self._lock:
            self._purge_locked(current_time)
            previous_token = self._latest_by_scope.get(scope_key)
            if previous_token:
                previous = self._pending.get(previous_token)
                if previous is not None and previous.expires_at > current_time:
                    self._pending.move_to_end(previous_token)
                    return previous
                self._pending.pop(previous_token, None)
            token = secrets.token_urlsafe(32)
            ticket = _PendingToolConfirmation(
                token=token,
                owner_user_id=normalized_user,
                session_key=normalized_session,
                agent_id=normalized_agent,
                tool_name=normalized_tool,
                owner_skill_ref=normalized_owner,
                contract_fingerprint=normalized_contract,
                arguments=frozen_arguments,
                arguments_fingerprint=arguments_fingerprint,
                provider_type=normalized_provider,
                provider_instance=normalized_instance,
                embed_scope=frozen_embed_scope,
                expires_at=current_time + self._ttl_seconds,
                scope_key=scope_key,
            )
            self._pending[token] = ticket
            self._latest_by_scope[scope_key] = token
            self._trim_locked()
            return ticket

    def claim(
        self,
        token: str,
        *,
        owner_user_id: str,
        session_key: str,
        agent_id: str,
        embed_scope: Any = None,
        now: float | None = None,
    ) -> ToolConfirmationGrant:
        """Atomically claim one ticket for its bound user, session, agent, and page."""
        current_time = time.monotonic() if now is None else now
        normalized_token = str(token or "").strip()
        with self._lock:
            self._purge_locked(current_time)
            if normalized_token in self._claimed_tokens:
                raise ToolConfirmationError("Tool confirmation was already claimed")
            ticket = self._pending.get(normalized_token)
            if ticket is None:
                raise ToolConfirmationError("Tool confirmation is invalid or expired")
            if ticket.owner_user_id != str(owner_user_id or "").strip():
                raise ToolConfirmationError("Tool confirmation belongs to another user")
            if ticket.session_key != str(session_key or "").strip():
                raise ToolConfirmationError("Tool confirmation belongs to another Chat Session")
            if ticket.agent_id != str(agent_id or "").strip():
                raise ToolConfirmationError("Tool confirmation belongs to another Agent")
            if ticket.embed_scope != _canonical_embed_scope(embed_scope):
                raise ToolConfirmationError("Tool confirmation page context changed")
            self._pending.pop(normalized_token, None)
            if self._latest_by_scope.get(ticket.scope_key) == normalized_token:
                self._latest_by_scope.pop(ticket.scope_key, None)
            self._claimed_tokens[normalized_token] = ticket.expires_at
            self._trim_locked()
            return ToolConfirmationGrant(
                token=ticket.token,
                owner_user_id=ticket.owner_user_id,
                session_key=ticket.session_key,
                agent_id=ticket.agent_id,
                tool_name=ticket.tool_name,
                owner_skill_ref=ticket.owner_skill_ref,
                contract_fingerprint=ticket.contract_fingerprint,
                arguments=dict(ticket.arguments),
                arguments_fingerprint=ticket.arguments_fingerprint,
                provider_type=ticket.provider_type,
                provider_instance=ticket.provider_instance,
                embed_scope=ticket.embed_scope,
                expires_at=ticket.expires_at,
            )

    def _purge_locked(self, now: float) -> None:
        expired_pending = [
            token for token, ticket in self._pending.items() if ticket.expires_at <= now
        ]
        for token in expired_pending:
            ticket = self._pending.pop(token)
            if self._latest_by_scope.get(ticket.scope_key) == token:
                self._latest_by_scope.pop(ticket.scope_key, None)
        expired_claimed = [
            token for token, expires_at in self._claimed_tokens.items() if expires_at <= now
        ]
        for token in expired_claimed:
            self._claimed_tokens.pop(token, None)

    def _trim_locked(self) -> None:
        while len(self._pending) > self._max_tickets:
            token, ticket = self._pending.popitem(last=False)
            if self._latest_by_scope.get(ticket.scope_key) == token:
                self._latest_by_scope.pop(ticket.scope_key, None)
        while len(self._claimed_tokens) > self._max_tickets:
            self._claimed_tokens.popitem(last=False)


def get_tool_confirmation_grant(deps: Any) -> ToolConfirmationGrant | None:
    """Read the server-owned grant from request-scoped dependencies."""
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, Mapping):
        return None
    grant = extra.get("_tool_confirmation_grant")
    if grant is None:
        context = extra.get("context")
        if isinstance(context, Mapping):
            grant = context.get("_tool_confirmation_grant")
    return grant if isinstance(grant, ToolConfirmationGrant) else None


def append_runtime_confirmation_action(deps: Any, ticket: _PendingToolConfirmation) -> None:
    """Append one trusted confirmation action to the current Run side channel."""
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict):
        return
    actions = extra.setdefault("_runtime_tool_confirmation_actions", [])
    if not isinstance(actions, list):
        actions = []
        extra["_runtime_tool_confirmation_actions"] = actions
    actions.clear()
    actions.append(
        {
            "object_actions": [
                {
                    "action_id": f"confirm-tool-{ticket.arguments_fingerprint[:16]}",
                    "kind": "agent_prompt",
                    "display_label": {"default": "Confirm action", "zh-CN": "确认操作"},
                    "agent_prompt": {
                        "default": "Execute the operation I just confirmed.",
                        "zh-CN": "执行我刚刚确认的操作。",
                    },
                    "confirmation_message": {
                        "default": "Confirm this operation?",
                        "zh-CN": "确认执行此操作吗？",
                    },
                    "effect": "mutate",
                    "requires_confirmation": True,
                    "confirmation_token": ticket.token,
                }
            ]
        }
    )


def pop_runtime_confirmation_actions(deps: Any) -> list[dict[str, Any]]:
    """Consume trusted confirmation actions emitted by Tool wrappers in this Run."""
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict):
        return []
    raw = extra.pop("_runtime_tool_confirmation_actions", [])
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
