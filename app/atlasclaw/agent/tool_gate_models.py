# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolPolicyMode(str, Enum):
    """Policy mode returned by the runtime tool gate."""

    ANSWER_DIRECT = "answer_direct"
    PREFER_TOOL = "prefer_tool"
    MUST_USE_TOOL = "must_use_tool"


class ToolIntentAction(str, Enum):
    """High-level turn action selected before entering the tool loop."""

    DIRECT_ANSWER = "direct_answer"
    ASK_CLARIFICATION = "ask_clarification"
    USE_TOOLS = "use_tools"


class ToolCandidate(BaseModel):
    """Concrete tool candidate matched for a required capability."""

    name: str
    capability_class: str
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolGateDecision(BaseModel):
    """Classification result describing whether tools are required."""

    needs_tool: bool = False
    needs_live_data: bool = False
    needs_private_context: bool = False
    needs_external_system: bool = False
    needs_browser_interaction: bool = False
    needs_grounded_verification: bool = False
    suggested_tool_classes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str
    policy: ToolPolicyMode = ToolPolicyMode.ANSWER_DIRECT


class ToolIntentPlan(BaseModel):
    """Structured planning result used to derive the minimal toolset for a turn."""

    action: ToolIntentAction = ToolIntentAction.DIRECT_ANSWER
    target_provider_types: list[str] = Field(default_factory=list)
    target_skill_names: list[str] = Field(default_factory=list)
    target_group_ids: list[str] = Field(default_factory=list)
    target_capability_classes: list[str] = Field(default_factory=list)
    target_tool_names: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    reason: str = ""


class CapabilityMatchResult(BaseModel):
    """Result of mapping gate decisions to available runtime tools."""

    resolved_policy: ToolPolicyMode
    tool_candidates: list[ToolCandidate] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    reason: str


class ToolEnforcementOutcome(BaseModel):
    """Result of enforcing the tool policy for the current run."""

    blocked_final_answer: bool = False
    requires_tool_first_path: bool = False
    failure_message: Optional[str] = None
