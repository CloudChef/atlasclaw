# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Resolve Host paths into permission-scoped immutable context snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.atlasclaw.api.agent_capabilities import build_agent_capabilities
from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    has_provider_instance_access,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.object_actions import normalize_object_actions
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from .integration_registry import EmbedIntegrationRegistry, LoadedEmbedIntegration
from .models import ContextSnapshot, ResolvedObject
from .route_matcher import match_route, normalize_host_path
from .snapshot_store import EmbedContextSnapshotStore

logger = logging.getLogger(__name__)

class EmbedPermissionError(PermissionError):
    """Raised when RBAC hides a manifest-bound Skill or Provider instance."""


class EmbedResolverError(RuntimeError):
    """Raised when a provider resolver violates or cannot satisfy the v1 contract."""


@dataclass(frozen=True)
class EmbedContextResolution:
    """Distinguish an unsupported path from a matched route that failed closed."""

    matched: bool
    snapshot: ContextSnapshot | None = None


def _build_resolver_deps(
    *,
    deps: SkillDeps,
    user_info: UserInfo,
    request_cookies: dict[str, str],
    provider_type: str,
    instance_name: str,
    provider_instance: dict[str, Any],
) -> SkillDeps:
    """Restrict an in-process resolver to the former cookie-only boundary.

    Context resolvers need the selected endpoint and the current Host request
    cookies. They must not inherit configured Provider credentials, runtime SSO
    state, or the AtlasClaw authentication token from ordinary Tool dependencies.
    """

    safe_instance = {
        key: provider_instance[key]
        for key in ("base_url", "timeout")
        if key in provider_instance
    }
    trace_extra = {
        key: deps.extra[key]
        for key in (
            "active_internal_request_trace_id",
            "internal_request_trace_id",
        )
        if key in deps.extra
    }
    resolver_user = UserInfo(
        user_id=user_info.user_id,
        display_name=user_info.display_name,
        tenant_id=user_info.tenant_id,
        roles=list(user_info.roles),
    )
    return SkillDeps(
        user_info=resolver_user,
        session_key=deps.session_key,
        abort_signal=deps.abort_signal,
        cookies=request_cookies,
        extra={
            **trace_extra,
            "provider_type": provider_type,
            "provider_instance_name": instance_name,
            "provider_instance": safe_instance,
        },
    )


def build_authorization_extras(
    authz: AuthorizationContext | None,
) -> dict[str, Any]:
    """Project effective Skill and Provider restrictions into runtime extras.

    An allow-all section is omitted, while a restricted section preserves its
    exact permission list, including an empty deny-all list. Missing
    authorization yields no extras and is handled by the caller's hard gate.
    """
    if authz is None:
        return {}
    skills = authz.permissions.get("skills", {}) if isinstance(authz.permissions, dict) else {}
    providers = authz.permissions.get("providers", {}) if isinstance(authz.permissions, dict) else {}
    extra: dict[str, Any] = {}
    if skills.get("allow_all") is not True:
        extra["_user_skill_permissions"] = skills.get("skill_permissions", [])
    if providers.get("allow_all") is not True:
        extra["_provider_permissions"] = providers.get("provider_permissions", [])
    return extra


def require_embed_resolver_access(
    authz: AuthorizationContext | None,
    *,
    provider_type: str,
    provider_instance: str,
) -> AuthorizationContext:
    """Require explicit Provider-instance authorization for a controlled resolver."""
    if authz is None:
        raise EmbedPermissionError("embed authorization context is unavailable")
    if not has_provider_instance_access(authz, provider_type, provider_instance):
        raise EmbedPermissionError("embed provider instance is not available to the current user")
    return authz


class EmbedContextService:
    """Resolve page objects and bind them to existing Provider Skills."""

    def __init__(
        self,
        api_context: APIContext,
        integrations: EmbedIntegrationRegistry,
        snapshots: EmbedContextSnapshotStore,
    ) -> None:
        self._ctx = api_context
        self._integrations = integrations
        self._snapshots = snapshots

    async def resolve(
        self,
        *,
        surface_id: str,
        generation: int,
        path: str,
        user_info: UserInfo,
        request_cookies: dict[str, str],
        authz: AuthorizationContext | None,
    ) -> EmbedContextResolution:
        """Resolve one path while preserving whether a Provider route matched."""
        integration = self._integrations.get()
        if integration is None:
            raise KeyError("embed integration not found")
        normalized_path = normalize_host_path(path)
        matched = match_route(integration.routes, normalized_path)
        if matched is None:
            return EmbedContextResolution(matched=False)

        resolver_authz = require_embed_resolver_access(
            authz,
            provider_type=integration.config.provider_type,
            provider_instance=integration.config.provider_instance,
        )
        try:
            # Only fields produced by the server-side route match cross the
            # Provider boundary. In particular, the route's Skill binding is
            # never resolver input, so a Provider cannot replace that binding.
            result = await self._execute_resolver(
                integration=integration,
                user_info=user_info,
                request_cookies=request_cookies,
                authz=resolver_authz,
                arguments={
                    "route_id": matched.rule.id,
                    "path": normalized_path,
                    "route_parameters": matched.parameters,
                    "page_type": matched.rule.result.page_type,
                    "object_type": matched.rule.result.object_type,
                },
            )
            object_value, object_actions = self._parse_resolved_context(
                result,
                expected_type=matched.rule.result.object_type,
            )
        except (asyncio.TimeoutError, EmbedResolverError, ValueError) as exc:
            logger.warning(
                "Embed context resolver failed: provider=%s route=%s error=%s",
                integration.config.provider_type,
                matched.rule.id,
                exc,
            )
            return EmbedContextResolution(matched=True)

        try:
            self.validate_snapshot_skill_binding(
                provider_type=integration.config.provider_type,
                skill_ref=matched.rule.result.skill_ref,
            )
        except EmbedResolverError as exc:
            logger.warning(
                "Embed page Skill binding became unavailable: "
                "provider=%s route=%s error=%s",
                integration.config.provider_type,
                matched.rule.id,
                exc,
            )
            return EmbedContextResolution(matched=True)

        default_skill = self.resolve_visible_default_skill(
            integration=integration,
            skill_ref=matched.rule.result.skill_ref,
            authz=resolver_authz,
        )
        if default_skill is None:
            logger.warning(
                "Embed default Skill is not authorized: provider=%s route=%s",
                integration.config.provider_type,
                matched.rule.id,
            )
            return EmbedContextResolution(matched=True)

        created_at = datetime.now(timezone.utc)
        try:
            snapshot = ContextSnapshot(
                context_id=self._snapshots.new_context_id(),
                owner_user_id=user_info.user_id,
                surface_id=surface_id,
                generation=generation,
                provider_type=integration.config.provider_type,
                provider_instance=integration.config.provider_instance,
                page_type=matched.rule.result.page_type,
                skill_ref=matched.rule.result.skill_ref,
                skill_name=default_skill["name"],
                skill_description=default_skill["description"],
                object=object_value,
                object_actions=object_actions,
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=integration.context_ttl_seconds),
            )
        except ValueError as exc:
            logger.warning(
                "Embed context snapshot validation failed: provider=%s route=%s error=%s",
                integration.config.provider_type,
                matched.rule.id,
                exc,
            )
            return EmbedContextResolution(matched=True)
        accepted = self._snapshots.put(
            snapshot,
            max_contexts_per_user=integration.max_contexts_per_user,
        )
        if not accepted:
            return EmbedContextResolution(matched=True)
        return EmbedContextResolution(matched=True, snapshot=snapshot)

    def validate_snapshot_skill_binding(
        self,
        *,
        provider_type: str,
        skill_ref: str,
    ) -> None:
        """Require the route's exact Skill to belong to its configured Provider."""
        normalized_ref = str(skill_ref or "").strip().lower()
        normalized_provider = str(provider_type or "").strip().lower()
        skill = self._ctx.skill_registry.get_md_skill(normalized_ref)
        if (
            skill is None
            or str(skill.qualified_name or "").strip().lower() != normalized_ref
            or str(skill.provider or "").strip().lower() != normalized_provider
        ):
            raise EmbedResolverError("route Skill does not match the configured Provider")

    def resolve_visible_default_skill(
        self,
        *,
        integration: LoadedEmbedIntegration,
        skill_ref: str,
        authz: AuthorizationContext,
    ) -> dict[str, str] | None:
        """Return the matched default Skill only when ordinary RBAC exposes it."""
        normalized_ref = str(skill_ref or "").strip().lower()
        catalog = build_agent_capabilities(
            ctx=self._ctx,
            authz=authz,
            provider_instances=self._ctx.provider_instances or {},
        )
        capability = next(
            (
                item
                for item in catalog.get("capabilities", [])
                if isinstance(item, dict)
                and str(item.get("kind") or "").strip() == "provider_skill"
                and str(item.get("qualified_skill_name") or "").strip().lower()
                == normalized_ref
                and str(item.get("provider_type") or "").strip().lower()
                == integration.config.provider_type.lower()
                and str(item.get("instance_name") or "").strip()
                == integration.config.provider_instance
            ),
            None,
        )
        if capability is None:
            return None
        return {
            "ref": normalized_ref,
            "name": str(
                capability.get("skill_name")
                or normalized_ref.split(":", 1)[-1]
            ).strip(),
            "description": str(capability.get("description") or "").strip(),
        }

    async def _execute_resolver(
        self,
        *,
        integration: LoadedEmbedIntegration,
        user_info: UserInfo,
        request_cookies: dict[str, str],
        authz: AuthorizationContext,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute the cached Provider resolver with request-scoped dependencies."""
        # The synthetic session supplies the normal scoped dependency contract;
        # it does not create or resume a user-visible Chat session.
        synthetic_session = SessionKey(
            agent_id=integration.agent_id,
            user_id=user_info.user_id,
            channel="web",
            account_id=integration.session_scope,
            chat_type=ChatType.DM,
            peer_id=user_info.user_id,
        ).to_string(SessionScope.PER_ACCOUNT_CHANNEL_PEER)
        deps = build_scoped_deps(
            self._ctx,
            user_info,
            synthetic_session,
            request_cookies=request_cookies,
            extra=build_authorization_extras(authz),
        )
        provider_instances = deps.extra.get("provider_instances", {})
        provider_bucket = provider_instances.get(integration.config.provider_type, {})
        provider_instance = provider_bucket.get(integration.config.provider_instance)
        if not isinstance(provider_instance, dict):
            raise EmbedPermissionError("configured embed provider instance is unavailable")
        resolver_deps = _build_resolver_deps(
            deps=deps,
            user_info=user_info,
            request_cookies=request_cookies,
            provider_type=integration.config.provider_type,
            instance_name=integration.config.provider_instance,
            provider_instance=provider_instance,
        )
        try:
            raw = await asyncio.wait_for(
                integration.resolver_handler(
                    SimpleNamespace(deps=resolver_deps),
                    **arguments,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            raise EmbedResolverError("provider resolver execution failed") from exc
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError) as exc:
            raise EmbedResolverError("provider resolver returned invalid JSON") from exc

    @staticmethod
    def _unwrap_resolver_payload(payload: Any) -> Any:
        """Unwrap the existing script result envelope without scanning nested metadata."""
        if (
            isinstance(payload, dict)
            and payload.get("success") is True
            and not isinstance(payload.get("object"), dict)
            and isinstance(payload.get("output"), str)
        ):
            try:
                nested = json.loads(payload["output"])
            except json.JSONDecodeError:
                nested = None
            if isinstance(nested, dict):
                payload = nested
        return payload

    @classmethod
    def _parse_resolved_context(
        cls,
        payload: Any,
        *,
        expected_type: str,
    ) -> tuple[ResolvedObject, list[dict[str, Any]]]:
        """Validate one resolver object and its provider-declared presentation actions."""
        payload = cls._unwrap_resolver_payload(payload)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise EmbedResolverError("provider resolver did not resolve a context object")
        object_payload = payload.get("object")
        try:
            object_value = ResolvedObject.model_validate(object_payload)
        except Exception as exc:
            raise EmbedResolverError("provider resolver returned an invalid object") from exc
        if object_value.type != expected_type:
            raise EmbedResolverError("provider resolver object type does not match route manifest")
        raw_actions = payload.get("object_actions")
        if isinstance(raw_actions, list) and len(raw_actions) > 32:
            raise EmbedResolverError("provider resolver returned too many object actions")
        # Providers own action availability, while Core owns the generic action
        # schema and drops fields that are not part of that shared contract.
        return object_value, normalize_object_actions(raw_actions)
