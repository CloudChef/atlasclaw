"""Durable normalized context for model-bound Skill stages."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def new_stage_state(*, run_id: str, original_request: str) -> dict[str, Any]:
    """Create the server-owned durable state for one routed task."""
    return {
        "schema_version": 1,
        "run_id": str(run_id or "").strip(),
        "original_request": str(original_request or ""),
        "completed_stages": [],
        "status": "running",
    }


def stage_identity(target_skill_id: str, planned_input: str) -> str:
    """Hash the stable target and actual planned work, excluding growing history."""
    payload = json.dumps(
        {
            "target_skill_id": str(target_skill_id or "").strip(),
            "planned_input": str(planned_input or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def completed_stage_for(
    state: dict[str, Any],
    target_skill_id: str,
    planned_input: str,
) -> dict[str, Any] | None:
    """Return an already-completed identical stage, if present."""
    identity = stage_identity(target_skill_id, planned_input)
    for stage in state.get("completed_stages", []) or []:
        if not isinstance(stage, dict):
            continue
        if stage.get("identity") == identity:
            return stage
    return None


def record_completed_stage(
    state: dict[str, Any],
    *,
    target_skill_id: str,
    planned_input: str,
    messages: list[dict[str, Any]],
    assistant_output: str,
    stage_metadata: dict[str, Any] | None = None,
) -> bool:
    """Persist full normalized stage messages once per target/input identity."""
    stages = state.setdefault("completed_stages", [])
    if not isinstance(stages, list):
        stages = []
        state["completed_stages"] = stages
    identity = stage_identity(target_skill_id, planned_input)
    if any(isinstance(item, dict) and item.get("identity") == identity for item in stages):
        return False
    stage_record = {
        "identity": identity,
        "target_skill_id": str(target_skill_id or "").strip(),
        "planned_input": str(planned_input or ""),
        "messages": [dict(message) for message in messages if isinstance(message, dict)],
        "assistant_output": str(assistant_output or ""),
        "status": "completed",
    }
    if isinstance(stage_metadata, dict):
        stage_record["metadata"] = dict(stage_metadata)
    stages.append(stage_record)
    return True


def build_stage_context_message(state: dict[str, Any], *, planned_input: str = "") -> str:
    """Serialize all required task constraints and prior stage evidence without truncation."""
    payload = {
        "original_user_request": str(state.get("original_request", "") or ""),
        "follow_up_requests": [
            str(item or "") for item in (state.get("follow_up_requests", []) or [])
        ],
        "planned_stage_input": str(planned_input or ""),
        "workflow_status": str(state.get("status") or "running"),
        "recovery_outcome_unknown": bool(state.get("recovery_outcome_unknown")),
        "interrupted_stage": (
            dict(state.get("active_stage"))
            if isinstance(state.get("active_stage"), dict)
            else None
        ),
        "completed_stages": [
            {
                "target_skill_id": stage.get("target_skill_id", ""),
                "planned_input": stage.get("planned_input", ""),
                "normalized_messages": stage.get("messages", []),
                "assistant_output": stage.get("assistant_output", ""),
                "status": stage.get("status", "completed"),
            }
            for stage in (state.get("completed_stages", []) or [])
            if isinstance(stage, dict)
        ],
    }
    from app.atlasclaw.agent.prompt_sections import (
        build_skill_stage_context_end,
        build_skill_stage_context_frame,
    )

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"{build_skill_stage_context_frame()}\n{serialized}\n{build_skill_stage_context_end()}"
