# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    agent_id: str = "main"
    channel: str = "api"
    chat_type: str = "dm"
    scope: str = "main"
    account_id: str = "default"
    peer_id: Optional[str] = None


class SessionThreadCreateRequest(BaseModel):
    agent_id: str = "main"
    channel: str = "web"
    chat_type: str = "dm"
    account_id: str = "default"
    peer_id: Optional[str] = None


class SessionResponse(BaseModel):
    session_key: str
    agent_id: str
    channel: str
    user_id: str
    account_id: str = "default"
    chat_type: str = "dm"
    peer_id: str = "default"
    thread_id: Optional[str] = None
    created_at: datetime
    last_activity: datetime
    message_count: int
    total_tokens: int


class SessionHistoryMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime


class SessionHistoryResponse(BaseModel):
    messages: list[SessionHistoryMessage] = Field(default_factory=list)


class SessionResetRequest(BaseModel):
    archive: bool = True


class AgentRunRequest(BaseModel):
    session_key: str
    message: str
    model: Optional[str] = None
    timeout_seconds: int = 600
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    session_key: str


class AgentStatusResponse(BaseModel):
    run_id: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tokens_used: int = 0
    error: Optional[str] = None


class SkillExecuteRequest(BaseModel):
    skill_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class SkillExecuteResponse(BaseModel):
    skill_name: str
    result: Any
    duration_ms: int


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 10
    apply_recency: bool = True


class MemorySearchResult(BaseModel):
    id: str
    content: str
    score: float
    source: str
    timestamp: datetime
    highlights: list[str]


class MemoryWriteRequest(BaseModel):
    content: str
    memory_type: str = "daily"
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    section: str = "General"


class QueueModeRequest(BaseModel):
    mode: str


class LocalLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class StatusResponse(BaseModel):
    session_key: str
    context_tokens: int
    input_tokens: int
    output_tokens: int
    queue_mode: str
    queue_size: int


class CompactRequest(BaseModel):
    instruction: Optional[str] = None


class WebhookDispatchRequest(BaseModel):
    skill: str
    args: dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    timeout_seconds: int = 600


class WebhookDispatchResponse(BaseModel):
    status: str

