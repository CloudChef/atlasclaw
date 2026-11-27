from __future__ import annotations

import json

from app.atlasclaw.agent.skill_stage_context import (
    build_stage_context_message,
    completed_stage_for,
    new_stage_state,
    record_completed_stage,
    stage_identity,
)


def test_stage_context_keeps_complete_normalized_tool_pairs_and_original_request() -> None:
    state = new_stage_state(run_id="run-1", original_request="Keep the original constraint: include every row.")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "name": "write_report", "args": {"name": "x"}}]},
        {"role": "tool", "tool_name": "write_report", "tool_call_id": "call-1", "content": {"success": True, "rows": [1, 2, 3]}},
        {"role": "assistant", "content": "Complete report output."},
    ]

    record_completed_stage(
        state,
        target_skill_id="audit-report:collect",
        planned_input="Collect every row and include all results.",
        messages=messages,
        assistant_output="Complete report output.",
    )

    context = build_stage_context_message(state)
    assert "Keep the original constraint: include every row." in context
    assert "Collect every row and include all results." in context
    assert '"tool_call_id": "call-1"' in context
    data = context.split("BEGIN_SKILL_STAGE_CONTEXT_DATA\n", 1)[1].split(
        "\nEND_SKILL_STAGE_CONTEXT_DATA", 1
    )[0]
    parsed = json.loads(data)
    assert parsed["completed_stages"][0]["normalized_messages"][1]["content"]["rows"] == [1, 2, 3]
    assert "Complete report output." in context


def test_stage_identity_skips_only_same_canonical_target_and_planned_input() -> None:
    state = new_stage_state(run_id="run-2", original_request="Check the account")
    record_completed_stage(
        state,
        target_skill_id="provider-a:account-read",
        planned_input="Read account 17",
        messages=[{"role": "tool", "tool_name": "read_account", "content": {"id": 17}}],
        assistant_output="Account 17 is active.",
    )

    assert completed_stage_for(state, "provider-a:account-read", "Read account 17") is not None
    assert completed_stage_for(state, "provider-a:account-read", "Read account 18") is None
    assert completed_stage_for(state, "provider-b:account-read", "Read account 17") is None
    assert stage_identity("provider-a:account-read", "Read account 17") != stage_identity(
        "provider-a:account-read", "Read account 18"
    )


def test_stage_completion_record_is_idempotent() -> None:
    state = new_stage_state(run_id="run-3", original_request="Make a report")
    first = record_completed_stage(
        state,
        target_skill_id="skill:report",
        planned_input="Use the uploaded data",
        messages=[{"role": "assistant", "content": "v1"}],
        assistant_output="v1",
    )
    second = record_completed_stage(
        state,
        target_skill_id="skill:report",
        planned_input="Use the uploaded data",
        messages=[{"role": "assistant", "content": "v2"}],
        assistant_output="v2",
    )

    assert first is True
    assert second is False
    assert len(state["completed_stages"]) == 1
    assert state["completed_stages"][0]["assistant_output"] == "v1"
