# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.

"""Resolve global Provider and Skill model policies to current credentials."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import (
    ensure_provider_instance_access,
    ensure_skill_access,
)
from app.atlasclaw.bootstrap.startup_helpers import (
    _missing_token_fields,
    build_token_entries,
    merge_provider_instances,
)
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool
from app.atlasclaw.db import get_db_manager
from app.atlasclaw.db.models import (
    CapabilityModelPolicyModel,
    ModelConfigModel,
    ServiceProviderConfigModel,
    TokenModel,
)
from app.atlasclaw.db.orm.capability_model_policy import (
    CapabilityModelPolicyService,
    ModelPolicyValidationError,
    normalize_model_policy,
    target_identity,
)
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.orm.model_token_config import ModelTokenConfigService
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BindingTarget:
    """Canonical Provider or Skill identity used for policy resolution."""

    kind: str
    provider_type: str = ""
    instance_name: str = ""
    skill_id: str = ""


@dataclass(frozen=True)
class ProviderAdmission:
    """Trusted server snapshot allowing an already-admitted Provider run to finish."""

    provider_type: str
    instance_name: str
    user_id: str
    run_id: str


@dataclass
class BindingSelection:
    """A chosen credential plus the bounded set permitted for this execution."""

    token: TokenEntry = field(repr=False)
    eligible_token_ids: set[str]
    policy_id: str | None
    revision: int
    source: str
    reason: str
    eligible_tokens: tuple[TokenEntry, ...] = field(default=(), repr=False)
    pool_token_ids: dict[str, str] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        """Describe a selection without ever rendering credential material."""
        return (
            "BindingSelection("
            f"token_id={self.token.token_id!r}, provider={self.token.provider!r}, "
            f"model={self.token.model!r}, eligible_token_ids={self.eligible_token_ids!r}, "
            f"policy_id={self.policy_id!r}, revision={self.revision}, "
            f"source={self.source!r}, reason={self.reason!r})"
        )


class ModelBindingError(RuntimeError):
    """Raised when a target or its saved policy cannot produce an allowed model."""


@dataclass
class ModelCatalogEntry:
    """Internal catalog entry; ``token`` contains credentials and stays server-side."""

    ref: str
    token: TokenEntry = field(repr=False)
    source: str
    is_active: bool
    display_name: str = ""
    pool_token_id: str = ""

    def to_option(self, *, missing: bool = False) -> dict[str, Any]:
        """Return safe catalog metadata without credential-bearing fields."""
        return {
            "ref": self.ref,
            "provider": self.token.provider,
            "model_id": self.token.model,
            "capabilities": dict(self.token.capabilities or {}),
            "source": self.source,
            "is_active": bool(self.is_active) and not missing,
            **({"missing": True} if missing else {}),
        }


def _record_policy(record: CapabilityModelPolicyModel | None) -> dict[str, Any]:
    if record is None:
        return {"mode": "inherit", "allowed_model_refs": [], "default_model_ref": None}
    return {
        "mode": record.model_mode,
        "allowed_model_refs": list(record.allowed_model_refs or []),
        "default_model_ref": record.default_model_ref,
    }


def _required_capabilities(skill_entry: Any | None) -> dict[str, Any]:
    if skill_entry is None:
        return {}
    metadata = getattr(skill_entry, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    required = (
        getattr(skill_entry, "required_capabilities", None)
        or getattr(skill_entry, "model_capabilities", None)
        or metadata.get("required_capabilities", {})
    )
    result = dict(required) if isinstance(required, dict) else {}
    if metadata.get("requires_vision") is True or getattr(skill_entry, "requires_vision", False) is True:
        result["vision"] = True
    return result


def _skill_provider_type(skill_entry: Any | None) -> str:
    if skill_entry is None:
        return ""
    provider_type = str(
        getattr(skill_entry, "provider", "")
        or getattr(skill_entry, "provider_type", "")
        or ""
    ).strip()
    if provider_type:
        return provider_type
    capability_class = str(getattr(skill_entry, "capability_class", "") or "").strip()
    if capability_class.lower().startswith("provider:"):
        return capability_class.split(":", 1)[1].strip()
    return ""


def _supports(token: TokenEntry, required_capabilities: dict[str, Any]) -> bool:
    capabilities = token.capabilities if isinstance(token.capabilities, dict) else {}
    for name, value in (required_capabilities or {}).items():
        if value is True and capabilities.get(name) is not True:
            return False
        if value not in (True, False, None) and capabilities.get(name) != value:
            return False
    return True


def _token_policy_pool(token_policy: Any) -> TokenPool | None:
    pool = getattr(token_policy, "token_pool", None)
    return pool if isinstance(pool, TokenPool) or hasattr(pool, "select_token") else None


def _policy_key(target: BindingTarget) -> tuple[str, str, str, str]:
    try:
        return target_identity(target)
    except ValueError as exc:
        raise ModelBindingError(str(exc)) from exc


def _provider_aliases(provider_registry: Any, provider_type: str) -> set[str]:
    aliases = {str(provider_type or "").strip().lower()}
    template_getter = getattr(provider_registry, "get_template_for_provider_type", None)
    if callable(template_getter):
        template = template_getter(provider_type)
        template_name = str(getattr(template, "name", "") or "").strip().lower()
        if template_name:
            aliases.add(template_name)
    return aliases - {""}


def registered_skill_catalog(skill_registry: Any) -> list[dict[str, Any]]:
    """Return canonical registered Markdown and executable Skill targets."""
    if skill_registry is None:
        return []
    by_id: dict[str, dict[str, Any]] = {}
    md_snapshot = getattr(skill_registry, "md_snapshot", None)
    if callable(md_snapshot):
        for item in md_snapshot():
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("qualified_name") or item.get("name") or "").strip()
            if not skill_id:
                continue
            metadata = item.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            by_id[skill_id] = {
                "skill_id": skill_id,
                "name": str(item.get("name") or skill_id),
                "description": str(item.get("description") or ""),
                "provider_type": str(item.get("provider") or "").strip(),
                "requires_vision": metadata.get("requires_vision") is True,
                "metadata": dict(metadata),
                "source": "markdown",
            }

    snapshot = getattr(skill_registry, "snapshot", None)
    if callable(snapshot):
        md_tool_names = {
            str(name or "").strip()
            for names in getattr(skill_registry, "_md_skill_tools", {}).values()
            for name in (names or set())
        }
        for item in snapshot():
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("name") or "").strip()
            if not skill_id or skill_id in md_tool_names or skill_id in by_id:
                continue
            source = str(item.get("source") or "").strip().lower()
            if source == "internal_runtime":
                continue
            provider_type = str(item.get("provider_type") or "").strip()
            if not provider_type and str(item.get("capability_class") or "").lower().startswith("provider:"):
                provider_type = str(item["capability_class"]).split(":", 1)[1].strip()
            executable = getattr(skill_registry, "get", lambda _name: None)(skill_id)
            metadata_obj = executable[0] if isinstance(executable, tuple) and executable else None
            declared = getattr(metadata_obj, "requires_vision", False) is True
            required = getattr(metadata_obj, "required_capabilities", {})
            declared = declared or (
                isinstance(required, dict) and required.get("vision") is True
            )
            by_id[skill_id] = {
                "skill_id": skill_id,
                "name": str(item.get("name") or skill_id),
                "description": str(item.get("description") or ""),
                "provider_type": provider_type,
                "requires_vision": declared,
                "metadata": {},
                "source": "executable",
            }
    return sorted(by_id.values(), key=lambda item: item["skill_id"].casefold())


def _registered_provider_instance(provider_registry: Any, provider_type: str, instance_name: str) -> bool:
    list_instances = getattr(provider_registry, "list_instances", None)
    if not callable(list_instances):
        return False
    try:
        return instance_name in list_instances(provider_type)
    except (KeyError, TypeError, ValueError):
        return False


class ModelBindingResolver:
    """Resolve policy references against current registries and committed credentials."""

    def __init__(self, config, skill_registry, provider_registry, token_policy):
        self.config = config
        self.skill_registry = skill_registry
        self.provider_registry = provider_registry
        self.token_policy = token_policy

    async def provider_is_enabled(self, provider_type: str, instance_name: str) -> bool:
        """Return the current activation overlay for one registered Provider instance."""
        normalized_provider = str(provider_type or "").strip()
        normalized_instance = str(instance_name or "").strip()
        if not normalized_provider or not normalized_instance:
            return False
        manager = get_db_manager()
        if not manager.is_initialized:
            return _registered_provider_instance(
                self.provider_registry, normalized_provider, normalized_instance
            )
        async with manager.get_session() as session:
            return await self._provider_is_enabled_in_session(
                session, normalized_provider, normalized_instance
            )

    async def ensure_provider_enabled(self, provider_type: str, instance_name: str) -> None:
        """Reject new execution for an unknown or globally disabled Provider instance."""
        if not await self.provider_is_enabled(provider_type, instance_name):
            raise ModelBindingError(
                f"Provider '{provider_type}.{instance_name}' is not registered or is disabled"
            )

    async def effective_provider_instances(
        self,
        instances: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Build a fresh server-side instance view with committed activation overlays.

        The passed map supplies resolved JSON/runtime configuration for currently
        configured JSON keys. Persisted rows are reloaded and take precedence so
        an inactive DB instance cannot reappear from its JSON counterpart unless
        its committed Provider policy explicitly enables it.
        """
        configured = getattr(self.config, "service_providers", {})
        configured = configured if isinstance(configured, dict) else {}
        base_instances = merge_provider_instances(instances or {}, configured)
        db_rows: dict[tuple[str, str], ServiceProviderConfigModel] = {}
        policy_rows: dict[tuple[str, str], CapabilityModelPolicyModel] = {}
        db_instances: dict[str, dict[str, dict[str, Any]]] = {}

        manager = get_db_manager()
        if manager.is_initialized:
            async with manager.get_session() as session:
                provider_result = await session.execute(select(ServiceProviderConfigModel))
                for row in provider_result.scalars().all():
                    identity = (row.provider_type, row.instance_name)
                    db_rows[identity] = row
                    db_instances.setdefault(row.provider_type, {})[row.instance_name] = (
                        ServiceProviderConfigService.get_config(row)
                    )
                for row in await CapabilityModelPolicyService.list_all(session):
                    if row.target_kind == "provider":
                        policy_rows[(row.provider_type, row.instance_name)] = row

        candidate_instances = merge_provider_instances(db_instances, base_instances)
        configured_keys = {
            (str(provider_type), str(instance_name))
            for provider_type, provider_bucket in configured.items()
            if isinstance(provider_bucket, dict)
            for instance_name in provider_bucket
        }
        # Extension-only Provider types expose a server-defined default instance
        # without a JSON/DB row; preserve that existing registry behavior.
        extension_defaults: set[tuple[str, str]] = set()
        if self.provider_registry is not None:
            schema_getter = getattr(self.provider_registry, "get_provider_schema_definition", None)
            instance_lister = getattr(self.provider_registry, "list_instances", None)
            provider_lister = getattr(self.provider_registry, "list_providers", None)
            provider_types = provider_lister() if callable(provider_lister) else []
            for provider_type in provider_types:
                schema = schema_getter(provider_type) if callable(schema_getter) else None
                if getattr(schema, "extension_only", False) and callable(instance_lister):
                    if "default" in instance_lister(provider_type):
                        extension_defaults.add((str(provider_type), "default"))

        allowed_keys = configured_keys | set(db_rows) | extension_defaults
        effective: dict[str, dict[str, dict[str, Any]]] = {}
        for provider_type, provider_bucket in candidate_instances.items():
            if not isinstance(provider_bucket, dict):
                continue
            for instance_name, instance_config in provider_bucket.items():
                identity = (str(provider_type), str(instance_name))
                if identity not in allowed_keys or not isinstance(instance_config, dict):
                    continue
                provider_row = db_rows.get(identity)
                policy_row = policy_rows.get(identity)
                if policy_row is not None and policy_row.enabled is not None:
                    is_enabled = bool(policy_row.enabled)
                elif provider_row is not None:
                    is_enabled = bool(provider_row.is_active)
                else:
                    is_enabled = instance_config.get("enabled") is not False
                if is_enabled:
                    effective.setdefault(str(provider_type), {})[str(instance_name)] = dict(
                        instance_config
                    )
        return effective

    async def resolve(
        self,
        target: BindingTarget,
        *,
        authz=None,
        required_capabilities: dict | None = None,
        admission: ProviderAdmission | None = None,
    ) -> BindingSelection:
        """Resolve the target's explicit/inherited policy using current committed state."""
        if not isinstance(target, BindingTarget):
            raise ModelBindingError("A canonical BindingTarget is required")
        refresh_tokens = getattr(self.token_policy, "refresh_tokens", None)
        if callable(refresh_tokens):
            await refresh_tokens()
        identity = _policy_key(target)
        manager = get_db_manager()
        if manager.is_initialized:
            async with manager.get_session() as session:
                await self._validate_target(session, target)
                policies = {
                    target_identity(row): row
                    for row in await CapabilityModelPolicyService.list_all(session)
                }
                catalog = await self._build_catalog(session)
        else:
            await self._validate_target(None, target)
            policies = {}
            catalog = await self._build_catalog(None)

        target_provider_identity = (target.provider_type, target.instance_name)
        admitted = False
        if admission is not None:
            if not isinstance(admission, ProviderAdmission):
                raise ModelBindingError("Invalid Provider admission context")
            admission_identity = (admission.provider_type, admission.instance_name)
            if (
                target.kind not in {"provider", "provider_skill"}
                or admission_identity != target_provider_identity
            ):
                raise ModelBindingError("Provider admission does not match the execution target")
            if authz is not None and str(admission.user_id) != str(authz.user.user_id):
                raise ModelBindingError("Provider admission does not match the authenticated user")
            admitted = True
        if (
            target.kind in {"provider", "provider_skill"}
            and not admitted
        ):
            await self.ensure_provider_enabled(target.provider_type, target.instance_name)

        if target.kind == "provider_skill":
            skill_entry = self._get_skill_entry(target.skill_id)
            self._validate_provider_skill(target, skill_entry)
        elif target.kind == "standalone_skill":
            skill_entry = self._get_skill_entry(target.skill_id)
            self._validate_standalone_skill(target, skill_entry)
        else:
            skill_entry = None

        if authz is not None:
            if target.kind in {"provider", "provider_skill"}:
                ensure_provider_instance_access(authz, target.provider_type, target.instance_name)
            if target.kind == "standalone_skill":
                ensure_skill_access(authz, target.skill_id)

        required = dict(required_capabilities or {})
        declared = _required_capabilities(skill_entry)
        for name, value in declared.items():
            if value is True:
                required[name] = True

        own_record = policies.get(identity)
        provider_record = None
        if target.kind == "provider_skill":
            provider_identity = (
                "provider",
                target.provider_type,
                target.instance_name,
                "",
            )
            provider_record = policies.get(provider_identity)

        effective_record: CapabilityModelPolicyModel | None = own_record
        effective_policy = _record_policy(own_record)
        source = "platform_default"
        if target.kind == "provider":
            if effective_policy["mode"] == "explicit":
                source = "explicit_provider"
        elif target.kind == "provider_skill":
            if effective_policy["mode"] == "explicit":
                source = "explicit_skill"
            elif provider_record is not None and provider_record.model_mode == "explicit":
                effective_record = provider_record
                effective_policy = _record_policy(provider_record)
                source = "inherited_provider"
        elif target.kind == "standalone_skill":
            if effective_policy["mode"] == "explicit":
                source = "explicit_skill"

        all_candidates = [entry for entry in catalog.values() if entry.is_active]
        if effective_policy["mode"] == "explicit":
            allowed_refs = set(effective_policy["allowed_model_refs"])
            if target.kind == "provider_skill" and provider_record is not None:
                parent_policy = _record_policy(provider_record)
                if parent_policy["mode"] == "explicit":
                    parent_refs = set(parent_policy["allowed_model_refs"])
                    if not allowed_refs.issubset(parent_refs):
                        raise ModelBindingError(
                            "Provider Skill policy references a model outside the Provider allowlist"
                        )
            candidates = [
                catalog[ref]
                for ref in effective_policy["allowed_model_refs"]
                if ref in catalog and catalog[ref].is_active
            ]
            if source == "explicit_skill" and target.kind == "provider_skill":
                parent_policy = _record_policy(provider_record)
                if parent_policy["mode"] == "explicit":
                    candidates = [entry for entry in candidates if entry.ref in parent_policy["allowed_model_refs"]]
        else:
            candidates = all_candidates

        candidates = [entry for entry in candidates if _supports(entry.token, required)]
        if not candidates:
            capability = "vision-capable " if required.get("vision") is True else ""
            raise ModelBindingError(
                f"No available {capability}model matches the configured policy for this target"
            )

        preferred_ref = effective_policy.get("default_model_ref")
        reason = "configured default is available"
        if preferred_ref:
            preferred = next((entry for entry in candidates if entry.ref == preferred_ref), None)
            if preferred is not None and self._candidate_is_healthy(preferred):
                selected = preferred
            else:
                healthy_candidates = [entry for entry in candidates if self._candidate_is_healthy(entry)]
                if preferred is not None and not healthy_candidates:
                    selected = preferred
                else:
                    selected = self._best_candidate(healthy_candidates or candidates)
                reason = "configured default is unavailable, unhealthy, or lacks required capabilities"
        else:
            selected = self._platform_default_candidate(candidates, catalog)
            if selected is not None and not self._candidate_is_healthy(selected):
                selected = None
            if selected is None:
                healthy_candidates = [entry for entry in candidates if self._candidate_is_healthy(entry)]
                selected = self._best_candidate(healthy_candidates or candidates)
            reason = "platform default selection"

        policy_constrained = effective_policy["mode"] == "explicit"
        if policy_constrained:
            selected_token = replace(selected.token, token_id=selected.ref)
            eligible_tokens = tuple(
                replace(entry.token, token_id=entry.ref) for entry in candidates
            )
            eligible_ids = {entry.ref for entry in candidates}
            pool_token_ids = {
                entry.ref: entry.pool_token_id or entry.token.token_id
                for entry in candidates
            }
        else:
            selected_token = selected.token
            eligible_tokens = tuple(entry.token for entry in candidates)
            eligible_ids = {entry.token.token_id for entry in candidates}
            pool_token_ids = {
                entry.token.token_id: entry.pool_token_id or entry.token.token_id
                for entry in candidates
            }

        return BindingSelection(
            token=selected_token,
            eligible_token_ids=eligible_ids,
            policy_id=getattr(effective_record, "id", None),
            revision=int(getattr(effective_record, "revision", 0) or 0),
            source=source,
            reason=reason,
            eligible_tokens=eligible_tokens,
            pool_token_ids=pool_token_ids,
        )

    async def validate_policy(
        self,
        target: BindingTarget,
        policy: dict[str, Any],
        *,
        provider_model_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate writable references, required capabilities, and Provider scope."""
        normalized = normalize_model_policy(policy)
        manager = get_db_manager()
        if manager.is_initialized:
            async with manager.get_session() as session:
                await self._validate_target(session, target)
                catalog = await self._build_catalog(session)
                await self._validate_policy_in_session(
                    session,
                    target,
                    normalized,
                    catalog,
                    provider_model_policy=provider_model_policy,
                )
        else:
            await self._validate_target(None, target)
            catalog = await self._build_catalog(None)
            await self._validate_policy_in_session(
                None,
                target,
                normalized,
                catalog,
                provider_model_policy=provider_model_policy,
            )
        return normalized

    async def _validate_policy_in_session(
        self,
        session: AsyncSession | None,
        target: BindingTarget,
        policy: dict[str, Any],
        catalog: dict[str, ModelCatalogEntry],
        *,
        provider_model_policy: dict[str, Any] | None = None,
    ) -> None:
        if policy["mode"] == "inherit":
            return
        for ref in policy["allowed_model_refs"]:
            entry = catalog.get(ref)
            if entry is None:
                raise ModelPolicyValidationError(f"Model reference is unavailable: {ref}")
            if not entry.is_active:
                raise ModelPolicyValidationError(f"Model reference is inactive or unusable: {ref}")

        skill_entry = None
        if target.kind in {"provider_skill", "standalone_skill"}:
            skill_entry = self._get_skill_entry(target.skill_id)
        required = _required_capabilities(skill_entry)
        required_names = ", ".join(
            name for name, enabled in required.items() if enabled is True
        ) or "declared Skill"
        for ref in policy["allowed_model_refs"]:
            if not _supports(catalog[ref].token, required):
                raise ModelPolicyValidationError(
                    f"Model reference does not satisfy the Skill's required capabilities ({required_names}): {ref}"
                )

        if target.kind != "provider_skill":
            return
        if provider_model_policy is None:
            parent = None
            if session is not None:
                parent = await CapabilityModelPolicyService.get_by_target(
                    session,
                    BindingTarget(
                        kind="provider",
                        provider_type=target.provider_type,
                        instance_name=target.instance_name,
                    ),
                )
            provider_model_policy = _record_policy(parent)
        else:
            provider_model_policy = normalize_model_policy(provider_model_policy)
        if provider_model_policy["mode"] == "explicit":
            outside = set(policy["allowed_model_refs"]) - set(
                provider_model_policy["allowed_model_refs"]
            )
            if outside:
                raise ModelPolicyValidationError(
                    "Provider Skill models must be included in the Provider allowlist"
                )

    async def _build_catalog(
        self,
        session: AsyncSession | None,
    ) -> dict[str, ModelCatalogEntry]:
        """Build a fresh source-specific safe-reference catalog with secret values internal."""
        entries: dict[str, ModelCatalogEntry] = {}
        config = self.config
        if config is not None and getattr(config, "model", None) is not None:
            configured, _ = build_token_entries(config, include_unusable=True)
            active, _ = build_token_entries(config)
            active_fingerprints = {
                (entry.token_id, entry.credential_fingerprint) for entry in active
            }
            seen_file_refs: set[str] = set()
            for token in configured:
                ref = f"file-token:{token.token_id}"
                if not token.token_id or ref in seen_file_refs:
                    continue
                seen_file_refs.add(ref)
                entries[ref] = ModelCatalogEntry(
                    ref=ref,
                    token=token,
                    source="file",
                    is_active=(token.token_id, token.credential_fingerprint) in active_fingerprints,
                    pool_token_id=token.token_id,
                )

        if session is None:
                return entries

        model_result = await session.execute(
            select(ModelConfigModel).order_by(
                ModelConfigModel.provider.asc(),
                ModelConfigModel.model_id.asc(),
                ModelConfigModel.id.asc(),
            )
        )
        for model in model_result.scalars().all():
            api_key = ModelConfigService.get_decrypted_api_key(model) or ""
            token = TokenEntry(
                token_id=model.name,
                provider=model.provider,
                model=model.model_id,
                base_url=model.base_url or "",
                api_key=api_key,
                api_type=model.api_type or "openai",
                priority=model.priority or 0,
                weight=model.weight or 100,
                context_window=model.context_window,
                capabilities=ModelConfigService.get_capabilities(model) or {},
                source_model_config_id=model.id,
            )
            entries[f"db-model:{model.id}"] = ModelCatalogEntry(
                ref=f"db-model:{model.id}",
                token=token,
                source="database",
                is_active=bool(model.is_active) and not _missing_token_fields(token),
                display_name=str(model.display_name or model.name),
                pool_token_id=token.token_id,
            )

        token_result = await session.execute(
            select(TokenModel).order_by(
                TokenModel.provider.asc(),
                TokenModel.model.asc(),
                TokenModel.id.asc(),
            )
        )
        for model in token_result.scalars().all():
            api_key = ModelTokenConfigService.get_decrypted_api_key(model) or ""
            token = TokenEntry(
                token_id=model.name,
                provider=model.provider,
                model=model.model,
                base_url=model.base_url or "",
                api_key=api_key,
                priority=model.priority or 0,
                weight=model.weight or 100,
            )
            entries[f"db-token:{model.id}"] = ModelCatalogEntry(
                ref=f"db-token:{model.id}",
                token=token,
                source="database",
                is_active=bool(model.is_active) and not _missing_token_fields(token),
                display_name=model.name,
                pool_token_id=token.token_id,
            )
        return entries

    async def _validate_target(
        self,
        session: AsyncSession | None,
        target: BindingTarget,
    ) -> None:
        """Require a concrete target in the loaded Provider/Skill registry."""
        _policy_key(target)
        if target.kind == "provider":
            if not await self._provider_exists(session, target.provider_type, target.instance_name):
                raise ModelBindingError("Unknown Provider target")
            return
        if target.kind == "provider_skill":
            if not await self._provider_exists(session, target.provider_type, target.instance_name):
                raise ModelBindingError("Unknown Provider target")
            skill_entry = self._get_skill_entry(target.skill_id)
            self._validate_provider_skill(target, skill_entry)
            return
        if target.kind == "standalone_skill":
            skill_entry = self._get_skill_entry(target.skill_id)
            self._validate_standalone_skill(target, skill_entry)
            return
        raise ModelBindingError("Unknown model binding target kind")

    async def _provider_exists(
        self,
        session: AsyncSession,
        provider_type: str,
        instance_name: str,
    ) -> bool:
        if _registered_provider_instance(self.provider_registry, provider_type, instance_name):
            return True
        if session is None:
            return False
        result = await session.execute(
            select(ServiceProviderConfigModel.id).where(
                ServiceProviderConfigModel.provider_type == provider_type,
                ServiceProviderConfigModel.instance_name == instance_name,
            )
        )
        return result.scalar_one_or_none() is not None

    def _get_skill_entry(self, skill_id: str) -> Any | None:
        """Resolve an exact registered Markdown or executable skill identity."""
        if self.skill_registry is None:
            return None
        md_getter = getattr(self.skill_registry, "get_md_skill", None)
        if callable(md_getter):
            entry = md_getter(skill_id)
            if entry is not None:
                return entry
        getter = getattr(self.skill_registry, "get", None)
        if not callable(getter):
            return None
        executable = getter(skill_id)
        if executable is None and ":" in skill_id:
            executable = getter(skill_id.rsplit(":", 1)[-1])
        if isinstance(executable, tuple) and executable:
            return executable[0]
        return None

    def _validate_provider_skill(self, target: BindingTarget, skill_entry: Any | None) -> None:
        if skill_entry is None:
            raise ModelBindingError("Unknown Skill target")
        provider_name = _skill_provider_type(skill_entry).lower()
        aliases = _provider_aliases(self.provider_registry, target.provider_type)
        if provider_name not in aliases:
            raise ModelBindingError("Skill does not belong to the requested Provider")

    @staticmethod
    def _validate_standalone_skill(target: BindingTarget, skill_entry: Any | None) -> None:
        provider_name = _skill_provider_type(skill_entry)
        if skill_entry is None or str(provider_name or "").strip():
            raise ModelBindingError("Unknown standalone Skill target")

    async def _provider_is_enabled_in_session(
        self,
        session: AsyncSession,
        provider_type: str,
        instance_name: str,
    ) -> bool:
        exists = await self._provider_exists(session, provider_type, instance_name)
        if not exists:
            return False
        record = await CapabilityModelPolicyService.get_by_target(
            session,
            BindingTarget(kind="provider", provider_type=provider_type, instance_name=instance_name),
        )
        if record is not None and record.enabled is not None:
            return bool(record.enabled)
        result = await session.execute(
            select(ServiceProviderConfigModel.is_active).where(
                ServiceProviderConfigModel.provider_type == provider_type,
                ServiceProviderConfigModel.instance_name == instance_name,
            )
        )
        database_active = result.scalar_one_or_none()
        if database_active is not None:
            return bool(database_active)
        return _registered_provider_instance(self.provider_registry, provider_type, instance_name)

    def _platform_default_candidate(
        self,
        candidates: list[ModelCatalogEntry],
        catalog: dict[str, ModelCatalogEntry],
    ) -> ModelCatalogEntry | None:
        primary_id = str(getattr(self.token_policy, "primary_token_id", "") or "").strip()
        if primary_id:
            for entry in candidates:
                if (entry.pool_token_id or entry.token.token_id) == primary_id:
                    current = None
                    pool = _token_policy_pool(self.token_policy)
                    if pool is not None:
                        current = getattr(pool, "tokens", {}).get(primary_id)
                    if current is None or (
                        current.credential_fingerprint == entry.token.credential_fingerprint
                    ):
                        return entry
        return None

    def _best_candidate(self, candidates: list[ModelCatalogEntry]) -> ModelCatalogEntry:
        pool = _token_policy_pool(self.token_policy)

        def rank(entry: ModelCatalogEntry) -> tuple[bool, float, int, int]:
            pool_id = entry.pool_token_id or entry.token.token_id
            health = pool.get_token_health(pool_id) if pool is not None else None
            pool_token = getattr(pool, "tokens", {}).get(pool_id) if pool is not None else None
            if pool_token is not None and pool_token.credential_fingerprint != entry.token.credential_fingerprint:
                health = None
            is_healthy = health.is_healthy if health is not None else True
            health_score = float(health.health_score) if health is not None else 100.0
            return (
                is_healthy,
                health_score,
                int(entry.token.priority or 0),
                int(entry.token.weight or 0),
            )

        return max(candidates, key=rank)

    def _candidate_is_healthy(self, entry: ModelCatalogEntry) -> bool:
        """Use health only when it belongs to the same concrete credential snapshot."""
        pool = _token_policy_pool(self.token_policy)
        if pool is None:
            return True
        pool_id = entry.pool_token_id or entry.token.token_id
        pool_token = getattr(pool, "tokens", {}).get(pool_id)
        if pool_token is None or pool_token.credential_fingerprint != entry.token.credential_fingerprint:
            return True
        health = pool.get_token_health(pool_id)
        return health.is_healthy if health is not None else True


async def list_model_options(
    session: AsyncSession,
    *,
    config: Any,
    target_kind: str,
    provider_type: str = "",
    instance_name: str = "",
    skill_id: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    excluded_refs: set[str] | None = None,
    scope_refs: set[str] | None = None,
    required_capabilities: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Search and page safe model options within the requested effective scope."""
    resolver = ModelBindingResolver(config, None, None, None)
    catalog = await resolver._build_catalog(session)
    excluded = excluded_refs or set()
    required = required_capabilities or {}
    normalized_search = str(search or "").strip().casefold()
    options = []
    for entry in catalog.values():
        if entry.ref in excluded:
            continue
        if scope_refs is not None and entry.ref not in scope_refs:
            continue
        if required and not _supports(entry.token, required):
            continue
        option = entry.to_option()
        searchable = " ".join((entry.ref, option["provider"], option["model_id"])).casefold()
        if normalized_search and normalized_search not in searchable:
            continue
        options.append(option)
    options.sort(key=lambda option: (
        str(option["provider"]).casefold(),
        str(option["model_id"]).casefold(),
        str(option["ref"]).casefold(),
    ))
    total = len(options)
    start = (page - 1) * page_size
    return options[start : start + page_size], total


def model_policy_response(
    policy: dict[str, Any],
    catalog: dict[str, ModelCatalogEntry],
) -> dict[str, Any]:
    """Attach safe metadata for saved refs and retain stale refs for recovery."""
    normalized = normalize_model_policy(policy)
    allowed_models: list[dict[str, Any]] = []
    for ref in normalized["allowed_model_refs"]:
        entry = catalog.get(ref)
        if entry is None:
            allowed_models.append({
                "ref": ref,
                "provider": "",
                "model_id": ref,
                "capabilities": {},
                "source": "missing",
                "is_active": False,
                "missing": True,
            })
        else:
            allowed_models.append(entry.to_option())
    return {**normalized, "allowed_models": allowed_models}
