from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.atlasclaw.agent.runner_tool.runner_execution_loop import RunnerExecutionLoopMixin
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.runner_tool.runner_execution_runtime import RunnerExecutionRuntimeMixin
from app.atlasclaw.agent.runner_tool.runner_execution_retry import RunnerExecutionRetryMixin
from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import RunnerToolGateModelMixin
from app.atlasclaw.agent.skill_stage_context import (
    build_stage_context_message,
    new_stage_state,
    record_completed_stage,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.tool_gate_models import (
    ConversationTurnAction,
    ConversationTurnPlan,
    ConversationTurnRoute,
)
from app.atlasclaw.api.agent_capabilities import build_agent_capabilities
from app.atlasclaw.api.routes_agent import _filter_enabled_provider_instances
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool


class _SessionManager:
    def __init__(self) -> None:
        self.session = SimpleNamespace(extra={})

    async def get_or_create(self, _session_key: str):
        return self.session

    async def update_extra(self, _session_key: str, updates: dict) -> None:
        self.session.extra.update(updates)


class _WorkflowRunner(RunnerExecutionLoopMixin):
    def __init__(self, session_manager: _SessionManager) -> None:
        self.session_manager = session_manager
        self.token_policy = None
        self.calls: list[str] = []
        self.observed_contexts: list[dict] = []

    def _resolve_session_manager(self, _session_key: str, _deps):
        return self.session_manager

    async def _run_single_stage(
        self,
        *,
        session_key,
        user_message,
        deps,
        max_tool_calls,
        timeout_seconds,
        _token_failover_attempt,
        _emit_lifecycle_bounds,
    ):
        extra = deps.extra
        self.calls.append(str(user_message or ""))
        continuation = bool(extra.get("_skill_stage_continuation"))

        def emit_stage(target: str, planned_input: str, call_id: str, result: str, final: str):
            extra.setdefault("_skill_stage_usage_row_ids", []).append(
                f"usage-{target}"
            )
            extra["_skill_stage_descriptor"] = {
                "target_skill_id": target,
                "planned_input": planned_input,
                "continue_after_success": True,
            }
            extra["_skill_stage_latest_messages"] = [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": call_id, "name": f"{target}.run", "args": {"input": planned_input}}],
                },
                {
                    "role": "tool",
                    "tool_name": f"{target}.run",
                    "tool_call_id": call_id,
                    "content": {"complete_result": result},
                },
                {"role": "assistant", "content": final},
            ]
            extra["_skill_stage_final_output"] = final

        if not continuation:
            emit_stage(
                "crm:collect",
                "Collect every row from the export.",
                "call-b",
                "B" * 6000,
                "Skill B collected all 6000 rows.",
            )
            yield StreamEvent.assistant_delta("Skill B collected all 6000 rows.")
        else:
            message = str(extra.get("_skill_stage_context_message") or "")
            parsed = json.loads(
                message.split("BEGIN_SKILL_STAGE_CONTEXT_DATA\n", 1)[1].split(
                    "\nEND_SKILL_STAGE_CONTEXT_DATA", 1
                )[0]
            )
            self.observed_contexts.append(parsed)
            if len(self.observed_contexts) == 1:
                assert parsed["original_user_request"] == (
                    "Compare the account export and retain every original row."
                )
                assert parsed["completed_stages"][0]["target_skill_id"] == "crm:collect"
                assert parsed["completed_stages"][0]["normalized_messages"][1]["tool_call_id"] == "call-b"
                assert parsed["completed_stages"][0]["normalized_messages"][1]["content"][
                    "complete_result"
                ] == "B" * 6000
                emit_stage(
                    "crm:compare",
                    "Compare the full collection result against the account records.",
                    "call-c",
                    "C" * 7000,
                    "Skill C compared all collected rows.",
                )
                yield StreamEvent.assistant_delta("Skill C compared all collected rows.")
            else:
                assert [stage["target_skill_id"] for stage in parsed["completed_stages"]] == [
                    "crm:collect",
                    "crm:compare",
                ]
                assert parsed["completed_stages"][1]["normalized_messages"][1][
                    "tool_call_id"
                ] == "call-c"
                assert parsed["completed_stages"][1]["normalized_messages"][1]["content"][
                    "complete_result"
                ] == "C" * 7000
                # The coordinator has enough prior evidence and ends the task.
                extra.pop("_skill_stage_descriptor", None)
                extra["_skill_stage_final_output"] = "Final answer includes both Skill results."
                yield StreamEvent.assistant_delta("Final answer includes both Skill results.")
        yield StreamEvent.runtime_update("answered", "Final answer ready.")


@pytest.mark.asyncio
async def test_runner_continues_b_to_c_with_complete_history_once_and_replays_final_answer() -> None:
    session_manager = _SessionManager()
    runner = _WorkflowRunner(session_manager)
    deps = SimpleNamespace(
        extra={"run_id": "run-b-c"},
        session_manager=session_manager,
    )
    original = "Compare the account export and retain every original row."

    events = [event async for event in runner.run("session", original, deps)]

    assert len(runner.calls) == 3
    assert runner.calls[0] == original
    assert runner.calls[1:] == ["", ""]
    assert len(runner.observed_contexts) == 2
    assert not any("usage-" in json.dumps(context) for context in runner.observed_contexts)
    assert [event.content for event in events if event.type == "assistant"] == [
        "Final answer includes both Skill results."
    ]
    assert [
        (event.type, event.phase)
        for event in events
        if event.type == "lifecycle"
    ] == [("lifecycle", "start"), ("lifecycle", "end")]
    assert sum(
        event.type == "runtime"
        and (event.metadata or {}).get("state") == "answered"
        and event.content == "Final answer ready."
        for event in events
    ) == 1

    stored = session_manager.session.extra["skill_stage_workflow:run-b-c"]
    assert stored["status"] == "completed"
    assert stored["final_output"] == "Final answer includes both Skill results."
    assert [stage["target_skill_id"] for stage in stored["completed_stages"]] == [
        "crm:collect",
        "crm:compare",
    ]
    assert [stage["metadata"]["usage_record_ids"] for stage in stored["completed_stages"]] == [
        ["usage-crm:collect"],
        ["usage-crm:compare"],
    ]

    replay = [event async for event in runner.run("session", original, deps)]
    assert len(runner.calls) == 3
    assert [event.content for event in replay if event.type == "assistant"] == [
        "Final answer includes both Skill results."
    ]
    assert sum(
        event.type == "runtime"
        and (event.metadata or {}).get("state") == "answered"
        for event in replay
    ) == 1


@pytest.mark.asyncio
async def test_runner_does_not_replay_an_interrupted_stage() -> None:
    manager = _SessionManager()
    run_id = "run-interrupted"
    state = new_stage_state(run_id=run_id, original_request="Fetch the account")
    state["active_stage"] = {
        "identity": "some-stage",
        "target_skill_id": "crm:fetch",
        "planned_input": "Fetch account 17",
        "status": "started",
    }
    manager.session.extra[f"skill_stage_workflow:{run_id}"] = state
    runner = _WorkflowRunner(manager)
    deps = SimpleNamespace(extra={"run_id": run_id}, session_manager=manager)

    events = [event async for event in runner.run("session", "Fetch the account", deps)]

    assert runner.calls == []
    assert manager.session.extra[f"skill_stage_workflow:{run_id}"]["status"] == "unknown"
    assert any(event.type == "error" for event in events)


@pytest.mark.asyncio
async def test_continuation_planner_gets_full_stage_data_and_safe_image_metadata() -> None:
    from app.atlasclaw.agent.skill_stage_context import build_stage_context_message, record_completed_stage

    class Planner(_WorkflowRunner, RunnerToolGateModelMixin):
        async def _run_single_with_optional_override(self, **kwargs):
            self.planner_call = kwargs
            return ConversationTurnPlan(
                route=ConversationTurnRoute.START_NEW,
                action=ConversationTurnAction.USE_TOOLS,
                target_capability_ids=["crm:compare"],
                stage_input="Compare all rows from the complete result.",
            )

    manager = _SessionManager()
    planner = Planner(manager)
    state = new_stage_state(
        run_id="planner-run",
        original_request="Keep all rows, even when comparing account totals.",
    )
    record_completed_stage(
        state,
        target_skill_id="crm:collect",
        planned_input="Collect all rows",
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "collect-1", "name": "collect", "args": {}}]},
            {"role": "tool", "tool_name": "collect", "tool_call_id": "collect-1", "content": {"rows": "R" * 5000}},
        ],
        assistant_output="Collected " + "R" * 5000,
    )
    context_message = build_stage_context_message(state)
    deps = SimpleNamespace(
        session_key="session",
        extra={
            "_skill_stage_continuation": True,
            "_skill_stage_context_message": context_message,
            "_chat_attachments": [{
                "id": "image-1", "kind": "image", "name": "page.png",
                "mime_type": "image/png", "size": 4, "data": b"NATIVE_IMAGE_BYTES",
            }],
            "_chat_image_inputs": {"image-1": b"NATIVE_IMAGE_BYTES"},
        },
    )

    plan = await planner._plan_conversation_turn_with_model(
        agent=object(),
        deps=deps,
        user_message="",
        recent_history=[{"role": "tool", "content": "small recent result"}],
        capability_index=[{"capability_id": "crm:compare", "name": "Compare", "description": "Compare accounts"}],
    )

    assert plan is not None
    assert plan.stage_input == "Compare all rows from the complete result."
    assert "R" * 5000 in planner.planner_call["user_message"]
    assert "NATIVE_IMAGE_BYTES" not in planner.planner_call["user_message"]
    assert "NATIVE_IMAGE_BYTES" not in planner.planner_call["system_prompt"]
    assert '"id":"image-1"' in planner.planner_call["user_message"]
    assert "complete original request" in planner.planner_call["system_prompt"]


@pytest.mark.asyncio
async def test_disabled_json_provider_is_removed_before_capability_catalog() -> None:
    class ProviderSkillRegistry:
        def md_snapshot(self):
            return [{
                "name": "audit",
                "qualified_name": "crm:audit",
                "provider": "crm",
                "metadata": {"provider_type": "crm"},
            }]

        def tools_snapshot(self):
            return []

    class Resolver:
        async def provider_is_enabled(self, provider_type, instance_name):
            return not (provider_type == "crm" and instance_name == "json-instance")

    provider_instances = {
        "crm": {"json-instance": {"base_url": "https://example.test"}},
        "active": {"default": {"base_url": "https://example.test"}},
    }
    ctx = SimpleNamespace(
        model_binding_resolver=Resolver(),
        skill_registry=ProviderSkillRegistry(),
        service_provider_registry=None,
        provider_instances=provider_instances,
    )
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice"),
        permissions={
            "skills": {"allow_all": False, "skill_permissions": []},
            "providers": {
                "allow_all": False,
                "provider_permissions": [{
                    "provider_type": "crm", "instance_name": "json-instance", "allowed": True,
                }],
            },
        },
    )
    visible_instances = await _filter_enabled_provider_instances(
        ctx=ctx,
        provider_instances=provider_instances,
    )

    catalog = build_agent_capabilities(
        ctx=ctx,
        authz=authz,
        provider_instances=visible_instances,
    )
    assert catalog["capabilities"] == []


@pytest.mark.asyncio
async def test_selected_provider_skill_runs_on_bound_model_and_next_skill_gets_full_evidence() -> None:
    from app.atlasclaw.core.model_bindings import BindingTarget

    original_constraint = "Retain every row from the export when comparing account totals."
    evidence = "ROW-EVIDENCE-" * 500
    tokens = {
        "model-b": TokenEntry("model-b", "openai", "skill-b", "https://b.invalid", "key-b"),
        "model-c": TokenEntry("model-c", "openai", "skill-c", "https://c.invalid", "key-c"),
    }
    resolved_targets: list[BindingTarget] = []
    captured_user_input: dict[str, list[str]] = {key: [] for key in tokens}
    write_calls: dict[str, int] = {key: 0 for key in tokens}

    class Resolver:
        async def resolve(self, target, *, authz=None, required_capabilities=None, admission=None):
            resolved_targets.append(target)
            return SimpleNamespace(
                token=tokens["model-b" if target.skill_id == "crm:audit" else "model-c"],
                policy_id="policy-1",
                eligible_tokens=tuple(tokens.values()),
                pool_token_ids={key: key for key in tokens},
            )

    class TokenPolicy:
        token_pool = SimpleNamespace(tokens=tokens)

        async def refresh_tokens(self):
            return None

    class Pool:
        async def get_or_create(self, _agent_id, token, factory):
            return SimpleNamespace(
                agent=factory("agent", token),
                concurrency_sem=asyncio.Semaphore(1),
            )

    class Runner(RunnerExecutionRuntimeMixin):
        pass

    def factory(_agent_id, token):
        token_id = token.token_id
        calls = 0

        def respond(messages, _info):
            nonlocal calls
            for message in messages:
                for part in getattr(message, "parts", []):
                    if getattr(part, "part_kind", "") == "user-prompt":
                        captured_user_input[token_id].append(str(part.content))
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[ToolCallPart("write_result", {"payload": token_id}, f"call-{token_id}")]
                )
            return ModelResponse(parts=[TextPart(f"{token_id} finished")])

        agent = PydanticAgent(FunctionModel(respond, model_name=token.model), deps_type=SkillDeps)

        async def write_result(payload: str) -> str:
            write_calls[token_id] += 1
            return evidence if token_id == "model-b" else f"C used {payload}"

        agent.tool_plain(write_result, name="write_result")
        return agent

    runner = Runner()
    runner.agent = PydanticAgent(FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart("base")])) )
    runner.agent_id = "main"
    runner.agent_pool = Pool()
    runner.agent_factory = factory
    runner.model_binding_resolver = Resolver()
    runner.token_policy = TokenPolicy()

    registry = SimpleNamespace(
        md_snapshot=lambda: [{
            "name": "audit",
            "qualified_name": "crm:audit",
            "provider": "crm",
            "metadata": {"provider_type": "crm"},
        }],
        tools_snapshot=lambda: [],
    )
    ctx = SimpleNamespace(skill_registry=registry, service_provider_registry=None)
    authz = AuthorizationContext(
        user=UserInfo(user_id="alice"),
        permissions={
            "skills": {"allow_all": False, "skill_permissions": []},
            "providers": {
                "allow_all": False,
                "provider_permissions": [{
                    "provider_type": "crm", "instance_name": "prod", "allowed": True,
                }],
            },
        },
    )
    catalog = build_agent_capabilities(
        ctx=ctx,
        authz=authz,
        provider_instances={"crm": {"prod": {}}},
    )
    selected = catalog["capabilities"][0]
    assert selected["qualified_skill_name"] == "crm:audit"
    deps = SkillDeps(extra={"run_id": "dispatch-run", "_selected_capability": selected})

    agent_b, token_b, release_b = await runner._resolve_runtime_agent("session", deps)
    assert token_b == "model-b"
    assert resolved_targets[0].skill_id == "crm:audit"
    initial_stage_input = "Audit each row and preserve the complete evidence."
    initial_state = new_stage_state(run_id="dispatch-run", original_request=original_constraint)
    b_context = build_stage_context_message(initial_state, planned_input=initial_stage_input)
    try:
        result_b = await agent_b.run(b_context, deps=deps)
    finally:
        release_b()
    assert result_b.output == "model-b finished"
    b_input = "\n".join(captured_user_input["model-b"])
    assert original_constraint in b_input
    assert initial_stage_input in b_input

    state = new_stage_state(run_id="dispatch-run", original_request=original_constraint)
    record_completed_stage(
        state,
        target_skill_id="crm:audit",
        planned_input=initial_stage_input,
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "call-model-b", "name": "write_result", "args": {}}]},
            {"role": "tool", "tool_name": "write_result", "tool_call_id": "call-model-b", "content": evidence},
            {"role": "assistant", "content": result_b.output},
        ],
        assistant_output=result_b.output,
    )
    stage_context = build_stage_context_message(
        state,
        planned_input="Compare every retained row to account totals.",
    )
    agent_c, token_c, release_c = await runner._resolve_runtime_agent(
        "session",
        deps,
        binding_target=BindingTarget(kind="standalone_skill", skill_id="reports:compare"),
    )
    assert token_c == "model-c"
    try:
        result_c = await agent_c.run(stage_context, deps=deps)
    finally:
        release_c()

    assert result_c.output == "model-c finished"
    assert write_calls == {"model-b": 1, "model-c": 1}
    c_input = "\n".join(captured_user_input["model-c"])
    assert original_constraint in c_input
    assert "Compare every retained row to account totals." in c_input
    assert evidence in c_input
    assert resolved_targets == [
        BindingTarget(kind="provider_skill", provider_type="crm", instance_name="prod", skill_id="crm:audit"),
        BindingTarget(kind="standalone_skill", skill_id="reports:compare"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_permitted_retry", [False, True])
async def test_bound_retry_stays_within_policy_and_does_not_reacquire_failed_slot(
    has_permitted_retry: bool,
) -> None:
    from app.atlasclaw.core.model_bindings import BindingTarget

    failed = TokenEntry("bound-failed", "openai", "model-a", "https://a.invalid", "key-a")
    permitted = TokenEntry("bound-retry", "openai", "model-b", "https://b.invalid", "key-b")
    outsider = TokenEntry("unrelated-platform", "anthropic", "model-c", "https://c.invalid", "key-c")
    pool = TokenPool()
    pool.replace_tokens([failed, permitted, outsider])
    selection = SimpleNamespace(
        token=failed,
        eligible_tokens=(failed, permitted) if has_permitted_retry else (failed,),
        pool_token_ids={
            failed.token_id: failed.token_id,
            **({permitted.token_id: permitted.token_id} if has_permitted_retry else {}),
        },
    )
    target = BindingTarget(kind="standalone_skill", skill_id="crm:audit")

    class TokenPolicy:
        token_pool = pool

    class RetryRunner(RunnerExecutionRetryMixin):
        token_policy = TokenPolicy()
        token_interceptor = None

        async def _resolve_runtime_agent(self, _session_key, deps, *, binding_target, acquire_slot):
            assert acquire_slot is False
            assert binding_target == target
            deps.extra["_current_binding_selection"] = selection
            return object(), failed.token_id, None

    runner = RetryRunner()
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    deps = SimpleNamespace(
        extra={
            "_current_binding_target": target,
            "_current_binding_selection": selection,
            "_selected_token_fingerprint": failed.credential_fingerprint,
        }
    )
    chosen = await asyncio.wait_for(
        runner._select_bound_retry_token(
            session_key="session",
            deps=deps,
            failed_token_id=failed.token_id,
            error_text="401 invalid API key",
        ),
        timeout=0.1,
    )
    sem.release()

    if has_permitted_retry:
        assert chosen == permitted
    else:
        assert chosen is None
    assert chosen != outsider
    assert pool.get_token_health(failed.token_id).is_healthy is False


@pytest.mark.asyncio
async def test_skill_retry_uses_current_stage_side_effects_and_keeps_the_validated_plan() -> None:
    failed = TokenEntry("bound-failed", "openai", "model-a", "https://a.invalid", "key-a")
    permitted = TokenEntry("bound-retry", "openai", "model-b", "https://b.invalid", "key-b")

    class TokenPolicy:
        token_pool = SimpleNamespace(tokens={failed.token_id: failed, permitted.token_id: permitted})

    class Emitter:
        async def close_if_active(self):
            if False:
                yield None

    class RetryRunner(RunnerExecutionRetryMixin):
        token_policy = TokenPolicy()
        token_interceptor = None
        TOKEN_FAILOVER_MAX_ATTEMPTS = 1

        def _is_hard_token_failure(self, _error):
            return True

        async def _select_bound_retry_token(self, **_kwargs):
            return permitted

        async def _run_single_stage(self, **kwargs):
            self.retried = kwargs
            if False:
                yield None

    retry_snapshot = {
        "tool_intent_plan": {"target_skill_names": ["crm:compare"]},
        "conversation_turn_plan": {"stage_input": "Compare the saved evidence."},
        "stage_input": "Compare the saved evidence.",
    }
    runner = RetryRunner()
    runner.retried = None
    deps = SimpleNamespace(extra={
        "_skill_stage_active": True,
        "_skill_stage_side_effect_started": False,
        "_chat_tool_started": True,  # prior Skill B had already written once
        "_skill_stage_retry_snapshot": retry_snapshot,
        "_current_binding_target": object(),
        "_current_binding_selection": SimpleNamespace(eligible_tokens=(failed, permitted)),
    })
    release_count = 0

    def release_slot():
        nonlocal release_count
        release_count += 1

    events = [
        event
        async for event in runner._retry_after_hard_token_failure(
            error=RuntimeError("401 invalid API key"),
            session_key="session",
            user_message="",
            deps=deps,
            selected_token_id=failed.token_id,
            release_slot=release_slot,
            thinking_emitter=Emitter(),
            start_time=0.0,
            max_tool_calls=4,
            timeout_seconds=10,
            token_failover_attempt=0,
            emit_lifecycle_bounds=False,
        )
    ]
    assert runner.retried is not None
    assert runner.retried["_token_failover_attempt"] == 1
    assert deps.extra["_skill_stage_retry_snapshot"] is retry_snapshot
    assert deps.extra["_binding_retry_token"] == permitted
    assert release_count == 1
    assert any((event.metadata or {}).get("state") == "retrying" for event in events)

    no_replay_runner = RetryRunner()
    no_replay_runner.retried = None
    side_effect_deps = SimpleNamespace(extra={
        "_skill_stage_active": True,
        "_skill_stage_side_effect_started": True,
        "_chat_tool_started": True,
        "_current_binding_target": object(),
        "_current_binding_selection": SimpleNamespace(eligible_tokens=(failed, permitted)),
    })
    blocked_events = [
        event
        async for event in no_replay_runner._retry_after_hard_token_failure(
            error=RuntimeError("401 invalid API key"),
            session_key="session",
            user_message="",
            deps=side_effect_deps,
            selected_token_id=failed.token_id,
            release_slot=None,
            thinking_emitter=Emitter(),
            start_time=0.0,
            max_tool_calls=4,
            timeout_seconds=10,
            token_failover_attempt=0,
            emit_lifecycle_bounds=False,
        )
    ]
    assert no_replay_runner.retried is None
    assert blocked_events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_bound", [False, True])
async def test_natural_executable_skill_uses_its_canonical_model_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    provider_bound: bool,
) -> None:
    import app.atlasclaw.agent.runner_tool.runner_execution_prepare as prepare_module
    from app.atlasclaw.core.model_bindings import BindingTarget

    coordinator_token = TokenEntry("model-coordinator", "openai", "coordinator", "https://a.invalid", "key-a")
    skill_token = TokenEntry("model-skill", "openai", "bound-skill", "https://b.invalid", "key-b")
    tokens = {token.token_id: token for token in (coordinator_token, skill_token)}
    executed: list[str] = []
    selected_targets: list[BindingTarget] = []
    canonical_skill_id = "reports-compare"
    expected_target = (
        BindingTarget(
            kind="provider_skill",
            provider_type="crm",
            instance_name="prod",
            skill_id=canonical_skill_id,
        )
        if provider_bound
        else BindingTarget(kind="standalone_skill", skill_id=canonical_skill_id)
    )

    class Registry:
        def snapshot(self):
            return [{
                "name": canonical_skill_id,
                "source": "user",
                "provider_type": "crm" if provider_bound else "",
            }]

        def get(self, name):
            if name != canonical_skill_id:
                return None
            return (
                SimpleNamespace(
                    name=canonical_skill_id,
                    provider_type="crm" if provider_bound else None,
                ),
                object(),
            )

    registry = Registry()

    class Resolver:
        skill_registry = registry

        async def resolve(self, target, *, authz=None, required_capabilities=None, admission=None):
            selected_targets.append(target)
            assert target == expected_target
            return SimpleNamespace(
                token=skill_token,
                policy_id="skill-policy",
                revision=3,
                source="skill",
                reason="explicit skill model policy",
                eligible_tokens=(skill_token,),
                pool_token_ids={skill_token.token_id: skill_token.token_id},
            )

    class TokenPolicy:
        token_pool = SimpleNamespace(tokens=tokens)

        async def refresh_tokens(self):
            return None

        def get_or_select_session_token(self, _session_key, *, provider=None, model=None):
            return coordinator_token

    class Pool:
        _instances: dict[str, Any] = {}

        async def get_or_create(self, _agent_id, token, factory):
            if token.token_id not in self._instances:
                self._instances[token.token_id] = SimpleNamespace(
                    agent=factory("main", token),
                    concurrency_sem=asyncio.Semaphore(1),
                )
            return self._instances[token.token_id]

    def factory(_agent_id, token):
        if token.token_id == coordinator_token.token_id:
            return PydanticAgent(
                FunctionModel(
                    lambda _messages, _info: ModelResponse(parts=[TextPart("Comparison is complete.")]),
                    model_name=token.model,
                ),
                deps_type=SkillDeps,
            )

        def respond(messages, _info):
            user_text = "\n".join(
                str(part.content)
                for message in messages
                for part in getattr(message, "parts", [])
                if getattr(part, "part_kind", "") == "user-prompt"
            )
            assert "Compare all retained report rows." in user_text
            return ModelResponse(
                parts=[ToolCallPart("run_skill", {"input": "Compare all retained report rows."}, "skill-call")]
                if not executed
                else [TextPart("The report rows match.")]
            )

        agent = PydanticAgent(FunctionModel(respond, model_name=token.model), deps_type=SkillDeps)

        async def run_skill(input: str) -> str:
            executed.append(input)
            return "ROW-COMPARE-EVIDENCE: every retained row matches."

        agent.tool_plain(run_skill, name="run_skill")
        return agent

    provider_skill_name = "prod.reports-compare"
    capability_index = ([{
        "capability_id": f"provider_skill:{provider_skill_name}",
        "kind": "provider_skill",
        "name": provider_skill_name,
        "qualified_skill_name": canonical_skill_id,
        "target_provider_skill_names": [provider_skill_name],
        "target_provider_instances": ["crm.prod"],
        "target_provider_types": ["crm"],
        "target_tool_names": ["run_skill"],
        "declared_tool_names": ["run_skill"],
    }] if provider_bound else [{
        "capability_id": f"skill:{canonical_skill_id}",
        "kind": "skill",
        "name": canonical_skill_id,
        "qualified_skill_name": canonical_skill_id,
        "declared_tool_names": ["run_skill"],
    }])
    executable_tool_snapshot = [{
            "name": "run_skill",
            "description": "Run the authorized report comparison Skill.",
            "provider_type": "crm" if provider_bound else "",
            "provider_skill_name": provider_skill_name if provider_bound else "",
            "skill_name": canonical_skill_id,
            "qualified_skill_name": canonical_skill_id,
            "source": "provider" if provider_bound else "user",
        }]
    if not provider_bound:
        monkeypatch.setattr(
            prepare_module,
            "collect_capability_index_snapshot",
            lambda **_kwargs: capability_index,
        )
        monkeypatch.setattr(
            prepare_module,
            "resolve_selected_md_skill_target",
            lambda **_kwargs: None,
        )
    monkeypatch.setattr(
        prepare_module,
        "collect_tools_snapshot",
        lambda **_kwargs: executable_tool_snapshot,
    )

    session_manager = SessionManager(agents_dir=str(tmp_path / "sessions"))
    runner = AgentRunner(
        agent=PydanticAgent(
            FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart("base")])),
            deps_type=SkillDeps,
        ),
        session_manager=session_manager,
        prompt_builder=PromptBuilder(PromptBuilderConfig(workspace_path=str(tmp_path / "workspace"))),
        token_policy=TokenPolicy(),
        agent_pool=Pool(),
        agent_factory=factory,
        model_binding_resolver=Resolver(),
    )
    plan_calls = 0

    async def plan_turn(*, deps, **_kwargs):
        nonlocal plan_calls
        plan_calls += 1
        completed = (deps.extra.get("_skill_stage_state") or {}).get("completed_stages", [])
        if completed:
            return ConversationTurnPlan(
                route=ConversationTurnRoute.ORDINARY,
                action=ConversationTurnAction.RESPOND,
            )
        return ConversationTurnPlan(
            route=ConversationTurnRoute.START_NEW,
            action=ConversationTurnAction.USE_TOOLS,
            target_capability_ids=[
                f"provider_skill:{provider_skill_name}"
                if provider_bound
                else f"skill:{canonical_skill_id}"
            ],
            stage_input="Compare all retained report rows.",
        )

    runner._plan_conversation_turn_with_model = plan_turn
    deps = SkillDeps(
        session_key="agent:main:user:alice",
        session_manager=session_manager,
        extra={
            "run_id": "executable-skill-run",
            **({"provider_instances": {"crm": {"prod": {}}}} if provider_bound else {}),
            **({
                "skills_snapshot": registry.snapshot(),
                "tools_snapshot": executable_tool_snapshot,
                "tools_snapshot_authoritative": True,
            } if provider_bound else {}),
        },
    )
    events = [
        event
        async for event in runner.run(
            deps.session_key,
            "Compare the report rows without dropping anything.",
            deps,
            max_tool_calls=4,
            timeout_seconds=30,
        )
    ]

    assert selected_targets == [expected_target]
    assert executed == ["Compare all retained report rows."]
    assert plan_calls == 2
    assert any(
        event.type == "runtime" and (event.metadata or {}).get("state") == "skill_execution_completed"
        for event in events
    )
    stored = (await session_manager.get_or_create(deps.session_key)).extra[
        "skill_stage_workflow:executable-skill-run"
    ]
    assert stored["completed_stages"][0]["target_skill_id"] == canonical_skill_id
    assert stored["completed_stages"][0]["metadata"]["policy_id"] == "skill-policy"


@pytest.mark.asyncio
async def test_public_runner_dispatches_bound_b_c_d_with_real_tool_calls_and_inherited_middle_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import app.atlasclaw.agent.runner_tool.runner_execution_prepare as prepare_module
    from app.atlasclaw.agent.tool_gate_models import ConversationTurnPlan

    original = "Retain every source row while checking the account totals."
    planned_inputs = {
        "crm:audit": "Audit every exported row and preserve its evidence.",
        "crm:compare": "Compare all audited rows with the account totals.",
        "crm:finalize": "Verify and summarize the completed comparison.",
    }
    evidence = "FULL-ROW-EVIDENCE-" * 500
    tokens = {
        token_id: TokenEntry(token_id, "openai", model, f"https://{token_id}.invalid", f"key-{token_id}")
        for token_id, model in (
            ("model-a", "coordinator"),
            ("model-b", "audit"),
            ("model-c", "compare"),
            ("model-d", "finalize"),
        )
    }
    model_calls: dict[str, int] = {key: 0 for key in tokens}
    tool_calls: dict[str, int] = {key: 0 for key in tokens}
    captured_user_inputs: dict[str, list[str]] = {key: [] for key in tokens}

    class Resolver:
        async def resolve(self, target, *, authz=None, required_capabilities=None, admission=None):
            token_id = {
                "crm:audit": "model-b",
                "crm:compare": "model-c",
                "crm:finalize": "model-d",
            }[target.skill_id]
            return SimpleNamespace(
                token=tokens[token_id],
                policy_id=None if target.skill_id == "crm:compare" else "inherited-policy",
                revision=7,
                eligible_tokens=tuple(tokens.values()),
                pool_token_ids={key: key for key in tokens},
                source="inherited_provider",
                reason="test policy selection",
            )

    class TokenPolicy:
        token_pool = SimpleNamespace(tokens=tokens)

        async def refresh_tokens(self):
            return None

        def get_or_select_session_token(self, _session_key, *, provider=None, model=None):
            return tokens["model-a"]

    class Pool:
        _instances: dict[str, Any] = {}

        async def get_or_create(self, _agent_id, token, factory):
            if token.token_id not in self._instances:
                self._instances[token.token_id] = SimpleNamespace(
                    agent=factory("main", token),
                    concurrency_sem=asyncio.Semaphore(1),
                )
            return self._instances[token.token_id]

    def factory(_agent_id, token):
        token_id = token.token_id

        def respond(messages, _info):
            model_calls[token_id] += 1
            for message in messages:
                for part in getattr(message, "parts", []):
                    if getattr(part, "part_kind", "") == "user-prompt":
                        captured_user_inputs[token_id].append(str(part.content))
            if token_id == "model-a":
                return ModelResponse(parts=[TextPart("Final answer includes audit, comparison, and verification.")])
            if token_id != "model-a" and model_calls[token_id] % 2 == 1:
                return ModelResponse(
                    parts=[ToolCallPart("execute_skill", {"work": "execute planned stage"}, f"call-{token_id}")]
                )
            return ModelResponse(parts=[TextPart(f"{token_id} completed")])

        agent = PydanticAgent(FunctionModel(respond, model_name=token.model), deps_type=SkillDeps)
        if token_id != "model-a":
            async def execute_skill(work: str) -> str:
                tool_calls[token_id] += 1
                if token_id == "model-b":
                    return evidence
                if token_id == "model-c":
                    return f"Compared the full prior evidence for: {work}"
                return f"Verified the comparison and retained evidence for: {work}"

            agent.tool_plain(execute_skill, name="execute_skill")
        return agent

    caps = [
        {
            "capability_id": "provider_skill:prod.audit",
            "name": "prod.audit",
            "qualified_skill_name": "crm:audit",
            "skill_name": "audit",
            "provider_type": "crm",
            "provider_name": "prod",
            "instance_name": "prod",
            "target_provider_instances": ["crm.prod"],
            "target_provider_types": ["crm"],
            "target_provider_skill_names": ["prod.audit"],
            "target_tool_names": ["execute_skill"],
        },
        {
            "capability_id": "provider_skill:prod.compare",
            "name": "prod.compare",
            "qualified_skill_name": "crm:compare",
            "skill_name": "compare",
            "provider_type": "crm",
            "provider_name": "prod",
            "instance_name": "prod",
            "target_provider_instances": ["crm.prod"],
            "target_provider_types": ["crm"],
            "target_provider_skill_names": ["prod.compare"],
            "target_tool_names": ["execute_skill"],
        },
        {
            "capability_id": "provider_skill:prod.finalize",
            "name": "prod.finalize",
            "qualified_skill_name": "crm:finalize",
            "skill_name": "finalize",
            "provider_type": "crm",
            "provider_name": "prod",
            "instance_name": "prod",
            "target_provider_instances": ["crm.prod"],
            "target_provider_types": ["crm"],
            "target_provider_skill_names": ["prod.finalize"],
            "target_tool_names": ["execute_skill"],
        },
    ]
    tool_snapshots = [
        {
            "name": "execute_skill",
            "provider_type": "crm",
            "provider_skill_name": "prod.audit",
            "qualified_skill_name": "crm:audit",
            "skill_name": "audit",
            "capability_class": "lookup",
            "source": "provider",
        },
        {
            "name": "execute_skill",
            "provider_type": "crm",
            "provider_skill_name": "prod.compare",
            "qualified_skill_name": "crm:compare",
            "skill_name": "compare",
            "capability_class": "lookup",
            "source": "provider",
        },
        {
            "name": "execute_skill",
            "provider_type": "crm",
            "provider_skill_name": "prod.finalize",
            "qualified_skill_name": "crm:finalize",
            "skill_name": "finalize",
            "capability_class": "lookup",
            "source": "provider",
        },
    ]
    monkeypatch.setattr(
        prepare_module,
        "collect_capability_index_snapshot",
        lambda **_kwargs: caps,
    )
    monkeypatch.setattr(prepare_module, "collect_tools_snapshot", lambda **_kwargs: tool_snapshots)

    def resolve_md_skill_target(*, intent_plan, **_kwargs):
        skill_id = {
            ("prod.audit",): "crm:audit",
            ("prod.compare",): "crm:compare",
            ("prod.finalize",): "crm:finalize",
        }[tuple(intent_plan.target_provider_skill_names)]
        return {
            "qualified_name": skill_id,
            "name": skill_id.split(":", 1)[1],
            "provider": "crm",
            "instance_name": "prod",
            "instructions": f"Execute {skill_id} with its authorized tool.",
        }

    monkeypatch.setattr(prepare_module, "resolve_selected_md_skill_target", resolve_md_skill_target)
    session_manager = SessionManager(agents_dir=str(tmp_path / "sessions"))
    runner = AgentRunner(
        agent=PydanticAgent(FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart("base")])) , deps_type=SkillDeps),
        session_manager=session_manager,
        prompt_builder=PromptBuilder(PromptBuilderConfig(workspace_path=str(tmp_path / "workspace"))),
        token_policy=TokenPolicy(),
        agent_pool=Pool(),
        agent_factory=factory,
        model_binding_resolver=Resolver(),
    )

    observed_plans: list[tuple[str, bool, ConversationTurnRoute]] = []

    async def plan_turn(*, deps, **_kwargs):
        if deps.extra.get("_test_start_new"):
            if deps.extra.get("_skill_stage_continuation"):
                plan = ConversationTurnPlan(
                    route=ConversationTurnRoute.ORDINARY,
                    action=ConversationTurnAction.RESPOND,
                )
            else:
                plan = ConversationTurnPlan(
                    route=ConversationTurnRoute.START_NEW,
                    action=ConversationTurnAction.USE_TOOLS,
                    target_capability_ids=["provider_skill:prod.compare"],
                    stage_input="Compare this new request's totals.",
                )
        elif isinstance(deps.extra.get("_skill_stage_candidate_state"), dict):
            plan = ConversationTurnPlan(
                route=ConversationTurnRoute.CONTINUE_ACTIVE,
                action=ConversationTurnAction.USE_TOOLS,
                reason="Continue the saved audit workflow.",
            )
        else:
            completed = (deps.extra.get("_skill_stage_state") or {}).get("completed_stages", [])
            if len(completed) == 0:
                skill_id = "crm:audit"
                route = ConversationTurnRoute.START_NEW
                action = ConversationTurnAction.USE_TOOLS
                targets = ["provider_skill:prod.audit"]
            elif len(completed) == 1:
                skill_id = "crm:compare"
                route = ConversationTurnRoute.START_NEW
                action = ConversationTurnAction.USE_TOOLS
                targets = ["provider_skill:prod.compare"]
            elif len(completed) == 2:
                skill_id = "crm:finalize"
                route = ConversationTurnRoute.START_NEW
                action = ConversationTurnAction.USE_TOOLS
                targets = ["provider_skill:prod.finalize"]
            else:
                skill_id = ""
                route = ConversationTurnRoute.ORDINARY
                action = ConversationTurnAction.RESPOND
                targets = []
            plan = ConversationTurnPlan(
                route=route,
                action=action,
                target_capability_ids=targets,
                stage_input=planned_inputs.get(skill_id, ""),
            )
        observed_plans.append(
            (
                str(deps.extra.get("run_id") or ""),
                isinstance(deps.extra.get("_skill_stage_candidate_state"), dict),
                plan.route,
            )
        )
        return plan

    runner._plan_conversation_turn_with_model = plan_turn
    deps = SkillDeps(
        session_key="agent:main:user:alice",
        session_manager=session_manager,
        extra={
            "run_id": "public-dispatch-run",
            "provider_instances": {"crm": {"prod": {}}},
            "md_skills_snapshot": [
                {"name": "audit", "qualified_name": "crm:audit", "provider": "crm", "metadata": {}},
                {"name": "compare", "qualified_name": "crm:compare", "provider": "crm", "metadata": {}},
                {"name": "finalize", "qualified_name": "crm:finalize", "provider": "crm", "metadata": {}},
            ],
        },
    )

    events = [
        event
        async for event in runner.run(
            deps.session_key,
            original,
            deps,
            max_tool_calls=6,
            timeout_seconds=30,
        )
    ]

    assert tool_calls == {"model-a": 0, "model-b": 1, "model-c": 1, "model-d": 1}
    assert [event.content for event in events if event.type == "assistant"] == [
        "Final answer includes audit, comparison, and verification."
    ]
    assert sum(
        event.type == "runtime" and (event.metadata or {}).get("state") == "answered"
        for event in events
    ) == 1
    assert [(event.type, event.phase) for event in events if event.type == "lifecycle"] == [
        ("lifecycle", "start"),
        ("lifecycle", "end"),
    ]
    b_input = "\n".join(captured_user_inputs["model-b"])
    c_input = "\n".join(captured_user_inputs["model-c"])
    d_input = "\n".join(captured_user_inputs["model-d"])
    coordinator_input = "\n".join(captured_user_inputs["model-a"])
    assert original in b_input and planned_inputs["crm:audit"] in b_input
    assert original in c_input and planned_inputs["crm:compare"] in c_input and evidence in c_input
    assert original in d_input and planned_inputs["crm:finalize"] in d_input
    assert evidence in d_input and "Compared the full prior evidence" in d_input
    assert evidence in coordinator_input
    assert all(
        item.count("BEGIN_SKILL_STAGE_CONTEXT_DATA") == 1
        for item in captured_user_inputs["model-b"]
        if "BEGIN_SKILL_STAGE_CONTEXT_DATA" in item
    )
    assert all(
        item.count("BEGIN_SKILL_STAGE_CONTEXT_DATA") == 1
        for item in captured_user_inputs["model-c"]
        if "BEGIN_SKILL_STAGE_CONTEXT_DATA" in item
    )
    assert coordinator_input.count("BEGIN_SKILL_STAGE_CONTEXT_DATA") == 1
    assert sum(
        event.type == "runtime" and (event.metadata or {}).get("state") == "model_binding_resolved"
        for event in events
    ) == 3
    assert sum(
        event.type == "runtime" and (event.metadata or {}).get("state") == "skill_execution_started"
        for event in events
    ) == 3
    assert sum(
        event.type == "runtime" and (event.metadata or {}).get("state") == "skill_execution_completed"
        for event in events
    ) == 3
    session = await session_manager.get_or_create(deps.session_key)
    transcript = await session_manager.load_transcript(deps.session_key)
    assert not any(
        "BEGIN_SKILL_STAGE_CONTEXT_DATA" in entry.content
        for entry in transcript
        if entry.role == "user"
    )
    completed_state = session.extra["skill_stage_workflow:public-dispatch-run"]
    assert [stage["target_skill_id"] for stage in completed_state["completed_stages"]] == [
        "crm:audit",
        "crm:compare",
        "crm:finalize",
    ]
    middle_metadata = completed_state["completed_stages"][1]["metadata"]
    assert middle_metadata["policy_id"] is None
    assert middle_metadata["policy_revision"] == 7
    assert middle_metadata["run_id"] == "public-dispatch-run"
    assert middle_metadata["stage_id"] == completed_state["completed_stages"][1]["identity"]
    assert middle_metadata["model_ref"] == "model-c"
    assert middle_metadata["model_id"] == "compare"
    assert middle_metadata["selection_reason"] == "test policy selection"
    assert "key-c" not in str(middle_metadata)

    prior_state = new_stage_state(run_id="previous-run", original_request=original)
    record_completed_stage(
        prior_state,
        target_skill_id="crm:audit",
        planned_input=planned_inputs["crm:audit"],
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "prior-b-call", "name": "execute_skill", "args": {}}]},
            {"role": "tool", "tool_name": "execute_skill", "tool_call_id": "prior-b-call", "content": evidence},
            {"role": "assistant", "content": "model-b completed"},
        ],
        assistant_output="model-b completed",
    )
    prior_state["status"] = "failed"
    prior_state["failure"] = "The next Skill failed before dispatching a tool."
    await session_manager.update_extra(
        deps.session_key,
        {
            "skill_stage_workflow:previous-run": prior_state,
            "active_skill_stage_workflow_run_id": "previous-run",
        },
    )
    follow_up_deps = SkillDeps(
        session_key=deps.session_key,
        session_manager=session_manager,
        extra={
            "run_id": "follow-up-run",
            "provider_instances": {"crm": {"prod": {}}},
            "md_skills_snapshot": deps.extra["md_skills_snapshot"],
        },
    )
    follow_up_events = [
        event
        async for event in runner.run(
            follow_up_deps.session_key,
            "Continue comparing the retained audit evidence.",
            follow_up_deps,
            max_tool_calls=6,
            timeout_seconds=30,
        )
    ]
    assert tool_calls == {"model-a": 0, "model-b": 1, "model-c": 2, "model-d": 2}
    assert [(event.type, event.phase) for event in follow_up_events if event.type == "lifecycle"] == [
        ("lifecycle", "start"),
        ("lifecycle", "end"),
    ]
    continued_state = session.extra["skill_stage_workflow:follow-up-run"]
    assert continued_state["original_request"] == original
    assert continued_state["follow_up_requests"] == [
        "Continue comparing the retained audit evidence."
    ]
    assert [stage["target_skill_id"] for stage in continued_state["completed_stages"]] == [
        "crm:audit",
        "crm:compare",
        "crm:finalize",
    ]
    assert "continue comparing the retained audit evidence" in "\n".join(
        captured_user_inputs["model-c"]
    ).lower()

    interrupted_state = new_stage_state(run_id="interrupted-run", original_request=original)
    record_completed_stage(
        interrupted_state,
        target_skill_id="crm:audit",
        planned_input=planned_inputs["crm:audit"],
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "interrupt-b-call", "name": "execute_skill", "args": {}}]},
            {"role": "tool", "tool_name": "execute_skill", "tool_call_id": "interrupt-b-call", "content": evidence},
        ],
        assistant_output="model-b completed",
    )
    interrupted_state["active_stage"] = {
        "identity": "interrupted-stage",
        "target_skill_id": "crm:compare",
        "planned_input": planned_inputs["crm:compare"],
        "status": "started",
    }
    await session_manager.update_extra(
        deps.session_key,
        {
            "skill_stage_workflow:interrupted-run": interrupted_state,
            "active_skill_stage_workflow_run_id": "interrupted-run",
        },
    )
    assert session.extra.get("active_skill_stage_workflow_run_id") == "interrupted-run"
    assert session.extra.get("skill_stage_workflow:interrupted-run", {}).get("active_stage")
    interrupted_deps = SkillDeps(
        session_key=deps.session_key,
        session_manager=session_manager,
        extra={
            "run_id": "interrupted-resume-run",
            "provider_instances": {"crm": {"prod": {}}},
            "md_skills_snapshot": deps.extra["md_skills_snapshot"],
        },
    )
    before_interrupted = dict(tool_calls)
    interrupted_events = [
        event
        async for event in runner.run(
            interrupted_deps.session_key,
            "Continue the comparison.",
            interrupted_deps,
            max_tool_calls=6,
            timeout_seconds=30,
        )
    ]
    assert ("interrupted-resume-run", True, ConversationTurnRoute.CONTINUE_ACTIVE) in observed_plans
    assert tool_calls == before_interrupted
    assert any(event.type == "error" for event in interrupted_events)
    assert session.extra["skill_stage_workflow:interrupted-run"]["status"] == "unknown"
    assert session.extra["skill_stage_workflow:interrupted-run"]["active_stage"][
        "target_skill_id"
    ] == "crm:compare"

    start_new_state = new_stage_state(run_id="start-new-candidate", original_request=original)
    record_completed_stage(
        start_new_state,
        target_skill_id="crm:audit",
        planned_input=planned_inputs["crm:audit"],
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "start-new-b-call", "name": "execute_skill", "args": {}}]},
            {"role": "tool", "tool_name": "execute_skill", "tool_call_id": "start-new-b-call", "content": evidence},
        ],
        assistant_output="model-b completed",
    )
    start_new_state["status"] = "running"
    await session_manager.update_extra(
        deps.session_key,
        {
            "skill_stage_workflow:start-new-candidate": start_new_state,
            "active_skill_stage_workflow_run_id": "start-new-candidate",
        },
    )
    unrelated_deps = SkillDeps(
        session_key=deps.session_key,
        session_manager=session_manager,
        extra={
            "run_id": "unrelated-start-new-run",
            "_test_start_new": True,
            "provider_instances": {"crm": {"prod": {}}},
            "md_skills_snapshot": deps.extra["md_skills_snapshot"],
        },
    )
    unrelated_inputs_before = len(captured_user_inputs["model-c"])
    unrelated_events = [
        event
        async for event in runner.run(
            unrelated_deps.session_key,
            "Count the new account totals only.",
            unrelated_deps,
            max_tool_calls=6,
            timeout_seconds=30,
        )
    ]
    assert tool_calls["model-c"] == before_interrupted["model-c"] + 1
    assert [(event.type, event.phase) for event in unrelated_events if event.type == "lifecycle"] == [
        ("lifecycle", "start"),
        ("lifecycle", "end"),
    ]
    unrelated_stage_input = "\n".join(captured_user_inputs["model-c"][unrelated_inputs_before:])
    assert "Count the new account totals only." in unrelated_stage_input
    assert evidence not in unrelated_stage_input
    unrelated_state = session.extra["skill_stage_workflow:unrelated-start-new-run"]
    assert unrelated_state["original_request"] == "Count the new account totals only."
    assert [stage["target_skill_id"] for stage in unrelated_state["completed_stages"]] == [
        "crm:compare"
    ]
