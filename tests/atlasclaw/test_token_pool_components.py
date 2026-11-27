# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.atlasclaw.agent.agent_pool import AgentInstancePool
from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.core.token_health_store import TokenHealthStore
from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
from app.atlasclaw.core.token_pool import TokenEntry, TokenHealth, TokenPool


def _token(token_id: str, *, provider: str = "openai", model: str = "gpt-4o") -> TokenEntry:
    return TokenEntry(
        token_id=token_id,
        provider=provider,
        model=model,
        base_url="https://example.com/v1",
        api_key="sk-test",
        api_type="openai",
        priority=0,
        weight=100,
    )


def test_token_pool_register_update_and_select() -> None:
    pool = TokenPool()
    t1 = _token("t1")
    t2 = _token("t2")
    pool.register_token(t1)
    pool.register_token(t2)

    pool.update_token_health(
        "t1",
        {
            "x-ratelimit-remaining-tokens": "100",
            "x-ratelimit-remaining-requests": "0",
        },
    )
    pool.update_token_health(
        "t2",
        {
            "x-ratelimit-remaining-tokens": "80000",
            "x-ratelimit-remaining-requests": "80",
        },
    )

    selected = pool.select_token(strategy="health")
    assert selected is not None
    assert selected.token_id == "t2"


def test_token_health_store_save_load_and_corrupt_fallback(tmp_path) -> None:
    store = TokenHealthStore(str(tmp_path))
    snapshot = {
        "t1": TokenHealth(
            remaining_tokens=1234,
            remaining_requests=12,
            reset_tokens_seconds=3,
            reset_requests_seconds=4,
        )
    }

    store.save(snapshot)
    loaded = store.load()
    assert "t1" in loaded
    assert loaded["t1"].remaining_tokens == 1234
    assert loaded["t1"].remaining_requests == 12

    store.file_path.write_text("{broken json", encoding="utf-8")
    assert store.load() == {}


def test_dynamic_token_policy_session_pin_and_refresh() -> None:
    pool = TokenPool()
    pool.register_token(_token("t1"))
    pool.register_token(_token("t2"))

    pool.update_token_health(
        "t1",
        {
            "x-ratelimit-remaining-tokens": "500",
            "x-ratelimit-remaining-requests": "1",
        },
    )
    pool.update_token_health(
        "t2",
        {
            "x-ratelimit-remaining-tokens": "90000",
            "x-ratelimit-remaining-requests": "90",
        },
    )

    policy = DynamicTokenPolicy(pool, strategy="health")
    first = policy.get_or_select_session_token("s1")
    second = policy.get_or_select_session_token("s1")
    assert first is not None and second is not None
    assert first.token_id == second.token_id

    # 当前 token 变成不健康后，refresh 应可切换
    pool.health_status[first.token_id] = TokenHealth(remaining_tokens=0, remaining_requests=0)
    refreshed = policy.refresh_session_token("s1")
    assert refreshed is not None
    assert refreshed.token_id != first.token_id


def test_dynamic_token_policy_primary_token_preferred() -> None:
    """Primary token should be preferred when healthy."""
    pool = TokenPool()
    pool.register_token(_token("primary-token", model="gpt-4"))
    pool.register_token(_token("backup-token", model="gpt-4"))

    # Set backup as healthier
    pool.update_token_health(
        "primary-token",
        {"x-ratelimit-remaining-tokens": "1000", "x-ratelimit-remaining-requests": "10"},
    )
    pool.update_token_health(
        "backup-token",
        {"x-ratelimit-remaining-tokens": "90000", "x-ratelimit-remaining-requests": "90"},
    )

    policy = DynamicTokenPolicy(pool, strategy="health", primary_token_id="primary-token")
    selected = policy.get_or_select_session_token("s1")
    assert selected is not None
    # Primary should be selected even though backup has higher health score
    assert selected.token_id == "primary-token"


def test_dynamic_token_policy_fallback_when_primary_unhealthy() -> None:
    """Should fallback to healthy token when primary is unhealthy."""
    pool = TokenPool()
    pool.register_token(_token("primary-token", model="gpt-4"))
    pool.register_token(_token("backup-token", model="gpt-4"))

    # Primary is unhealthy
    pool.update_token_health(
        "primary-token",
        {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-remaining-requests": "0"},
    )
    pool.update_token_health(
        "backup-token",
        {"x-ratelimit-remaining-tokens": "90000", "x-ratelimit-remaining-requests": "90"},
    )

    policy = DynamicTokenPolicy(pool, strategy="health", primary_token_id="primary-token")
    selected = policy.get_or_select_session_token("s1")
    assert selected is not None
    # Should fallback to backup since primary is unhealthy
    assert selected.token_id == "backup-token"


@pytest.mark.asyncio
async def test_agent_instance_pool_cache_and_concurrency_limit() -> None:
    pool = AgentInstancePool(max_concurrent_per_instance=4)
    token = _token("t1")

    async def factory(agent_id: str, token_entry: TokenEntry):
        return {"agent_id": agent_id, "token_id": token_entry.token_id}

    instance1 = await pool.get_or_create("main", token, factory)
    instance2 = await pool.get_or_create("main", token, factory)
    assert instance1 is instance2

    for _ in range(4):
        await instance1.concurrency_sem.acquire()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(instance1.concurrency_sem.acquire(), timeout=0.05)

    for _ in range(4):
        instance1.concurrency_sem.release()


@pytest.mark.asyncio
async def test_updated_credentials_rebuild_agent_without_interrupting_existing_instance() -> None:
    pool = AgentInstancePool()
    token = _token("editable")
    factory = lambda agent_id, entry: (entry.api_key, entry.model, entry.base_url)
    original = await pool.get_or_create("main", token, factory)
    await original.concurrency_sem.acquire()
    rotated = replace(token, api_key="replacement-key", model="new-model")
    updated = await pool.get_or_create("main", rotated, factory)
    assert updated is not original
    assert updated.agent == ("replacement-key", "new-model", token.base_url)
    assert original.agent[0] == "sk-test"
    assert original.concurrency_sem._value == 3
    assert await pool.get_or_create("main", rotated, factory) is updated
    original.concurrency_sem.release()


@pytest.mark.asyncio
async def test_refresh_tokens_updates_pinned_sessions_and_removes_disabled_tokens() -> None:
    original = _token("editable")
    fallback = _token("json")
    entries = [original, fallback]
    async def loader():
        return entries
    pool = TokenPool()
    pool.register_token(original)
    policy = DynamicTokenPolicy(pool, primary_token_id="editable", token_loader=loader)
    assert policy.get_or_select_session_token("existing") is original
    pool.mark_token_unhealthy("editable", reason="old key rejected")
    entries = [replace(original, api_key="new-key"), fallback]
    await policy.refresh_tokens()
    assert policy.get_session_token("existing").api_key == "new-key"
    assert pool.get_token_health("editable").is_healthy
    pool.mark_token_unhealthy("editable", reason="provider error")
    await policy.refresh_tokens()
    assert not pool.get_token_health("editable").is_healthy
    entries = [fallback]
    await policy.refresh_tokens()
    assert policy.get_session_token("existing") is None
    assert policy.get_or_select_session_token("existing") is fallback
    assert "editable" not in pool.health_status


@pytest.mark.asyncio
async def test_empty_refreshed_pool_cannot_fall_back_to_seed_agent() -> None:
    async def loader():
        return []
    pool = TokenPool()
    pool.register_token(_token("removed"))
    policy = DynamicTokenPolicy(pool, token_loader=loader)
    with pytest.raises(RuntimeError, match="No usable model"):
        await policy.refresh_tokens()
    assert not pool.tokens


def test_late_old_key_failure_and_quota_cannot_poison_replacement_key(tmp_path) -> None:
    old = _token("rotated")
    new = replace(old, api_key="replacement-key")
    pool = TokenPool()
    pool.register_token(old)
    policy = DynamicTokenPolicy(pool)
    policy.get_or_select_session_token("old-run")
    interceptor = TokenHealthInterceptor(pool, TokenHealthStore(str(tmp_path)))
    pool.replace_tokens([new])
    interceptor.on_hard_failure("rotated", "old request 401", expected_fingerprint=old.credential_fingerprint)
    policy.mark_session_token_unhealthy("old-run", reason="old request 401", expected_fingerprint=old.credential_fingerprint)
    interceptor.on_response("rotated", {"x-ratelimit-remaining-tokens": "0"}, expected_fingerprint=old.credential_fingerprint)
    assert pool.get_token_health("rotated").is_healthy
    interceptor.on_hard_failure("rotated", "current request 401", expected_fingerprint=new.credential_fingerprint)
    assert not pool.get_token_health("rotated").is_healthy


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["health", "random", "round_robin"])
async def test_refresh_preserves_fallback_pin_until_managed_credentials_change(strategy) -> None:
    managed = replace(_token("managed"), source_model_config_id="config-id", priority=10)
    fallback = _token("file")
    entries = [managed, fallback]
    async def loader():
        return entries
    pool = TokenPool()
    policy = DynamicTokenPolicy(pool, strategy=strategy, token_loader=loader)
    await policy.refresh_tokens()
    assert policy.get_or_select_session_token("session") is managed
    assert policy.mark_session_token_unhealthy("session") is fallback
    await policy.refresh_tokens()
    assert policy.get_or_select_session_token("session") is fallback
    entries = [replace(managed, api_key="fixed-key"), fallback]
    await policy.refresh_tokens()
    assert policy.get_or_select_session_token("session").api_key == "fixed-key"


def test_token_interceptor_updates_pool_and_persists(tmp_path) -> None:
    token_pool = TokenPool()
    token_pool.register_token(_token("t1"))
    store = TokenHealthStore(str(tmp_path))
    interceptor = TokenHealthInterceptor(token_pool, store)

    interceptor.on_response(
        "t1",
        {
            "X-RateLimit-Remaining-Tokens": "4321",
            "X-RateLimit-Remaining-Requests": "43",
            "X-RateLimit-Reset-Tokens": "10",
            "X-RateLimit-Reset-Requests": "12",
        },
    )

    health = token_pool.get_token_health("t1")
    assert health is not None
    assert health.remaining_tokens == 4321
    assert health.remaining_requests == 43
    assert store.file_path.exists()


def test_token_interceptor_marks_hard_failure_unhealthy_and_persists(tmp_path) -> None:
    token_pool = TokenPool()
    token_pool.register_token(_token("t1"))
    store = TokenHealthStore(str(tmp_path))
    interceptor = TokenHealthInterceptor(token_pool, store)

    interceptor.on_hard_failure("t1", "status_code: 403, AccountOverdueError")

    health = token_pool.get_token_health("t1")
    assert health is not None
    assert not health.is_healthy
    assert health.last_error == "status_code: 403, AccountOverdueError"
    loaded = store.load()
    assert loaded["t1"].last_error == "status_code: 403, AccountOverdueError"
