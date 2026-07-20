"""Failure-path integration tests for confirmed Tool run execution."""

from __future__ import annotations

from contextlib import asynccontextmanager
import time

import pytest

from app.atlasclaw.api.deps_context import APIContext
from app.atlasclaw.api.services import run_service
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.tool_confirmation import ToolConfirmationGrant
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry


class _AuthorizationDb:
    """Minimal initialized DB facade for the post-queue authorization refresh."""

    is_initialized = True

    @asynccontextmanager
    async def get_session(self):
        yield object()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "provider_revoked", "embed_stale"])
async def test_confirmed_tool_run_rejects_post_queue_drift_without_handler(
    tmp_path,
    monkeypatch,
    failure: str,
) -> None:
    calls = []

    async def mutation_handler(ctx=None, **kwargs):
        calls.append(kwargs)
        return {"success": True}

    registry = SkillRegistry()
    metadata = SkillMetadata(
        name="provider_mutation",
        source="provider",
        provider_type="provider",
        owner_skill_ref="provider:resource",
        effect="mutate",
        requires_approval=True,
        parameters_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
        },
    )
    registry.register(metadata, mutation_handler)
    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / "agents")),
        session_queue=SessionQueue(),
        skill_registry=registry,
        provider_instances={
            "provider": {
                "primary": {
                    "provider_type": "provider",
                    "instance_name": "primary",
                }
            }
        },
    )
    user = UserInfo(user_id="user-1", display_name="User One")
    session_key = "agent:main:user:user-1:web:dm:user-1:topic:confirm"
    embed_scope = {"context_id": "ctx-1"} if failure == "embed_stale" else None
    grant = ToolConfirmationGrant(
        token="claimed-token",
        owner_user_id="user-1",
        session_key=session_key,
        agent_id="main",
        tool_name="provider_mutation",
        owner_skill_ref="provider:resource",
        contract_fingerprint=registry._tool_contract_fingerprint(metadata),
        arguments={"resource_id": "resource-1"},
        arguments_fingerprint="args",
        provider_type="provider",
        provider_instance="primary",
        embed_scope="",
        expires_at=-1 if failure == "expired" else time.monotonic() + 60,
    )
    context = {"_tool_confirmation_grant": grant}
    if embed_scope:
        context["embed_scope"] = embed_scope

    allow_provider = failure != "provider_revoked"
    authz = AuthorizationContext(
        user=user,
        permissions={
            "skills": {"allow_all": True},
            "providers": {
                "allow_all": allow_provider,
                "provider_permissions": [] if not allow_provider else None,
            },
        },
    )

    async def resolve_authz(_session, _user):
        return authz

    monkeypatch.setattr(run_service, "get_db_manager", lambda: _AuthorizationDb())
    monkeypatch.setattr(run_service, "resolve_authorization_context", resolve_authz)
    if failure == "embed_stale":
        async def reject_stale_embed(*_args, **_kwargs):
            raise PermissionError("Embed context no longer targets the current page")

        monkeypatch.setattr(
            run_service,
            "_refresh_embed_run_context_after_queue",
            reject_stale_embed,
        )

    run_service.init_run(ctx, "run-confirm", session_key, "confirm", 30)
    await run_service.execute_confirmed_tool_run(
        ctx,
        "run-confirm",
        session_key,
        30,
        user,
        {},
        context,
    )

    assert calls == []
    assert ctx.active_runs["run-confirm"]["status"] == "error"
