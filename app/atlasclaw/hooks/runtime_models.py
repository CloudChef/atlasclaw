# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def _parse_datetime(value: str | datetime) -> datetime:
    """Normalize ISO strings or datetimes into timezone-aware UTC datetimes."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class HookEventType(str, Enum):
    """Canonical hook runtime events aligned with lifecycle-oriented runtimes."""

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CONTEXT_READY = "run.context_ready"
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_USER_CORRECTED = "message.user_corrected"
    LLM_REQUESTED = "llm.requested"
    LLM_COMPLETED = "llm.completed"
    LLM_FAILED = "llm.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_GATE_EVALUATED = "tool_gate.evaluated"
    TOOL_GATE_REQUIRED = "tool_gate.required"
    TOOL_GATE_OPTIONAL = "tool_gate.optional"
    TOOL_MATCHER_RESOLVED = "tool_matcher.resolved"
    TOOL_MATCHER_MISSING_CAPABILITY = "tool_matcher.missing_capability"
    HINT_RANKING_STARTED = "hint_ranking.started"
    HINT_RANKING_COMPLETED = "hint_ranking.completed"
    HINT_RANKING_FALLBACK = "hint_ranking.fallback"
    TOOL_ENFORCEMENT_BLOCKED_FINAL_ANSWER = "tool_enforcement.blocked_final_answer"
    TOOL_ENFORCEMENT_PREFETCH_STARTED = "tool_enforcement.prefetch_started"
    TOOL_ENFORCEMENT_PREFETCH_COMPLETED = "tool_enforcement.prefetch_completed"
    TOOL_ENFORCEMENT_PREFETCH_FAILED = "tool_enforcement.prefetch_failed"
    HEARTBEAT_AGENT_STARTED = "heartbeat.agent.started"
    HEARTBEAT_AGENT_COMPLETED = "heartbeat.agent.completed"
    HEARTBEAT_AGENT_FAILED = "heartbeat.agent.failed"
    HEARTBEAT_CHANNEL_CHECK_STARTED = "heartbeat.channel.check_started"
    HEARTBEAT_CHANNEL_CHECK_SUCCEEDED = "heartbeat.channel.check_succeeded"
    HEARTBEAT_CHANNEL_CHECK_FAILED = "heartbeat.channel.check_failed"
    HEARTBEAT_CHANNEL_RECONNECT_STARTED = "heartbeat.channel.reconnect_started"
    HEARTBEAT_CHANNEL_RECONNECT_SUCCEEDED = "heartbeat.channel.reconnect_succeeded"
    HEARTBEAT_CHANNEL_RECONNECT_FAILED = "heartbeat.channel.reconnect_failed"
    HEARTBEAT_CHANNEL_DEGRADED = "heartbeat.channel.degraded"
    MEMORY_CONFIRMED = "memory.confirmed"
    MEMORY_REJECTED = "memory.rejected"
    MEMORY_AUTO_WRITE_COMPLETED = "memory.auto_write.completed"


class PendingHookStatus(str, Enum):
    """Lifecycle state for a hook-generated pending item."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class HookDecision(str, Enum):
    """User-facing decisions for pending hook items."""

    CONFIRM = "confirm"
    REJECT = "reject"


class HookScriptActionType(str, Enum):
    """Supported generic actions that may be returned by script hooks."""

    CREATE_PENDING = "create_pending"
    WRITE_MEMORY = "write_memory"
    ADD_CONTEXT = "add_context"


@dataclass
class HookEventEnvelope:
    """Runtime event envelope emitted by the generic hook runtime."""

    id: str
    event_type: HookEventType
    user_id: str
    session_key: str
    run_id: str
    channel: str
    agent_id: str
    created_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "user_id": self.user_id,
            "session_key": self.session_key,
            "run_id": self.run_id,
            "channel": self.channel,
            "agent_id": self.agent_id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HookEventEnvelope":
        return cls(
            id=data["id"],
            event_type=HookEventType(data["event_type"]),
            user_id=data["user_id"],
            session_key=data.get("session_key", ""),
            run_id=data.get("run_id", ""),
            channel=data.get("channel", ""),
            agent_id=data.get("agent_id", ""),
            created_at=_parse_datetime(data["created_at"]),
            payload=dict(data.get("payload", {})),
        )


@dataclass
class PendingHookItem:
    """Stored pending item owned by a hook module for later user review."""

    id: str
    module_name: str
    user_id: str
    source_event_ids: list[str]
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: PendingHookStatus | str = PendingHookStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.status = PendingHookStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module_name": self.module_name,
            "user_id": self.user_id,
            "source_event_ids": list(self.source_event_ids),
            "summary": self.summary,
            "payload": self.payload,
            "status": self.status.value,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "updated_at": self.updated_at.astimezone(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingHookItem":
        return cls(
            id=data["id"],
            module_name=data["module_name"],
            user_id=data["user_id"],
            source_event_ids=list(data.get("source_event_ids", [])),
            summary=data.get("summary", ""),
            payload=dict(data.get("payload", {})),
            status=data.get("status", PendingHookStatus.PENDING.value),
            created_at=_parse_datetime(data["created_at"]),
            updated_at=_parse_datetime(data["updated_at"]),
        )


@dataclass
class HookDecisionRecord:
    """Audit record describing how a pending hook item was resolved."""

    id: str
    module_name: str
    user_id: str
    pending_id: str
    decision: HookDecision | str
    decided_by: str
    decided_at: datetime
    note: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.decision = HookDecision(self.decision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module_name": self.module_name,
            "user_id": self.user_id,
            "pending_id": self.pending_id,
            "decision": self.decision.value,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.astimezone(timezone.utc).isoformat(),
            "note": self.note,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HookDecisionRecord":
        return cls(
            id=data["id"],
            module_name=data["module_name"],
            user_id=data["user_id"],
            pending_id=data["pending_id"],
            decision=data["decision"],
            decided_by=data["decided_by"],
            decided_at=_parse_datetime(data["decided_at"]),
            note=data.get("note", ""),
            payload=dict(data.get("payload", {})),
        )


@dataclass
class HookWriteMemoryRequest:
    """Generic request passed to a memory sink implementation."""

    module_name: str
    user_id: str
    title: str
    body: str
    source_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookContextInjection:
    """Generic confirmed item returned by a context sink."""

    module_name: str
    user_id: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_event_ids: list[str] = field(default_factory=list)
    confirmed_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "user_id": self.user_id,
            "summary": self.summary,
            "payload": self.payload,
            "source_event_ids": list(self.source_event_ids),
            "confirmed_at": (
                self.confirmed_at.astimezone(timezone.utc).isoformat()
                if self.confirmed_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HookContextInjection":
        confirmed_at = data.get("confirmed_at")
        return cls(
            module_name=data["module_name"],
            user_id=data["user_id"],
            summary=data.get("summary", ""),
            payload=dict(data.get("payload", {})),
            source_event_ids=list(data.get("source_event_ids", [])),
            confirmed_at=(
                _parse_datetime(confirmed_at)
                if isinstance(confirmed_at, str) and confirmed_at
                else None
            ),
        )


@dataclass
class HookScriptAction:
    """Parsed structured action produced by a script hook."""

    action_type: HookScriptActionType
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookScriptActionBatch:
    """Structured action batch returned by a script hook over stdout."""

    actions: list[HookScriptAction] = field(default_factory=list)
