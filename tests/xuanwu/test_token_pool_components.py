# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from app.xuanwu.agent.agent_pool import AgentInstancePool
from app.xuanwu.agent.token_policy import DynamicTokenPolicy
from app.xuanwu.core.token_health_store import TokenHealthStore
from app.xuanwu.core.token_interceptor import TokenHealthInterceptor
from app.xuanwu.core.token_pool import TokenEntry, TokenHealth, TokenPool


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
