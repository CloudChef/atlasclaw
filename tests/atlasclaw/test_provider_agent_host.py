# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Provider Agent Host: skill pinning, events, artifacts and cancellation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.atlasclaw.api.sse import SSEManager
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.provider_agent_host import ProviderAgentHost
from app.atlasclaw.core.provider_host_services import ProviderFileRef, ProviderHostServices, ProviderUser
from app.atlasclaw.core.provider_http_runtime import ProviderHttpRuntimeError


class _Sessions:
    def __init__(self, workspace: Path) -> None:
        self.workspace_path = str(workspace)
        self.deleted: list[str] = []

    async def get_or_create(self, session_key: str):
        return SimpleNamespace(session_key=session_key)

    async def delete_session(self, session_key: str) -> bool:
        self.deleted.append(session_key)
        return True


def test_provider_shared_storage_is_global_within_provider(tmp_path: Path) -> None:
    first = ProviderHostServices(tmp_path, tmp_path / "alice", ProviderUser("alice", tenant_id="t1"), provider_type="audit-report")
    second = ProviderHostServices(tmp_path, tmp_path / "bob", ProviderUser("bob", tenant_id="t2"), provider_type="audit-report")
    other = ProviderHostServices(tmp_path, tmp_path / "bob", second.user, provider_type="another-provider")
    assert first.shared_data_dir == second.shared_data_dir == tmp_path / ".provider-data" / "audit-report" / "_shared"
    assert first.shared_data_dir != other.shared_data_dir


class _Router:
    def __init__(self, workspace: Path) -> None:
        self.sessions = _Sessions(workspace)
        self.workspace = workspace

    def for_user(self, user_id: str) -> _Sessions:
        return self.sessions

    def for_session_key(self, session_key: str) -> _Sessions:
        return self.sessions


class _SkillRegistry:
    def md_snapshot(self):
        return [{"name": "audit-report", "qualified_name": "audit-report"}]


class _ProviderSkillRegistry:
    def md_snapshot(self):
        return [{"name": "audit-report", "qualified_name": "audit-report:audit-report"}]


def _context(tmp_path: Path):
    return SimpleNamespace(
        active_runs={},
        sse_manager=SSEManager(heartbeat_interval=60, stream_timeout=60, max_events_buffer=100),
        session_manager_router=_Router(tmp_path),
        skill_registry=_SkillRegistry(),
        agent_runner=None,
    )


def _authz(allowed: bool = True):
    return SimpleNamespace(
        is_admin=True,
        permissions={"skills": {"allow_all": allowed}},
        user=SimpleNamespace(user_id="u1", tenant_id="default", roles=["admin"], extra={}),
    )


def _host(ctx, tmp_path: Path, *, provider_type: str = "audit-report") -> ProviderAgentHost:
    return ProviderAgentHost(
        ctx,
        authz=_authz(),
        provider_type=provider_type,
        user=ProviderUser(user_id="u1", display_name="User One", is_admin=True, roles=("admin",)),
        data_dir=tmp_path / "provider-data",
        provider_config={},
    )


def _write_artifact(tmp_path: Path, name: str = "report.html") -> Path:
    work_dir = tmp_path / "users" / "u1" / "work_dir"
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / name
    target.write_text("<html>audit</html>", encoding="utf-8")
    return target


@pytest.fixture(autouse=True)
def _stub_capability_catalog(monkeypatch):
    """Keep host tests focused on run lifecycle, not the capability catalog."""
    def _stub(self, skills, provider_config):
        return {
            "id": f"skill:{skills[0]}",
            "kind": "skill",
            "target_skill_names": list(skills),
        }
    monkeypatch.setattr(ProviderAgentHost, "_canonical_capability", _stub)


@pytest.mark.asyncio
async def test_run_skill_pins_skill_and_collects_artifact(tmp_path: Path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    _write_artifact(tmp_path)
    captured: dict = {}

    async def fake_execute(
        api_ctx, run_id, session_key, message, timeout, user_info, cookies,
        provider_config, request_context, run_limits=None,
    ):
        captured.update({
            "session_key": session_key,
            "message": message,
            "request_context": request_context,
        })
        api_ctx.sse_manager.push_lifecycle(run_id, "start")
        api_ctx.sse_manager.push_assistant(run_id, "审核完成")
        api_ctx.sse_manager.push_tool(
            run_id, "skill_exec", "end",
            result=json.dumps({"details": {"download_path": "report.html"}}),
        )
        api_ctx.active_runs[run_id]["status"] = "completed"
        api_ctx.sse_manager.push_lifecycle(run_id, "end")
        api_ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)

    host = _host(ctx, tmp_path)
    handle = await host.run_skill(
        skill_id="audit-report",
        prompt="请审核这份报告",
        files=[ProviderFileRef(path=_write_artifact(tmp_path), name="R2026001.pdf")],
        metadata={"document_id": "doc-1"},
    )
    snapshot = await handle.wait(timeout=5)

    assert snapshot["status"] == "succeeded"
    assert captured["request_context"]["_selected_capability"]["target_skill_names"] == ["audit-report"]
    assert "uploads/provider/" in captured["message"]
    assert captured["session_key"].startswith("agent:main:user:u1:provider-audit-report-")
    assert [artifact["name"] for artifact in snapshot["artifacts"]] == ["report.html"]
    copied = Path(snapshot["artifacts"][0]["path"])
    assert copied.is_file()
    assert snapshot["metadata"] == {"document_id": "doc-1"}
    assert snapshot["progress"] == 100
    assert ctx.session_manager_router.sessions.deleted == [captured["session_key"]]


@pytest.mark.asyncio
async def test_unpinned_run_uses_actor_and_current_skill_and_provider_grants(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    host = _host(context, tmp_path)
    actor = UserInfo("u1", extra={"provider_type": "test"}, auth_type="cookie")
    host.authz.user = actor
    grants = [
        {"skill_id": "audit-report", "authorized": True, "enabled": True},
        {"skill_id": "other", "authorized": True, "enabled": False},
    ]
    host.authz.permissions = {"skills": {"skill_permissions": grants}, "providers": {"provider_permissions": []}}
    captured = {}

    async def fake_execute(ctx, run_id, session_key, prompt, timeout, user_info, cookies, config, request_context, run_limits):
        captured.update(user=user_info, context=request_context)
        ctx.active_runs[run_id]["status"] = "completed"
        ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)
    handle = await host.start_run(prompt="Audit this report")
    await handle.wait(timeout=5)
    assert captured["user"] is actor
    assert captured["context"]["_user_skill_permissions"] == grants
    assert captured["context"]["_provider_permissions"] == []
    assert "_selected_capability" not in captured["context"]


@pytest.mark.asyncio
async def test_provider_host_preserves_optional_timeout_and_has_no_run_count_cap(
    tmp_path: Path, monkeypatch,
) -> None:
    context = _context(tmp_path)
    host = _host(context, tmp_path)
    seen_timeouts = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute(ctx, run_id, _session_key, _message, timeout_seconds, *_args):
        seen_timeouts.append(timeout_seconds)
        started.set()
        await release.wait()
        ctx.active_runs[run_id]["status"] = "completed"
        ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)
    handles = [
        await host.start_run(prompt=f"run {index}", timeout_seconds=value)
        for index, value in enumerate((None, 7200, 120))
    ]
    await started.wait()
    assert len(handles) == 3
    release.set()
    for handle in handles:
        await handle.wait(timeout=5)

    assert sorted(str(value) for value in seen_timeouts) == ["120", "7200", "None"]
    assert all(context.active_runs[handle.run_id]["timeout_seconds"] == value
               for handle, value in zip(handles, (None, 7200, 120)))


@pytest.mark.asyncio
async def test_unknown_skill_fails_fast(tmp_path: Path) -> None:
    host = _host(_context(tmp_path), tmp_path)

    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await host.run_skill(skill_id="missing-skill", prompt="x")

    assert exc.value.code == "skill_unavailable"


@pytest.mark.asyncio
async def test_skill_permission_denied(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    host = ProviderAgentHost(
        ctx, authz=_authz(allowed=False), provider_type="audit-report",
        user=ProviderUser(user_id="u1"), data_dir=tmp_path / "data",
    )

    with pytest.raises(ProviderHttpRuntimeError) as exc:
        await host.run_skill(skill_id="audit-report", prompt="x")

    assert exc.value.code == "skill_permission_denied"


@pytest.mark.asyncio
async def test_provider_skill_is_resolved_to_qualified_name(tmp_path: Path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    ctx.skill_registry = _ProviderSkillRegistry()
    captured: dict = {}

    async def fake_execute(api_ctx, run_id, *args, **kwargs):
        captured["request_context"] = args[-2]
        api_ctx.sse_manager.push_lifecycle(run_id, "start")
        api_ctx.active_runs[run_id]["status"] = "completed"
        api_ctx.sse_manager.push_lifecycle(run_id, "end")
        api_ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)
    host = _host(ctx, tmp_path)

    handle = await host.run_skill(skill_id="audit-report", prompt="x")
    snapshot = await handle.wait(timeout=5)

    assert snapshot["skill_ids"] == ["audit-report:audit-report"]
    assert captured["request_context"]["_selected_capability"]["target_skill_names"] == [
        "audit-report:audit-report",
    ]


@pytest.mark.asyncio
async def test_cancelled_run_maps_to_cancelled(tmp_path: Path, monkeypatch) -> None:
    ctx = _context(tmp_path)

    async def fake_execute(api_ctx, run_id, *args, **kwargs):
        api_ctx.sse_manager.push_lifecycle(run_id, "start")
        api_ctx.active_runs[run_id]["status"] = "aborted"
        api_ctx.sse_manager.push_lifecycle(run_id, "aborted")
        api_ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)
    host = _host(ctx, tmp_path)
    handle = await host.run_skill(skill_id="audit-report", prompt="x")
    snapshot = await handle.wait(timeout=5)

    assert snapshot["status"] == "cancelled"
    assert snapshot["error"]["code"] == "run_cancelled"


@pytest.mark.asyncio
async def test_unknown_run_reports_host_restarted(tmp_path: Path) -> None:
    host = _host(_context(tmp_path), tmp_path)

    snapshot = await host.get_run("missing-run")

    assert snapshot["status"] == "failed"
    assert snapshot["error"]["code"] == "host_restarted"


@pytest.mark.asyncio
async def test_stream_replays_events(tmp_path: Path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    _write_artifact(tmp_path)

    async def fake_execute(api_ctx, run_id, *args, **kwargs):
        api_ctx.sse_manager.push_lifecycle(run_id, "start")
        api_ctx.sse_manager.push_assistant(run_id, "done")
        api_ctx.active_runs[run_id]["status"] = "completed"
        api_ctx.sse_manager.push_lifecycle(run_id, "end")
        api_ctx.sse_manager.close_stream(run_id)

    monkeypatch.setattr("app.atlasclaw.core.provider_agent_host.execute_agent_run", fake_execute)
    host = _host(ctx, tmp_path)
    handle = await host.run_skill(skill_id="audit-report", prompt="x")

    events = []
    async for event in handle.events():
        events.append(event)
        if event["type"] in {"run_finished", "run_failed"}:
            break

    kinds = [event["type"] for event in events]
    assert "assistant_delta" in kinds
    assert kinds[-1] == "run_finished"
