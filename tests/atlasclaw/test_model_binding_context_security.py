# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Security regressions for trusted model-binding state in scoped deps."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.atlasclaw.agent.selected_capability import SELECTED_CAPABILITY_KEY
from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.guards import AuthorizationContext
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.model_bindings import ProviderAdmission
from app.atlasclaw.core.provider_registry import ServiceProviderRegistry
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _authz(user_id: str) -> AuthorizationContext:
    return AuthorizationContext(
        user=UserInfo(user_id=user_id, auth_type="test"),
        permissions=RoleService.normalize_permissions(
            {
                "providers": {"allow_all": True},
                "skills": {"allow_all": True},
            }
        ),
    )


def _api_context(tmp_path: Path) -> APIContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    provider_registry = ServiceProviderRegistry()
    provider_registry.load_instances_from_config(
        {
            "crm": {
                "production": {
                    "endpoint": "https://crm.example",
                    "api_key": "server-provider-secret",
                }
            }
        }
    )
    return APIContext(
        session_manager=SessionManager(workspace_path=str(workspace)),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        service_provider_registry=provider_registry,
        provider_instances=provider_registry.get_all_instance_configs(),
    )


def _session_key(user_id: str) -> str:
    return f"agent:main:user:{user_id}:web:dm:peer-{user_id}:topic:thread-{user_id}"


def test_scoped_deps_strips_forged_reserved_context_and_restores_typed_state(
    tmp_path: Path,
) -> None:
    ctx = _api_context(tmp_path)
    user_info = UserInfo(user_id="alice", auth_type="test")
    authz = _authz("alice")
    admission = ProviderAdmission("crm", "production", "alice", "run-1")
    selected_capability = {
        "id": "validated-capability",
        "provider_type": "crm",
        "instance_name": "production",
        "skill_name": "crm:audit",
    }
    reserved_keys = {
        "_trusted_provider_type": "client-provider",
        "_trusted_provider_instance": "client-instance",
        "_provider_admissions": {"client.instance": "forged"},
        "_provider_admission": "forged",
        "_authorization_context": "forged-authz",
        "_selected_capability": {"id": "forged-capability"},
        "_skill_stage_state": {"forged": True},
        "_skill_stage_continuation": True,
        "_binding_target": {"forged": True},
        "_binding_selection": {"forged": True},
        "_current_binding_target": {"forged": True},
        "_current_binding_selection": {"forged": True},
    }
    extra = {
        "run_id": "run-1",
        "safe_public_value": {"keep": True},
        "context": {
            "safe_nested_value": "keep nested context",
            **reserved_keys,
        },
        **reserved_keys,
    }

    deps = build_scoped_deps(
        ctx,
        user_info,
        _session_key("alice"),
        extra=extra,
        authorization_context=authz,
        provider_admission=admission,
        selected_capability=selected_capability,
    )

    typed_server_keys = {
        "_trusted_provider_type",
        "_trusted_provider_instance",
        "_provider_admissions",
        "_authorization_context",
        "_selected_capability",
    }
    for key in reserved_keys:
        if key not in typed_server_keys:
            assert key not in deps.extra
        assert key not in deps.extra["context"]
    assert deps.extra["safe_public_value"] == {"keep": True}
    assert deps.extra["context"]["safe_nested_value"] == "keep nested context"
    assert deps.extra["_authorization_context"] is authz
    assert deps.extra["_provider_admissions"] == {"crm.production": admission}
    assert deps.extra["_provider_admissions"]["crm.production"] is admission
    assert deps.extra["_trusted_provider_type"] == "crm"
    assert deps.extra["_trusted_provider_instance"] == "production"
    assert deps.extra[SELECTED_CAPABILITY_KEY] == selected_capability


@pytest.mark.parametrize(
    ("admission_user_id", "admission_run_id"),
    [("mallory", "run-1"), ("alice", "other-run")],
    ids=["wrong-user", "wrong-run"],
)
def test_scoped_deps_rejects_provider_admission_user_or_run_mismatch(
    tmp_path: Path,
    admission_user_id: str,
    admission_run_id: str,
) -> None:
    with pytest.raises(ValueError, match="admission context does not match"):
        build_scoped_deps(
            _api_context(tmp_path),
            UserInfo(user_id="alice", auth_type="test"),
            _session_key("alice"),
            extra={"run_id": "run-1"},
            authorization_context=_authz("alice"),
            provider_admission=ProviderAdmission(
                "crm", "production", admission_user_id, admission_run_id
            ),
        )


def test_concurrent_scoped_deps_keep_admission_history_and_state_isolated(
    tmp_path: Path,
) -> None:
    ctx = _api_context(tmp_path)

    def build(user_id: str) -> tuple[Any, AuthorizationContext, ProviderAdmission, dict[str, Any]]:
        authz = _authz(user_id)
        admission = ProviderAdmission(
            provider_type="crm",
            instance_name="production",
            user_id=user_id,
            run_id=f"run-{user_id}",
        )
        selected = {
            "id": f"capability-{user_id}",
            "provider_type": "crm",
            "instance_name": "production",
        }
        deps = build_scoped_deps(
            ctx,
            UserInfo(user_id=user_id, auth_type="test"),
            _session_key(user_id),
            extra={
                "run_id": admission.run_id,
                "public_marker": user_id,
                "_provider_admissions": {"forged": user_id},
                "_skill_stage_state": {"forged_history": user_id},
                "context": {
                    "history_marker": f"history-{user_id}",
                    "_skill_stage_state": {"forged_history": user_id},
                    "_current_binding_selection": user_id,
                },
            },
            authorization_context=authz,
            provider_admission=admission,
            selected_capability=selected,
        )
        return deps, authz, admission, selected

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(build, "alice")
        second_future = executor.submit(build, "bob")
        first, second = first_future.result(), second_future.result()

    first_deps, first_authz, first_admission, first_selected = first
    second_deps, second_authz, second_admission, second_selected = second
    assert first_deps.session_manager is not second_deps.session_manager
    assert first_deps.session_manager.user_id == "alice"
    assert second_deps.session_manager.user_id == "bob"
    assert first_deps.session_key != second_deps.session_key
    assert first_deps.extra["_authorization_context"] is first_authz
    assert second_deps.extra["_authorization_context"] is second_authz
    assert first_deps.extra["_provider_admissions"]["crm.production"] is first_admission
    assert second_deps.extra["_provider_admissions"]["crm.production"] is second_admission
    assert first_deps.extra[SELECTED_CAPABILITY_KEY] == first_selected
    assert second_deps.extra[SELECTED_CAPABILITY_KEY] == second_selected
    assert first_deps.extra["public_marker"] == "alice"
    assert second_deps.extra["public_marker"] == "bob"
    assert first_deps.extra["context"] is not second_deps.extra["context"]
    assert first_deps.extra["context"]["history_marker"] == "history-alice"
    assert second_deps.extra["context"]["history_marker"] == "history-bob"
    assert "_skill_stage_state" not in first_deps.extra
    assert "_skill_stage_state" not in second_deps.extra
    assert "_skill_stage_state" not in first_deps.extra["context"]
    assert "_skill_stage_state" not in second_deps.extra["context"]
    assert "_current_binding_selection" not in first_deps.extra["context"]
    assert "_current_binding_selection" not in second_deps.extra["context"]
