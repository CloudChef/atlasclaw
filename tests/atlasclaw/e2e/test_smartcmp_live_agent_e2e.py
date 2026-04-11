# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import pytest
import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"


@dataclass
class LiveRunResult:
    run_id: str
    message: str
    wall_seconds: float
    lifecycle: list[str]
    runtime_states: list[str]
    runtime_items: list[dict[str, Any]]
    errors: list[str]
    assistant_text: str


def _require_live_e2e() -> tuple[str, str, str]:
    if os.getenv("ATLASCLAW_LIVE_E2E", "").strip() != "1":
        pytest.skip("Set ATLASCLAW_LIVE_E2E=1 to run SmartCMP live agent E2E.")
    return (
        os.getenv("ATLASCLAW_LIVE_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        os.getenv("ATLASCLAW_LIVE_USERNAME", DEFAULT_USERNAME).strip() or DEFAULT_USERNAME,
        os.getenv("ATLASCLAW_LIVE_PASSWORD", DEFAULT_PASSWORD).strip() or DEFAULT_PASSWORD,
    )


def _parse_sse(response: requests.Response):
    event_name: str | None = None
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip("\n")
        if not line:
            if event_name is not None and data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())


def _login(session: requests.Session, *, base_url: str, username: str, password: str) -> str:
    response = session.post(
        f"{base_url}/api/auth/local/login",
        json={"username": username, "password": password},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("token", "")).strip()
    assert token, f"login token missing: {payload}"
    return token


def _create_thread(session: requests.Session, *, base_url: str, token: str) -> str:
    response = session.post(
        f"{base_url}/api/sessions/threads",
        headers={"AtlasClaw-Authenticate": token},
        json={"agent_id": "main", "channel": "web", "chat_type": "dm", "account_id": "default"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    session_key = str(payload.get("session_key", "")).strip()
    assert session_key, f"session_key missing: {payload}"
    return session_key


def _run_round(
    session: requests.Session,
    *,
    base_url: str,
    token: str,
    session_key: str,
    message: str,
) -> LiveRunResult:
    headers = {"AtlasClaw-Authenticate": token}
    started_at = time.perf_counter()

    response = session.post(
        f"{base_url}/api/agent/run",
        headers=headers,
        json={"session_key": session_key, "message": message, "timeout_seconds": 120},
        timeout=30,
    )
    response.raise_for_status()
    run_id = response.json()["run_id"]

    assistant_chunks: list[str] = []
    runtime_states: list[str] = []
    runtime_items: list[dict[str, Any]] = []
    lifecycle: list[str] = []
    errors: list[str] = []

    with session.get(
        f"{base_url}/api/agent/runs/{run_id}/stream",
        headers=headers,
        stream=True,
        timeout=180,
    ) as stream_response:
        stream_response.raise_for_status()
        for event_name, payload in _parse_sse(stream_response):
            try:
                obj = json.loads(payload)
            except Exception:
                obj = {"raw": payload}
            if event_name == "assistant":
                text = str(obj.get("text", ""))
                if text:
                    assistant_chunks.append(text)
            elif event_name == "runtime":
                state = str(obj.get("state", "")).strip()
                if state:
                    runtime_states.append(state)
                runtime_items.append(obj)
            elif event_name == "lifecycle":
                phase = str(obj.get("phase", "")).strip()
                if phase:
                    lifecycle.append(phase)
                if phase == "end":
                    break
            elif event_name == "error":
                errors.append(str(obj.get("message", obj)))

    return LiveRunResult(
        run_id=run_id,
        message=message,
        wall_seconds=round(time.perf_counter() - started_at, 3),
        lifecycle=lifecycle,
        runtime_states=runtime_states,
        runtime_items=runtime_items,
        errors=errors,
        assistant_text="".join(assistant_chunks).strip(),
    )


def _looks_like_transient_provider_failure(result: LiveRunResult) -> bool:
    combined = "\n".join(
        [
            result.assistant_text,
            *result.errors,
            *[str(item.get("message", "")) for item in result.runtime_items],
        ]
    ).lower()
    return (
        "connectionreseterror" in combined
        or "远程主机强迫关闭了一个现有的连接" in combined
        or "forcibly closed by the remote host" in combined
    )


def _run_round_with_transient_retry(
    session: requests.Session,
    *,
    base_url: str,
    token: str,
    session_key: str,
    message: str,
    retries: int = 2,
) -> LiveRunResult:
    attempts = max(1, retries + 1)
    last_result: LiveRunResult | None = None
    for attempt in range(1, attempts + 1):
        result = _run_round(
            session,
            base_url=base_url,
            token=token,
            session_key=session_key,
            message=message,
        )
        last_result = result
        if not _looks_like_transient_provider_failure(result):
            return result
        if attempt < attempts:
            time.sleep(1.5)
    assert last_result is not None
    return last_result


def _assert_elapsed_monotonic(result: LiveRunResult) -> None:
    last_elapsed = -1.0
    for item in result.runtime_items:
        elapsed = float(item.get("elapsed", 0.0) or 0.0)
        assert elapsed >= last_elapsed, f"elapsed not monotonic in {result.run_id}: {result.runtime_items}"
        last_elapsed = elapsed


def _assert_no_timeout_or_failure(result: LiveRunResult) -> None:
    assert "failed" not in result.runtime_states, f"run failed: {result}"
    assert not result.errors, f"stream errors: {result.errors}"
    for item in result.runtime_items:
        message = str(item.get("message", "")).lower()
        assert "timeout" not in message, f"timeout detected: {item}"


def _assert_real_tool_call(result: LiveRunResult, expected_tool_name: str) -> None:
    planned_tools: list[str] = []
    for item in result.runtime_items:
        tools = item.get("tools")
        if isinstance(tools, list):
            planned_tools.extend(str(tool).strip() for tool in tools if str(tool).strip())
    assert expected_tool_name in planned_tools, f"expected tool {expected_tool_name}, got {planned_tools}"


def _assert_common_runtime_shape(result: LiveRunResult) -> None:
    assert result.lifecycle[:1] == ["start"], f"missing lifecycle start: {result.lifecycle}"
    assert result.lifecycle[-1:] == ["end"], f"missing lifecycle end: {result.lifecycle}"
    assert result.runtime_states[-1:] == ["answered"], f"final state not answered: {result.runtime_states}"
    assert "waiting_for_tool" in result.runtime_states, f"no tool phase: {result.runtime_states}"
    assert result.assistant_text, f"assistant text missing for run {result.run_id}"
    _assert_elapsed_monotonic(result)
    _assert_no_timeout_or_failure(result)


@pytest.mark.e2e
@pytest.mark.integration
def test_live_smartcmp_agent_three_turns_return_grounded_results() -> None:
    base_url, username, password = _require_live_e2e()
    session = requests.Session()

    token = _login(session, base_url=base_url, username=username, password=password)
    session_key = _create_thread(session, base_url=base_url, token=token)

    pending = _run_round_with_transient_retry(
        session,
        base_url=base_url,
        token=token,
        session_key=session_key,
        message="查下CMP 里目前所有待审批",
    )
    _assert_common_runtime_shape(pending)
    _assert_real_tool_call(pending, "smartcmp_list_pending")
    assert "TIC20260316000001" in pending.assistant_text
    assert "TIC20260313000006" in pending.assistant_text
    assert "TIC20260313000004" in pending.assistant_text
    assert "一级审批" in pending.assistant_text

    detail = _run_round_with_transient_retry(
        session,
        base_url=base_url,
        token=token,
        session_key=session_key,
        message="我要看下TIC20260316000001的详情",
    )
    _assert_common_runtime_shape(detail)
    _assert_real_tool_call(detail, "smartcmp_get_request_detail")
    assert "TIC20260316000001" in detail.assistant_text
    assert "Test ticket for build verification" in detail.assistant_text
    assert "approvalId" in detail.assistant_text or "requestId" in detail.assistant_text

    services = _run_round_with_transient_retry(
        session,
        base_url=base_url,
        token=token,
        session_key=session_key,
        message="还有查下CMP 里目前有的服务目录",
    )
    _assert_common_runtime_shape(services)
    _assert_real_tool_call(services, "smartcmp_list_services")
    assert "Incident Ticket" in services.assistant_text
    assert "Machine Service" in services.assistant_text
    assert "VPC Service" in services.assistant_text
    assert "Support Service" in services.assistant_text

    print(
        "SMARTCMP_LIVE_E2E_TIMING "
        f"pending={pending.wall_seconds}s "
        f"detail={detail.wall_seconds}s "
        f"services={services.wall_seconds}s"
    )
