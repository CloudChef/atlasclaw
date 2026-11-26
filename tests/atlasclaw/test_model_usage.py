# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Usage accounting through real native model calls and private API queries."""
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic_ai import Agent, BinaryContent, ModelRetry
from pydantic_ai.messages import FilePart, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select

from app.atlasclaw.agent.model_usage import build_usage_hooks, usage_action, usage_values
from app.atlasclaw.api.model_usage_routes import router
from app.atlasclaw.auth.guards import get_current_user
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.token_pool import TokenEntry
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.models import ModelUsageModel


@pytest_asyncio.fixture
async def db(tmp_path):
    manager = await init_database(DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "usage.db")))
    await manager.create_tables()
    yield manager
    await manager.close()


def token():
    return TokenEntry(token_id="personal", provider="openrouter", model="example-model",
                      base_url="https://example.invalid/v1", api_key="secret-private-key-1234")


@pytest.mark.asyncio
async def test_native_calls_record_retries_without_counting_cache_twice(db):
    async def respond(messages, info):
        return ModelResponse(parts=[TextPart("answer")], usage=RequestUsage(
            input_tokens=100, output_tokens=30, cache_read_tokens=70,
            details={"reasoning_tokens": 20}), model_name="actual-model")

    agent = Agent(FunctionModel(respond, model_name="actual-model"), deps_type=SkillDeps, capabilities=[build_usage_hooks(token())])
    validations = 0

    @agent.output_validator
    def validate(result):
        nonlocal validations
        validations += 1
        if validations == 1:
            raise ModelRetry("try again")
        return result

    deps = SkillDeps(UserInfo(user_id="alice"), session_key="private-session",
                     extra={"run_id": "run-1"})
    with usage_action("conversation_turn_planning"):
        await agent.run("private prompt", deps=deps)
    async with db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel))).all()
        assert len(rows) == 2
        assert sum(row.total_tokens for row in rows) == 260
        assert all(row.action == "conversation_turn_planning" for row in rows)
        assert all(row.run_id == "run-1" and row.user_id == "alice" for row in rows)
        assert rows[0].key_label == "•••• 1234"
        assert rows[0].model == "actual-model"
        assert rows[0].reasoning_tokens == 20
        assert rows[0].source == "reported"
        assert "private prompt" not in repr(rows[0].__dict__)
        assert token().api_key not in repr(rows[0].__dict__)


@pytest.mark.asyncio
async def test_failures_record_unknown_usage_and_preserve_error(db):
    async def fail(messages, info):
        raise RuntimeError("upstream failed")
    agent = Agent(FunctionModel(fail), deps_type=SkillDeps, capabilities=[build_usage_hooks(token())])
    with pytest.raises(RuntimeError, match="upstream failed"):
        await agent.run("hello", deps=SkillDeps(UserInfo(user_id="alice")))
    async with db.get_session() as session:
        row = (await session.scalars(select(ModelUsageModel))).one()
        assert row.status == "failed"
        assert row.total_tokens is None and row.source == "unavailable"


@pytest.mark.asyncio
async def test_streaming_and_tool_result_calls_are_recorded(db):
    async def stream(messages, info):
        yield "hello "
        yield "world"

    deps = SkillDeps(UserInfo(user_id="alice"), extra={"run_id": "stream-run"})
    agent = Agent(FunctionModel(stream_function=stream), deps_type=SkillDeps,
                  capabilities=[build_usage_hooks(token())])
    async with agent.run_stream("hello", deps=deps) as result:
        assert await result.get_output() == "hello world"

    tool_agent = Agent(TestModel(custom_output_text="done"), deps_type=SkillDeps,
                       capabilities=[build_usage_hooks(token())])

    @tool_agent.tool_plain
    def lookup() -> str:
        return "found"

    await tool_agent.run("look up", deps=SkillDeps(UserInfo(user_id="bob"), extra={"run_id": "tool-run"}))
    async with db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel))).all()
        stream_rows = [row for row in rows if row.run_id == "stream-run"]
        assert len(stream_rows) == 1
        assert stream_rows[0].total_tokens > 0
        tool_rows = [row for row in rows if row.run_id == "tool-run"]
        assert len(tool_rows) == 2
        assert tool_rows[-1].action == "tool_result: lookup"
        assert all(row.user_id == "bob" for row in tool_rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_second", [False, True])
async def test_continuations_keep_reported_usage_when_later_segment_fails(db, fail_second):
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2 and fail_second:
            raise RuntimeError("continuation failed")
        return ModelResponse(parts=[TextPart("pending" if calls == 1 else "done")],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
            state="suspended" if calls == 1 else "complete",
            provider_response_id=str(calls), provider_name="anthropic")

    agent = Agent(FunctionModel(respond), deps_type=SkillDeps,
                  capabilities=[build_usage_hooks(token())])
    deps = SkillDeps(UserInfo(user_id="alice"), extra={"run_id": "continuation"})
    if fail_second:
        with pytest.raises(RuntimeError, match="continuation failed"):
            await agent.run("continue", deps=deps)
    else:
        await agent.run("continue", deps=deps)
    async with db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel)
                                     .order_by(ModelUsageModel.created_at))).all()
        assert len(rows) == 2
        assert rows[0].total_tokens == 15 and rows[0].status == "completed"
        assert rows[1].total_tokens == (None if fail_second else 15)
        assert rows[1].status == ("failed" if fail_second else "completed")


@pytest.mark.asyncio
async def test_same_response_id_records_only_incremental_usage(db):
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("pending" if calls < 3 else "done")],
            usage=RequestUsage(input_tokens=10, output_tokens=5 * calls,
                               cache_read_tokens=4, details={} if calls == 2 else {"reasoning_tokens": calls}),
            state="suspended" if calls < 3 else "complete",
            provider_response_id="same-job", provider_name="openai")

    agent = Agent(FunctionModel(respond), deps_type=SkillDeps,
                  capabilities=[build_usage_hooks(token())])
    await agent.run("continue", deps=SkillDeps(UserInfo(user_id="alice")))
    async with db.get_session() as session:
        rows = (await session.scalars(select(ModelUsageModel)
                                     .order_by(ModelUsageModel.created_at))).all()
        assert len(rows) == 3
        assert [row.total_tokens for row in rows] == [15, 5, 5]
        assert sum(row.cache_read_tokens for row in rows) == 4
        assert rows[1].reasoning_tokens is None
        assert sum(row.reasoning_tokens or 0 for row in rows) == 3


@pytest.mark.asyncio
async def test_resuming_a_job_excludes_its_previously_recorded_usage(db):
    async def respond(messages, info):
        return ModelResponse(parts=[TextPart("done")],
            usage=RequestUsage(input_tokens=10, output_tokens=15),
            provider_response_id="same-job", provider_name="openai")

    model = FunctionModel(respond)
    seed = ModelResponse(parts=[TextPart("pending")],
        usage=RequestUsage(input_tokens=10, output_tokens=5), state="suspended",
        provider_response_id="same-job", provider_name="openai", model_name=model.model_name)
    history = [ModelRequest(parts=[UserPromptPart("continue")]), seed]
    agent = Agent(model, deps_type=SkillDeps, capabilities=[build_usage_hooks(token())])
    await agent.run(message_history=history, deps=SkillDeps(UserInfo(user_id="alice")))
    async with db.get_session() as session:
        row = (await session.scalars(select(ModelUsageModel))).one()
        assert row.input_tokens == 0 and row.output_tokens == row.total_tokens == 10


def test_missing_usage_is_estimated_only_for_text():
    context = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart("hello")])],
                              model_request_parameters=SimpleNamespace(function_tools=[], output_tools=[]))
    response = ModelResponse(parts=[TextPart("response")])
    values = usage_values(response, context)
    assert values["source"] == "estimated"
    assert values["total_tokens"] == values["input_tokens"] + values["output_tokens"] > 0
    context.messages = [ModelRequest(parts=[UserPromptPart(["hello", BinaryContent(b"image", media_type="image/png")])])]
    values = usage_values(response, context)
    assert values["source"] == "unavailable" and values["total_tokens"] is None
    context.messages = [ModelRequest(parts=[UserPromptPart("draw a picture")])]
    response = ModelResponse(parts=[FilePart(BinaryContent(b"image", media_type="image/png"))])
    assert usage_values(response, context)["source"] == "unavailable"


def test_estimate_excludes_usage_and_transport_metadata():
    context = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart("Hi")])],
                              model_request_parameters=SimpleNamespace(function_tools=[], output_tools=[]))
    response = ModelResponse(parts=[TextPart("OK")], provider_details={"diagnostic": "x" * 10000})
    values = usage_values(response, context)
    assert values["input_tokens"] == 1 and values["output_tokens"] == 1
    context.model_request_parameters.function_tools = [SimpleNamespace(
        name="lookup", description="Lookup items", parameters_json_schema={"type": "object"})]
    assert usage_values(response, context)["input_tokens"] > values["input_tokens"]
    response.state = "suspended"
    assert usage_values(response, context)["source"] == "unavailable"


@pytest.mark.asyncio
async def test_api_is_private_and_sums_full_scope_beyond_page(db):
    async with db.get_session() as session:
        for user, key in (("alice", "one"), ("alice", "two"), ("bob", "other")):
            session.add(ModelUsageModel(user_id=user, run_id="run-" + key, session_key="s",
                provider="openai", model="gpt", token_id=key, key_label="•••• 1234",
                action="model_response", input_tokens=100, output_tokens=20, total_tokens=120,
                source="reported", status="completed", duration_ms=40))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    async def alice():
        return UserInfo(user_id="alice")
    app.dependency_overrides[get_current_user] = alice
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = (await client.get("/api/model-usage?page_size=1")).json()
        assert len(result["items"]) == 1
        assert result["total"] == 2
        assert result["summary"]["total_tokens"] == 240
        assert result["items"][0]["user_id"] == "alice"
        assert result["items"][0]["created_at"].endswith("+00:00")
        filtered = (await client.get("/api/model-usage?token_id=one")).json()
        assert filtered["total"] == 1 and filtered["summary"]["total_tokens"] == 120
        assert (await client.get("/api/model-usage?page_size=0")).status_code == 422
    app.dependency_overrides.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/model-usage")).status_code == 401
