# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Resolve Host paths into permission-scoped immutable context snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.atlasclaw.api.deps_context import APIContext, build_scoped_deps
from app.atlasclaw.auth.guards import (
    AuthorizationContext,
    has_provider_instance_access,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.object_actions import collect_object_action_references
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from app.atlasclaw.skills.md_tool_runtime import (
    ScriptInvocationConfig,
    load_handler_from_file,
)

from .integration_registry import EmbedIntegrationRegistry, LoadedEmbedIntegration
from .models import (
    ContextSnapshot,
    ResolvedObject,
)
from .route_matcher import match_route, normalize_host_path
from .snapshot_store import EmbedContextSnapshotStore

logger = logging.getLogger(__name__)

RESOLVER_OBJECT_ACTION_MAX_COUNT = 16
RESOLVER_OBJECT_ACTION_MAX_INPUTS = 8
RESOLVER_OBJECT_ACTION_MAX_ID_LENGTH = 128
RESOLVER_OBJECT_ACTION_MAX_URL_LENGTH = 2048
RESOLVER_OBJECT_ACTION_MAX_STRING_LENGTH = 4096
RESOLVER_OBJECT_ACTION_MAX_JSON_BYTES = 32768


class EmbedPermissionError(PermissionError):
    """Raised when RBAC hides a manifest-bound Skill or Provider instance."""


class EmbedResolverError(RuntimeError):
    """Raised when a provider resolver violates or cannot satisfy the v1 contract."""


@dataclass(frozen=True)
class EmbedContextResolution:
    """Distinguish an unsupported path from a matched route that failed closed."""

    matched: bool
    snapshot: ContextSnapshot | None = None


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
        integration_id: str,
        surface_id: str,
        generation: int,
        path: str,
        user_info: UserInfo,
        request_cookies: dict[str, str],
        authz: AuthorizationContext | None,
    ) -> EmbedContextResolution:
        """Resolve one path while preserving whether a Provider route matched."""
        integration = self._integrations.get(integration_id)
        if integration is None:
            raise KeyError("embed integration not found")
        normalized_path = normalize_host_path(path)
        matched = match_route(integration.routes, normalized_path)
        if matched is None:
            return EmbedContextResolution(matched=False)

        resolver = integration.routes.context_resolver
        resolver_authz = require_embed_resolver_access(
            authz,
            provider_type=integration.config.provider_type,
            provider_instance=integration.config.provider_instance,
        )
        try:
            result = await asyncio.wait_for(
                self._execute_resolver(
                    integration=integration,
                    user_info=user_info,
                    request_cookies=request_cookies,
                    authz=resolver_authz,
                    entrypoint=resolver.entrypoint,
                    arguments={
                        "route_id": matched.rule.id,
                        "path": normalized_path,
                        "route_parameters": matched.parameters,
                        "page_type": matched.rule.result.page_type,
                        "object_type": matched.rule.result.object_type,
                    },
                ),
                timeout=30,
            )
            object_value, object_actions = self._parse_resolved_context(
                result,
                expected_type=matched.rule.result.object_type,
            )
        except (asyncio.TimeoutError, EmbedResolverError, ValueError) as exc:
            logger.warning(
                "Embed context resolver failed: integration=%s route=%s error=%s",
                integration_id,
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
                "integration=%s route=%s error=%s",
                integration_id,
                matched.rule.id,
                exc,
            )
            return EmbedContextResolution(matched=True)

        created_at = datetime.now(timezone.utc)
        snapshot = ContextSnapshot(
            context_id=self._snapshots.new_context_id(),
            owner_user_id=user_info.user_id,
            integration_id=integration_id,
            surface_id=surface_id,
            generation=generation,
            provider_type=integration.config.provider_type,
            provider_instance=integration.config.provider_instance,
            page_type=matched.rule.result.page_type,
            skill_ref=matched.rule.result.skill_ref,
            object=object_value,
            object_actions=object_actions,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=integration.config.context_ttl_seconds),
        )
        accepted = self._snapshots.put(
            snapshot,
            max_contexts_per_user=integration.config.max_contexts_per_user,
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

    async def _execute_resolver(
        self,
        *,
        integration: LoadedEmbedIntegration,
        user_info: UserInfo,
        request_cookies: dict[str, str],
        authz: AuthorizationContext,
        entrypoint: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute one validated Provider-owned resolver outside SkillRegistry."""
        synthetic_session = SessionKey(
            agent_id=integration.config.agent_id,
            user_id=user_info.user_id,
            channel="web",
            account_id=integration.config.session_scope,
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
        deps.extra.update(
            {
                "provider_type": integration.config.provider_type,
                "provider_instance_name": integration.config.provider_instance,
                "provider_instance": dict(provider_instance),
            }
        )
        resolver_path = (integration.provider_root / entrypoint).resolve()
        if (
            integration.provider_root != resolver_path
            and integration.provider_root not in resolver_path.parents
        ):
            raise EmbedResolverError("provider resolver entrypoint escapes provider root")
        if not resolver_path.is_file() or resolver_path.suffix != ".py":
            raise EmbedResolverError("provider resolver entrypoint is unavailable")
        handler = load_handler_from_file(
            resolver_path,
            "handler",
            provider_type=integration.config.provider_type,
            invocation_config=ScriptInvocationConfig(
                positional_args=(
                    "route_id",
                    "path",
                    "route_parameters",
                    "page_type",
                    "object_type",
                ),
            ),
            tool_name=f"embed_context_resolver:{entrypoint}",
            result_mode="tool_only_ok",
        )
        raw = await handler(SimpleNamespace(deps=deps), **arguments)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError) as exc:
            raise EmbedResolverError("provider resolver returned invalid JSON") from exc

    @classmethod
    def _parse_resolved_context(
        cls,
        payload: Any,
        *,
        expected_type: str,
    ) -> tuple[ResolvedObject, list[dict[str, Any]]]:
        """Validate one resolver object and freeze its optional public actions."""
        normalized_payload = cls._unwrap_resolver_payload(payload)
        object_value = cls._parse_resolved_object(
            normalized_payload,
            expected_type=expected_type,
        )
        return object_value, cls._freeze_resolver_object_actions(
            normalized_payload,
            object_value=object_value,
        )

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
    def _parse_resolved_object(cls, payload: Any, *, expected_type: str) -> ResolvedObject:
        """Validate the resolver's object while preserving the existing helper contract."""
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
        return object_value

    @staticmethod
    def _freeze_resolver_object_actions(
        payload: dict[str, Any],
        *,
        object_value: ResolvedObject,
    ) -> list[dict[str, Any]]:
        """Normalize resolver actions and bind them to the verified object.

        Resolver-provided identity is replaced with the verified Context object.
        Presentation fields, including inline confirmation, use the same shared
        object-action normalizer as ordinary Chat before the action sends an
        explicit intent through the existing Agent workflow.
        """
        raw_actions = payload.get("object_actions")
        if not isinstance(raw_actions, list):
            return []
        if len(raw_actions) > RESOLVER_OBJECT_ACTION_MAX_COUNT:
            return []
        try:
            serialized_actions = json.dumps(
                raw_actions,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            return []
        if len(serialized_actions) > RESOLVER_OBJECT_ACTION_MAX_JSON_BYTES:
            return []
        direct_actions = [
            action
            for action in raw_actions
            if isinstance(action, dict)
            and "action_id" in action
            and "kind" in action
            and EmbedContextService._resolver_object_action_within_limits(action)
        ]
        root_marker = secrets.token_urlsafe(32)
        references = collect_object_action_references(
            {
                "object_id": root_marker,
                "object_actions": direct_actions,
            }
        )
        root_reference = next(
            (
                reference
                for reference in references
                if reference.get("object_id") == root_marker
            ),
            None,
        )
        if root_reference is None:
            return []
        actions = root_reference.get("object_actions")
        if not isinstance(actions, list) or not actions:
            return []
        return [
            {
                "object_type": object_value.type,
                "object_id": object_value.id,
                "object_name": object_value.name or object_value.id,
                "object_actions": actions,
            }
        ]

    @staticmethod
    def _resolver_object_action_within_limits(action: dict[str, Any]) -> bool:
        """Keep resolver-origin presentation actions small before snapshot storage."""
        action_id = action.get("action_id")
        kind = action.get("kind")
        if (
            not isinstance(action_id, str)
            or len(action_id) > RESOLVER_OBJECT_ACTION_MAX_ID_LENGTH
            or not isinstance(kind, str)
            or len(kind) > RESOLVER_OBJECT_ACTION_MAX_ID_LENGTH
        ):
            return False
        href = action.get("href")
        if isinstance(href, str) and len(href) > RESOLVER_OBJECT_ACTION_MAX_URL_LENGTH:
            return False
        inputs = action.get("inputs")
        if isinstance(inputs, list) and len(inputs) > RESOLVER_OBJECT_ACTION_MAX_INPUTS:
            return False
        return EmbedContextService._resolver_value_strings_within_limit(action)

    @staticmethod
    def _resolver_value_strings_within_limit(value: Any) -> bool:
        """Bound every nested resolver string without changing the shared Chat protocol."""
        if isinstance(value, str):
            return len(value) <= RESOLVER_OBJECT_ACTION_MAX_STRING_LENGTH
        if isinstance(value, dict):
            return all(
                EmbedContextService._resolver_value_strings_within_limit(key)
                and EmbedContextService._resolver_value_strings_within_limit(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return all(
                EmbedContextService._resolver_value_strings_within_limit(item)
                for item in value
            )
        return True
