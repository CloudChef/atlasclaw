# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.

"""Provider and Skill model policy management APIs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    ensure_any_permission,
    ensure_permission,
    get_authorization_context,
)
from app.atlasclaw.core.model_bindings import (
    BindingTarget,
    ModelBindingError,
    ModelBindingResolver,
    ModelPolicyValidationError,
    _provider_aliases,
    _required_capabilities,
    list_model_options,
    model_policy_response,
    normalize_model_policy,
    registered_skill_catalog,
)
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.models import (
    CapabilityModelPolicyModel,
    ServiceProviderConfigModel,
)
from app.atlasclaw.db.orm.audit import AuditService
from app.atlasclaw.db.orm.capability_model_policy import (
    CapabilityModelPolicyService,
    RevisionConflictError,
    target_identity,
)
from app.atlasclaw.api.deps_context import get_api_context

router = APIRouter(tags=["Provider Management"])


class ModelPolicyPayload(BaseModel):
    """Writable policy shape; response-only safe metadata is rejected."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["inherit", "explicit"]
    allowed_model_refs: list[str] = Field(default_factory=list)
    default_model_ref: str | None = None


class ProviderSkillPolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    model_policy: ModelPolicyPayload


class ProviderUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    enabled: bool
    model_policy: ModelPolicyPayload
    skills: list[ProviderSkillPolicyPayload] = Field(default_factory=list)


class ActivationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    enabled: bool


class StandaloneSkillPolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    model_policy: ModelPolicyPayload


def _context_parts(request: Request) -> tuple[Any, Any, Any, Any]:
    ctx = get_api_context()
    provider_registry = getattr(ctx, "service_provider_registry", None)
    skill_registry = getattr(ctx, "skill_registry", None)
    config = getattr(request.app.state, "config", None)
    return ctx, provider_registry, skill_registry, config


def _resolver(request: Request) -> ModelBindingResolver:
    _ctx, provider_registry, skill_registry, config = _context_parts(request)
    return ModelBindingResolver(config, skill_registry, provider_registry, None)


def _registry_instance_config(provider_registry: Any, provider_type: str, instance_name: str) -> dict[str, Any]:
    getter = getattr(provider_registry, "get_instance_config", None)
    if callable(getter):
        value = getter(provider_type, instance_name)
        return value if isinstance(value, dict) else {}
    return {}


async def _provider_catalog(
    session: AsyncSession,
    request: Request,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], ServiceProviderConfigModel]]:
    """Enumerate actual registered Provider instances and persisted activation state."""
    ctx, provider_registry, _skill_registry, _config = _context_parts(request)
    if provider_registry is None:
        raise HTTPException(status_code=503, detail="Provider registry is unavailable")

    names: dict[tuple[str, str], dict[str, Any]] = {}
    type_lister = getattr(provider_registry, "list_providers", None)
    instance_lister = getattr(provider_registry, "list_instances", None)
    for provider_type in type_lister() if callable(type_lister) else []:
        for instance_name in instance_lister(provider_type) if callable(instance_lister) else []:
            config = _registry_instance_config(provider_registry, provider_type, instance_name)
            names[(provider_type, instance_name)] = config

    context_instances = getattr(ctx, "provider_instances", {})
    if isinstance(context_instances, dict):
        for provider_type, instances in context_instances.items():
            if not isinstance(instances, dict):
                continue
            for instance_name, config in instances.items():
                names.setdefault(
                    (str(provider_type), str(instance_name)),
                    config if isinstance(config, dict) else {},
                )

    result = await session.execute(select(ServiceProviderConfigModel))
    db_rows = {
        (row.provider_type, row.instance_name): row
        for row in result.scalars().all()
    }
    registered_types = set(type_lister() if callable(type_lister) else [])
    template_getter = getattr(provider_registry, "get_template_for_provider_type", None)
    for key, row in db_rows.items():
        provider_type, instance_name = key
        has_template = bool(callable(template_getter) and template_getter(provider_type))
        if provider_type in registered_types or has_template:
            names.setdefault(key, {})

    return names, db_rows


async def _policy_rows(session: AsyncSession) -> dict[tuple[str, str, str, str], CapabilityModelPolicyModel]:
    rows = await CapabilityModelPolicyService.list_all(session)
    return {target_identity(row): row for row in rows}


def _raw_policy(record: CapabilityModelPolicyModel | None) -> dict[str, Any]:
    if record is None:
        return {"mode": "inherit", "allowed_model_refs": [], "default_model_ref": None}
    return {
        "mode": record.model_mode,
        "allowed_model_refs": list(record.allowed_model_refs or []),
        "default_model_ref": record.default_model_ref,
    }


def _provider_name(
    provider_registry: Any,
    provider_type: str,
    instance_name: str,
    config: dict[str, Any],
) -> str:
    for value in (config.get("display_name"), config.get("name")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    schema_getter = getattr(provider_registry, "get_provider_schema_definition", None)
    schema = schema_getter(provider_type) if callable(schema_getter) else None
    return str(getattr(schema, "display_name", "") or instance_name or provider_type)


def _skill_records_for_provider(skill_registry: Any, provider_registry: Any, provider_type: str) -> list[dict[str, Any]]:
    aliases = _provider_aliases(provider_registry, provider_type)
    return [
        item
        for item in registered_skill_catalog(skill_registry)
        if str(item.get("provider_type") or "").strip().lower() in aliases
    ]


def _standalone_skill_records(skill_registry: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in registered_skill_catalog(skill_registry)
        if not str(item.get("provider_type") or "").strip()
    ]


def _provider_dto(
    *,
    provider_type: str,
    instance_name: str,
    config: dict[str, Any],
    provider_registry: Any,
    skill_registry: Any,
    db_provider_row: ServiceProviderConfigModel | None,
    records: dict[tuple[str, str, str, str], CapabilityModelPolicyModel],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    provider_target = BindingTarget(
        kind="provider",
        provider_type=provider_type,
        instance_name=instance_name,
    )
    provider_record = records.get(target_identity(provider_target))
    provider_policy = _raw_policy(provider_record)
    if provider_record is not None and provider_record.enabled is not None:
        enabled = bool(provider_record.enabled)
    elif db_provider_row is not None:
        enabled = bool(db_provider_row.is_active)
    else:
        enabled = config.get("enabled") is not False

    skills: list[dict[str, Any]] = []
    for item in _skill_records_for_provider(skill_registry, provider_registry, provider_type):
        skill_id = item["skill_id"]
        skill_target = BindingTarget(
            kind="provider_skill",
            provider_type=provider_type,
            instance_name=instance_name,
            skill_id=skill_id,
        )
        skill_record = records.get(target_identity(skill_target))
        skill_policy = _raw_policy(skill_record)
        effective_policy = (
            skill_policy
            if skill_policy["mode"] == "explicit"
            else provider_policy
            if provider_policy["mode"] == "explicit"
            else {"mode": "inherit", "allowed_model_refs": [], "default_model_ref": None}
        )
        skills.append({
            "skill_id": skill_id,
            "name": item["name"],
            "description": item["description"],
            "requires_vision": bool(item["requires_vision"]),
            "revision": int(getattr(skill_record, "revision", 0) or 0),
            "model_policy": model_policy_response(skill_policy, catalog),
            "effective_model_policy": model_policy_response(effective_policy, catalog),
        })

    return {
        "provider_type": provider_type,
        "instance_name": instance_name,
        "name": _provider_name(provider_registry, provider_type, instance_name, config),
        "enabled": enabled,
        "revision": int(getattr(provider_record, "revision", 0) or 0),
        "model_policy": model_policy_response(provider_policy, catalog),
        "skills": skills,
    }


async def _find_provider(
    session: AsyncSession,
    request: Request,
    provider_type: str,
    instance_name: str,
) -> tuple[dict[str, Any], ServiceProviderConfigModel | None]:
    providers, db_rows = await _provider_catalog(session, request)
    key = (provider_type, instance_name)
    if key not in providers:
        raise HTTPException(status_code=404, detail="Unknown Provider target")
    provider_config = dict(providers[key])
    provider_target = BindingTarget(
        kind="provider", provider_type=provider_type, instance_name=instance_name
    )
    policy = await CapabilityModelPolicyService.get_by_target(session, provider_target)
    if policy is not None and policy.enabled is not None:
        provider_config["_effective_enabled"] = bool(policy.enabled)
    elif key in db_rows:
        provider_config["_effective_enabled"] = bool(db_rows[key].is_active)
    else:
        provider_config["_effective_enabled"] = provider_config.get("enabled") is not False
    return provider_config, db_rows.get(key)


def _target_for_skill(
    *,
    provider_type: str,
    instance_name: str,
    skill_id: str,
    skill_registry: Any,
    provider_registry: Any,
) -> BindingTarget:
    if not any(item["skill_id"] == skill_id for item in _skill_records_for_provider(
        skill_registry, provider_registry, provider_type
    )):
        raise HTTPException(status_code=404, detail="Unknown Provider Skill target")
    return BindingTarget(
        kind="provider_skill",
        provider_type=provider_type,
        instance_name=instance_name,
        skill_id=skill_id,
    )


def _required_permission(authz: AuthorizationContext, name: str) -> None:
    ensure_permission(
        authz,
        f"provider_management.{name}",
        detail=f"Missing permission: provider_management.{name}",
    )


def _normalize_policy_payload(payload: ModelPolicyPayload) -> dict[str, Any]:
    """Map canonical policy validation failures to a client error at the API boundary."""
    try:
        return normalize_model_policy(payload.model_dump())
    except ModelPolicyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _safe_policy_audit(record: CapabilityModelPolicyModel | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "target_kind": record.target_kind,
        "provider_type": record.provider_type,
        "instance_name": record.instance_name,
        "skill_id": record.skill_id,
        "enabled": record.enabled,
        "model_policy": _raw_policy(record),
        "revision": record.revision,
    }


async def _audit_saved_rows(
    session: AsyncSession,
    before: dict[tuple[str, str, str, str], dict[str, Any] | None],
    saved: list[CapabilityModelPolicyModel],
    authz: AuthorizationContext,
) -> None:
    for row in saved:
        identity = target_identity(row)
        await AuditService.log_audit(
            session=session,
            entity_type="capability_model_policy",
            entity_id=row.id,
            action="UPDATE" if before.get(identity) is not None else "CREATE",
            user_id=authz.user.user_id,
            old_value=before.get(identity),
            new_value=_safe_policy_audit(row),
        )


async def _catalog_for_response(session: AsyncSession, resolver: ModelBindingResolver) -> dict[str, Any]:
    return await resolver._build_catalog(session)


async def _provider_response(
    session: AsyncSession,
    request: Request,
    provider_type: str,
    instance_name: str,
) -> dict[str, Any]:
    ctx, provider_registry, skill_registry, _config = _context_parts(request)
    config, db_provider_row = await _find_provider(session, request, provider_type, instance_name)
    records = await _policy_rows(session)
    catalog = await _catalog_for_response(session, _resolver(request))
    return _provider_dto(
        provider_type=provider_type,
        instance_name=instance_name,
        config=config,
        provider_registry=provider_registry,
        skill_registry=skill_registry,
        db_provider_row=db_provider_row,
        records=records,
        catalog=catalog,
    )


@router.get("/providers")
async def list_providers(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "view")
    ctx, provider_registry, skill_registry, _config = _context_parts(request)
    providers, db_rows = await _provider_catalog(session, request)
    records = await _policy_rows(session)
    catalog = await _catalog_for_response(session, _resolver(request))
    result = [
        _provider_dto(
            provider_type=provider_type,
            instance_name=instance_name,
            config=config,
            provider_registry=provider_registry,
            skill_registry=skill_registry,
            db_provider_row=db_rows.get((provider_type, instance_name)),
            records=records,
            catalog=catalog,
        )
        for (provider_type, instance_name), config in sorted(providers.items())
    ]
    return {"providers": result}


@router.get("/providers/{provider_type}/{instance_name}")
async def get_provider(
    provider_type: str,
    instance_name: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "view")
    return await _provider_response(session, request, provider_type, instance_name)


@router.put("/providers/{provider_type}/{instance_name}")
async def update_provider(
    provider_type: str,
    instance_name: str,
    payload: ProviderUpdatePayload,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    ensure_any_permission(
        authz,
        (
            "provider_management.enable_disable",
            "provider_management.configure_models",
        ),
        detail="Missing permission to update Provider management settings",
    )
    _ctx, provider_registry, skill_registry, config = _context_parts(request)
    provider_config, _db_provider_row = await _find_provider(
        session, request, provider_type, instance_name
    )
    parent_target = BindingTarget(
        kind="provider",
        provider_type=provider_type,
        instance_name=instance_name,
    )
    parent_record = await CapabilityModelPolicyService.get_by_target(session, parent_target)
    current_revision = int(getattr(parent_record, "revision", 0) or 0)
    if payload.revision != current_revision:
        raise HTTPException(status_code=409, detail="The Provider changed; refresh before saving")

    provider_policy = _normalize_policy_payload(payload.model_policy)
    current_provider_policy = _raw_policy(parent_record)
    child_payloads: dict[str, dict[str, Any]] = {}
    for child in payload.skills:
        if child.skill_id in child_payloads:
            raise HTTPException(status_code=422, detail="Duplicate Provider Skill target")
        target = _target_for_skill(
            provider_type=provider_type,
            instance_name=instance_name,
            skill_id=child.skill_id,
            skill_registry=skill_registry,
            provider_registry=provider_registry,
        )
        child_payloads[child.skill_id] = {
            "target": target,
            "model_policy": _normalize_policy_payload(child.model_policy),
            "record": await CapabilityModelPolicyService.get_by_target(session, target),
        }

    current_enabled = bool(provider_config["_effective_enabled"])
    enabled_changed = payload.enabled != current_enabled
    model_changed = provider_policy != current_provider_policy or any(
        item["model_policy"] != _raw_policy(item["record"])
        for item in child_payloads.values()
    )
    if enabled_changed:
        _required_permission(authz, "enable_disable")
    if model_changed:
        _required_permission(authz, "configure_models")
    if not enabled_changed and not model_changed:
        return await _provider_response(session, request, provider_type, instance_name)

    resolver = ModelBindingResolver(
        config,
        skill_registry,
        provider_registry,
        None,
    )
    catalog = await resolver._build_catalog(session)
    if provider_policy != current_provider_policy:
        try:
            await resolver._validate_policy_in_session(
                session,
                parent_target,
                provider_policy,
                catalog,
            )
        except ModelPolicyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # A narrower Provider set cannot strand an existing explicit Skill policy.
        for item in _skill_records_for_provider(skill_registry, provider_registry, provider_type):
            skill_id = item["skill_id"]
            target = BindingTarget(
                kind="provider_skill",
                provider_type=provider_type,
                instance_name=instance_name,
                skill_id=skill_id,
            )
            child_record = await CapabilityModelPolicyService.get_by_target(session, target)
            submitted = child_payloads.get(skill_id)
            effective_child_policy = (
                submitted["model_policy"] if submitted is not None else _raw_policy(child_record)
            )
            if effective_child_policy["mode"] == "explicit":
                try:
                    await resolver._validate_policy_in_session(
                        session,
                        target,
                        effective_child_policy,
                        catalog,
                        provider_model_policy=provider_policy,
                    )
                except ModelPolicyValidationError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Provider model removal affects Skill '{skill_id}': {exc}",
                    ) from exc

    records_to_save: list[dict[str, Any]] = [{
        "target": parent_target,
        "revision": current_revision,
        "enabled": payload.enabled,
        "model_policy": provider_policy,
    }]
    before: dict[tuple[str, str, str, str], dict[str, Any] | None] = {
        target_identity(parent_target): _safe_policy_audit(parent_record),
    }
    if model_changed:
        for item in child_payloads.values():
            target = item["target"]
            child_record = item["record"]
            child_policy = item["model_policy"]
            if child_policy == _raw_policy(child_record):
                continue
            try:
                await resolver._validate_policy_in_session(
                    session,
                    target,
                    child_policy,
                    catalog,
                    provider_model_policy=provider_policy,
                )
            except ModelPolicyValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            records_to_save.append({
                "target": target,
                "revision": int(getattr(child_record, "revision", 0) or 0),
                "enabled": None,
                "model_policy": child_policy,
            })
            before[target_identity(target)] = _safe_policy_audit(child_record)

    try:
        saved = await CapabilityModelPolicyService.save_many(
            session,
            records_to_save,
            updated_by=authz.user.user_id,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit_saved_rows(session, before, saved, authz)
    await session.flush()
    return await _provider_response(session, request, provider_type, instance_name)


@router.patch("/providers/{provider_type}/{instance_name}/activation")
async def update_provider_activation(
    provider_type: str,
    instance_name: str,
    payload: ActivationPayload,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "enable_disable")
    provider_config, _db_provider = await _find_provider(session, request, provider_type, instance_name)
    target = BindingTarget(kind="provider", provider_type=provider_type, instance_name=instance_name)
    record = await CapabilityModelPolicyService.get_by_target(session, target)
    current_revision = int(getattr(record, "revision", 0) or 0)
    if payload.revision != current_revision:
        raise HTTPException(status_code=409, detail="The Provider changed; refresh before saving")
    if payload.enabled == bool(provider_config["_effective_enabled"]):
        return await _provider_response(session, request, provider_type, instance_name)

    before = {target_identity(target): _safe_policy_audit(record)}
    try:
        saved = await CapabilityModelPolicyService.save_many(
            session,
            [{
                "target": target,
                "revision": current_revision,
                "enabled": payload.enabled,
                "model_policy": _raw_policy(record),
            }],
            updated_by=authz.user.user_id,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit_saved_rows(session, before, saved, authz)
    await session.flush()
    return await _provider_response(session, request, provider_type, instance_name)


@router.get("/standalone-skills")
async def list_standalone_skills(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "view")
    _ctx, _provider_registry, skill_registry, _config = _context_parts(request)
    records = await _policy_rows(session)
    catalog = await _catalog_for_response(session, _resolver(request))
    skills = []
    for item in _standalone_skill_records(skill_registry):
        target = BindingTarget(kind="standalone_skill", skill_id=item["skill_id"])
        record = records.get(target_identity(target))
        policy = _raw_policy(record)
        skills.append({
            "skill_id": item["skill_id"],
            "name": item["name"],
            "description": item["description"],
            "requires_vision": bool(item["requires_vision"]),
            "revision": int(getattr(record, "revision", 0) or 0),
            "model_policy": model_policy_response(policy, catalog),
        })
    return {"skills": skills}


@router.put("/standalone-skills/{skill_id:path}/model-policy")
async def update_standalone_skill_policy(
    skill_id: str,
    payload: StandaloneSkillPolicyPayload,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "configure_models")
    _ctx, provider_registry, skill_registry, config = _context_parts(request)
    item = next(
        (candidate for candidate in _standalone_skill_records(skill_registry) if candidate["skill_id"] == skill_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown standalone Skill target")
    target = BindingTarget(kind="standalone_skill", skill_id=skill_id)
    current = await CapabilityModelPolicyService.get_by_target(session, target)
    current_revision = int(getattr(current, "revision", 0) or 0)
    if payload.revision != current_revision:
        raise HTTPException(status_code=409, detail="The Skill policy changed; refresh before saving")
    policy = _normalize_policy_payload(payload.model_policy)
    resolver = ModelBindingResolver(config, skill_registry, provider_registry, None)
    catalog = await resolver._build_catalog(session)
    try:
        await resolver._validate_policy_in_session(session, target, policy, catalog)
    except ModelPolicyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    before = {target_identity(target): _safe_policy_audit(current)}
    try:
        saved = await CapabilityModelPolicyService.save_many(
            session,
            [{
                "target": target,
                "revision": current_revision,
                "enabled": None,
                "model_policy": policy,
            }],
            updated_by=authz.user.user_id,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit_saved_rows(session, before, saved, authz)
    await session.flush()
    refreshed = await CapabilityModelPolicyService.get_by_target(session, target)
    catalog = await resolver._build_catalog(session)
    return {
        "skill_id": skill_id,
        "name": item["name"],
        "description": item["description"],
        "requires_vision": bool(item["requires_vision"]),
        "revision": int(getattr(refreshed, "revision", 0) or 0),
        "model_policy": model_policy_response(_raw_policy(refreshed), catalog),
    }


@router.get("/model-options")
async def get_model_options(
    request: Request,
    target_kind: Literal["provider", "provider_skill", "standalone_skill"],
    provider_type: str = Query(""),
    instance_name: str = Query(""),
    skill_id: str = Query(""),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> dict[str, Any]:
    _required_permission(authz, "configure_models")
    ctx, provider_registry, skill_registry, config = _context_parts(request)
    records = await _policy_rows(session)
    resolver = ModelBindingResolver(config, skill_registry, provider_registry, None)
    excluded_refs: set[str] = set()
    scope_refs: set[str] | None = None

    if target_kind == "provider":
        if not provider_type or not instance_name or skill_id:
            raise HTTPException(status_code=422, detail="Provider model options require Provider identity only")
        await _find_provider(session, request, provider_type, instance_name)
        target = BindingTarget(kind="provider", provider_type=provider_type, instance_name=instance_name)
    elif target_kind == "provider_skill":
        if not provider_type or not instance_name or not skill_id:
            raise HTTPException(status_code=422, detail="Provider Skill model options require complete target identity")
        await _find_provider(session, request, provider_type, instance_name)
        target = _target_for_skill(
            provider_type=provider_type,
            instance_name=instance_name,
            skill_id=skill_id,
            skill_registry=skill_registry,
            provider_registry=provider_registry,
        )
        provider_record = await CapabilityModelPolicyService.get_by_target(
            session,
            BindingTarget(kind="provider", provider_type=provider_type, instance_name=instance_name),
        )
        if provider_record is not None and provider_record.model_mode == "explicit":
            scope_refs = set(provider_record.allowed_model_refs or [])
    else:
        if provider_type or instance_name or not skill_id:
            raise HTTPException(status_code=422, detail="Standalone Skill model options require skill_id only")
        if not any(item["skill_id"] == skill_id for item in _standalone_skill_records(skill_registry)):
            raise HTTPException(status_code=404, detail="Unknown standalone Skill target")
        target = BindingTarget(kind="standalone_skill", skill_id=skill_id)

    current = records.get(target_identity(target))
    if current is not None and current.model_mode == "explicit":
        excluded_refs = set(current.allowed_model_refs or [])
    required_capabilities = (
        _required_capabilities(resolver._get_skill_entry(skill_id))
        if target_kind in {"provider_skill", "standalone_skill"}
        else {}
    )

    models, total = await list_model_options(
        session,
        config=config,
        target_kind=target_kind,
        provider_type=provider_type,
        instance_name=instance_name,
        skill_id=skill_id,
        search=search,
        page=page,
        page_size=page_size,
        excluded_refs=excluded_refs,
        scope_refs=scope_refs,
        required_capabilities=required_capabilities,
    )
    return {
        "models": models,
        "total": total,
        "page": page,
        "page_size": page_size,
        "required_capabilities": required_capabilities,
    }
